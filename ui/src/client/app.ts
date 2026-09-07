import { Session, Message } from '../types';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import typescript from 'highlight.js/lib/languages/typescript';
import javascript from 'highlight.js/lib/languages/javascript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import yaml from 'highlight.js/lib/languages/yaml';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';

// Register essential languages
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('css', css);

// Configure marked with highlight.js
marked.use({
  renderer: {
    code(code: string, infostring?: string, escaped?: boolean) {
      const lang = ((infostring || '') as string).match(/\S*/)?.[0] || '';
      let highlighted = code;
      if (lang && hljs.getLanguage(lang)) {
        try {
          highlighted = hljs.highlight(code, { language: lang }).value;
        } catch (__) {}
      } else {
        try {
          highlighted = hljs.highlightAuto(code).value;
        } catch (__) {}
      }
      return `<pre><code class="hljs language-${lang || 'text'}">${highlighted}</code></pre>`;
    }
  }
});

let currentSessionId: string | null = null;
let isRunning = false;
let startTime: number | null = null;
let timerInterval: any = null;
let currentEventSource: EventSource | null = null;

// DOM Elements
const workspaceInput = document.getElementById('workspace-input') as HTMLInputElement;
const newSessionBtn = document.getElementById('new-session-btn') as HTMLButtonElement;
const sessionsList = document.getElementById('sessions-list') as HTMLUListElement;
const sessionTitle = document.getElementById('session-title') as HTMLHeadingElement;
const messagesContainer = document.getElementById('messages-container') as HTMLDivElement;
const workingIndicator = document.getElementById('working-indicator') as HTMLDivElement;
const indicatorText = document.getElementById('indicator-text') as HTMLSpanElement;
const elapsedTimeSpan = document.getElementById('elapsed-time') as HTMLSpanElement;
const liveOutput = document.getElementById('live-output') as HTMLPreElement;
const stopBtn = document.getElementById('stop-btn') as HTMLButtonElement;
const chatForm = document.getElementById('chat-form') as HTMLFormElement;
const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement;
const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;

// Initialize
async function init() {
  setupEventListeners();
  await loadSessions();
  
  if (!currentSessionId) {
    await createNewSession();
  } else {
    await selectSession(currentSessionId);
  }
}

// Event Listeners
function setupEventListeners() {
  newSessionBtn.addEventListener('click', async () => {
    if (isRunning) return;
    await createNewSession();
  });

  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (isRunning) return;
    
    const content = chatInput.value.trim();
    if (!content || !currentSessionId) return;

    await sendMessage(content);
  });

  stopBtn.addEventListener('click', async () => {
    if (!isRunning || !currentSessionId) return;
    try {
      const res = await fetch(`/api/sessions/${currentSessionId}/stop`, { method: 'POST' });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        alert(`Failed to stop run: ${errData.error || res.statusText}`);
      }
    } catch (err) {
      console.error('Error stopping run:', err);
      alert('Error stopping run: ' + (err as Error).message);
    }
  });

  // Allow Enter to submit, Shift+Enter for newline
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });
}

// Load all sessions
async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    const sessions: Session[] = await res.json();
    
    sessionsList.innerHTML = '';
    
    if (sessions.length > 0 && !currentSessionId) {
      currentSessionId = sessions[0].id;
    }

    sessions.forEach(session => {
      const li = document.createElement('li');
      li.className = `session-item ${session.id === currentSessionId ? 'active' : ''}`;
      
      const titleSpan = document.createElement('span');
      titleSpan.className = 'session-title-text';
      titleSpan.textContent = session.title;
      titleSpan.addEventListener('click', () => {
        if (isRunning) return;
        selectSession(session.id);
      });

      const deleteBtn = document.createElement('button');
      deleteBtn.className = 'btn-delete';
      deleteBtn.innerHTML = '🗑️';
      deleteBtn.title = 'Delete session';
      deleteBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (isRunning) return;
        if (confirm(`Are you sure you want to delete "${session.title}"?`)) {
          await deleteSession(session.id);
        }
      });

      li.appendChild(titleSpan);
      li.appendChild(deleteBtn);
      sessionsList.appendChild(li);
    });
  } catch (error) {
    console.error('Error loading sessions:', error);
  }
}

// Create a new session
async function createNewSession() {
  try {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New Task' })
    });
    const data = await res.json();
    currentSessionId = data.id;
    await loadSessions();
    await selectSession(currentSessionId!);
  } catch (error) {
    console.error('Error creating session:', error);
  }
}

// Select a session
async function selectSession(id: string) {
  currentSessionId = id;
  
  await loadSessions(); // This will re-render and set active class correctly

  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) {
      currentSessionId = null;
      await loadSessions();
      return;
    }
    const session: Session = await res.json();
    
    sessionTitle.textContent = session.title;
    renderMessages(session.messages);
    
    if (session.status === 'working') {
      // Re-attach live stream if agent is actively running
      connectStream(id, true);
    } else {
      setRunningState(false);
    }
  } catch (error) {
    console.error('Error selecting session:', error);
  }
}

// Delete a session
async function deleteSession(id: string) {
  try {
    await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
    
    if (currentSessionId === id) {
      currentSessionId = null;
    }
    
    await loadSessions();
    
    if (!currentSessionId) {
      const res = await fetch('/api/sessions');
      const sessions: Session[] = await res.json();
      if (sessions.length > 0) {
        currentSessionId = sessions[0].id;
        await selectSession(currentSessionId);
      } else {
        await createNewSession();
      }
    } else {
      await selectSession(currentSessionId);
    }
  } catch (error) {
    console.error('Error deleting session:', error);
  }
}

// Render messages with markdown support for assistant and safety sanitization
function renderMessages(messages: Message[]) {
  messagesContainer.innerHTML = '';
  
  if (messages.length === 0) {
    const emptyDiv = document.createElement('div');
    emptyDiv.className = 'empty-state';
    emptyDiv.innerHTML = `
      <h2>Welcome to Coding Agent</h2>
      <p>Enter your coding instructions below. The autonomous agent will inspect your workspace, plan, execute tools, and solve coding tasks step-by-step.</p>
      <p><strong>Tips:</strong> Use Shift+Enter for newlines. Configure the workspace folder in the sidebar.</p>
    `;
    messagesContainer.appendChild(emptyDiv);
    return;
  }

  messages.forEach(msg => {
    const div = document.createElement('div');
    div.className = `message ${msg.role}`;
    
    if (msg.role === 'assistant') {
      try {
        const rawHtml = marked.parse(msg.content) as string;
        div.innerHTML = DOMPurify.sanitize(rawHtml);
      } catch (err) {
        div.textContent = msg.content;
      }
    } else if (msg.role === 'user') {
      div.textContent = msg.content;
    } else {
      div.textContent = msg.content;
    }
    
    messagesContainer.appendChild(div);
  });
  scrollToBottom();
}

// Scroll messages to bottom
function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Set running state & timer
function setRunningState(running: boolean) {
  isRunning = running;
  chatInput.disabled = running;
  sendBtn.disabled = running;
  workspaceInput.disabled = running;
  newSessionBtn.disabled = running;
  
  if (running) {
    workingIndicator.classList.remove('hidden');
    indicatorText.textContent = 'Working... (Routing model...)';
    liveOutput.textContent = '';
    startTime = Date.now();
    elapsedTimeSpan.textContent = '0s';
    
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
      if (startTime) {
        const secs = Math.floor((Date.now() - startTime) / 1000);
        elapsedTimeSpan.textContent = `${secs}s`;
      }
    }, 1000);
  } else {
    workingIndicator.classList.add('hidden');
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
    startTime = null;
  }
}

// Connect streaming agent execution via SSE
function connectStream(sessionId: string, isReconnect = false) {
  setRunningState(true);
  if (!isReconnect) {
    liveOutput.textContent = '';
  }

  const workspace = workspaceInput.value.trim() || '.';
  const eventSourceUrl = `/api/sessions/${sessionId}/run?workspace=${encodeURIComponent(workspace)}`;

  if (currentEventSource) {
    currentEventSource.close();
  }

  currentEventSource = new EventSource(eventSourceUrl);

  currentEventSource.addEventListener('output', (e: any) => {
    try {
      const data = JSON.parse(e.data);
      const chunk = data.chunk;
      liveOutput.textContent += chunk;
      liveOutput.scrollTop = liveOutput.scrollHeight;

      // Parse status indicators from stream output
      if (chunk.includes('Routing to')) {
        const match = chunk.match(/Routing to ([^. \n]+)/);
        if (match) {
          indicatorText.textContent = `Working via ${match[1]}...`;
        }
      }
      if (chunk.includes('step') || chunk.includes('Iteration')) {
        indicatorText.textContent = `Working (executing steps)...`;
      }
    } catch (err) {
      console.error('Error parsing output event:', err);
    }
  });

  currentEventSource.addEventListener('error', (e: any) => {
    console.error('SSE Error:', e);
    try {
      const data = JSON.parse(e.data);
      liveOutput.textContent += `\n[Error: ${data.error || 'Unknown error'}]`;
    } catch (err) {
      liveOutput.textContent += `\n[Connection Closed]`;
    }
    if (currentEventSource) {
      currentEventSource.close();
      currentEventSource = null;
    }
    setRunningState(false);
    if (currentSessionId) selectSession(currentSessionId);
  });

  currentEventSource.addEventListener('end', (e: any) => {
    try {
      const data = JSON.parse(e.data);
      indicatorText.textContent = data.status === 'completed' ? 'Complete!' : 'Completed with errors';
    } catch (err) {
      indicatorText.textContent = 'Finished';
    }
    if (currentEventSource) {
      currentEventSource.close();
      currentEventSource = null;
    }
    setTimeout(() => {
      setRunningState(false);
      if (currentSessionId) selectSession(currentSessionId);
    }, 1000);
  });
}

// Send message and run agent
async function sendMessage(content: string) {
  if (!currentSessionId) return;
  
  setRunningState(true);
  chatInput.value = '';

  try {
    // 1. Add message to session
    const res = await fetch(`/api/sessions/${currentSessionId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content })
    });
    
    if (!res.ok) {
      throw new Error('Failed to send message');
    }
    
    const data = await res.json();
    const session: Session = data.session;
    
    // Update UI with new messages
    sessionTitle.textContent = session.title;
    renderMessages(session.messages);
    await loadSessions();

    // 2. Start streaming agent execution via SSE
    connectStream(currentSessionId, false);

  } catch (error) {
    console.error('Error sending message:', error);
    alert('Error: ' + (error as Error).message);
    setRunningState(false);
  }
}

// Start the app
window.addEventListener('DOMContentLoaded', init);
