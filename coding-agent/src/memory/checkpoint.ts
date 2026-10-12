import { SessionStore, sessionStore } from './session_store.js';

export interface AgentState {
  messages: any[];
  currentTask: string;
  iteration: number;
  toolsUsed: string[];
  metadata: Record<string, any>;
}

export class CheckpointManager {
  private sessionStore: SessionStore;

  constructor(sessionStoreInstance?: SessionStore) {
    this.sessionStore = sessionStoreInstance || sessionStore;
  }

  async createCheckpoint(sessionId: string, label: string, state: AgentState): Promise<string> {
    const checkpoint = await this.sessionStore.createCheckpoint(sessionId, label, state);
    return checkpoint.id;
  }

  async restoreCheckpoint(sessionId: string, checkpointId: string): Promise<AgentState | null> {
    const state = await this.sessionStore.restoreCheckpoint(checkpointId);
    return state as AgentState ?? null;
  }

  async listCheckpoints(sessionId: string): Promise<Array<{ id: string; label: string; createdAt: Date }>> {
    const checkpoints = await this.sessionStore.getCheckpointsBySession(sessionId);
    return checkpoints.map(c => ({
      id: c.id,
      label: c.label,
      createdAt: c.createdAt,
    }));
  }

  async deleteCheckpoint(checkpointId: string): Promise<void> {
  }
}

export const checkpointManager = new CheckpointManager();