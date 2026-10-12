import { AgentGraph } from '../agent/loop.js';
import { LLMClient } from '../llm/client.js';
import { registerAllTools, toolRegistry } from '../tools/index.js';
import { sessionStore } from '../memory/session_store.js';
import { initializeDatabase, closePool } from '../config/database.js';
import { config } from '../config/settings.js';
import { providerManager } from '../config/provider_manager.js';
import { handleProviderCommand } from './provider_commands.js';
import { v4 as uuidv4 } from 'uuid';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  
  // Handle provider subcommands
  if (args[0] === 'provider') {
    const handled = await handleProviderCommand(args.slice(1));
    if (handled) process.exit(0);
  }

  console.log('=== Autonomous Coding Agent ===');
  
  const activeProvider = providerManager.getActiveProvider();
  if (activeProvider) {
    console.log(`Provider: ${activeProvider.name} (${activeProvider.type}) - ${activeProvider.model}`);
  } else {
    console.log('No provider configured. Run: provider add');
  }
  console.log('Initializing...');

  try {
    await initializeDatabase();
    
    registerAllTools();
    
    const llmClient = new LLMClient();
    
    const sessionId = uuidv4();
    await sessionStore.createSession('cli-session', { createdBy: 'cli' }, sessionId);
    
    const agent = new AgentGraph(llmClient, toolRegistry, sessionStore);
    
    console.log(`Session: ${sessionId}`);
    console.log(`Available tools: ${toolRegistry.listTools().map(t => t.name).join(', ')}`);
    console.log('\nCommands:');
    console.log('  provider <cmd>  - Manage providers (list, add, remove, use, current)');
    console.log('  exit / quit     - Exit the agent');
    console.log('\nEnter your task:');
    
    const readline = await import('readline');
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });

    const askQuestion = (): Promise<string> => {
      return new Promise((resolve) => {
        rl.question('> ', resolve);
      });
    };

    while (true) {
      const input = await askQuestion();
      const trimmed = input.trim();
      
      if (trimmed.toLowerCase() === 'exit' || trimmed.toLowerCase() === 'quit') {
        break;
      }
      
      if (!trimmed) continue;
      
      // Handle provider commands inline
      if (trimmed.startsWith('provider ')) {
        const cmdArgs = trimmed.slice(9).split(' ');
        await handleProviderCommand(cmdArgs);
        continue;
      }
      
      // Handle provider switching inline: "use groq"
      if (trimmed.startsWith('use ')) {
        const name = trimmed.slice(4).trim();
        if (providerManager.setActiveProvider(name)) {
          llmClient.switchProvider(name);
          console.log(`Switched to provider: ${name}`);
        } else {
          console.log(`Provider not found: ${name}`);
        }
        continue;
      }
      
      console.log('\nProcessing...\n');
      
      try {
        let streamingOutput = '';
        const result = await agent.run(sessionId, trimmed, (token: string) => {
          process.stdout.write(token);
          streamingOutput += token;
        });
        console.log('\n\n--- Result ---');
        console.log(`Iterations: ${result.iteration}`);
        console.log(`Tools used: ${result.toolsUsed.join(', ') || 'none'}`);
        const lastMsg = result.messages[result.messages.length - 1];
        if (lastMsg) {
          const content = typeof lastMsg.content === 'string' ? lastMsg.content : JSON.stringify(lastMsg.content);
          if (!streamingOutput.includes(content)) {
            console.log(`Response: ${content}`);
          }
        }
        console.log('--------------\n');
      } catch (error) {
        console.error('Error:', error);
      }
    }

    rl.close();
  } catch (error) {
    console.error('Fatal error:', error);
  } finally {
    await closePool();
    process.exit(0);
  }
}

main().catch((err) => {
  console.error('Fatal CLI Error:', err);
  process.exit(1);
});