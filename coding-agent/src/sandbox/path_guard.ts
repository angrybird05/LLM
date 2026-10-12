import { config } from '../config/settings.js';
import { resolve, isAbsolute, relative } from 'path';

export class PathGuard {
  private workspace: string;

  constructor(workspace?: string) {
    this.workspace = workspace || config.sandboxWorkspace;
  }

  resolvePath(path: string): string {
    const resolved = isAbsolute(path) ? path : resolve(this.workspace, path);
    return resolved;
  }

  validatePath(path: string): string {
    const resolved = this.resolvePath(path);
    const relativePath = relative(this.workspace, resolved);

    if (relativePath.startsWith('..') || relativePath.startsWith('.')) {
      throw new Error(`Path traversal attempt blocked: ${path}`);
    }

    return resolved;
  }

  isWithinWorkspace(path: string): boolean {
    try {
      this.validatePath(path);
      return true;
    } catch {
      return false;
    }
  }

  getWorkspace(): string {
    return this.workspace;
  }
}

export const pathGuard = new PathGuard();