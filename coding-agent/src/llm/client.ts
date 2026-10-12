import { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { config } from '../config/settings.js';
import { HumanMessage, SystemMessage, AIMessage, BaseMessage, BaseMessageChunk, ToolMessage } from '@langchain/core/messages';
import { CallbackManagerForLLMRun } from '@langchain/core/callbacks/manager';
import { providerManager, CustomProviderConfig } from '../config/provider_manager.js';
import { toolDefinitionToOpenAIFunction } from './tool_schema.js';
import { StreamingHandler } from './streaming.js';

export interface LLMRequest {
  prompt: string;
  systemPrompt?: string;
  temperature?: number;
  maxTokens?: number;
  tools?: any[];
  toolChoice?: 'auto' | 'none' | { type: 'function'; function: { name: string } };
}

export interface LLMResponse {
  content: string;
  toolCalls?: Array<{
    id: string;
    name: string;
    args: Record<string, any>;
  }>;
  usage?: {
    inputTokens: number;
    outputTokens: number;
  };
}

export type LLMProvider = 'openai' | 'anthropic' | 'ollama' | 'groq' | 'openrouter' | 'custom';

export class LLMClient {
  private model: BaseChatModel;
  private providerConfig: CustomProviderConfig;

  constructor(providerName?: string) {
    const provider = providerName 
      ? providerManager.getProvider(providerName)
      : providerManager.getActiveProvider();
    
    if (!provider) {
      throw new Error(`No provider configured. Run 'provider add' to set one up.`);
    }
    
    this.providerConfig = provider;
    this.model = this.createModel(provider);
  }

  switchProvider(providerName: string): void {
    const provider = providerManager.getProvider(providerName);
    if (!provider) {
      throw new Error(`Provider '${providerName}' not found`);
    }
    this.providerConfig = provider;
    this.model = this.createModel(provider);
    providerManager.setActiveProvider(providerName);
  }

  getCurrentProvider(): CustomProviderConfig {
    return this.providerConfig;
  }

  getAvailableProviders(): CustomProviderConfig[] {
    return providerManager.listProviders();
  }

  private createModel(provider: CustomProviderConfig): BaseChatModel {
    switch (provider.type) {
      case 'openai': {
        const { ChatOpenAI } = require('@langchain/openai');
        return new ChatOpenAI({
          apiKey: provider.apiKey,
          model: provider.model,
          temperature: provider.temperature ?? 0.1,
          maxTokens: provider.maxTokens ?? 4000,
        } as any);
      }
      case 'anthropic': {
        const { ChatAnthropic } = require('@langchain/anthropic');
        return new ChatAnthropic({
          apiKey: provider.apiKey,
          model: provider.model,
          temperature: provider.temperature ?? 0.1,
          maxTokens: provider.maxTokens ?? 4000,
        } as any);
      }
      case 'ollama': {
        const { ChatOllama } = require('@langchain/ollama');
        return new ChatOllama({
          baseUrl: provider.baseUrl || 'http://localhost:11434',
          model: provider.model,
          temperature: provider.temperature ?? 0.1,
          maxTokens: provider.maxTokens ?? 4000,
        } as any);
      }
      case 'groq': {
        const { ChatGroq } = require('@langchain/groq');
        return new ChatGroq({
          apiKey: provider.apiKey,
          model: provider.model,
          temperature: provider.temperature ?? 0.1,
          maxTokens: provider.maxTokens ?? 4000,
        } as any);
      }
      case 'openrouter':
      case 'custom': {
        const { BaseChatModel: LangChainBaseChatModel } = require('@langchain/core/language_models/chat_models');
        const { AIMessage, BaseMessage, BaseMessageChunk } = require('@langchain/core/messages');
        
        class CustomProviderModel extends LangChainBaseChatModel {
          _model: string;
          _apiKey: string | undefined;
          _baseUrl: string;
          _temperature: number;
          _maxTokens: number;
          _headers: Record<string, string>;

          constructor(params: CustomProviderConfig) {
            super({});
            this._model = params.model;
            this._apiKey = params.apiKey;
            this._baseUrl = params.baseUrl || 'https://openrouter.ai/api/v1';
            this._temperature = params.temperature ?? 0.1;
            this._maxTokens = params.maxTokens ?? 4000;
            this._headers = params.headers || {};
          }

          _llmType(): string {
            return this._model;
          }

          async _generate(
            messages: BaseMessage[],
            options?: any,
            _runManager?: CallbackManagerForLLMRun
          ): Promise<any> {
            const formattedMessages = messages.map(m => ({
              role: m._getType() === 'human' ? 'user' : m._getType() === 'ai' ? 'assistant' : m._getType(),
              content: m.content,
              ...(m._getType() === 'tool' ? { tool_call_id: (m as any).tool_call_id } : {}),
            }));

            // Handle tool calls from AIMessage
            const lastMessage = messages[messages.length - 1];
            if (lastMessage._getType() === 'ai' && (lastMessage as any).tool_calls) {
              formattedMessages[formattedMessages.length - 1] = {
                role: 'assistant',
                content: lastMessage.content,
                tool_calls: (lastMessage as any).tool_calls.map((tc: any) => ({
                  id: tc.id,
                  type: 'function',
                  function: {
                    name: tc.name,
                    arguments: JSON.stringify(tc.args),
                  },
                })),
              } as any;
            }

            const headers: Record<string, string> = {
              'Content-Type': 'application/json',
              ...this._headers
            };
            
            if (this._apiKey) {
              headers['Authorization'] = `Bearer ${this._apiKey}`;
            }

            const requestBody: any = {
              model: this._model,
              messages: formattedMessages,
              temperature: this._temperature,
              max_tokens: this._maxTokens,
            };

            // Add tools if provided in options
            if (options?.tools && options.tools.length > 0) {
              requestBody.tools = options.tools;
              requestBody.tool_choice = options.toolChoice || 'auto';
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 60000);

            let response;
            try {
              response = await fetch(`${this._baseUrl}/chat/completions`, {
                method: 'POST',
                headers,
                body: JSON.stringify(requestBody),
                signal: controller.signal,
              });
            } catch (error) {
              clearTimeout(timeoutId);
              if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('Provider API request timed out after 60 seconds');
              }
              throw error;
            }
            clearTimeout(timeoutId);

            if (!response.ok) {
              const error = await response.text();
              throw new Error(`Provider API error: ${response.status} - ${error}`);
            }

            const data: any = await response.json();
            const choice = data.choices?.[0];
            const message = choice?.message;
            
            let content = message?.content || '';
            let toolCalls: any[] = [];

            if (message?.tool_calls) {
              toolCalls = message.tool_calls.map((tc: any) => ({
                id: tc.id,
                name: tc.function.name,
                args: JSON.parse(tc.function.arguments || '{}'),
              }));
              // If there are tool calls but no content, set content to empty string
              if (!content) content = '';
            }

            const aiMessage = new AIMessage(content);
            if (toolCalls.length > 0) {
              (aiMessage as any).tool_calls = toolCalls;
            }
            
            return {
              generations: [{ message: aiMessage }],
              llmOutput: {}
            };
          }

          async *_streamResponseChunks(
            messages: BaseMessage[],
            options?: any,
            _runManager?: CallbackManagerForLLMRun
          ): AsyncGenerator<BaseMessageChunk> {
            const formattedMessages = messages.map(m => ({
              role: m._getType() === 'human' ? 'user' : m._getType() === 'ai' ? 'assistant' : m._getType(),
              content: m.content,
              ...(m._getType() === 'tool' ? { tool_call_id: (m as any).tool_call_id } : {}),
            }));

            const lastMessage = messages[messages.length - 1];
            if (lastMessage._getType() === 'ai' && (lastMessage as any).tool_calls) {
              formattedMessages[formattedMessages.length - 1] = {
                role: 'assistant',
                content: lastMessage.content,
                tool_calls: (lastMessage as any).tool_calls.map((tc: any) => ({
                  id: tc.id,
                  type: 'function',
                  function: {
                    name: tc.name,
                    arguments: JSON.stringify(tc.args),
                  },
                })),
              } as any;
            }

            const headers: Record<string, string> = {
              'Content-Type': 'application/json',
              ...this._headers
            };
            
            if (this._apiKey) {
              headers['Authorization'] = `Bearer ${this._apiKey}`;
            }

            const requestBody: any = {
              model: this._model,
              messages: formattedMessages,
              temperature: this._temperature,
              max_tokens: this._maxTokens,
              stream: true,
            };

            if (options?.tools && options.tools.length > 0) {
              requestBody.tools = options.tools;
              requestBody.tool_choice = options.toolChoice || 'auto';
            }

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 60000);

            let response;
            try {
              response = await fetch(`${this._baseUrl}/chat/completions`, {
                method: 'POST',
                headers,
                body: JSON.stringify(requestBody),
                signal: controller.signal,
              });
            } catch (error) {
              clearTimeout(timeoutId);
              if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('Provider API request timed out after 60 seconds');
              }
              throw error;
            }
            clearTimeout(timeoutId);

            if (!response.ok) {
              const error = await response.text();
              throw new Error(`Provider API error: ${response.status} - ${error}`);
            }

            const reader = response.body?.getReader();
            if (!reader) return;

            const decoder = new TextDecoder();
            let buffer = '';
            let accumulatedContent = '';
            let accumulatedToolCalls: any[] = [];

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              
              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() || '';
              
              for (const line of lines) {
                if (line.startsWith('data: ')) {
                  const data = line.slice(6);
                  if (data === '[DONE]') return;
                  try {
                    const parsed = JSON.parse(data);
                    const delta = parsed.choices?.[0]?.delta;
                    if (delta?.content) {
                      accumulatedContent += delta.content;
                      yield new BaseMessageChunk(delta.content);
                    }
                    if (delta?.tool_calls) {
                      for (const tc of delta.tool_calls) {
                        if (tc.index !== undefined) {
                          while (accumulatedToolCalls.length <= tc.index) {
                            accumulatedToolCalls.push({ id: '', name: '', args: '' });
                          }
                          if (tc.id) accumulatedToolCalls[tc.index].id = tc.id;
                          if (tc.function?.name) accumulatedToolCalls[tc.index].name = tc.function.name;
                          if (tc.function?.arguments) accumulatedToolCalls[tc.index].args += tc.function.arguments;
                        }
                      }
                    }
                  } catch {
                    // Ignore parse errors
                  }
                }
              }
            }
          }
        }

        return new CustomProviderModel(provider) as any;
      }
      default:
        throw new Error(`Unknown provider type: ${provider.type}`);
    }
  }

  async complete(request: LLMRequest): Promise<LLMResponse> {
    const messages = [];
    
    if (request.systemPrompt) {
      messages.push(new SystemMessage(request.systemPrompt));
    }
    messages.push(new HumanMessage(request.prompt));

    const options: any = {};
    if (request.tools && request.tools.length > 0) {
      options.tools = request.tools.map(t => toolDefinitionToOpenAIFunction(t));
      options.toolChoice = request.toolChoice || 'auto';
    }

    const response = await this.model.invoke(messages, options);
    
    const toolCalls = (response as any).tool_calls || [];
    
    return {
      content: response.content as string,
      toolCalls: toolCalls.map((tc: any) => ({
        id: tc.id,
        name: tc.name,
        args: tc.args,
      })),
      usage: {
        inputTokens: 0,
        outputTokens: 0,
      },
    };
  }

  async stream(request: LLMRequest, onChunk: (chunk: string) => void): Promise<LLMResponse> {
    const messages = [];
    
    if (request.systemPrompt) {
      messages.push(new SystemMessage(request.systemPrompt));
    }
    messages.push(new HumanMessage(request.prompt));

    const options: any = {};
    if (request.tools && request.tools.length > 0) {
      options.tools = request.tools.map(t => toolDefinitionToOpenAIFunction(t));
      options.toolChoice = request.toolChoice || 'auto';
    }

    let fullContent = '';
    let toolCalls: any[] = [];
    
    const stream = await this.model.stream(messages, options);
    
    for await (const chunk of stream) {
      const content = chunk.content as string;
      fullContent += content;
      onChunk(content);
      
      // Check for tool calls in chunk
      if ((chunk as any).tool_calls) {
        toolCalls = (chunk as any).tool_calls;
      }
    }

    return {
      content: fullContent,
      toolCalls: toolCalls.map((tc: any) => ({
        id: tc.id,
        name: tc.name,
        args: tc.args,
      })),
      usage: { inputTokens: 0, outputTokens: 0 },
    };
  }

  async streamWithHandler(request: LLMRequest, handler: StreamingHandler): Promise<LLMResponse> {
    const messages = [];
    
    if (request.systemPrompt) {
      messages.push(new SystemMessage(request.systemPrompt));
    }
    messages.push(new HumanMessage(request.prompt));

    const options: any = {};
    if (request.tools && request.tools.length > 0) {
      options.tools = request.tools.map(t => toolDefinitionToOpenAIFunction(t));
      options.toolChoice = request.toolChoice || 'auto';
    }

    const stream = await this.model.stream(messages, options);
    
    for await (const chunk of stream) {
      const content = chunk.content as string;
      if (content) {
        handler.handleToken(content);
      }
      
      // Check for tool calls in chunk
      if ((chunk as any).tool_calls) {
        for (const tc of (chunk as any).tool_calls) {
          handler.handleToolCallDelta(tc);
        }
      }
    }
    
    handler.handleToolCallComplete();
    return handler.handleComplete();
  }

  getModel(): BaseChatModel {
    return this.model;
  }

  getProviderConfig(): CustomProviderConfig {
    return this.providerConfig;
  }
}