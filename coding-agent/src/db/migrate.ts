import { initializeDatabase, closePool } from '../config/database.js';

async function main(): Promise<void> {
  console.log('[Migration] Starting database initialization...');
  
  try {
    await initializeDatabase();
    console.log('[Migration] Database initialized successfully');
  } catch (error) {
    console.error('[Migration] Failed to initialize database:', error);
    process.exit(1);
  } finally {
    await closePool();
  }
}

main();