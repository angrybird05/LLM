import { BaseMessage, HumanMessage, AIMessage } from '@langchain/core/messages';
import { LLMClient } from '../llm/client.js';

export interface TaskItem {
  id: string;
  title: string;
  description?: string;
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  dependencies: string[];
  createdAt: Date;
  updatedAt: Date;
}

export interface Plan {
  id: string;
  goal: string;
  tasks: TaskItem[];
  createdAt: Date;
}

export class TaskPlanner {
  private plans: Map<string, Plan> = new Map();
  private llmClient: LLMClient;

  constructor(llmClient: LLMClient) {
    this.llmClient = llmClient;
  }

  async createPlan(goal: string): Promise<Plan> {
    const prompt = `Break down this goal into actionable tasks:
Goal: ${goal}

Return a JSON array of tasks with: id, title, description, dependencies (array of task ids)`;
    
    const response = await this.llmClient.complete({
      prompt,
      systemPrompt: 'You are a task planner. Output only valid JSON.',
      temperature: 0.1,
    });

    try {
      const tasksData = JSON.parse(response.content);
      const tasks: TaskItem[] = tasksData.map((t: any, i: number) => ({
        id: t.id || `task-${i}`,
        title: t.title,
        description: t.description,
        status: 'pending' as const,
        dependencies: t.dependencies || [],
        createdAt: new Date(),
        updatedAt: new Date(),
      }));

      const plan: Plan = {
        id: `plan-${Date.now()}`,
        goal,
        tasks,
        createdAt: new Date(),
      };

      this.plans.set(plan.id, plan);
      return plan;
    } catch {
      const task: TaskItem = {
        id: `task-${Date.now()}`,
        title: goal,
        status: 'pending',
        dependencies: [],
        createdAt: new Date(),
        updatedAt: new Date(),
      };
      const plan: Plan = {
        id: `plan-${Date.now()}`,
        goal,
        tasks: [task],
        createdAt: new Date(),
      };
      this.plans.set(plan.id, plan);
      return plan;
    }
  }

  addTask(planId: string, title: string, description?: string, dependencies: string[] = []): TaskItem | null {
    const plan = this.plans.get(planId);
    if (!plan) return null;

    const task: TaskItem = {
      id: `task-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
      title,
      description,
      status: 'pending',
      dependencies,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    plan.tasks.push(task);
    return task;
  }

  updateTaskStatus(planId: string, taskId: string, status: TaskItem['status']): boolean {
    const plan = this.plans.get(planId);
    if (!plan) return false;

    const task = plan.tasks.find(t => t.id === taskId);
    if (!task) return false;

    task.status = status;
    task.updatedAt = new Date();
    return true;
  }

  getNextTask(planId: string): TaskItem | null {
    const plan = this.plans.get(planId);
    if (!plan) return null;

    return plan.tasks.find(t => 
      t.status === 'pending' && 
      t.dependencies.every(depId => {
        const dep = plan.tasks.find(d => d.id === depId);
        return dep?.status === 'completed';
      })
    ) ?? null;
  }

  getPlan(planId: string): Plan | undefined {
    return this.plans.get(planId);
  }

  getAllPlans(): Plan[] {
    return Array.from(this.plans.values());
  }
}