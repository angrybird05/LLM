import { providerManager, CustomProviderConfig } from '../config/provider_manager.js';

export async function handleProviderCommand(args: string[]): Promise<boolean> {
  const subcommand = args[0];

  switch (subcommand) {
    case 'list':
      return listProviders();
    case 'add':
      return await addProvider();
    case 'remove':
      return removeProvider(args[1]);
    case 'use':
      return useProvider(args[1]);
    case 'current':
      return showCurrentProvider();
    case 'help':
    default:
      showProviderHelp();
      return true;
  }
}

function listProviders(): boolean {
  const providers = providerManager.listProviders();
  const active = providerManager.getActiveProviderName();
  
  console.log('\n=== Configured Providers ===');
  if (providers.length === 0) {
    console.log('No providers configured. Use "provider add" to add one.');
    return true;
  }

  for (const p of providers) {
    const marker = p.name === active ? '> ' : '  ';
    const status = p.apiKey ? '✓' : (p.type === 'ollama' ? '✓' : '✗ (no API key)');
    console.log(`${marker}${p.name} (${p.type}) - ${p.model} [${status}]`);
  }
  return true;
}

async function addProvider(): Promise<boolean> {
  const config = await providerManager.promptForProvider();
  if (config) {
    console.log(`\n✓ Added provider: ${config.name}`);
    console.log(`  Type: ${config.type}`);
    console.log(`  Model: ${config.model}`);
    if (config.baseUrl) console.log(`  Base URL: ${config.baseUrl}`);
  }
  return true;
}

function removeProvider(name?: string): boolean {
  if (!name) {
    console.log('Usage: provider remove <name>');
    return true;
  }
  if (providerManager.removeProvider(name)) {
    console.log(`Removed provider: ${name}`);
  } else {
    console.log(`Provider not found: ${name}`);
  }
  return true;
}

function useProvider(name?: string): boolean {
  if (!name) {
    console.log('Usage: provider use <name>');
    return true;
  }
  if (providerManager.setActiveProvider(name)) {
    console.log(`Active provider: ${name}`);
  } else {
    console.log(`Provider not found: ${name}`);
    console.log('Available:', providerManager.listProviders().map(p => p.name).join(', '));
  }
  return true;
}

function showCurrentProvider(): boolean {
  const current = providerManager.getActiveProvider();
  if (current) {
    console.log(`\nCurrent: ${current.name} (${current.type})`);
    console.log(`Model: ${current.model}`);
    if (current.baseUrl) console.log(`Base URL: ${current.baseUrl}`);
    console.log(`Temperature: ${current.temperature}`);
    console.log(`Max Tokens: ${current.maxTokens}`);
  } else {
    console.log('No active provider');
  }
  return true;
}

function showProviderHelp(): void {
  console.log(`
Provider Management Commands:
  provider list           - List all configured providers
  provider add            - Interactively add a new provider
  provider remove <name>  - Remove a provider
  provider use <name>     - Set active provider
  provider current        - Show current provider details
  provider help           - Show this help

Examples:
  provider add
  provider use groq
  provider remove old-provider
`);
}