import { BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage } from '@langchain/core/messages';
import { toolDefinitionToOpenAIFunction } from './tool_schema.js';

export interface PromptBuilderOptions {
  systemPrompt: string;
  tools: any[];
  maxTokens?: number;
  includeToolSchemas?: boolean;
}

export class PromptBuilder {
  private systemPrompt: string;
  private tools: any[];
  private maxTokens: number;
  private includeToolSchemas: boolean;

  constructor(options: PromptBuilderOptions) {
    this.systemPrompt = options.systemPrompt;
    this.tools = options.tools || [];
    this.maxTokens = options.maxTokens || 128000;
    this.includeToolSchemas = options.includeToolSchemas ?? true;
  }

  buildSystemPrompt(): string {
    let prompt = this.systemPrompt;

    if (this.includeToolSchemas && this.tools.length > 0) {
      prompt += '\n\n## Available Tools\n';
      prompt += 'You have access to the following tools. Use them when appropriate.\n\n';
      
      for (const tool of this.tools) {
        const schema = toolDefinitionToOpenAIFunction(tool);
        const func = schema.function;
        prompt += `### ${func.name}\n`;
        prompt += `${func.description}\n\n`;
        prompt += 'Parameters:\n';
        prompt += JSON.stringify(func.parameters, null, 2);
        prompt += '\n\n';
      }

      prompt += '## Tool Use Guidelines\n';
      prompt += '- Think step by step before calling tools\n';
      prompt += '- Call tools with valid JSON arguments matching the schema\n';
      prompt += '- Prefer multiple tool calls in a single response when possible\n';
      prompt += '- Wait for tool results before continuing\n';
    }

    return prompt;
  }

  formatMessages(messages: BaseMessage[]): string {
    return messages.map(m => this.formatMessage(m)).join('\n\n');
  }

  private formatMessage(message: BaseMessage): string {
    const type = message._getType();
    const content = message.content;
    
    switch (type) {
      case 'human':
        return `User: ${content}`;
      case 'ai':
        const toolCalls = (message as any).tool_calls;
        if (toolCalls && toolCalls.length > 0) {
          const calls = toolCalls.map((tc: any) => 
            `  - ${tc.name}(${JSON.stringify(tc.args)})`
          ).join('\n');
          return `Assistant: ${content}\nTool Calls:\n${calls}`;
        }
        return `Assistant: ${content}`;
      case 'tool':
        return `Tool Result (${(message as any).tool_call_id}): ${content}`;
      case 'system':
        return `System: ${content}`;
      default:
        return `${type}: ${content}`;
    }
  }

  buildPrompt(messages: BaseMessage[]): { systemPrompt: string; prompt: string } {
    return {
      systemPrompt: this.buildSystemPrompt(),
      prompt: this.formatMessages(messages),
    };
  }

  estimateTokens(text: string): number {
    return Math.ceil(text.length / 4);
  }

  truncateHistory(messages: BaseMessage[], maxTokens: number): BaseMessage[] {
    let totalTokens = 0;
    const result: BaseMessage[] = [];
    
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      const tokens = this.estimateTokens(String(msg.content));
      if (totalTokens + tokens > maxTokens && result.length > 0) {
        break;
      }
      result.unshift(msg);
      totalTokens += tokens;
    }
    
    return result;
  }
}

export function createPromptBuilder(options: PromptBuilderOptions): PromptBuilder {
  return new PromptBuilder(options);
}