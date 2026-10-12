import { z } from 'zod';
import { toolRegistry } from '../registry.js';
import { config } from '../../config/settings.js';
import { spawn } from 'child_process';

function runGit(args: string[], cwd: string): Promise<{ stdout: string; stderr: string; code: number }> {
  return new Promise((resolve) => {
    const proc = spawn('git', args, { cwd, env: { ...process.env, PATH: process.env.PATH } });
    let stdout = '';
    let stderr = '';
    proc.stdout?.on('data', (d) => { stdout += d.toString(); });
    proc.stderr?.on('data', (d) => { stderr += d.toString(); });
    proc.on('close', (code) => resolve({ stdout, stderr, code: code ?? 0 }));
  });
}

export function registerGitTools(): void {
  toolRegistry.registerTool({
    name: 'git_status',
    description: 'Get git status',
    schema: z.object({
      cwd: z.string().optional().describe('Working directory'),
    }),
    execute: async ({ cwd }: { cwd?: string }) => {
      const workingDir = cwd || config.sandboxWorkspace;
      const result = await runGit(['status', '--porcelain'], workingDir);
      return result.stdout || 'Clean working tree';
    },
  });

  toolRegistry.registerTool({
    name: 'git_diff',
    description: 'Get git diff',
    schema: z.object({
      cwd: z.string().optional().describe('Working directory'),
      staged: z.boolean().optional().default(false),
    }),
    execute: async ({ cwd, staged = false }: { cwd?: string; staged?: boolean }) => {
      const workingDir = cwd || config.sandboxWorkspace;
      const args = ['diff'];
      if (staged) args.push('--staged');
      const result = await runGit(args, workingDir);
      return result.stdout || 'No changes';
    },
  });

  toolRegistry.registerTool({
    name: 'git_log',
    description: 'Get git log',
    schema: z.object({
      cwd: z.string().optional().describe('Working directory'),
      limit: z.number().optional().default(10),
    }),
    execute: async ({ cwd, limit = 10 }: { cwd?: string; limit?: number }) => {
      const workingDir = cwd || config.sandboxWorkspace;
      const result = await runGit(['log', '--oneline', `-${limit}`], workingDir);
      return result.stdout || 'No commits';
    },
  });

  toolRegistry.registerTool({
    name: 'git_commit',
    description: 'Create a git commit',
    schema: z.object({
      message: z.string().describe('Commit message'),
      cwd: z.string().optional().describe('Working directory'),
      addAll: z.boolean().optional().default(true),
    }),
    execute: async ({ message, cwd, addAll = true }: { message: string; cwd?: string; addAll?: boolean }) => {
      const workingDir = cwd || config.sandboxWorkspace;
      if (addAll) {
        await runGit(['add', '-A'], workingDir);
      }
      const result = await runGit(['commit', '-m', message as string], workingDir);
      return result.stdout || result.stderr || 'Committed';
    },
  });
}