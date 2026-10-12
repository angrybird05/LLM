import { toolRegistry } from '../registry.js';
import { bashTool } from './bash.js';

export function registerShellTools(): void {
  toolRegistry.registerTool(bashTool);
}