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

// API Endpoints

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

  // Verify workspace directory exists
  const absoluteWorkspace = path.isAbsolute(workspace)
    ? workspace
    : path.join(process.cwd(), workspace);

  if (!fs.existsSync(absoluteWorkspace) || !fs.statSync(absoluteWorkspace).isDirectory()) {
    const errMsg = `Directory not found: ${workspace}`;
    res.write(`event: error\ndata: ${JSON.stringify({ error: errMsg })}\n\n`);
    
    // Update session status to error
    const messages = [...session.messages];
    messages.push({ role: 'assistant', content: errMsg });
    updateSession(sessionId, messages, 'error');
    res.end();
    return;
  }

  const combinedPrompt = formatCombinedPrompt(session.messages);
  let output = '';

  const child = runAgent({
    workspace,
    combinedPrompt,
    onData: (chunk) => {
      output += chunk;
      res.write(`event: output\ndata: ${JSON.stringify({ chunk })}\n\n`);
    },
    onExit: (code) => {
      const status: SessionStatus = code === 0 ? 'completed' : 'error';
      const finalOutput = output.trim() || '(Agent completed with no output)';
      
      const messages = [...session.messages];
      messages.push({ role: 'assistant', content: finalOutput });
      updateSession(sessionId, messages, status);

      res.write(`event: end\ndata: ${JSON.stringify({ code, output: finalOutput, status })}\n\n`);
      res.end();
    },
    onError: (err) => {
      const errMsg = `Error executing agent: ${err.message}`;
      output += errMsg;
      res.write(`event: error\ndata: ${JSON.stringify({ error: errMsg })}\n\n`);

      const messages = [...session.messages];
      messages.push({ role: 'assistant', content: output });
      updateSession(sessionId, messages, 'error');
      res.end();
    },
  });

  // If client disconnects, we can optionally kill the child process
  req.on('close', () => {
    if (child && child.exitCode === null) {
      child.kill();
    }
  });
});

// Fallback to index.html for SPA routing if needed
app.get('*', (req: Request, res: Response) => {
  res.sendFile(path.join(__dirname, '../src/client/index.html'));
});

app.listen(PORT, () => {
  console.log(`Server is running on http://localhost:${PORT}`);
});
