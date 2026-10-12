import { z } from 'zod';
import { toolRegistry } from '../registry.js';
import { config } from '../../config/settings.js';
import { promises as fs } from 'fs';
import { resolve, isAbsolute, relative } from 'path';

function guardPath(path: string): string {
  const workspace = config.sandboxWorkspace;
  const resolved = isAbsolute(path) ? path : resolve(workspace, path);
  const relativePath = relative(workspace, resolved);
  
  if (relativePath.startsWith('..') || relativePath.startsWith('.')) {
    throw new Error(`Path traversal attempt blocked: ${path}`);
  }
  
  return resolved;
}

export function registerFsTools(): void {
  toolRegistry.registerTool({
    name: 'read_file',
    description: 'Read contents of a file',
    schema: z.object({
      path: z.string().describe('File path to read'),
      encoding: z.enum(['utf-8', 'base64']).optional().default('utf-8'),
    }),
    execute: async ({ path, encoding = 'utf-8' }: { path: string; encoding?: 'utf-8' | 'base64' }) => {
      const safePath = guardPath(path as string);
      const content = await fs.readFile(safePath, encoding as BufferEncoding);
      return content.toString();
    },
  });

  toolRegistry.registerTool({
    name: 'write_file',
    description: 'Write content to a file',
    schema: z.object({
      path: z.string().describe('File path to write'),
      content: z.string().describe('Content to write'),
      encoding: z.enum(['utf-8', 'base64']).optional().default('utf-8'),
    }),
    execute: async ({ path, content, encoding = 'utf-8' }: { path: string; content: string; encoding?: 'utf-8' | 'base64' }) => {
      const safePath = guardPath(path as string);
      await fs.mkdir(resolve(safePath, '..'), { recursive: true });
      await fs.writeFile(safePath, content as string, encoding as BufferEncoding);
      return `File written: ${path}`;
    },
  });

  toolRegistry.registerTool({
    name: 'edit_file',
    description: 'Edit a file by replacing a string',
    schema: z.object({
      path: z.string().describe('File path to edit'),
      oldString: z.string().describe('String to replace'),
      newString: z.string().describe('New string'),
      replaceAll: z.boolean().optional().default(false),
    }),
    execute: async ({ path, oldString, newString, replaceAll = false }: { path: string; oldString: string; newString: string; replaceAll?: boolean }) => {
      const safePath = guardPath(path as string);
      let content = await fs.readFile(safePath, 'utf-8');
      
      if (replaceAll) {
        content = content.replaceAll(oldString as string, newString as string);
      } else {
        if (!content.includes(oldString as string)) {
          throw new Error(`String not found in file: ${oldString}`);
        }
        content = content.replace(oldString as string, newString as string);
      }
      
      await fs.writeFile(safePath, content, 'utf-8');
      return `File edited: ${path}`;
    },
  });

  toolRegistry.registerTool({
    name: 'glob',
    description: 'Find files matching a glob pattern',
    schema: z.object({
      pattern: z.string().describe('Glob pattern to match'),
      cwd: z.string().optional().describe('Working directory'),
    }),
    execute: async ({ pattern, cwd }: { pattern: string; cwd?: string }) => {
      const { glob } = await import('glob');
      const searchCwd = cwd ? guardPath(cwd as string) : config.sandboxWorkspace;
      const files = await glob(pattern as string, { cwd: searchCwd, nodir: true });
      return JSON.stringify(files);
    },
  });

  toolRegistry.registerTool({
    name: 'grep',
    description: 'Search for pattern in files',
    schema: z.object({
      pattern: z.string().describe('Regex pattern to search'),
      path: z.string().optional().describe('Path to search in'),
      include: z.string().optional().describe('File pattern to include'),
    }),
    execute: async ({ pattern, path, include }: { pattern: string; path?: string; include?: string }) => {
      const { promises: fs } = await import('fs');
      const { resolve } = await import('path');
      
      const searchPath = path ? guardPath(path as string) : config.sandboxWorkspace;
      const regex = new RegExp(pattern as string);
      const results: Array<{ file: string; line: number; content: string }> = [];

      async function searchDir(dir: string): Promise<void> {
        const entries = await fs.readdir(dir, { withFileTypes: true });
        
        for (const entry of entries) {
          const fullPath = resolve(dir, entry.name);
          
          if (entry.isDirectory()) {
            if (!entry.name.startsWith('.') && entry.name !== 'node_modules') {
              await searchDir(fullPath);
            }
          } else if (entry.isFile()) {
            if (!include || entry.name.match(new RegExp(include as string))) {
              try {
                const content = await fs.readFile(fullPath, 'utf-8');
                const lines = content.split('\n');
                lines.forEach((line, i) => {
                  if (regex.test(line)) {
                    results.push({ file: fullPath, line: i + 1, content: line.trim() });
                  }
                });
              } catch {
                // Skip binary files
              }
            }
          }
        }
      }

      await searchDir(searchPath);
      return JSON.stringify(results.slice(0, 100));
    },
  });
}