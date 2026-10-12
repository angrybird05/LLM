import { z } from 'zod';
import { toolRegistry } from '../registry.js';

export function registerWebTools(): void {
  toolRegistry.registerTool({
    name: 'fetch',
    description: 'Fetch a URL',
    schema: z.object({
      url: z.string().url().describe('URL to fetch'),
      method: z.enum(['GET', 'POST', 'PUT', 'DELETE']).optional().default('GET'),
      headers: z.record(z.string()).optional(),
      body: z.string().optional(),
      timeout: z.number().optional().default(30000),
    }),
    execute: async ({ url, method = 'GET', headers, body, timeout = 30000 }: { url: string; method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; headers?: Record<string, string>; body?: string; timeout?: number }) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeout);

      try {
        const response = await fetch(url as string, {
          method,
          headers,
          body,
          signal: controller.signal,
        });

        clearTimeout(timeoutId);
        const text = await response.text();
        return `Status: ${response.status}\n${text}`;
      } catch (error) {
        clearTimeout(timeoutId);
        throw new Error(`Fetch failed: ${error}`);
      }
    },
  });

  toolRegistry.registerTool({
    name: 'search',
    description: 'Search the web (requires search API)',
    schema: z.object({
      query: z.string().describe('Search query'),
      numResults: z.number().optional().default(5),
    }),
    execute: async ({ query, numResults = 5 }: { query: string; numResults?: number }) => {
      return `Search not implemented. Query: ${query}. Use fetch with a search engine URL instead.`;
    },
  });
}