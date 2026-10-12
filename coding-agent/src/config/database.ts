import pg from 'pg';
import pgvector from 'pgvector';
import sqlite3 from 'sqlite3';
import { open, Database } from 'sqlite';
import { config } from './settings.js';
import { mkdirSync, existsSync } from 'fs';
import { dirname } from 'path';
import type { QueryResultRow } from 'pg';

const { Pool } = pg;

export type DatabaseType = 'postgresql' | 'sqlite';

let dbType: DatabaseType = 'postgresql';
let pgPool: pg.Pool | null = null;
let sqliteDb: Database | null = null;

function getSqlitePath(): string {
  const path = config.sqliteDbPath || './data/coding-agent.db';
  const dir = dirname(path);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  return path;
}

async function initializeSqlite(): Promise<Database> {
  const db = await open({
    filename: getSqlitePath(),
    driver: sqlite3.Database,
  });

  await db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now')),
      metadata TEXT DEFAULT '{}'
    );
  `);

  await db.exec(`
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      tool_calls TEXT,
      tool_call_id TEXT,
      name TEXT,
      tokens_used INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    );
  `);

  await db.exec(`
    CREATE TABLE IF NOT EXISTS checkpoints (
      id TEXT PRIMARY KEY,
      session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
      label TEXT NOT NULL,
      state TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    );
  `);

  await db.exec(`
    CREATE TABLE IF NOT EXISTS embeddings (
      id TEXT PRIMARY KEY,
      session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
      content_hash TEXT NOT NULL,
      content_text TEXT NOT NULL,
      embedding TEXT NOT NULL,
      metadata TEXT DEFAULT '{}',
      created_at TEXT DEFAULT (datetime('now'))
    );
  `);

  await db.exec(`
    CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_checkpoints_session_id ON checkpoints(session_id);
    CREATE INDEX IF NOT EXISTS idx_embeddings_session_id ON embeddings(session_id);
    CREATE INDEX IF NOT EXISTS idx_embeddings_content_hash ON embeddings(content_hash);
  `);

  console.log('[Database] SQLite initialized successfully');
  return db;
}

async function tryPostgreSQL(): Promise<boolean> {
  try {
    const testPool = new Pool({
      connectionString: config.databaseUrl,
      max: 1,
      connectionTimeoutMillis: 3000,
    });

    await testPool.query('SELECT 1');
    await testPool.end();
    return true;
  } catch {
    return false;
  }
}

export async function initializeDatabase(): Promise<void> {
  const pgAvailable = await tryPostgreSQL();

  if (pgAvailable) {
    dbType = 'postgresql';
    pgPool = new Pool({
      connectionString: config.databaseUrl,
      max: config.dbPoolSize,
      idleTimeoutMillis: 30000,
      connectionTimeoutMillis: 5000,
    });

    pgPool.on('error', (err) => {
      console.error('Unexpected database pool error:', err);
    });

    await pgPool.query(`CREATE EXTENSION IF NOT EXISTS vector;`);

    await pgPool.query(`
      CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(255) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        metadata JSONB DEFAULT '{}'
      );
    `);

    await pgPool.query(`
      CREATE TABLE IF NOT EXISTS messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
        role VARCHAR(50) NOT NULL,
        content TEXT NOT NULL,
        tool_calls JSONB,
        tool_call_id VARCHAR(255),
        name VARCHAR(255),
        tokens_used INT DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    await pgPool.query(`
      CREATE TABLE IF NOT EXISTS checkpoints (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
        label VARCHAR(255) NOT NULL,
        state JSONB NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    await pgPool.query(`
      CREATE TABLE IF NOT EXISTS embeddings (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
        content_hash VARCHAR(64) NOT NULL,
        content_text TEXT NOT NULL,
        embedding vector(${config.pgvectorDimensions}) NOT NULL,
        metadata JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    await pgPool.query(`
      CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
      CREATE INDEX IF NOT EXISTS idx_checkpoints_session_id ON checkpoints(session_id);
      CREATE INDEX IF NOT EXISTS idx_embeddings_session_id ON embeddings(session_id);
      CREATE INDEX IF NOT EXISTS idx_embeddings_content_hash ON embeddings(content_hash);
    `);

    await pgPool.query(`
      CREATE INDEX IF NOT EXISTS idx_embeddings_vector ON embeddings 
      USING hnsw (embedding vector_cosine_ops)
      WITH (m = 16, ef_construction = 64);
    `);

    console.log('[Database] PostgreSQL with pgvector initialized successfully');
  } else {
    dbType = 'sqlite';
    sqliteDb = await initializeSqlite();
    console.log('[Database] Using SQLite fallback');
  }
}

export function getDatabaseType(): DatabaseType {
  return dbType;
}

export interface QueryResult<T = any> {
  rows: T[];
  rowCount: number | null;
}

export async function query<T extends QueryResultRow = QueryResultRow>(text: string, params?: any[]): Promise<QueryResult<T>> {
  if (dbType === 'postgresql') {
    if (!pgPool) throw new Error('PostgreSQL pool not initialized');
    const client = await pgPool.connect();
    try {
      const result = await client.query<T>(text, params);
      return { rows: result.rows, rowCount: result.rowCount };
    } finally {
      client.release();
    }
  } else {
    if (!sqliteDb) throw new Error('SQLite database not initialized');
    
    const sql = convertToSQLite(text);
    
    if (text.trim().toUpperCase().startsWith('SELECT')) {
      const rows = params ? await sqliteDb.all(sql, ...params) : await sqliteDb.all(sql);
      return { rows: rows as T[], rowCount: rows.length };
    } else {
      const result = params ? await sqliteDb.run(sql, ...params) : await sqliteDb.run(sql);
      return { rows: [], rowCount: result.changes };
    }
  }
}

function convertToSQLite(sql: string): string {
  return sql
    .replace(/gen_random_uuid\(\)/g, "(lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))),2) || '-' || substr('89ab',abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))),2) || '-' || lower(hex(randomblob(6))))")
    .replace(/TIMESTAMPTZ/g, 'TEXT')
    .replace(/JSONB/g, 'TEXT')
    .replace(/vector\(\d+\)/g, 'TEXT')
    .replace(/NOW\(\)/g, "datetime('now')")
    .replace(/\$(\d+)/g, '?')
    .replace(/ON CONFLICT \(id\) DO UPDATE SET/g, 'ON CONFLICT(id) DO UPDATE SET')
    .replace(/RETURNING id/g, '');
}

export async function getClient(): Promise<pg.PoolClient | Database> {
  if (dbType === 'postgresql') {
    if (!pgPool) throw new Error('PostgreSQL pool not initialized');
    return pgPool.connect();
  } else {
    if (!sqliteDb) throw new Error('SQLite database not initialized');
    return sqliteDb;
  }
}

export function vectorToArray(vec: number[]): string {
  return '[' + vec.join(',') + ']';
}

export function arrayToVector(vec: string): number[] {
  if (vec.startsWith('[')) {
    return vec.slice(1, -1).split(',').map(Number);
  }
  return vec.split(',').map(Number);
}

export async function closePool(): Promise<void> {
  if (pgPool) {
    await pgPool.end();
    pgPool = null;
  }
  if (sqliteDb) {
    await sqliteDb.close();
    sqliteDb = null;
  }
}