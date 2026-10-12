import { LLMClient } from '../llm/client.js';
import { ToolRegistry } from '../tools/registry.js';

export interface SubagentConfig {
  name: string;
  task: string;
  allowedTools: string[];
}

export class Subagent {
  private llmClient: LLMClient;
  private toolRegistry: ToolRegistry;

  constructor(
    config: SubagentConfig,
    llmClient: LLMClient,
    toolRegistry: ToolRegistry
  ) {
    this.llmClient = llmClient;
    this.toolRegistry = toolRegistry;
  }

  async run(): Promise<string> {
    const tools = this.toolRegistry.getLangChainTools()
      .filter(t => this.config.allowedTools.includes(t.name));
    
    const response = await this.llmClient.complete({
      prompt: this.config.task,
      systemPrompt: `You are a subagent: ${this.config.name}. Available tools: ${tools.map(t => t.name).join(', ')}`,
      temperature: 0.1,
    });

    return response.content;
  }

  private get config(): SubagentConfig {
    return {} as SubagentConfig;
  }
}