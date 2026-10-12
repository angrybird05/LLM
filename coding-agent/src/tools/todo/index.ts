import { z } from 'zod';
import { toolRegistry } from '../registry.js';

interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  priority: 'high' | 'medium' | 'low';
}

const todos: Map<string, TodoItem> = new Map();

export function registerTodoTools(): void {
  toolRegistry.registerTool({
    name: 'todo_write',
    description: 'Write multiple todos at once',
    schema: z.object({
      todos: z.array(z.object({
        id: z.string().optional(),
        content: z.string(),
        status: z.enum(['pending', 'in_progress', 'completed']).default('pending'),
        priority: z.enum(['high', 'medium', 'low']).default('medium'),
      })).describe('List of todos to write'),
    }),
    execute: async ({ todos: todoList }: { todos: Array<{ id?: string; content: string; status?: 'pending' | 'in_progress' | 'completed'; priority?: 'high' | 'medium' | 'low' }> }) => {
      todos.clear();
      for (const todo of todoList) {
        const id = todo.id || `todo-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`;
        todos.set(id, { ...todo, id } as TodoItem);
      }
      return `Written ${todoList.length} todos`;
    },
  });

  toolRegistry.registerTool({
    name: 'todo_read',
    description: 'Read todos',
    schema: z.object({
      filter: z.enum(['all', 'pending', 'in_progress', 'completed']).optional().default('all'),
    }),
    execute: async ({ filter = 'all' }: { filter?: 'all' | 'pending' | 'in_progress' | 'completed' }) => {
      const items = Array.from(todos.values());
      const filtered = filter === 'all' 
        ? items 
        : items.filter(t => t.status === filter);
      return JSON.stringify(filtered, null, 2);
    },
  });

  toolRegistry.registerTool({
    name: 'todo_update',
    description: 'Update a todo',
    schema: z.object({
      id: z.string().describe('Todo ID'),
      content: z.string().optional(),
      status: z.enum(['pending', 'in_progress', 'completed']).optional(),
      priority: z.enum(['high', 'medium', 'low']).optional(),
    }),
    execute: async ({ id, content, status, priority }: { id: string; content?: string; status?: 'pending' | 'in_progress' | 'completed'; priority?: 'high' | 'medium' | 'low' }) => {
      const todo = todos.get(id);
      if (!todo) throw new Error(`Todo not found: ${id}`);
      
      if (content) todo.content = content;
      if (status) todo.status = status;
      if (priority) todo.priority = priority;
      
      todos.set(id, todo);
      return `Updated todo: ${id}`;
    },
  });
}