import { BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage } from '@langchain/core/messages';
import { config } from '../config/settings.js';

export class ContextManager {
  private messages: BaseMessage[] = [];
  private systemPrompt: string = '';
  private maxTokens: number;

  constructor(systemPrompt: string = '', maxTokens: number = config.agentTokenBudget) {
    this.systemPrompt = systemPrompt;
    this.maxTokens = maxTokens;
  }

  setSystemPrompt(prompt: string): void {
    this.systemPrompt = prompt;
  }

  addMessage(message: BaseMessage): void {
    this.messages.push(message);
    this.compactIfNeeded();
  }

  addMessages(messages: BaseMessage[]): void {
    this.messages.push(...messages);
    this.compactIfNeeded();
  }

  getMessages(): BaseMessage[] {
    const systemMsg = this.systemPrompt ? [new SystemMessage(this.systemPrompt)] : [];
    return [...systemMsg, ...this.messages];
  }

  getRawMessages(): BaseMessage[] {
    return [...this.messages];
  }

  clear(): void {
    this.messages = [];
  }

  getTokenCount(): number {
    return this.messages.reduce((acc, m) => acc + this.estimateTokens(String(m.content)), 0);
  }

  getMaxTokens(): number {
    return this.maxTokens;
  }

  setMaxTokens(max: number): void {
    this.maxTokens = max;
  }

  private compactIfNeeded(): void {
    while (this.getTokenCount() > this.maxTokens && this.messages.length > 1) {
      this.messages.shift();
    }
  }

  private estimateTokens(text: string): number {
    return Math.ceil(text.length / 4);
  }

  async summarize(context: BaseMessage[]): Promise<string> {
    const conversation = context.map(m => `${m.constructor.name}: ${m.content}`).join('\n');
    return `Summary of ${context.length} messages: ${conversation.substring(0, 500)}...`;
  }
}