import { config } from '../config/settings.js';
import { pathGuard } from './path_guard.js';
import { permissionManager, PermissionContext } from './permissions.js';
import { spawn } from 'child_process';

export interface ExecutionResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number;
  duration: number;
}

export interface SandboxConfig {
  workspace: string;
  timeout: number;
  allowedCommands?: string[];
  blockedCommands?: string[];
}

export class SandboxExecutor {
  private executorConfig: SandboxConfig;

  constructor(options?: Partial<SandboxConfig>) {
    this.executorConfig = {
      workspace: options?.workspace || config.sandboxWorkspace,
      timeout: options?.timeout || config.sandboxTimeoutMs,
      allowedCommands: options?.allowedCommands,
      blockedCommands: options?.blockedCommands || ['rm -rf', 'sudo', 'su', 'chmod 777'],
    };
  }

  async executeCommand(
    command: string,
    args: string[] = [],
    options: { cwd?: string; timeout?: number; env?: Record<string, string> } = {}
  ): Promise<ExecutionResult> {
    const startTime = Date.now();
    const workingDir = options.cwd ? pathGuard.validatePath(options.cwd) : this.executorConfig.workspace;

    const permissionContext: PermissionContext = {
      tool: 'bash',
      args: { command: `${command} ${args.join(' ')}`, cwd: workingDir, timeout: options.timeout },
      sessionId: 'default',
    };

    const permission = permissionManager.checkPermission(permissionContext);
    if (!permission.allowed) {
      return {
        success: false,
        stdout: '',
        stderr: `Permission denied: ${permission.reason}`,
        exitCode: -1,
        duration: Date.now() - startTime,
      };
    }

    if (this.isCommandBlocked(command, args)) {
      return {
        success: false,
        stdout: '',
        stderr: `Command blocked for security: ${command} ${args.join(' ')}`,
        exitCode: -1,
        duration: Date.now() - startTime,
      };
    }

    return new Promise((resolve) => {
      const timeout = options.timeout || this.executorConfig.timeout;
      const proc = spawn(command, args, {
        cwd: workingDir,
        timeout,
        env: { ...process.env, ...options.env, PATH: process.env.PATH },
        shell: process.platform === 'win32',
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data) => { stdout += data.toString(); });
      proc.stderr?.on('data', (data) => { stderr += data.toString(); });

      proc.on('close', (code) => {
        resolve({
          success: code === 0,
          stdout,
          stderr,
          exitCode: code ?? 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on('error', (err) => {
        resolve({
          success: false,
          stdout,
          stderr: err.message,
          exitCode: -1,
          duration: Date.now() - startTime,
        });
      });
    });
  }

  async executeShell(command: string, options: { cwd?: string; timeout?: number } = {}): Promise<ExecutionResult> {
    const startTime = Date.now();
    const workingDir = options.cwd ? pathGuard.validatePath(options.cwd) : this.executorConfig.workspace;

    const permissionContext: PermissionContext = {
      tool: 'bash',
      args: { command, cwd: workingDir, timeout: options.timeout },
      sessionId: 'default',
    };

    const permission = permissionManager.checkPermission(permissionContext);
    if (!permission.allowed) {
      return {
        success: false,
        stdout: '',
        stderr: `Permission denied: ${permission.reason}`,
        exitCode: -1,
        duration: Date.now() - startTime,
      };
    }

    const shell = process.platform === 'win32' ? 'cmd.exe' : 'bash';
    const shellArgs = process.platform === 'win32' ? ['/c', command] : ['-c', command];

    return new Promise((resolve) => {
      const timeout = options.timeout || this.executorConfig.timeout;
      const proc = spawn(shell, shellArgs, {
        cwd: workingDir,
        timeout,
        env: { ...process.env, PATH: process.env.PATH },
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data) => { stdout += data.toString(); });
      proc.stderr?.on('data', (data) => { stderr += data.toString(); });

      proc.on('close', (code) => {
        resolve({
          success: code === 0,
          stdout,
          stderr,
          exitCode: code ?? 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on('error', (err) => {
        resolve({
          success: false,
          stdout,
          stderr: err.message,
          exitCode: -1,
          duration: Date.now() - startTime,
        });
      });
    });
  }

  private isCommandBlocked(command: string, args: string[]): boolean {
    const fullCommand = `${command} ${args.join(' ')}`.toLowerCase();
    return this.executorConfig.blockedCommands?.some(blocked => fullCommand.includes(blocked.toLowerCase())) ?? false;
  }

  getConfig(): SandboxConfig {
    return { ...this.executorConfig };
  }

  updateConfig(updates: Partial<SandboxConfig>): void {
    this.executorConfig = { ...this.executorConfig, ...updates };
  }
}

export const sandboxExecutor = new SandboxExecutor();