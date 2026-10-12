import { StateGraph } from '@langchain/langgraph';
import { BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage } from '@langchain/core/messages';
import { config } from '../config/settings.js';
import { SessionStore, sessionStore } from '../memory/session_store.js';
import { ProjectIndex } from '../memory/project_index.js';
import { CheckpointManager, AgentState } from '../memory/checkpoint.js';
import { ToolRegistry } from '../tools/registry.js';
import { LLMClient } from '../llm/client.js';
import { PromptBuilder } from '../llm/prompt_builder.js';
import { TaskPlanner, Plan, TaskItem } from './planner.js';
import { StreamingHandler } from '../llm/streaming.js';

export interface GraphState {
  messages: BaseMessage[];
  sessionId: string;
  currentTask: string;
  iteration: number;
  maxIterations: number;
  toolsUsed: string[];
  shouldContinue: boolean;
  agentState: AgentState;
  plan: Plan | null;
  currentTaskIndex: number;
  streamingHandler?: StreamingHandler;
  onToken?: (token: string) => void;
}

export class AgentGraph {
  private graph: any;
  private llmClient: LLMClient;
  private toolRegistry: ToolRegistry;
  private sessionStore: SessionStore;
  private projectIndex: ProjectIndex;
  private checkpointManager: CheckpointManager;
  private promptBuilder: PromptBuilder;
  private taskPlanner: TaskPlanner;

  constructor(
    llmClient: LLMClient,
    toolRegistry: ToolRegistry,
    sessionStoreInstance?: SessionStore
  ) {
    this.llmClient = llmClient;
    this.toolRegistry = toolRegistry;
    this.sessionStore = sessionStoreInstance || sessionStore;
    this.projectIndex = new ProjectIndex('');
    this.checkpointManager = new CheckpointManager(this.sessionStore);
    this.promptBuilder = new PromptBuilder({
      systemPrompt: '',
      tools: toolRegistry.getLangChainTools(),
      maxTokens: config.agentTokenBudget,
      includeToolSchemas: true,
    });
    this.taskPlanner = new TaskPlanner(llmClient);

    this.graph = new StateGraph<GraphState>({
      channels: {
        messages: {
          value: (x: BaseMessage[], y: BaseMessage[]) => [...x, ...y],
          default: () => [],
        },
        sessionId: {
          value: (x: string, y: string) => y,
          default: () => '',
        },
        currentTask: {
          value: (x: string, y: string) => y,
          default: () => '',
        },
        iteration: {
          value: (x: number, y: number) => y,
          default: () => 0,
        },
        maxIterations: {
          value: (x: number, y: number) => y,
          default: () => config.agentMaxIterations,
        },
        toolsUsed: {
          value: (x: string[], y: string[]) => [...x, ...y],
          default: () => [],
        },
        shouldContinue: {
          value: (x: boolean, y: boolean) => y,
          default: () => true,
        },
        agentState: {
          value: (x: AgentState, y: AgentState) => y,
          default: () => ({} as AgentState),
        },
        plan: {
          value: (x: Plan | null, y: Plan | null) => y,
          default: () => null,
        },
        currentTaskIndex: {
          value: (x: number, y: number) => y,
          default: () => 0,
        },
      },
    });

    this.setupNodes();
    this.setupEdges();
  }

  private setupNodes(): void {
    (this.graph as any).addNode('createPlan', this.planNode.bind(this));
    (this.graph as any).addNode('think', this.thinkNode.bind(this));
    (this.graph as any).addNode('act', this.actNode.bind(this));
    (this.graph as any).addNode('observe', this.observeNode.bind(this));
    (this.graph as any).addNode('checkpoint', this.checkpointNode.bind(this));
  }

  private setupEdges(): void {
    (this.graph as any).addEdge('createPlan', 'think');
    (this.graph as any).addEdge('think', 'act');
    (this.graph as any).addEdge('act', 'observe');
    (this.graph as any).addEdge('observe', 'checkpoint');
    (this.graph as any).addEdge('checkpoint', '__end__');
    (this.graph as any).setEntryPoint('createPlan');
  }

  private async planNode(state: GraphState): Promise<Partial<GraphState>> {
    // Create plan if not already created
    if (!state.plan) {
      console.log(`[AgentGraph] Creating plan for task: ${state.currentTask}`);
      const plan = await this.taskPlanner.createPlan(state.currentTask);
      console.log(`[AgentGraph] Plan created with ${plan.tasks.length} tasks`);
      
      // Mark first task as in_progress
      if (plan.tasks.length > 0) {
        this.taskPlanner.updateTaskStatus(plan.id, plan.tasks[0].id, 'in_progress');
      }
      
      return {
        plan,
        currentTaskIndex: 0,
        currentTask: plan.tasks[0]?.title || state.currentTask,
      };
    }
    return {};
  }

  private async thinkNode(state: GraphState): Promise<Partial<GraphState>> {
    // Update prompt builder with current task
    this.promptBuilder = new PromptBuilder({
      systemPrompt: `You are an autonomous coding agent. Your task: ${state.currentTask}

Guidelines:
- Think step by step before acting
- Use tools to accomplish the task
- Be concise and precise
- Stop when the task is complete`,
      tools: this.toolRegistry.getLangChainTools(),
      maxTokens: config.agentTokenBudget,
      includeToolSchemas: true,
    });

    const { systemPrompt, prompt } = this.promptBuilder.buildPrompt(state.messages);
    const messages = [new SystemMessage(systemPrompt), ...state.messages];
    
    // Get available tools and convert to LLM format
    const availableTools = this.toolRegistry.getLangChainTools();

    // Create streaming handler with callback for token output
    const handler = new StreamingHandler({
      onToken: (token: string) => {
        state.onToken?.(token);
      },
      onToolCall: (toolCall) => {
        console.log(`\n[Tool Call] ${toolCall.name}(${JSON.stringify(toolCall.args)})`);
      },
      onToolResult: (toolCallId, result) => {
        console.log(`\n[Tool Result] ${toolCallId}: ${result.substring(0, 100)}...`);
      },
      onError: (error) => {
        console.error(`\n[Stream Error] ${error.message}`);
      },
      onComplete: (response) => {
        console.log('\n[Stream Complete]');
      },
    });

    const response = await this.llmClient.streamWithHandler({
      prompt,
      systemPrompt,
      temperature: config.agentTemperature,
      tools: availableTools,
      toolChoice: 'auto',
    }, handler);

    const aiMessage = new AIMessage(response.content);
    // Attach tool calls to the message if present
    if (response.toolCalls && response.toolCalls.length > 0) {
      (aiMessage as any).tool_calls = response.toolCalls;
    }
    return {
      messages: [aiMessage],
      iteration: state.iteration + 1,
      agentState: {
        ...state.agentState,
        messages: [...state.agentState.messages, aiMessage],
        iteration: state.iteration + 1,
      },
    };
  }

  private async actNode(state: GraphState): Promise<Partial<GraphState>> {
    const lastMessage = state.messages[state.messages.length - 1];
    if (!(lastMessage instanceof AIMessage)) {
      return { shouldContinue: false };
    }

    const toolCalls = (lastMessage as any).tool_calls;
    if (!toolCalls || toolCalls.length === 0) {
      return { shouldContinue: false };
    }

    const toolMessages: ToolMessage[] = [];
    const newToolsUsed: string[] = [];
    
    for (const toolCall of toolCalls) {
      try {
        const result = await this.toolRegistry.invokeTool(toolCall);
        toolMessages.push(new ToolMessage({
          content: result,
          tool_call_id: toolCall.id,
        }));
        newToolsUsed.push(toolCall.name);
      } catch (error) {
        toolMessages.push(new ToolMessage({
          content: `Error: ${error}`,
          tool_call_id: toolCall.id,
        }));
        newToolsUsed.push(toolCall.name);
      }
    }

    return {
      messages: toolMessages,
      toolsUsed: [...state.toolsUsed, ...newToolsUsed],
      agentState: {
        ...state.agentState,
        toolsUsed: [...state.agentState.toolsUsed, ...newToolsUsed],
      },
      shouldContinue: toolMessages.length > 0,
    };
  }

  private async observeNode(state: GraphState): Promise<Partial<GraphState>> {
    const lastMessage = state.messages[state.messages.length - 1];
    
    if (state.iteration >= state.maxIterations) {
      return { shouldContinue: false };
    }

    if (lastMessage instanceof ToolMessage) {
      return { shouldContinue: true };
    }

    if (lastMessage instanceof AIMessage) {
      const toolCalls = (lastMessage as any).tool_calls;
      if (!toolCalls || toolCalls.length === 0) {
        // Current task completed, check if there are more tasks in the plan
        if (state.plan && state.currentTaskIndex < state.plan.tasks.length - 1) {
          const nextTaskIndex = state.currentTaskIndex + 1;
          const nextTask = state.plan.tasks[nextTaskIndex];
          
          // Mark current task as completed
          this.taskPlanner.updateTaskStatus(state.plan.id, state.plan.tasks[state.currentTaskIndex].id, 'completed');
          
          // Mark next task as in_progress
          this.taskPlanner.updateTaskStatus(state.plan.id, nextTask.id, 'in_progress');
          
          console.log(`[AgentGraph] Advancing to task ${nextTaskIndex + 1}/${state.plan.tasks.length}: ${nextTask.title}`);
          
          return {
            shouldContinue: true,
            currentTaskIndex: nextTaskIndex,
            currentTask: nextTask.title,
            plan: state.plan,
          };
        }
        // No more tasks, we're done
        return { shouldContinue: false };
      }
    }

    return { shouldContinue: true };
  }

  private async checkpointNode(state: GraphState): Promise<Partial<GraphState>> {
    console.log(`[AgentGraph] Creating checkpoint for session: ${state.sessionId}`);
    await this.checkpointManager.createCheckpoint(
      state.sessionId,
      `iteration-${state.iteration}`,
      state.agentState
    );
    return { shouldContinue: false };
  }

  async run(sessionId: string, task: string, onToken?: (token: string) => void): Promise<GraphState> {
    console.log(`[AgentGraph] Running agent for session: ${sessionId}`);
    const initialState: GraphState = {
      messages: [new HumanMessage(task)],
      sessionId,
      currentTask: task,
      iteration: 0,
      maxIterations: config.agentMaxIterations,
      toolsUsed: [],
      shouldContinue: true,
      agentState: {
        messages: [],
        currentTask: task,
        iteration: 0,
        toolsUsed: [],
        metadata: {},
      },
      plan: null,
      currentTaskIndex: 0,
      onToken,
    };

    const compiled = (this.graph as any).compile();
    const result = await compiled.invoke(initialState);
    return result;
  }
}