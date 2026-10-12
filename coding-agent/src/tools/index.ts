import { toolRegistry } from './registry.js';
import { registerFsTools } from './fs/index.js';
import { registerShellTools } from './shell/index.js';
import { registerGitTools } from './git/index.js';
import { registerWebTools } from './web/index.js';
import { registerTodoTools } from './todo/index.js';

export function registerAllTools(): void {
  registerFsTools();
  registerShellTools();
  registerGitTools();
  registerWebTools();
  registerTodoTools();
  console.log(`[Tools] Registered ${toolRegistry.listTools().length} tools`);
}

export { toolRegistry } from './registry.js';
export type { ToolDefinition } from './registry.js';