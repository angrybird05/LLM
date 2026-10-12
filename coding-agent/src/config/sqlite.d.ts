declare module 'sqlite' {
  import sqlite3 from 'sqlite3';
  
  export interface Database {
    exec(sql: string): Promise<void>;
    all<T>(sql: string, ...params: any[]): Promise<T[]>;
    run(sql: string, ...params: any[]): Promise<{ changes: number; lastID: number }>;
    get<T>(sql: string, ...params: any[]): Promise<T | undefined>;
    close(): Promise<void>;
  }

  export interface OpenOptions {
    filename: string;
    driver: typeof sqlite3.Database;
  }

  export function open(options: OpenOptions): Promise<Database>;
}