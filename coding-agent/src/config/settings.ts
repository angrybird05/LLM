import 'dotenv/config';
import { z } from 'zod';

const envSchema = z.object({
  DATABASE_URL: z.string().default('postgresql://postgres:postgres@localhost:5433/coding_agent'),
  PGVECTOR_DIMENSIONS: z.coerce.number().default(768),
  DB_POOL_SIZE: z.coerce.number().default(10),
  SQLITE_DB_PATH: z.string().default('./data/coding-agent.db'),
  
  OPENAI_API_KEY: z.string().optional(),
  ANTHROPIC_API_KEY: z.string().optional(),
  GROQ_API_KEY: z.string().optional(),
  OPENROUTER_API_KEY: z.string().optional(),
  
  OLLAMA_BASE_URL: z.string().default('http://localhost:11434'),
  OLLAMA_MODEL: z.string().default('qwen2.5-coder:7b'),
  OLLAMA_EMBEDDING_MODEL: z.string().default('nomic-embed-text'),
  
  LLM_PROVIDER: z.enum(['openai', 'anthropic', 'ollama', 'groq', 'openrouter']).default('openrouter'),
  LLM_MODEL: z.string().optional(),
  
  AGENT_MAX_ITERATIONS: z.coerce.number().default(50),
  AGENT_TOKEN_BUDGET: z.coerce.number().default(128000),
  AGENT_TEMPERATURE: z.coerce.number().default(0.1),
  
  LOG_LEVEL: z.enum(['trace', 'debug', 'info', 'warn', 'error']).default('info'),
  LOG_PRETTY: z.coerce.boolean().default(true),
  
  SANDBOX_WORKSPACE: z.string().default('/tmp/coding-agent-workspace'),
  SANDBOX_TIMEOUT_MS: z.coerce.number().default(30000),
});

const env = envSchema.parse(process.env);

// Validate API keys
if (env.LLM_PROVIDER !== 'ollama' && !env.OPENROUTER_API_KEY && !env.OPENAI_API_KEY && !env.ANTHROPIC_API_KEY && !env.GROQ_API_KEY) {
  console.warn('[Config] Warning: No valid API key found for selected provider. Falling back to Ollama.');
}

export const config = {
  databaseUrl: env.DATABASE_URL,
  pgvectorDimensions: env.PGVECTOR_DIMENSIONS,
  dbPoolSize: env.DB_POOL_SIZE,
  sqliteDbPath: env.SQLITE_DB_PATH,
  
  openaiApiKey: env.OPENAI_API_KEY,
  anthropicApiKey: env.ANTHROPIC_API_KEY,
  groqApiKey: env.GROQ_API_KEY,
  openrouterApiKey: env.OPENROUTER_API_KEY,
  
  ollamaBaseUrl: env.OLLAMA_BASE_URL,
  ollamaModel: env.OLLAMA_MODEL,
  ollamaEmbeddingModel: env.OLLAMA_EMBEDDING_MODEL,
  
  llmProvider: env.LLM_PROVIDER,
  llmModel: env.LLM_MODEL,
  
  agentMaxIterations: env.AGENT_MAX_ITERATIONS,
  agentTokenBudget: env.AGENT_TOKEN_BUDGET,
  agentTemperature: env.AGENT_TEMPERATURE,
  
  logLevel: env.LOG_LEVEL,
  logPretty: env.LOG_PRETTY,
  
  sandboxWorkspace: env.SANDBOX_WORKSPACE,
  sandboxTimeoutMs: env.SANDBOX_TIMEOUT_MS,
} as const;

export type Config = typeof config;