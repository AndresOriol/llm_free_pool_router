import { spawn } from 'child_process';
import * as path from 'path';
import { Message } from './types';

export function formatCombinedPrompt(messages: Message[]): string {
  return messages
    .map((msg) => {
      const roleLabel =
        msg.role === 'user'
          ? 'User'
          : msg.role === 'assistant'
          ? 'Assistant'
          : 'System';
      return `${roleLabel}: ${msg.content}`;
    })
    .join('\n\n');
}

export interface RunAgentOptions {
  workspace: string;
  combinedPrompt: string;
  onData: (data: string) => void;
  onExit: (code: number | null, signal: string | null) => void;
  onError: (err: Error) => void;
}

export function runAgent({
  workspace,
  combinedPrompt,
  onData,
  onExit,
  onError,
}: RunAgentOptions) {
  const pythonCmd = process.env.PYTHON_EXECUTABLE || 'python';
  const args = ['-m', 'agent.code', workspace];

  // Ensure project root is in PYTHONPATH so 'agent' module can be found
  const projectRoot = path.resolve(__dirname, '..', '..');
  const env = { ...process.env };
  const pythonPath = env.PYTHONPATH 
    ? `${env.PYTHONPATH}${path.delimiter}${projectRoot}`
    : projectRoot;
  env.PYTHONPATH = pythonPath;

  try {
    const child = spawn(pythonCmd, args, {
      cwd: process.cwd(),
      env,
    });

    if (child.stdin) {
      child.stdin.write(combinedPrompt);
      child.stdin.end();
    }

    child.stdout?.on('data', (chunk: Buffer) => {
      onData(chunk.toString('utf-8'));
    });

    child.stderr?.on('data', (chunk: Buffer) => {
      onData(chunk.toString('utf-8'));
    });

    child.on('error', (err) => {
      onError(err);
    });

    child.on('exit', (code, signal) => {
      onExit(code, signal ? signal.toString() : null);
    });

    return child;
  } catch (err) {
    onError(err as Error);
    return null;
  }
}
