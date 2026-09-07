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
import diff from 'highlight.js/lib/languages/diff';
import sql from 'highlight.js/lib/languages/sql';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import cpp from 'highlight.js/lib/languages/cpp';
import java from 'highlight.js/lib/languages/java';
import csharp from 'highlight.js/lib/languages/csharp';
import ruby from 'highlight.js/lib/languages/ruby';
import dockerfile from 'highlight.js/lib/languages/dockerfile';

// Register comprehensive language support
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
hljs.registerLanguage('diff', diff);
hljs.registerLanguage('patch', diff);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('rs', rust);
hljs.registerLanguage('go', go);
hljs.registerLanguage('golang', go);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('c++', cpp);
hljs.registerLanguage('java', java);
hljs.registerLanguage('csharp', csharp);
hljs.registerLanguage('cs', csharp);
hljs.registerLanguage('ruby', ruby);
hljs.registerLanguage('rb', ruby);
hljs.registerLanguage('dockerfile', dockerfile);
hljs.registerLanguage('docker', dockerfile);

// File-level header lines carry no line number of their own.
function isDiffHeader(line: string): boolean {
  return (
    line.startsWith('---') ||
    line.startsWith('+++') ||
    line.startsWith('diff --git ') ||
    line.startsWith('index ') ||
    line.startsWith('old mode ') ||
    line.startsWith('new mode ') ||
    line.startsWith('new file mode ') ||
    line.startsWith('deleted file mode ') ||
    line.startsWith('similarity index ') ||
    line.startsWith('rename from ') ||
    line.startsWith('rename to ') ||
    line.startsWith('Binary files ') ||
    line.startsWith('\ No newline')
  );
}

// Helper to render unified diff with line-number gutters
function renderUnifiedDiffHtml(diffText: string): string {
  const lines = diffText.split('\n');
  let oldLine = 1;
  let newLine = 1;
  let rowsHtml = '';

  for (const line of lines) {
    if (line.startsWith('@@')) {
      const match = line.match(/@@\s*-(\d+)(?:,\d+)?\s*\+(\d+)(?:,\d+)?\s*@@/);
      if (match) {
        oldLine = parseInt(match[1], 10);
        newLine = parseInt(match[2], 10);
      }
      const safeHunk = escapeHtml(line);
      rowsHtml += `<div class="diff-row hunk"><span class="diff-gutter">...</span><span class="diff-content">${safeHunk}</span></div>`;
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      const safeContent = escapeHtml(line);
      rowsHtml += `<div class="diff-row add"><span class="diff-gutter">+${newLine}</span><span class="diff-content">${safeContent}</span></div>`;
      newLine++;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      const safeContent = escapeHtml(line);
      rowsHtml += `<div class="diff-row remove"><span class="diff-gutter">-${oldLine}</span><span class="diff-content">${safeContent}</span></div>`;
      oldLine++;
    } else {
      const safeContent = escapeHtml(line);
      const header = isDiffHeader(line);
      const gutterText = header ? ' ' : `${oldLine}`;
      rowsHtml += `<div class="diff-row${header ? ' header' : ''}"><span class="diff-gutter">${gutterText}</span><span class="diff-content">${safeContent}</span></div>`;
      if (!header) {
        oldLine++;
        newLine++;
      }
    }
  }

  return `<div class="diff-view"><div class="diff-header-bar"><span>Unified Diff View</span></div>${rowsHtml}</div>`;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Configure marked with syntax highlighting & custom code blocks
marked.use({
  renderer: {
    code(code: string, infostring?: string) {
      const lang = ((infostring || '') as string).match(/\S*/)?.[0]?.toLowerCase() || '';

      // Check for unified diff
      if (lang === 'diff' || lang === 'patch' || (code.includes('--- ') && code.includes('+++ ') && code.includes('@@ '))) {
        return renderUnifiedDiffHtml(code);
      }

      let highlighted = code;
      const validLang = lang && hljs.getLanguage(lang) ? lang : '';
      if (validLang) {
        try {
          highlighted = hljs.highlight(code, { language: validLang }).value;
        } catch (__) {
          highlighted = escapeHtml(code);
        }
      } else {
        try {
          highlighted = hljs.highlightAuto(code).value;
        } catch (__) {
          highlighted = escapeHtml(code);
        }
      }

      const displayLang = validLang || lang || 'code';
      const encodedCode = encodeURIComponent(code);

      return `
        <div class="code-block-container">
          <div class="code-block-header">
            <span class="code-lang-tag">${displayLang}</span>
            <button type="button" class="btn-copy-code" data-code="${encodedCode}">Copy</button>
          </div>
          <pre><code class="hljs language-${displayLang}">${highlighted}</code></pre>
        </div>
      `;
    }
  }
});

// App State
let currentSessionId: string | null = null;
let isRunning = false;
let startTime: number | null = null;
let timerInterval: any = null;
let currentEventSource: EventSource | null = null;
let slashSelectedIdx = -1;

// The folder the agent is pointed at. It used to live in a text box; now the
// picker owns it, and localStorage carries it across reloads.
const WORKSPACE_KEY = 'dcode.workspace';
let workspace = '.';
// Where the picker is currently looking, which is also what it would select.
let browsePath = '';

// DOM Elements
const workspacePickerBtn = document.getElementById('workspace-picker-btn') as HTMLButtonElement;
const workspaceDisplay = document.getElementById('workspace-display') as HTMLSpanElement;
const folderModal = document.getElementById('folder-modal') as HTMLDivElement;
const folderRoots = document.getElementById('folder-roots') as HTMLDivElement;
const folderBreadcrumb = document.getElementById('folder-breadcrumb') as HTMLElement;
const folderList = document.getElementById('folder-list') as HTMLUListElement;
const folderSelectionPath = document.getElementById('folder-selection-path') as HTMLSpanElement;
const folderCloseBtn = document.getElementById('folder-close-btn') as HTMLButtonElement;
const folderCancelBtn = document.getElementById('folder-cancel-btn') as HTMLButtonElement;
const folderSelectBtn = document.getElementById('folder-select-btn') as HTMLButtonElement;
const newSessionBtn = document.getElementById('new-session-btn') as HTMLButtonElement;
const sessionsList = document.getElementById('sessions-list') as HTMLUListElement;
const sessionsCountBadge = document.getElementById('sessions-count') as HTMLSpanElement;
const sessionTitle = document.getElementById('session-title') as HTMLHeadingElement;
const agentStatusPill = document.getElementById('agent-status-pill') as HTMLDivElement;
const agentStatusText = document.getElementById('agent-status-text') as HTMLSpanElement;
const currentModelSpan = document.getElementById('current-model') as HTMLSpanElement;
const clearChatBtn = document.getElementById('clear-chat-btn') as HTMLButtonElement;
const messagesContainer = document.getElementById('messages-container') as HTMLDivElement;
const workingIndicator = document.getElementById('working-indicator') as HTMLDivElement;
const indicatorText = document.getElementById('indicator-text') as HTMLSpanElement;
const elapsedTimeSpan = document.getElementById('elapsed-time') as HTMLSpanElement;
const liveOutput = document.getElementById('live-output') as HTMLPreElement;
const stopBtn = document.getElementById('stop-btn') as HTMLButtonElement;
const chatForm = document.getElementById('chat-form') as HTMLFormElement;
const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement;
const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;
const slashPopup = document.getElementById('slash-popup') as HTMLDivElement;
const slashPopupList = document.getElementById('slash-popup-list') as HTMLUListElement;

// Initialize Application
async function init() {
  restoreWorkspace();
  setupEventListeners();
  await loadSessions();
  
  if (!currentSessionId) {
    await createNewSession();
  } else {
    await selectSession(currentSessionId);
  }
}

// Event Listeners setup
function setupEventListeners() {
  // New session button
  newSessionBtn.addEventListener('click', async () => {
    if (isRunning) return;
    await createNewSession();
  });

  // Clear chat button
  clearChatBtn.addEventListener('click', async () => {
    if (isRunning || !currentSessionId) return;
    if (confirm('Clear all conversation messages in this thread?')) {
      await clearCurrentSession();
    }
  });

  // Quick Action Chips
  document.querySelectorAll('.btn-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = (btn as HTMLElement).getAttribute('data-cmd');
      if (cmd) handleSlashCommand(cmd);
    });
  });

  // Form submit
  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (isRunning) return;
    
    const content = chatInput.value.trim();
    if (!content || !currentSessionId) return;

    hideSlashPopup();

    // Check for slash commands
    if (content.startsWith('/')) {
      handleSlashCommand(content);
      chatInput.value = '';
      autoResizeInput();
      return;
    }

    await sendMessage(content);
  });

  // Stop button
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

  // Chat Input Keyboard Events & Autocomplete
  chatInput.addEventListener('input', () => {
    autoResizeInput();
    checkSlashPopup();
  });

  chatInput.addEventListener('keydown', (e) => {
    // Slash popup navigation
    if (!slashPopup.classList.contains('hidden')) {
      const items = Array.from(slashPopupList.querySelectorAll('li'));
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        slashSelectedIdx = (slashSelectedIdx + 1) % items.length;
        updateSlashSelection(items);
        return;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        slashSelectedIdx = (slashSelectedIdx - 1 + items.length) % items.length;
        updateSlashSelection(items);
        return;
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        if (slashSelectedIdx >= 0 && slashSelectedIdx < items.length) {
          e.preventDefault();
          const cmd = items[slashSelectedIdx].getAttribute('data-cmd');
          if (cmd) {
            chatInput.value = cmd;
            hideSlashPopup();
            chatInput.focus();
            autoResizeInput();
          }
          return;
        }
      } else if (e.key === 'Escape') {
        hideSlashPopup();
        return;
      }
    }

    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  // Slash popup list item clicks
  slashPopupList.querySelectorAll('li').forEach(li => {
    li.addEventListener('click', () => {
      const cmd = li.getAttribute('data-cmd');
      if (cmd) {
        chatInput.value = cmd;
        hideSlashPopup();
        chatInput.focus();
        autoResizeInput();
      }
    });
  });

  // Workspace folder picker
  workspacePickerBtn.addEventListener('click', openFolderPicker);
  folderCloseBtn.addEventListener('click', closeFolderPicker);
  folderCancelBtn.addEventListener('click', closeFolderPicker);
  folderSelectBtn.addEventListener('click', () => {
    if (browsePath) setWorkspace(browsePath);
    closeFolderPicker();
  });
  folderModal.addEventListener('click', (e) => {
    // Only the backdrop closes; a click inside the dialog must not.
    if (e.target === folderModal) closeFolderPicker();
  });
  document.addEventListener('keydown', (e) => {
    if (folderModal.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      closeFolderPicker();
    } else if (e.key === 'Enter' && browsePath) {
      setWorkspace(browsePath);
      closeFolderPicker();
    }
  });

  // Global click to close slash popup
  document.addEventListener('click', (e) => {
    if (!slashPopup.contains(e.target as Node) && e.target !== chatInput) {
      hideSlashPopup();
    }
  });

  // Delegate Copy Code block button clicks
  messagesContainer.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    if (target && target.classList.contains('btn-copy-code')) {
      const rawCode = decodeURIComponent(target.getAttribute('data-code') || '');
      if (rawCode) {
        navigator.clipboard.writeText(rawCode).then(() => {
          const origText = target.textContent;
          target.textContent = '✓ Copied!';
          setTimeout(() => {
            target.textContent = origText;
          }, 2000);
        }).catch(() => {
          target.textContent = 'Error';
        });
      }
    }
  });
}

// Auto-resize chat textarea
function autoResizeInput() {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
}

// Check if slash popup should be displayed
function checkSlashPopup() {
  const text = chatInput.value;
  if (text.startsWith('/')) {
    const query = text.toLowerCase();
    let hasMatch = false;
    slashPopupList.querySelectorAll('li').forEach(li => {
      const cmd = li.getAttribute('data-cmd') || '';
      if (cmd.startsWith(query)) {
        (li as HTMLElement).style.display = 'flex';
        hasMatch = true;
      } else {
        (li as HTMLElement).style.display = 'none';
      }
    });

    if (hasMatch) {
      slashPopup.classList.remove('hidden');
      slashSelectedIdx = -1;
      const items = Array.from(slashPopupList.querySelectorAll('li')).filter(li => (li as HTMLElement).style.display !== 'none');
      if (items.length > 0) {
        slashSelectedIdx = 0;
        updateSlashSelection(items);
      }
    } else {
      hideSlashPopup();
    }
  } else {
    hideSlashPopup();
  }
}

function hideSlashPopup() {
  slashPopup.classList.add('hidden');
  slashSelectedIdx = -1;
  slashPopupList.querySelectorAll('li').forEach(li => {
    li.classList.remove('selected');
    (li as HTMLElement).style.display = 'flex';
  });
}

function updateSlashSelection(items: HTMLElement[]) {
  items.forEach((item, idx) => {
    if (idx === slashSelectedIdx) {
      item.classList.add('selected');
      item.scrollIntoView({ block: 'nearest' });
    } else {
      item.classList.remove('selected');
    }
  });
}

// Slash command handling
async function handleSlashCommand(cmdStr: string) {
  const parts = cmdStr.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();

  if (cmd === '/clear') {
    await clearCurrentSession();
    return;
  }

  if (cmd === '/help') {
    const helpMsg: Message = {
      role: 'assistant',
      content: `### Commands\n\n- \`/help\` - Show this list\n- \`/diff\` - Show \`git diff\` for the workspace\n- \`/model\` - Show the model the router last routed to\n- \`/clear\` - Clear the messages in this session\n\nAnything else you type is sent to the agent as a task: inspecting files, editing code, running tests.\n\n*Commands render here only. They are not saved to the session and are never sent to the agent.*`
    };
    appendLocalMessage(helpMsg);
    return;
  }

  if (cmd === '/diff') {
    try {
      const res = await fetch(`/api/diff?workspace=${encodeURIComponent(workspace)}`);
      const data = await res.json();
      let content: string;
      if (!res.ok || data.error) {
        content = `**No diff:** ${data.error || res.statusText}`;
      } else if (!data.diff.trim()) {
        content = `No uncommitted changes in \`${workspace}\`.`;
      } else {
        // The diff can itself contain a run of backticks (this file's source
        // does), so the fence has to be longer than the longest run in it.
        const longest = Math.max(0, ...(data.diff.match(/`+/g) || []).map((m: string) => m.length));
        const fence = '`'.repeat(Math.max(3, longest + 1));
        content = `### git diff - ${workspace}\n\n${fence}diff\n${data.diff}\n${fence}`;
      }
      appendLocalMessage({ role: 'assistant', content });
    } catch (err) {
      appendLocalMessage({ role: 'assistant', content: `**Failed to read diff:** ${(err as Error).message}` });
    }
    return;
  }

  if (cmd === '/model') {
    const modelMsg: Message = {
      role: 'assistant',
      content: `**Last routed model:** \`${currentModelSpan.textContent}\`\n\nThe pool picks a member per request, so this is the most recent one seen in the run log, not a fixed setting.`
    };
    appendLocalMessage(modelMsg);
    return;
  }

  // Unknown command fallback -> send as regular task
  await sendMessage(cmdStr);
}

function appendLocalMessage(msg: Message) {
  const emptyState = messagesContainer.querySelector('.empty-state');
  if (emptyState) emptyState.remove();
  messagesContainer.appendChild(createMessageElement(msg));
  scrollToBottom();
}

// Clear current session messages
async function clearCurrentSession() {
  if (!currentSessionId) return;
  try {
    const res = await fetch(`/api/sessions/${currentSessionId}/clear`, { method: 'POST' });
    if (res.ok) {
      await selectSession(currentSessionId);
    }
  } catch (err) {
    console.error('Error clearing session:', err);
  }
}

// Load all sessions
async function loadSessions() {
  try {
    const res = await fetch('/api/sessions');
    const sessions: Session[] = await res.json();
    
    sessionsList.innerHTML = '';
    sessionsCountBadge.textContent = String(sessions.length);
    
    if (sessions.length > 0 && !currentSessionId) {
      currentSessionId = sessions[0].id;
    }

    sessions.forEach(session => {
      const li = document.createElement('li');
      li.className = `session-item ${session.id === currentSessionId ? 'active' : ''}`;
      
      const sessionMain = document.createElement('div');
      sessionMain.className = 'session-main';

      const titleSpan = document.createElement('span');
      titleSpan.className = 'session-title-text';
      titleSpan.textContent = session.title || 'Untitled Task';

      const metaSpan = document.createElement('span');
      metaSpan.className = 'session-meta-text';
      const msgCount = session.messages ? session.messages.length : 0;
      metaSpan.textContent = `${msgCount} msgs · ${formatRelativeTime(session.updated_at || session.created_at)}`;

      sessionMain.appendChild(titleSpan);
      sessionMain.appendChild(metaSpan);

      sessionMain.addEventListener('click', () => {
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
        if (confirm(`Delete session "${session.title}"?`)) {
          await deleteSession(session.id);
        }
      });

      li.appendChild(sessionMain);
      li.appendChild(deleteBtn);
      sessionsList.appendChild(li);
    });
  } catch (error) {
    console.error('Error loading sessions:', error);
  }
}

function formatRelativeTime(dateStr?: string): string {
  if (!dateStr) return 'just now';
  const time = new Date(dateStr).getTime();
  const diff = Math.floor((Date.now() - time) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
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
  await loadSessions();

  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) {
      currentSessionId = null;
      await loadSessions();
      return;
    }
    const session: Session = await res.json();
    
    sessionTitle.textContent = session.title;
    renderMessages(session.messages || []);
    
    if (session.status === 'working') {
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

// Render messages
function renderMessages(messages: Message[]) {
  messagesContainer.innerHTML = '';
  
  if (messages.length === 0) {
    const emptyDiv = document.createElement('div');
    emptyDiv.className = 'empty-state';
    emptyDiv.innerHTML = `
      <pre class="banner-ascii">
     _                   _       
  __| |  ___   ___    __| |  ___ 
 / _\` | / __| / _ \\  / _\` | / _ \\
| (_| || (__ | (_) || (_| ||  __/
 \\__,_| \\___| \\___/  \\__,_| \\___|
      </pre>
      <h2>Deep Agents Coding Harness (dcode)</h2>
      <p class="empty-state-desc">
        Autonomous coding agent powered by LangChain & LangGraph. Pick a workspace folder, provide tasks or bugs to fix, and dcode will inspect, plan, write code, and run tests.
      </p>

      <div class="suggestion-grid">
        <div class="suggestion-card" data-prompt="Inspect this workspace and explain the repository structure.">
          <div class="suggestion-title">🔍 Inspect Workspace</div>
          <div class="suggestion-desc">Analyze files, architecture, and dependencies.</div>
        </div>
        <div class="suggestion-card" data-prompt="Find any syntax or typecheck issues in the codebase and fix them.">
          <div class="suggestion-title">🛠️ Fix Code Issues</div>
          <div class="suggestion-desc">Run typecheck and resolve compiler errors.</div>
        </div>
        <div class="suggestion-card" data-prompt="Implement unit tests for edge cases in the project modules.">
          <div class="suggestion-title">🧪 Add Unit Tests</div>
          <div class="suggestion-desc">Generate test suites and verify assertions.</div>
        </div>
        <div class="suggestion-card" data-prompt="Refactor the codebase with clean typings, comments, and style.">
          <div class="suggestion-title">✨ Code Refactoring</div>
          <div class="suggestion-desc">Improve code quality and maintainability.</div>
        </div>
      </div>
    `;

    // Suggestion card clicks
    emptyDiv.querySelectorAll('.suggestion-card').forEach(card => {
      card.addEventListener('click', () => {
        const prompt = card.getAttribute('data-prompt');
        if (prompt) {
          chatInput.value = prompt;
          autoResizeInput();
          chatInput.focus();
        }
      });
    });

    messagesContainer.appendChild(emptyDiv);
    return;
  }

  messages.forEach(msg => {
    messagesContainer.appendChild(createMessageElement(msg));
  });

  scrollToBottom();
}

// Build one message element. Assistant content is markdown; user content is not.
function createMessageElement(msg: Message): HTMLDivElement {
  const wrapper = document.createElement('div');
  wrapper.className = `message-wrapper ${msg.role}`;

  const header = document.createElement('div');
  header.className = 'message-header';

  if (msg.role === 'assistant') {
    header.innerHTML = `<span class="message-author dcode">🤖 dcode</span>`;
  } else if (msg.role === 'user') {
    header.innerHTML = `<span class="message-author user">👤 You</span>`;
  } else {
    header.innerHTML = `<span class="message-author system">⚙️ System</span>`;
  }

  const body = document.createElement('div');
  body.className = 'message-body';

  if (msg.role === 'assistant') {
    try {
      const rawHtml = marked.parse(msg.content) as string;
      body.innerHTML = DOMPurify.sanitize(rawHtml);
    } catch (err) {
      body.textContent = msg.content;
    }
  } else {
    body.textContent = msg.content;
  }

  wrapper.appendChild(header);
  wrapper.appendChild(body);
  return wrapper;
}

function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Running state management
function setRunningState(running: boolean) {
  isRunning = running;
  chatInput.disabled = running;
  sendBtn.disabled = running;
  workspacePickerBtn.disabled = running;
  newSessionBtn.disabled = running;
  
  if (running) {
    agentStatusPill.className = 'agent-status-pill working';
    agentStatusText.textContent = 'Executing...';
    workingIndicator.classList.remove('hidden');
    indicatorText.textContent = 'Planning strategy & routing tools...';
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
    agentStatusPill.className = 'agent-status-pill ready';
    agentStatusText.textContent = 'Agent Ready';
    workingIndicator.classList.add('hidden');
    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }
    startTime = null;
  }
}

// Connect live SSE streaming
function connectStream(sessionId: string, isReconnect = false) {
  setRunningState(true);
  if (!isReconnect) {
    liveOutput.textContent = '';
  }

  const eventSourceUrl =
    `/api/sessions/${sessionId}/run?workspace=${encodeURIComponent(workspace)}` +
    (isReconnect ? '' : '&start=1');

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

      // Intelligent status phase updates
      if (chunk.includes('read_file') || chunk.includes('glob') || chunk.includes('grep')) {
        indicatorText.textContent = '🔍 Inspecting files & searching codebase...';
      } else if (chunk.includes('edit_file') || chunk.includes('write_file')) {
        indicatorText.textContent = '🛠️ Modifying files & applying diffs...';
      } else if (chunk.includes('execute') || chunk.includes('pytest') || chunk.includes('npm')) {
        indicatorText.textContent = '⚡ Running sandboxed shell command...';
      } else if (chunk.includes('write_todos') || chunk.includes('todos')) {
        indicatorText.textContent = '📋 Planning tasks & goal rubric...';
      } else if (chunk.includes('task(')) {
        indicatorText.textContent = '👥 Delegating subagent task...';
      } else if (chunk.includes('Routing to')) {
        const match = chunk.match(/Routing to ([^. \n]+)/);
        if (match) {
          indicatorText.textContent = `Working via ${match[1]}...`;
          currentModelSpan.textContent = match[1];
        }
      } else if (chunk.includes('step') || chunk.includes('Iteration')) {
        indicatorText.textContent = '🧠 Executing agent step...';
      }
    } catch (err) {
      console.error('Error parsing output event:', err);
    }
  });

  currentEventSource.addEventListener('error', (e: any) => {
    console.error('SSE Error:', e);
    try {
      const data = JSON.parse(e.data);
      liveOutput.textContent += `\n[Error: ${data.error || 'Execution interrupted'}]`;
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
      indicatorText.textContent = data.status === 'completed' ? '✨ Complete!' : 'Completed with errors';
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

// Send user message
async function sendMessage(content: string) {
  if (!currentSessionId) return;
  
  setRunningState(true);
  chatInput.value = '';
  autoResizeInput();

  try {
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
    
    sessionTitle.textContent = session.title;
    renderMessages(session.messages || []);
    await loadSessions();

    connectStream(currentSessionId, false);

  } catch (error) {
    console.error('Error sending message:', error);
    alert('Error: ' + (error as Error).message);
    setRunningState(false);
  }
}

// Initialize on DOM load
// ==========================================================================
// Workspace folder picker
//
// A browser cannot hand a server an absolute path -- a native folder dialog
// gives the page a name and a sandboxed handle, never a location on disk. So
// the picker walks the filesystem server-side over /api/browse, and what the
// user clicks through is the machine the agent actually runs on.
// ==========================================================================

const MAX_PATH_CHARS = 34;

function shortenPath(full: string): string {
  if (full.length <= MAX_PATH_CHARS) return full;
  const sep = full.includes('\\') ? '\\' : '/';
  const parts = full.split(/[\\/]/).filter(Boolean);
  const kept: string[] = [];
  for (let i = parts.length - 1; i >= 0; i--) {
    const candidate = [parts[i], ...kept];
    if (candidate.join(sep).length + 2 > MAX_PATH_CHARS && kept.length) break;
    kept.unshift(parts[i]);
  }
  return '…' + sep + kept.join(sep);
}

function setWorkspace(dir: string) {
  workspace = dir;
  workspaceDisplay.textContent = shortenPath(dir);
  workspacePickerBtn.title = `Workspace: ${dir}\nClick to choose another folder`;
  try {
    localStorage.setItem(WORKSPACE_KEY, dir);
  } catch {
    // Private browsing, or storage is full: the choice just will not persist.
  }
}

function restoreWorkspace() {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(WORKSPACE_KEY);
  } catch {
    saved = null;
  }
  setWorkspace(saved || '.');
}

function openFolderPicker() {
  if (isRunning) return;
  folderModal.classList.remove('hidden');
  // '.' has no meaning to the picker, so start it wherever the server is.
  void browseTo(workspace === '.' ? '' : workspace);
}

function closeFolderPicker() {
  folderModal.classList.add('hidden');
}

async function browseTo(dir: string) {
  folderList.innerHTML = '<li class="folder-empty">Loading…</li>';
  try {
    const res = await fetch(`/api/browse?path=${encodeURIComponent(dir)}`);
    const data = await res.json();
    if (!res.ok) {
      folderList.innerHTML = '';
      const li = document.createElement('li');
      li.className = 'folder-empty error';
      li.textContent = data.error || 'Could not open that folder.';
      folderList.appendChild(li);
      return;
    }
    renderBrowse(data);
  } catch (error) {
    folderList.innerHTML = '';
    const li = document.createElement('li');
    li.className = 'folder-empty error';
    li.textContent = `Could not reach the server: ${(error as Error).message}`;
    folderList.appendChild(li);
  }
}

interface BrowseResponse {
  path: string;
  parent: string | null;
  separator: string;
  roots: string[];
  dirs: { name: string; path: string }[];
}

function renderBrowse(data: BrowseResponse) {
  browsePath = data.path;
  folderSelectionPath.textContent = data.path;

  // Drives (Windows) or '/' (everywhere else)
  folderRoots.innerHTML = '';
  for (const root of data.roots) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'folder-root-chip';
    if (data.path.toLowerCase().startsWith(root.toLowerCase())) {
      chip.classList.add('active');
    }
    chip.textContent = root;
    chip.addEventListener('click', () => void browseTo(root));
    folderRoots.appendChild(chip);
  }

  renderBreadcrumb(data.path, data.separator);

  folderList.innerHTML = '';
  if (data.parent) {
    const up = document.createElement('li');
    up.className = 'folder-row up';
    up.innerHTML = '<span class="folder-icon">⬆</span><span class="folder-name">..</span>';
    up.addEventListener('click', () => void browseTo(data.parent as string));
    folderList.appendChild(up);
  }

  for (const dir of data.dirs) {
    const li = document.createElement('li');
    li.className = 'folder-row';
    const icon = document.createElement('span');
    icon.className = 'folder-icon';
    icon.textContent = '\u{1F4C1}';
    const name = document.createElement('span');
    name.className = 'folder-name';
    // A directory name is not ours to trust as markup.
    name.textContent = dir.name;
    li.appendChild(icon);
    li.appendChild(name);
    li.addEventListener('click', () => void browseTo(dir.path));
    folderList.appendChild(li);
  }

  if (!data.dirs.length && !data.parent) {
    const li = document.createElement('li');
    li.className = 'folder-empty';
    li.textContent = 'No sub-folders here.';
    folderList.appendChild(li);
  }
  folderList.scrollTop = 0;
}

function renderBreadcrumb(full: string, sep: string) {
  folderBreadcrumb.innerHTML = '';

  const crumbs: { label: string; path: string }[] = [];
  if (full.startsWith('\\\\')) {
    // A UNC share has no useful segments to climb through.
    crumbs.push({ label: full, path: full });
  } else {
    const parts = full.split(/[\\/]/).filter(Boolean);
    if (sep === '/') {
      crumbs.push({ label: '/', path: '/' });
      let acc = '';
      for (const part of parts) {
        acc += '/' + part;
        crumbs.push({ label: part, path: acc });
      }
    } else {
      parts.forEach((part, i) => {
        crumbs.push({
          label: i === 0 ? part + sep : part,
          path: i === 0 ? part + sep : parts.slice(0, i + 1).join(sep),
        });
      });
    }
  }

  crumbs.forEach((crumb, i) => {
    if (i > 0 && !crumbs[i - 1].label.endsWith(sep)) {
      const caret = document.createElement('span');
      caret.className = 'crumb-sep';
      caret.textContent = sep;
      folderBreadcrumb.appendChild(caret);
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'crumb';
    if (i === crumbs.length - 1) btn.classList.add('current');
    btn.textContent = crumb.label;
    btn.addEventListener('click', () => void browseTo(crumb.path));
    folderBreadcrumb.appendChild(btn);
  });
}

window.addEventListener('DOMContentLoaded', init);
