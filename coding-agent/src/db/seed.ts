import { sessionStore } from '../memory/session_store.js';
import { initializeDatabase, closePool } from '../config/database.js';

async function main(): Promise<void> {
  console.log('[Seed] Creating sample data...');
  
  try {
    await initializeDatabase();
    
    const session = await sessionStore.createSession('sample-session', { sample: true });
    console.log(`Created session: ${session.id}`);
    
    await sessionStore.addMessage({
      sessionId: session.id,
      role: 'user',
      content: 'Create a hello world function in TypeScript',
      tokensUsed: 10,
    });
    
    await sessionStore.addMessage({
      sessionId: session.id,
      role: 'assistant',
      content: 'Here is a hello world function:\n\n```typescript\nexport function hello(): string {\n  return "Hello, World!";\n}\n```',
      tokensUsed: 30,
    });
    
    console.log('Sample data created successfully');
  } catch (error) {
    console.error('[Seed] Error:', error);
    process.exit(1);
  } finally {
    await closePool();
  }
}

main();