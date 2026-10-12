import chalk from 'chalk';

export class Renderer {
  static info(message: string): void {
    console.log(chalk.blue('ℹ'), message);
  }

  static success(message: string): void {
    console.log(chalk.green('✓'), message);
  }

  static warning(message: string): void {
    console.log(chalk.yellow('⚠'), message);
  }

  static error(message: string): void {
    console.log(chalk.red('✗'), message);
  }

  static task(message: string): void {
    console.log(chalk.cyan('▶'), message);
  }

  static tool(name: string, args: Record<string, any>): void {
    console.log(chalk.magenta('🔧'), chalk.bold(name), chalk.gray(JSON.stringify(args)));
  }

  static toolResult(result: string): void {
    console.log(chalk.gray('  →'), result.substring(0, 200) + (result.length > 200 ? '...' : ''));
  }

  static thinking(message: string): void {
    console.log(chalk.gray('💭'), message);
  }

  static divider(): void {
    console.log(chalk.gray('─'.repeat(50)));
  }
}