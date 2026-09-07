import express, { Request, Response } from 'express';
import * as path from 'path';
import * as fs from 'fs';
import {
  getAllSessions,
  getSession,
  createSession,
  updateSession,
  deleteSession,
} from './storage';
import { runAgent, formatCombinedPrompt } from './agent';
import { Message, SessionStatus } from './types';

const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

// Serve static files from compiled dist/client and source src/client
app.use(express.static(path.join(__dirname, 'client')));
app.use(express.static(path.join(__dirname, '../src/client')));

// Active child process state mapped by session ID
interface ActiveRun {
  sessionId: string;
  child: any;
  output: string;
  userStopped: boolean;
  listeners: Set<Response>;
  startTime: number;
}

const activeRuns = new Map<string, ActiveRun>();

// Helper to extract clean reply from raw stdout log
function extractReply(raw: string): string {
  const trimmed = raw.trim();
  // Check for === DONE ... === or === STOPPED ... ===
  const doneMatch = trimmed.match(/===\s*(?:DONE|STOPPED)[^=]*===\s*([\s\S]*)$/i);
  if (doneMatch && doneMatch[1].trim()) {
    return doneMatch[1].trim();
  }
  // If no marker, return last non-empty significant section or fallback
  return trimmed || '(Agent completed with no output)';
}

// Helper to extract meaningful error summary from crash logs without full router debug output
function extractErrorReply(raw: string, code: number | null, signal: string | null): string {
  const trimmed = raw.trim();
  // Check for Python Traceback
  const tbMatch = trimmed.match(/(?:Traceback \(most recent call last\)[\s\S]*)/);
  if (tbMatch) {
    return `Agent failed with error:\n\`\`\`\n${tbMatch[0].trim()}\n\`\`\``;
  }
  // Check for error lines
  const lines = trimmed.split('\n').filter((l) => l.trim().length > 0);
  const errorLines = lines.filter((l) => /error|exception|failed|fatal|cannot|could not/i.test(l));
  if (errorLines.length > 0) {
    const snippet = errorLines.slice(-5).join('\n');
    return `Agent failed (exit code ${code !== null ? code : signal}):\n${snippet}`;
  }
  if (lines.length > 0) {
    const snippet = lines.slice(-3).join('\n');
    return `Agent exited with code ${code !== null ? code : signal}:\n${snippet}`;
  }
  return `Agent exited unexpectedly with code ${code !== null ? code : signal}.`;
}

// API Endpoints - Stop route registered ONCE globally
app.post('/api/sessions/:id/stop', (req: Request, res: Response) => {
  const sessionId = req.params.id;
  const run = activeRuns.get(sessionId);
  if (run && run.child && run.child.exitCode === null) {
    run.userStopped = true;
    run.child.kill();
    res.json({ success: true });
  } else {
    res.status(400).json({ error: 'No active run for this session' });
  }
});

// Get all sessions
app.get('/api/sessions', (req: Request, res: Response) => {
  try {
    const sessions = getAllSessions();
    res.json(sessions);
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

// Get a single session
app.get('/api/sessions/:id', (req: Request, res: Response) => {
  try {
    const session = getSession(req.params.id);
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }
    res.json(session);
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

// Create a session
app.post('/api/sessions', (req: Request, res: Response) => {
  try {
    const { title } = req.body;
    const id = createSession(title || 'New Task');
    res.json({ id });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

// Delete a session
app.delete('/api/sessions/:id', (req: Request, res: Response) => {
  try {
    deleteSession(req.params.id);
    res.json({ success: true });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

// Add a message to a session
app.post('/api/sessions/:id/messages', (req: Request, res: Response) => {
  try {
    const session = getSession(req.params.id);
    if (!session) {
      res.status(404).json({ error: 'Session not found' });
      return;
    }

    const { content } = req.body;
    if (!content) {
      res.status(400).json({ error: 'Content is required' });
      return;
    }

    const messages = [...session.messages];
    let title = session.title;

    // If first message, update title
    if (messages.length === 0) {
      title = content.length > 40 ? content.slice(0, 40) + '...' : content;
    }

    messages.push({ role: 'user', content });
    updateSession(session.id, messages, 'working', title);

    const updatedSession = getSession(session.id);
    res.json({ success: true, session: updatedSession });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

// Run the agent and stream output via SSE
app.get('/api/sessions/:id/run', (req: Request, res: Response) => {
  const sessionId = req.params.id;
  const workspace = (req.query.workspace as string) || '.';

  const session = getSession(sessionId);
  if (!session) {
    res.status(404).json({ error: 'Session not found' });
    return;
  }

  // Set headers for SSE
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  // If there is already an active run for this session, re-attach listener and catch up
  const existingRun = activeRuns.get(sessionId);
  if (existingRun && existingRun.child && existingRun.child.exitCode === null) {
    if (existingRun.output) {
      res.write(`event: output\ndata: ${JSON.stringify({ chunk: existingRun.output })}\n\n`);
    }
    existingRun.listeners.add(res);
    req.on('close', () => {
      existingRun.listeners.delete(res);
    });
    return;
  }

  // If session is not in working status (e.g., already completed or error), send end event
  if (session.status !== 'working') {
    res.write(`event: end\ndata: ${JSON.stringify({ code: 0, output: '', status: session.status })}\n\n`);
    res.end();
    return;
  }

  // Verify workspace directory exists
  const absoluteWorkspace = path.isAbsolute(workspace)
    ? workspace
    : path.join(process.cwd(), workspace);

  if (!fs.existsSync(absoluteWorkspace) || !fs.statSync(absoluteWorkspace).isDirectory()) {
    const errMsg = `Directory not found: ${workspace}`;
    res.write(`event: error\ndata: ${JSON.stringify({ error: errMsg })}\n\n`);
    
    // Update session status to error
    const latestSession = getSession(sessionId) || session;
    const messages = [...latestSession.messages];
    messages.push({ role: 'assistant', content: errMsg });
    updateSession(sessionId, messages, 'error');
    res.end();
    return;
  }

  const combinedPrompt = formatCombinedPrompt(session.messages);

  const currentRun: ActiveRun = {
    sessionId,
    child: null,
    output: '',
    userStopped: false,
    listeners: new Set<Response>([res]),
    startTime: Date.now(),
  };
  activeRuns.set(sessionId, currentRun);

  // When this SSE connection drops, remove listener but keep the child process running
  req.on('close', () => {
    currentRun.listeners.delete(res);
  });

  const child = runAgent({
    workspace,
    combinedPrompt,
    onData: (chunk) => {
      currentRun.output += chunk;
      for (const listener of currentRun.listeners) {
        listener.write(`event: output\ndata: ${JSON.stringify({ chunk })}\n\n`);
      }
    },
    onExit: (code, signal) => {
      activeRuns.delete(sessionId);
      const isStopped = currentRun.userStopped;
      const status: SessionStatus = code === 0 && !isStopped ? 'completed' : 'error';
      
      let cleanReply: string;
      if (isStopped) {
        cleanReply = 'Stopped by the user.';
      } else if (code === 0) {
        cleanReply = extractReply(currentRun.output);
      } else {
        cleanReply = extractErrorReply(currentRun.output, code, signal);
      }
      
      const latestSession = getSession(sessionId) || session;
      const messages = [...latestSession.messages];
      messages.push({ role: 'assistant', content: cleanReply });
      updateSession(sessionId, messages, status);

      for (const listener of currentRun.listeners) {
        listener.write(`event: end\ndata: ${JSON.stringify({ code, output: cleanReply, rawOutput: currentRun.output, status })}\n\n`);
        listener.end();
      }
      currentRun.listeners.clear();
    },
    onError: (err) => {
      activeRuns.delete(sessionId);
      const errMsg = `Agent failed to start: ${err.message}`;
      currentRun.output += errMsg;

      const latestSession = getSession(sessionId) || session;
      const messages = [...latestSession.messages];
      messages.push({ role: 'assistant', content: errMsg });
      updateSession(sessionId, messages, 'error');

      for (const listener of currentRun.listeners) {
        listener.write(`event: error\ndata: ${JSON.stringify({ error: errMsg })}\n\n`);
        listener.end();
      }
      currentRun.listeners.clear();
    },
  });

  currentRun.child = child;
  if (!child) {
    activeRuns.delete(sessionId);
  }
});

// Fallback to index.html for SPA routing if needed
app.get('*', (req: Request, res: Response) => {
  res.sendFile(path.join(__dirname, '../src/client/index.html'));
});

app.listen(PORT, () => {
  console.log(`Server is running on http://localhost:${PORT}`);
});
