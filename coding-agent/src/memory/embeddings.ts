import { config } from '../config/settings.js';
import { query, vectorToArray, arrayToVector } from '../config/database.js';
import { createHash } from 'crypto';

export interface EmbeddingRecord {
  id: string;
  sessionId: string;
  contentHash: string;
  contentText: string;
  embedding: number[];
  metadata: Record<string, any>;
  createdAt: Date;
}

export interface SimilaritySearchResult {
  record: EmbeddingRecord;
  similarity: number;
}

export class EmbeddingService {
  private dimensions: number;
  private baseUrl: string;
  private model: string;

  constructor(dimensions: number = config.pgvectorDimensions) {
    this.dimensions = dimensions;
    this.baseUrl = config.ollamaBaseUrl;
    this.model = config.ollamaEmbeddingModel;
  }

  async generateEmbedding(text: string): Promise<number[]> {
    try {
      const response = await fetch(`${this.baseUrl}/api/embeddings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: this.model,
          prompt: text,
        }),
      });

      if (!response.ok) {
        throw new Error(`Ollama embedding error: ${response.status}`);
      }

      const data = await response.json() as { embedding?: number[] };
      return data.embedding || new Array(this.dimensions).fill(0);
    } catch (error) {
      console.warn('[EmbeddingService] Failed to generate embedding, using fallback:', error);
      return this.fallbackEmbedding(text);
    }
  }

  private fallbackEmbedding(text: string): number[] {
    const hash = createHash('sha256').update(text).digest('hex');
    const embedding = new Array(this.dimensions).fill(0);
    for (let i = 0; i < this.dimensions; i++) {
      const byte = parseInt(hash.substr(i % 64, 2), 16);
      embedding[i] = (byte - 128) / 128;
    }
    return embedding;
  }

  async storeEmbedding(
    sessionId: string,
    contentText: string,
    embedding: number[],
    metadata: Record<string, any> = {}
  ): Promise<string> {
    const contentHash = this.hashContent(contentText);
    
    const existing = await query(
      'SELECT id FROM embeddings WHERE session_id = $1 AND content_hash = $2',
      [sessionId, contentHash]
    );

    const vecStr = vectorToArray(embedding);

    if (existing.rows.length > 0) {
      await query(
        `UPDATE embeddings SET embedding = $1, metadata = $2, created_at = NOW()
         WHERE session_id = $3 AND content_hash = $4`,
        [vecStr, JSON.stringify(metadata), sessionId, contentHash]
      );
      return existing.rows[0].id;
    }

    const result = await query(
      `INSERT INTO embeddings (session_id, content_hash, content_text, embedding, metadata)
       VALUES ($1, $2, $3, $4, $5) RETURNING id`,
      [sessionId, contentHash, contentText, vecStr, JSON.stringify(metadata)]
    );

    return result.rows[0].id;
  }

  async searchSimilar(
    sessionId: string,
    queryEmbedding: number[],
    limit: number = 10,
    threshold: number = 0.7
  ): Promise<SimilaritySearchResult[]> {
    const vecStr = vectorToArray(queryEmbedding);
    const result = await query(
      `SELECT id, session_id, content_hash, content_text, embedding, metadata, created_at,
              1 - (embedding <=> $1) AS similarity
       FROM embeddings
       WHERE session_id = $2 AND 1 - (embedding <=> $1) > $3
       ORDER BY embedding <=> $1
       LIMIT $4`,
      [vecStr, sessionId, threshold, limit]
    );

    return result.rows.map(row => ({
      record: {
        id: row.id,
        sessionId: row.session_id,
        contentHash: row.content_hash,
        contentText: row.content_text,
        embedding: arrayToVector(row.embedding),
        metadata: row.metadata,
        createdAt: row.created_at,
      },
      similarity: row.similarity,
    }));
  }

  async getEmbeddingsBySession(sessionId: string): Promise<EmbeddingRecord[]> {
    const result = await query(
      'SELECT * FROM embeddings WHERE session_id = $1 ORDER BY created_at DESC',
      [sessionId]
    );

    return result.rows.map(row => ({
      id: row.id,
      sessionId: row.session_id,
      contentHash: row.content_hash,
      contentText: row.content_text,
      embedding: arrayToVector(row.embedding),
      metadata: row.metadata,
      createdAt: row.created_at,
    }));
  }

  async deleteEmbeddingsBySession(sessionId: string): Promise<number> {
    const result = await query(
      'DELETE FROM embeddings WHERE session_id = $1',
      [sessionId]
    );
    return result.rowCount ?? 0;
  }

  private hashContent(content: string): string {
    return createHash('sha256').update(content).digest('hex');
  }
}

export const embeddingService = new EmbeddingService();