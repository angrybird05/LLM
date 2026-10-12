import { sessionStore } from './session_store.js';

export interface ProjectFile {
  path: string;
  content: string;
  language: string;
  symbols: Symbol[];
  embeddings?: number[];
}

export interface Symbol {
  name: string;
  type: 'function' | 'class' | 'interface' | 'variable' | 'import';
  line: number;
  column: number;
  signature?: string;
}

export class ProjectIndex {
  private files: Map<string, ProjectFile> = new Map();
  private sessionId: string;

  constructor(sessionId: string) {
    this.sessionId = sessionId;
  }

  async addFile(file: ProjectFile): Promise<void> {
    this.files.set(file.path, file);
    await this.indexFile(file);
  }

  async removeFile(path: string): Promise<void> {
    this.files.delete(path);
  }

  async getFile(path: string): Promise<ProjectFile | undefined> {
    return this.files.get(path);
  }

  async searchSymbols(query: string): Promise<Symbol[]> {
    const results: Symbol[] = [];
    const lowerQuery = query.toLowerCase();
    
    for (const file of this.files.values()) {
      for (const symbol of file.symbols) {
        if (symbol.name.toLowerCase().includes(lowerQuery) ||
            symbol.signature?.toLowerCase().includes(lowerQuery)) {
          results.push(symbol);
        }
      }
    }
    return results;
  }

  async findReferences(symbolName: string): Promise<Array<{ file: string; line: number }>> {
    const refs: Array<{ file: string; line: number }> = [];
    
    for (const [path, file] of this.files.entries()) {
      for (const symbol of file.symbols) {
        if (symbol.name === symbolName) {
          refs.push({ file: path, line: symbol.line });
        }
      }
    }
    return refs;
  }

  async getContextForTask(task: string, maxFiles: number = 10): Promise<ProjectFile[]> {
    const relevant: Array<{ file: ProjectFile; score: number }> = [];
    
    for (const file of this.files.values()) {
      let score = 0;
      const taskLower = task.toLowerCase();
      
      if (file.path.toLowerCase().includes(taskLower)) score += 10;
      for (const symbol of file.symbols) {
        if (symbol.name.toLowerCase().includes(taskLower)) score += 5;
        if (symbol.signature?.toLowerCase().includes(taskLower)) score += 3;
      }
      if (file.content.toLowerCase().includes(taskLower)) score += 1;
      
      if (score > 0) {
        relevant.push({ file, score });
      }
    }
    
    relevant.sort((a, b) => b.score - a.score);
    return relevant.slice(0, maxFiles).map(r => r.file);
  }

  async getAllFiles(): Promise<ProjectFile[]> {
    return Array.from(this.files.values());
  }

  private async indexFile(file: ProjectFile): Promise<void> {
    const content = `${file.path}\n${file.content}`;
    await sessionStore.storeContextEmbedding(this.sessionId, content, {
      type: 'file',
      path: file.path,
      language: file.language,
    });
  }
}