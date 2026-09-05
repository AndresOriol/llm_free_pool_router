import { Session, Message } from '../types';

let currentSessionId: string | null = null;
let isRunning = false;

// DOM Elements
const workspaceInput = document.getElementById('workspace-input') as HTMLInputElement;
const newSessionBtn = document.getElementById('new-session-btn') as HTMLButtonElement;
const sessionsList = document.getElementById('sessions-list') as HTMLUListElement;
const sessionTitle = document.getElementById('session-title') as HTMLHeadingElement;
const messagesContainer = document.getElementById('messages-container') as HTMLDivElement;
const workingIndicator = document.getElementById('working-indicator') as HTMLDivElement;
const indicatorText = document.getElementById('indicator-text') as HTMLSpanElement;
const liveOutput = document.getElementById('live-output') as HTMLPreElement;
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
  
  // Update active class in sidebar
  const items = sessionsList.querySelectorAll('.session-item');
  items.forEach(item => {
    item.classList.remove('active');
  });
  
  // Find and highlight active item
  const activeItem = Array.from(items).find(item => {
    const titleText = item.querySelector('.session-title-text');
    return titleText && titleText.textContent === sessionTitle.textContent; // simple fallback, better to re-render
  });
  
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
    
    // If session was left in working state, we can't easily resume SSE, but we can reset UI
    if (session.status === 'working') {
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

// Render messages
function renderMessages(messages: Message[]) {
  messagesContainer.innerHTML = '';
  messages.forEach(msg => {
    const div = document.createElement('div');
    div.className = `message ${msg.role}`;
    div.textContent = msg.content;
    messagesContainer.appendChild(div);
  });
  scrollToBottom();
}

// Scroll messages to bottom
function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Set running state
function setRunningState(running: boolean) {
  isRunning = running;
  chatInput.disabled = running;
  sendBtn.disabled = running;
  workspaceInput.disabled = running;
  newSessionBtn.disabled = running;
  
  if (running) {
    workingIndicator.classList.remove('hidden');
    indicatorText.textContent = 'Working...';
    liveOutput.textContent = '';
  } else {
    workingIndicator.classList.add('hidden');
  }
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
    await loadSessions(); // update sidebar title if needed

    // 2. Start streaming agent execution
    const workspace = workspaceInput.value.trim() || '.';
    const eventSourceUrl = `/api/sessions/${currentSessionId}/run?workspace=${encodeURIComponent(workspace)}`;
    
    const eventSource = new EventSource(eventSourceUrl);
    
    eventSource.addEventListener('output', (e: any) => {
      try {
        const data = JSON.parse(e.data);
        liveOutput.textContent += data.chunk;
        liveOutput.scrollTop = liveOutput.scrollHeight;
      } catch (err) {
        console.error('Error parsing output event:', err);
      }
    });

    eventSource.addEventListener('error', (e: any) => {
      console.error('SSE Error:', e);
      try {
        const data = JSON.parse(e.data);
        liveOutput.textContent += `\n[Error: ${data.error || 'Unknown error'}]`;
      } catch (err) {
        liveOutput.textContent += `\n[Connection Error]`;
      }
      eventSource.close();
      setRunningState(false);
      if (currentSessionId) selectSession(currentSessionId);
    });

    eventSource.addEventListener('end', (e: any) => {
      try {
        const data = JSON.parse(e.data);
        indicatorText.textContent = data.status === 'completed' ? 'Complete!' : 'Completed with errors';
      } catch (err) {
        indicatorText.textContent = 'Finished';
      }
      eventSource.close();
      setTimeout(() => {
        setRunningState(false);
        if (currentSessionId) selectSession(currentSessionId);
      }, 1000);
    });

  } catch (error) {
    console.error('Error sending message:', error);
    alert('Error: ' + (error as Error).message);
    setRunningState(false);
  }
}

// Start the app
window.addEventListener('DOMContentLoaded', init);
