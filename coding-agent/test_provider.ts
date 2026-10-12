import { providerManager, CustomProviderConfig } from './config/provider_manager.js';

// Add a test Groq provider
const testProvider: CustomProviderConfig = {
  name: 'groq',
  type: 'groq',
  apiKey: 'gsk_test_key',
  model: 'llama3-70b-8192',
  temperature: 0.1,
  maxTokens: 4000
};

providerManager.addProvider(testProvider);

console.log('Providers:', providerManager.listProviders().map(p => p.name));
console.log('Active:', providerManager.getActiveProviderName());

// Test switching
providerManager.setActiveProvider('groq');
console.log('Active after switch:', providerManager.getActiveProviderName());

const current = providerManager.getActiveProvider();
console.log('Current config:', current);