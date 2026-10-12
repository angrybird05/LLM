export interface PermissionRule {
  tool: string;
  allowed: boolean;
  conditions?: Record<string, any>;
}

export interface PermissionContext {
  tool: string;
  args: Record<string, any>;
  sessionId: string;
}

export class PermissionManager {
  private rules: PermissionRule[] = [
    { tool: 'read_file', allowed: true },
    { tool: 'write_file', allowed: true },
    { tool: 'edit_file', allowed: true },
    { tool: 'glob', allowed: true },
    { tool: 'grep', allowed: true },
    { tool: 'bash', allowed: true, conditions: { maxTimeout: 30000 } },
    { tool: 'git_status', allowed: true },
    { tool: 'git_diff', allowed: true },
    { tool: 'git_log', allowed: true },
    { tool: 'git_commit', allowed: true },
    { tool: 'fetch', allowed: true },
    { tool: 'search', allowed: true },
    { tool: 'todo_write', allowed: true },
    { tool: 'todo_read', allowed: true },
    { tool: 'todo_update', allowed: true },
  ];

  addRule(rule: PermissionRule): void {
    const existingIndex = this.rules.findIndex(r => r.tool === rule.tool);
    if (existingIndex >= 0) {
      this.rules[existingIndex] = rule;
    } else {
      this.rules.push(rule);
    }
  }

  removeRule(tool: string): void {
    this.rules = this.rules.filter(r => r.tool !== tool);
  }

  checkPermission(context: PermissionContext): { allowed: boolean; reason?: string } {
    const rule = this.rules.find(r => r.tool === context.tool);

    if (!rule) {
      return { allowed: false, reason: `No permission rule for tool: ${context.tool}` };
    }

    if (!rule.allowed) {
      return { allowed: false, reason: `Tool ${context.tool} is not allowed` };
    }

    if (rule.conditions) {
      if (rule.conditions.maxTimeout && context.args.timeout) {
        if (context.args.timeout > rule.conditions.maxTimeout) {
          return { allowed: false, reason: `Timeout exceeds maximum allowed: ${rule.conditions.maxTimeout}ms` };
        }
      }
    }

    return { allowed: true };
  }

  getRules(): PermissionRule[] {
    return [...this.rules];
  }
}

export const permissionManager = new PermissionManager();