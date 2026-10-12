import { z } from 'zod';

export interface ToolDefinition {
  name: string;
  description: string;
  schema: z.ZodSchema<any>;
  execute(args: Record<string, unknown>): Promise<string>;
}

function createTool(def: ToolDefinition): any {
  return {
    name: def.name,
    description: def.description,
    schema: def.schema as any,
    returnDirect: false,
    lc_namespace: ['coding-agent', 'tools'],
    verbose: false,
    lc_attributes: {},
    getName: () => def.name,
    call: async (args: string) => {
      try {
        const parsed = JSON.parse(args);
        return def.execute(parsed);
      } catch {
        return def.execute({});
      }
    },
    invoke: async (args: Record<string, unknown>) => def.execute(args),
    invokeWithState: async (args: Record<string, unknown>) => def.execute(args),
    _call: async (args: Record<string, unknown>) => def.execute(args),
    bind: () => createTool(def),
    map: () => createTool(def),
    withRetry: () => createTool(def),
    withConfig: () => createTool(def),
    withListeners: () => createTool(def),
    lc_runnable: true,
  };
}

export class ToolRegistry {
  private tools = new Map<string, any>();

  registerTool(def: ToolDefinition): void {
    this.tools.set(def.name, createTool(def));
  }

  getTool(name: string): any | undefined {
    return this.tools.get(name);
  }

  listTools(): Array<{ name: string; description: string }> {
    return Array.from(this.tools.values()).map(t => ({ name: t.name, description: t.description }));
  }

  getLangChainTools(): any[] {
    return Array.from(this.tools.values());
  }

  hasTool(name: string): boolean {
    return this.tools.has(name);
  }

  async invokeTool(toolCall: any): Promise<string> {
    const tool = this.tools.get(toolCall.name);
    if (!tool) {
      throw new Error(`Tool ${toolCall.name} not found`);
    }
    return tool.invoke(toolCall.args);
  }
}

export const toolRegistry = new ToolRegistry();