import { config } from './settings.js';

export interface CustomProviderConfig {
  name: string;
  type: 'openai' | 'anthropic' | 'ollama' | 'groq' | 'openrouter' | 'custom';
  apiKey?: string;
  baseUrl?: string;
  model: string;
  temperature?: number;
  maxTokens?: number;
  headers?: Record<string, string>;
}

export interface ProviderRegistry {
  providers: Map<string, CustomProviderConfig>;
  activeProvider: string;
}

export class ProviderManager {
  private registry: ProviderRegistry = {
    providers: new Map(),
    activeProvider: 'default'
  };

  constructor() {
    this.loadFromEnv();
  }

  private loadFromEnv(): void {
    const builtIn: CustomProviderConfig[] = [
      {
        name: 'openrouter',
        type: 'openrouter',
        apiKey: config.openrouterApiKey,
        baseUrl: 'https://openrouter.ai/api/v1',
        model: config.llmModel || 'meta-llama/llama-3.1-70b-instruct:free',
        temperature: config.agentTemperature,
        maxTokens: 4000
      },
      {
        name: 'ollama',
        type: 'ollama',
        baseUrl: config.ollamaBaseUrl,
        model: config.ollamaModel,
        temperature: config.agentTemperature,
        maxTokens: 4000
      },
      {
        name: 'groq',
        type: 'groq',
        apiKey: config.groqApiKey,
        model: 'llama3-70b-8192',
        temperature: config.agentTemperature,
        maxTokens: 4000
      },
      {
        name: 'openai',
        type: 'openai',
        apiKey: config.openaiApiKey,
        model: 'gpt-4-turbo-preview',
        temperature: config.agentTemperature,
        maxTokens: 4000
      },
      {
        name: 'anthropic',
        type: 'anthropic',
        apiKey: config.anthropicApiKey,
        model: 'claude-3-opus-20240229',
        temperature: config.agentTemperature,
        maxTokens: 4000
      }
    ];

    for (const provider of builtIn) {
      const hasValidKey = provider.apiKey && provider.apiKey !== 'your-openrouter-key-here' && provider.apiKey.trim() !== '';
      if (hasValidKey || provider.type === 'ollama') {
        this.registry.providers.set(provider.name, provider);
        console.log(`[ProviderManager] Registered: ${provider.name} (${provider.type})`);
      } else {
        console.log(`[ProviderManager] Skipped: ${provider.name} (no valid API key)`);
      }
    }

    const defaultProvider = config.llmProvider;
    if (this.registry.providers.has(defaultProvider)) {
      this.registry.activeProvider = defaultProvider;
      console.log(`[ProviderManager] Active provider: ${defaultProvider}`);
    } else if (this.registry.providers.size > 0) {
      const firstKey = this.registry.providers.keys().next().value;
      if (firstKey) {
        this.registry.activeProvider = firstKey;
        console.log(`[ProviderManager] Fallback to: ${firstKey}`);
      }
    } else {
      console.log('[ProviderManager] No providers available!');
    }
  }

  addProvider(config: CustomProviderConfig): void {
    this.registry.providers.set(config.name, config);
    if (this.registry.providers.size === 1) {
      this.registry.activeProvider = config.name;
    }
  }

  removeProvider(name: string): boolean {
    if (name === this.registry.activeProvider) {
      const remaining = Array.from(this.registry.providers.keys()).filter(k => k !== name);
      this.registry.activeProvider = remaining[0] || 'default';
    }
    return this.registry.providers.delete(name);
  }

  setActiveProvider(name: string): boolean {
    if (this.registry.providers.has(name)) {
      this.registry.activeProvider = name;
      return true;
    }
    return false;
  }

  getActiveProvider(): CustomProviderConfig | undefined {
    return this.registry.providers.get(this.registry.activeProvider);
  }

  getProvider(name: string): CustomProviderConfig | undefined {
    return this.registry.providers.get(name);
  }

  listProviders(): CustomProviderConfig[] {
    return Array.from(this.registry.providers.values());
  }

  getActiveProviderName(): string {
    return this.registry.activeProvider;
  }

  async promptForProvider(): Promise<CustomProviderConfig | null> {
    const readline = await import('readline');
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout
    });

    const ask = (q: string): Promise<string> => new Promise(r => rl.question(q, r));

    console.log('\n=== Add Custom Provider ===');
    console.log('Types: openai, anthropic, ollama, groq, openrouter, custom');
    
    const name = await ask('Provider name: ');
    if (!name.trim()) {
      rl.close();
      return null;
    }

    const type = await ask('Type: ') as CustomProviderConfig['type'];
    const model = await ask('Model name: ');
    const apiKey = await ask('API Key (optional): ');
    const baseUrl = await ask('Base URL (optional): ');
    const temperature = parseFloat(await ask('Temperature (default 0.1): ') || '0.1');
    const maxTokens = parseInt(await ask('Max tokens (default 4000): ') || '4000');

    rl.close();

    const config: CustomProviderConfig = {
      name: name.trim(),
      type: type || 'custom',
      model: model.trim(),
      apiKey: apiKey.trim() || undefined,
      baseUrl: baseUrl.trim() || undefined,
      temperature,
      maxTokens
    };

    this.addProvider(config);
    return config;
  }
}

export const providerManager = new ProviderManager();