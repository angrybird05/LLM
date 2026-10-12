export interface StreamCallbacks {
  onToken?: (token: string) => void;
  onToolCall?: (toolCall: { id: string; name: string; args: Record<string, any> }) => void;
  onToolResult?: (toolCallId: string, result: string) => void;
  onError?: (error: Error) => void;
  onComplete?: (response: { content: string; toolCalls: any[] }) => void;
}

export class StreamingHandler {
  private callbacks: StreamCallbacks;
  private accumulatedContent: string = '';
  private accumulatedToolCalls: Map<number, { id: string; name: string; args: string }> = new Map();
  private currentToolCallIndex: number = -1;

  constructor(callbacks: StreamCallbacks = {}) {
    this.callbacks = callbacks;
  }

  handleToken(token: string): void {
    this.accumulatedContent += token;
    this.callbacks.onToken?.(token);
  }

  handleToolCallDelta(delta: any): void {
    if (delta.index !== undefined) {
      this.currentToolCallIndex = delta.index;
      
      if (!this.accumulatedToolCalls.has(delta.index)) {
        this.accumulatedToolCalls.set(delta.index, { id: '', name: '', args: '' });
      }
      
      const tc = this.accumulatedToolCalls.get(delta.index)!;
      
      if (delta.id) tc.id = delta.id;
      if (delta.function?.name) tc.name = delta.function.name;
      if (delta.function?.arguments) tc.args += delta.function.arguments;
    }
  }

  handleToolCallComplete(): void {
    if (this.currentToolCallIndex >= 0) {
      const tc = this.accumulatedToolCalls.get(this.currentToolCallIndex);
      if (tc && tc.name) {
        try {
          const args = JSON.parse(tc.args || '{}');
          this.callbacks.onToolCall?.({
            id: tc.id,
            name: tc.name,
            args,
          });
        } catch (e) {
          // Invalid JSON, skip
        }
      }
    }
  }

  handleToolResult(toolCallId: string, result: string): void {
    this.callbacks.onToolResult?.(toolCallId, result);
  }

  handleError(error: Error): void {
    this.callbacks.onError?.(error);
  }

  handleComplete(): { content: string; toolCalls: any[] } {
    const toolCalls: any[] = [];
    for (const [, tc] of this.accumulatedToolCalls) {
      if (tc.name) {
        try {
          toolCalls.push({
            id: tc.id,
            name: tc.name,
            args: JSON.parse(tc.args || '{}'),
          });
        } catch (e) {
          // Skip invalid
        }
      }
    }
    
    const result = {
      content: this.accumulatedContent,
      toolCalls,
    };
    
    this.callbacks.onComplete?.(result);
    return result;
  }

  reset(): void {
    this.accumulatedContent = '';
    this.accumulatedToolCalls.clear();
    this.currentToolCallIndex = -1;
  }
}

export function createStreamingHandler(callbacks: StreamCallbacks): StreamingHandler {
  return new StreamingHandler(callbacks);
}