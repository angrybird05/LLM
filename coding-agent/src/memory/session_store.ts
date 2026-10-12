import { v4 as uuidv4 } from 'uuid';
import { config, query } from '../config/index.js';
import { EmbeddingService, embeddingService } from './embeddings.js';

export interface Session {
  id: string;
  name: string;
  createdAt: Date;
  updatedAt: Date;
  metadata: Record<string, any>;
}

export interface Message {
  id: string;
  sessionId: string;
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  toolCalls?: any[];
  toolCallId?: string;
  name?: string;
  tokensUsed: number;
  createdAt: Date;
}

export interface Checkpoint {
  id: string;
  sessionId: string;
  label: string;
  state: Record<string, any>;
  createdAt: Date;
}

export class SessionStore {
  private embeddingService: EmbeddingService;

  constructor(embeddingServiceInstance?: EmbeddingService) {
    this.embeddingService = embeddingServiceInstance || embeddingService;
  }

  async createSession(name: string, metadata: Record<string, any> = {}, id?: string): Promise<Session> {
    const sessionId = id || uuidv4();
    console.log(`[SessionStore] Creating session: ${sessionId}`);
    await query(
      `INSERT INTO sessions (id, name, metadata) VALUES ($1, $2, $3)
       ON CONFLICT (id) DO UPDATE SET name = $2, metadata = $3, updated_at = NOW()`,
      [sessionId, name, JSON.stringify(metadata)]
    );
    const session = await this.getSession(sessionId);
    if (!session) throw new Error('Failed to create session');
    console.log(`[SessionStore] Session created: ${session.id}`);
    return session;
  }

  async getSession(id: string): Promise<Session | null> {
    const result = await query(
      'SELECT * FROM sessions WHERE id = $1',
      [id]
    );
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      id: row.id,
      name: row.name,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      metadata: row.metadata,
    };
  }

  async listSessions(limit: number = 50, offset: number = 0): Promise<Session[]> {
    const result = await query(
      'SELECT * FROM sessions ORDER BY updated_at DESC LIMIT $1 OFFSET $2',
      [limit, offset]
    );
    return result.rows.map(row => ({
      id: row.id,
      name: row.name,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      metadata: row.metadata,
    }));
  }

  async updateSession(id: string, updates: Partial<Pick<Session, 'name' | 'metadata'>>): Promise<void> {
    const fields: string[] = [];
    const values: any[] = [];
    let paramIndex = 1;

    if (updates.name !== undefined) {
      fields.push(`name = $${paramIndex++}`);
      values.push(updates.name);
    }
    if (updates.metadata !== undefined) {
      fields.push(`metadata = $${paramIndex++}`);
      values.push(JSON.stringify(updates.metadata));
    }

    if (fields.length === 0) return;

    fields.push(`updated_at = NOW()`);
    values.push(id);

    await query(
      `UPDATE sessions SET ${fields.join(', ')} WHERE id = $${paramIndex}`,
      values
    );
  }

  async deleteSession(id: string): Promise<void> {
    await query('DELETE FROM sessions WHERE id = $1', [id]);
  }

  async addMessage(message: Omit<Message, 'id' | 'createdAt'>): Promise<Message> {
    const id = uuidv4();
    await query(
      `INSERT INTO messages (id, session_id, role, content, tool_calls, tool_call_id, name, tokens_used)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [id, message.sessionId, message.role, message.content,
       message.toolCalls ? JSON.stringify(message.toolCalls) : null,
       message.toolCallId ?? null, message.name ?? null, message.tokensUsed]
    );
    const msg = await this.getMessage(id);
    if (!msg) throw new Error('Failed to create message');
    return msg;
  }

  async getMessage(id: string): Promise<Message | null> {
    const result = await query('SELECT * FROM messages WHERE id = $1', [id]);
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      id: row.id,
      sessionId: row.session_id,
      role: row.role,
      content: row.content,
      toolCalls: row.tool_calls,
      toolCallId: row.tool_call_id,
      name: row.name,
      tokensUsed: row.tokens_used,
      createdAt: row.created_at,
    };
  }

  async getMessagesBySession(sessionId: string, limit: number = 100): Promise<Message[]> {
    const result = await query(
      'SELECT * FROM messages WHERE session_id = $1 ORDER BY created_at ASC LIMIT $2',
      [sessionId, limit]
    );
    return result.rows.map(row => ({
      id: row.id,
      sessionId: row.session_id,
      role: row.role,
      content: row.content,
      toolCalls: row.tool_calls,
      toolCallId: row.tool_call_id,
      name: row.name,
      tokensUsed: row.tokens_used,
      createdAt: row.created_at,
    }));
  }

  async createCheckpoint(sessionId: string, label: string, state: Record<string, any>): Promise<Checkpoint> {
    const id = uuidv4();
    console.log(`[SessionStore] Creating checkpoint for session: ${sessionId}`);
    await query(
      `INSERT INTO checkpoints (id, session_id, label, state) VALUES ($1, $2, $3, $4)`,
      [id, sessionId, label, JSON.stringify(state)]
    );
    const result = await query('SELECT * FROM checkpoints WHERE id = $1', [id]);
    const row = result.rows[0];
    return {
      id: row.id,
      sessionId: row.session_id,
      label: row.label,
      state: row.state,
      createdAt: row.created_at,
    };
  }

  async getCheckpoint(id: string): Promise<Checkpoint | null> {
    const result = await query('SELECT * FROM checkpoints WHERE id = $1', [id]);
    if (result.rows.length === 0) return null;
    const row = result.rows[0];
    return {
      id: row.id,
      sessionId: row.session_id,
      label: row.label,
      state: row.state,
      createdAt: row.created_at,
    };
  }

  async getCheckpointsBySession(sessionId: string): Promise<Checkpoint[]> {
    const result = await query(
      'SELECT * FROM checkpoints WHERE session_id = $1 ORDER BY created_at DESC',
      [sessionId]
    );
    return result.rows.map(row => ({
      id: row.id,
      sessionId: row.session_id,
      label: row.label,
      state: row.state,
      createdAt: row.created_at,
    }));
  }

  async restoreCheckpoint(checkpointId: string): Promise<Record<string, any> | null> {
    const checkpoint = await this.getCheckpoint(checkpointId);
    return checkpoint?.state ?? null;
  }

  async storeContextEmbedding(
    sessionId: string,
    content: string,
    metadata: Record<string, any> = {}
  ): Promise<void> {
    const embedding = await this.embeddingService.generateEmbedding(content);
    await this.embeddingService.storeEmbedding(sessionId, content, embedding, metadata);
  }

  async searchContext(
    sessionId: string,
    query: string,
    limit: number = 5,
    threshold: number = 0.7
  ): Promise<Array<{ content: string; similarity: number; metadata: any }>> {
    const queryEmbedding = await this.embeddingService.generateEmbedding(query);
    const results = await this.embeddingService.searchSimilar(sessionId, queryEmbedding, limit, threshold);
    return results.map(r => ({
      content: r.record.contentText,
      similarity: r.similarity,
      metadata: r.record.metadata,
    }));
  }
}

export const sessionStore = new SessionStore();