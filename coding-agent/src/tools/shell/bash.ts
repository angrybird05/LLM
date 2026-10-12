import { z } from 'zod';
import { config } from '../../config/settings.js';
import { sandboxExecutor } from '../../sandbox/executor.js';

export const bashTool = {
  name: 'bash',
  description: 'Execute a shell command',
  schema: z.object({
    command: z.string().describe('Command to execute'),
    cwd: z.string().optional().describe('Working directory'),
    timeout: z.number().optional().default(config.sandboxTimeoutMs),
  }),
  execute: async ({ command, cwd, timeout = config.sandboxTimeoutMs }: { command: string; cwd?: string; timeout?: number }) => {
    const result = await sandboxExecutor.executeShell(command, { cwd, timeout });
    if (result.success) {
      return result.stdout || 'Command completed successfully (no output)';
    } else {
      throw new Error(`Command failed with code ${result.exitCode}: ${result.stderr}`);
    }
  },
};