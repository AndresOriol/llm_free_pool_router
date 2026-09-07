# Comprehensive Research Report: UI Architecture, Styling, Protocols, and Frontend Integration for LangChain Deep Agents (`libs/code` and related packages)

## 1. Directory Structure, Architecture, and Tech Stack

### 1.1 Architecture Overview
The coding agent in `libs/code` is distributed as the Python package **`deepagents-code`** (CLI executable `dcode`) [1]. It is designed as a batteries-included coding agent harness inspired by Claude Code, Cursor, and OpenCode, running on top of **LangGraph** and the **Deep Agents SDK** [1, 2].

The user interface ecosystem in `langchain-ai/deepagents` spans three complementary presentation layers:
1. **Interactive Terminal UI (TUI)**: Built with **Textual** (Python's async terminal UI application framework) and **Rich** in `libs/code/deepagents_code/tui/` [1].
2. **IDE & Editor Protocol Server (ACP)**: Implemented in `libs/acp/` (both Python `deepagents-acp` and TypeScript `@langchain/deepagents-acp`), standardizing editor integrations with Zed, JetBrains, VS Code, and Toad [4].
3. **Web & Generative UI (AG-UI / CopilotKit & TypeScript Web UIs)**: Standardized via the AG-UI protocol and lightweight ES-Module / Next.js web clients [5, 6].

```
deepagents/
├── libs/
│   ├── code/                         # deepagents-code (dcode CLI & TUI)
│   │   ├── deepagents_code/
│   │   │   ├── __init__.py
│   │   │   ├── app.py                # Textual DeepAgentsApp main entry
│   │   │   ├── agent.py              # LangGraph agent orchestration & middleware
│   │   │   ├── theme.py              # Semantic color tokens & ThemeColors registry
│   │   │   ├── diff_utils.py         # Unified diff parsing, lexing & hunk slicing
│   │   │   ├── auto_mode/            # Autonomous policy decisions & classifier review
│   │   │   ├── goal_rubric/          # Goal acceptance criteria & rubrics
│   │   │   ├── tui/
│   │   │   │   ├── app.tcss          # Textual CSS stylesheet for layout & themes
│   │   │   │   ├── key_hints.py      # Universal navigation & keybinding hints
│   │   │   │   ├── textual_adapter.py# Adapter bridging LangGraph streams to widgets
│   │   │   │   ├── screens/          # Modal screens (Auth, Model, Threads, etc.)
│   │   │   │   │   ├── auth.py
│   │   │   │   │   ├── model_selector.py
│   │   │   │   │   ├── thread_selector.py
│   │   │   │   │   ├── theme_selector.py
│   │   │   │   │   ├── debug_console.py
│   │   │   │   │   └── launch_init.py
│   │   │   │   └── widgets/          # Granular UI widgets & message renderers
│   │   │   │       ├── chat_input.py # Prompt input with autocomplete & history
│   │   │   │       ├── messages.py   # Assistant, User, System, and Error bubbles
│   │   │   │       ├── diff.py       # Syntax-highlighted unified diff renderer
│   │   │   │       ├── loading.py    # Animated multi-stage thinking spinner
│   │   │   │       ├── goal_status.py# Persistent inline goal / task tracker
│   │   │   │       ├── autocomplete.py# Slash (/) and file mention (@) triggers
│   │   │   │       ├── message_store.py# Virtualized DOM sliding window
│   │   │   │       ├── tool_widgets.py # Tool-specific approval & execution cards
│   │   │   │       ├── subagent_panel.py# Live subagent fan-out task dashboard
│   │   │   │       ├── ask_user.py   # Interactive question / prompt widget
│   │   │   │       └── status.py     # Bottom status bar (model, cost, tokens, mode)
│   │   ├── pyproject.toml
│   │   └── README.md
│   ├── acp/                          # Agent Client Protocol integration
│   │   ├── deepagents_acp/           # Python ACP Server (stdio JSON-RPC)
│   │   └── run_demo_agent.sh         # Zed / IDE runner
│   └── deepagents/                   # Deep Agents Core SDK & Middleware
```

---

## 2. Dependencies, Packages, and Component Frameworks

### 2.1 Backend / Python TUI Dependencies (`libs/code/pyproject.toml`)
* **`textual`** (`>=0.80.0`): The async terminal application framework providing DOM hierarchy, CSS styling engine, layout containers (`VerticalScroll`, `Horizontal`, `Dock`), event dispatching, and reactive attributes.
* **`rich`** (`>=13.7.0`): Low-level text formatting, ANSI color rendering, syntax tokens, and markdown generation.
* **`pygments`** (`>=2.17.0`): Syntax highlighting lexers used for whole-file context during diff hunk generation and code block display.
* **`langgraph`** & **`deepagents`**: Core harness runtime, graph checkpointing, state channels, and subagent orchestration.

### 2.2 Web & TypeScript Frontend Ecosystem
For web UIs interacting with Deep Agents:
* **AG-UI / CopilotKit Stack**: `@copilotkit/react-core`, `@copilotkit/react-ui`, `@copilotkit/runtime` with `@copilotkit/runtime/langgraph`.
* **Zero-overhead TypeScript Stack**:
  * **`marked`** (`^12.0.2`): High-speed markdown parser with customized code block renderers.
  * **`highlight.js`** (`^11.9.0`): Lightweight client-side syntax highlighting for code blocks.
  * **`dompurify`** (`^3.1.6`): Strict sanitization against XSS in rendered HTML.
  * **`esbuild`** (`^0.21.5`): Native browser ES-Module bundling (`dist/client/app.js`).
  * **`express`** (`^4.19.2`): Web server managing SSE streaming (`/api/sessions/:id/stream`) and process execution.

---

## 3. UI Layout, Styling, Theme, and Color Scheme

### 3.1 LangChain Official Semantic Color Palette
Defined in `deepagents_code/theme.py`, the color system provides consistent dark and light themes with strict semantic role tokens:

| Token Name | Dark Theme Hex | Light Theme Hex | Semantic Usage / Visual Role |
|---|---|---|---|
| `LC_DARK` / `LC_LIGHT_BG` | `#181926` / `#1e1e2e` | `#f4f4f5` | App Background (visible dark-blue neutral tint) |
| `LC_CARD` / `LC_LIGHT_SURFACE` | `#24273a` / `#252538` | `#ffffff` | Surface / Card container, elevated above background |
| `LC_PANEL` / `LC_LIGHT_PANEL` | `#1e2030` | `#eaecef` | Sub-panel & drawer background |
| `LC_BORDER_DK` / `LC_LIGHT_BORDER` | `#363a4f` / `#313244` | `#d0d7de` | Primary borders and divider lines |
| `LC_BORDER_LT` / `LC_LIGHT_BORDER_HVR`| `#494d64` / `#45475a`| `#afb8c1` | Hovered or focused card border |
| `LC_BODY` / `LC_LIGHT_BODY` | `#cad3f5` / `#cdd6f4` | `#24292f` | High-contrast main body text |
| `LC_MUTED` / `LC_LIGHT_MUTED` | `#8087a2` / `#a6adc8` | `#57606a` | Secondary text, timestamps, line numbers |
| `LC_BLUE` / `LC_LIGHT_BLUE` | `#8aadf4` / `#89b4fa` | `#0969da` | Primary brand accent, headings, links, active elements |
| `LC_PURPLE` / `LC_LIGHT_PURPLE` | `#c6a0f6` / `#b4befe` | `#8250df` | Secondary accent, badges, subagent tags, labels |
| `LC_GREEN` / `LC_LIGHT_GREEN` | `#a6da95` / `#a6e3a1` | `#1a7f37` | Success status, approved tool calls |
| `LC_AMBER` / `LC_LIGHT_AMBER` | `#eed49f` / `#f9e2af` | `#9a6700` | Tool call accent, warnings, caution alerts |
| `LC_PINK` / `LC_LIGHT_PINK` | `#ed8796` / `#f38ba8` | `#cf222e` | Destructive actions, errors, stopped runs |
| `LC_INCOGNITO` / `LC_LIGHT_INCOGNITO`| `#8bd5ca` | `#1b7c83` | Isolated sandbox / incognito execution indicator |
| `DIFF_ADD_BG` / `LC_GREEN_BG` | `#223b2c` / `#a6da9520`| `#dafbe1` | Added line background in unified diff |
| `DIFF_REMOVE_BG` / `LC_PINK_BG` | `#3d212b` / `#ed879620`| `#ffebe9` | Removed line background in unified diff |

### 3.2 UI Component Breakdown

#### A. Sidebar
* **Workspace Directory Selector**: Input box configured with placeholder and path autocomplete, locking the agent's target working directory (`workspace_path`).
* **Session Manager**: "+ New Task / Chat" CTA button, scrollable list of historical sessions with active highlights, relative timestamps, and one-click deletion.

#### B. Main Chat Window
* **Chat Header**: Displays the current task title, active agent mode (`Manual`, `Auto`, `YOLO`), model badge (e.g. `gpt-5.5` / `claude-sonnet-4-6`), and running cost counter.
* **Virtualized Message Scroll**:
  * **User Message**: Right-aligned or distinct container with subtle background `#313244` and high-contrast text.
  * **Assistant Message**: Left-aligned card `#24273a` with full markdown rendering, LaTeX, table layouts, and syntax-highlighted code blocks.
  * **Tool Execution Card**: Collapsible bordered card (`border-color: #eed49f`) displaying tool name, parameter pills, and execution status (`running`, `success`, `error`).
  * **Subagent Fan-Out Panel**: Nested tree card showing parallel subtasks (`task(...)`), individual token/time consumption, and sub-goals.

#### C. Syntax-Highlighted Diff Views
* **Line Number Gutters**: Dual column line numbers for before (`-`) and after (`+`).
* **Hunk Context**: Unchanged context lines displayed with muted color `#8087a2`.
* **Word-Level Span Highlighting**: Highlighting specific character changes within modified lines.
* **Interactive HITL Controls**: "Approve", "Reject with Feedback", or "Edit" buttons directly inside the diff card.

#### D. Thinking Indicator & Live Streaming
* **Multi-Stage Animated Spinner**: Shows active phase (`Planning`, `Reading files`, `Executing shell command`, `Synthesizing output`).
* **Collapsible Log Drawer (`<details>` / Accordion)**: Real-time raw stdout/stderr telemetry separated from the polished conversational chat history.

---

## 4. Frontend Code & Component Implementation

### 4.1 CSS Stylesheet (`style.css`)
```css
:root {
  --bg-primary: #181926;
  --bg-secondary: #24273a;
  --bg-sidebar: #1e2030;
  --text-primary: #cad3f5;
  --text-secondary: #8087a2;
  --accent-color: #8aadf4;
  --accent-hover: #b4befe;
  --border-color: #363a4f;
  --border-hover: #494d64;
  --user-msg-bg: #313244;
  --assistant-msg-bg: #24273a;
  --error-color: #ed8796;
  --success-color: #a6da95;
  --warning-color: #eed49f;
  --diff-add-bg: rgba(166, 218, 149, 0.15);
  --diff-remove-bg: rgba(237, 135, 150, 0.15);
  --diff-add-border: #a6da95;
  --diff-remove-border: #ed8796;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background-color: var(--bg-primary);
  color: var(--text-primary);
  height: 100vh;
  display: flex;
  overflow: hidden;
}

.app-container {
  display: flex;
  width: 100vw;
  height: 100vh;
}

/* Sidebar */
.sidebar {
  width: 320px;
  background-color: var(--bg-sidebar);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  padding: 20px;
  gap: 16px;
}

.sidebar-header h2 {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--accent-color);
}

.workspace-container input {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-primary);
}

/* Messages */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  max-width: 85%;
  padding: 14px 18px;
  border-radius: 8px;
  line-height: 1.6;
}

.user-message {
  align-self: flex-end;
  background-color: var(--user-msg-bg);
  border: 1px solid var(--border-color);
}

.assistant-message {
  align-self: flex-start;
  background-color: var(--assistant-msg-bg);
  border: 1px solid var(--border-color);
}

/* Tool execution cards */
.tool-card {
  background: #1c1d2d;
  border: 1px solid var(--warning-color);
  border-radius: 6px;
  padding: 12px;
  margin: 8px 0;
}

.tool-header {
  font-weight: 600;
  color: var(--warning-color);
  display: flex;
  justify-content: space-between;
}

/* Diffs */
.diff-view {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.85rem;
  background: #14141e;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  overflow-x: auto;
  margin: 10px 0;
}

.diff-row {
  display: flex;
  padding: 2px 8px;
}

.diff-row.add {
  background-color: var(--diff-add-bg);
  border-left: 3px solid var(--diff-add-border);
}

.diff-row.remove {
  background-color: var(--diff-remove-bg);
  border-left: 3px solid var(--diff-remove-border);
}

.diff-gutter {
  width: 40px;
  color: var(--text-secondary);
  user-select: none;
}
```

### 4.2 TypeScript Client Application (`src/client/app.ts`)
```typescript
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';

// Setup marked with syntax highlighting
marked.use({
  renderer: {
    code(code: string, infostring?: string) {
      const lang = ((infostring || '').match(/\S*/)?.[0]) || 'plaintext';
      const validLang = hljs.getLanguage(lang) ? lang : 'plaintext';
      const highlighted = hljs.highlight(code, { language: validLang }).value;
      return `<pre><code class="hljs language-${validLang}">${highlighted}</code></pre>`;
    }
  }
});

export function renderMarkdown(content: string): string {
  return DOMPurify.sanitize(marked.parse(content) as string);
}

export function renderDiff(diffText: string): HTMLElement {
  const container = document.createElement('div');
  container.className = 'diff-view';

  const lines = diffText.split('\n');
  lines.forEach((line, index) => {
    const row = document.createElement('div');
    row.className = 'diff-row';
    
    if (line.startsWith('+') && !line.startsWith('+++')) {
      row.classList.add('add');
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      row.classList.add('remove');
    }

    const gutter = document.createElement('span');
    gutter.className = 'diff-gutter';
    gutter.textContent = String(index + 1);

    const text = document.createElement('span');
    text.className = 'diff-content';
    text.textContent = line;

    row.appendChild(gutter);
    row.appendChild(text);
    container.appendChild(row);
  });

  return container;
}
```

---

## 5. Tools, Events, Streaming Protocols, and Backend APIs

### 5.1 Deep Agents Built-in Tools
* **Planning**: `write_todos(todos: Array<{content: string, status: 'pending'|'in_progress'|'completed'}>)`, `read_todos()`
* **Virtual Filesystem**: `ls(path)`, `read_file(file_path, offset, limit)`, `write_file(file_path, content)`, `edit_file(file_path, old_string, new_string, replace_all)`, `delete(path)`, `glob(pattern, path)`, `grep(pattern, path, glob, output_mode)`
* **Code Execution**: `execute(command, timeout)` (Sandboxed shell), `eval(code)` / `js_eval(code)` (QuickJS runtime)
* **Delegation**: `task(description, subagent_type, prompt)` (Spawns isolated subagent)
* **Interactivity**: `ask_user(question, choices)` (Human-in-the-loop interruption)

### 5.2 Streaming Format & Events
Communication between the backend and UI uses **Server-Sent Events (SSE)** emitting typed events:

1. **`event: message`**:
   ```json
   { "type": "content", "delta": "I will inspect the codebase..." }
   ```
2. **`event: tool_call`**:
   ```json
   {
     "type": "tool_start",
     "tool": "edit_file",
     "id": "call_123",
     "input": { "file_path": "src/server.ts", "old_string": "foo", "new_string": "bar" }
   }
   ```
3. **`event: tool_result`**:
   ```json
   {
     "type": "tool_done",
     "id": "call_123",
     "output": "File updated successfully.",
     "diff": "--- src/server.ts\n+++ src/server.ts\n@@ -10,3 +10,3 @@\n-foo\n+bar"
   }
   ```
4. **`event: subagent_dispatch`**:
   ```json
   { "type": "subagent_start", "id": "sub_456", "name": "researcher", "task": "Search docs" }
   ```
5. **`event: done`**:
   ```json
   { "type": "complete", "session_id": "sess_789", "tokens": 1420, "cost_usd": 0.012 }
   ```

### 5.3 Backend REST & Protocol Endpoints
* **`GET /api/sessions`**: List all saved sessions and statuses [1].
* **`POST /api/sessions`**: Create a new session `{ id, title, workspace }` [1].
* **`GET /api/sessions/:id`**: Retrieve session state and message history [1].
* **`DELETE /api/sessions/:id`**: Purge session and associated artifacts [1].
* **`POST /api/sessions/:id/messages`**: Post user message and trigger agent step [1].
* **`GET /api/sessions/:id/stream`**: Connect to live SSE stream for real-time tokens, tool logs, and state [1].
* **`POST /api/sessions/:id/stop`**: Abort active run / kill child process [1].
* **`POST /api/copilotkit` (AG-UI)**: Bi-directional AG-UI proxy forwarding to LangGraph runtime [5, 6].
* **`ACP stdio / JSON-RPC`**: Stdio communication for Zed/VS Code editor integrations (`deepagents-acp`) [4].

---

## 6. Summary & Recommendations for Web UI Implementation
To faithfully reproduce the UI in a TypeScript Web UI:
1. Adopt the official LangChain dark palette (`#181926` background, `#24273a` card surface, `#8aadf4` blue primary accent, `#eed49f` tool accent, `#a6da95` / `#ed8796` diff colors) [1].
2. Separate high-frequency execution stdout logs into a collapsible drawer from the structured conversation [1].
3. Render unified diffs with line-number gutters, hunk bounding, and syntax highlighting [1].
4. Support slash commands (`/help`, `/model`, `/theme`, `/threads`, `/restart`) and `@` file autocomplete in the chat input [1].
5. Connect with either the native SSE endpoint or the AG-UI / CopilotKit streaming protocol [1, 5, 6].

---

### Sources
[1] LangChain Deep Agents Code Repository: https://github.com/langchain-ai/deepagents/tree/main/libs/code
[2] LangChain Deep Agents Overview Documentation: https://docs.langchain.com/oss/python/deepagents/overview
[3] LangChain Deep Agents Code API Reference: https://reference.langchain.com/python/deepagents-code
[4] Agent Client Protocol (ACP) Specification: https://docs.langchain.com/oss/python/deepagents/acp
[5] AG-UI Protocol Repository: https://github.com/ag-ui-protocol/ag-ui
[6] CopilotKit LangGraph Integration: https://docs.copilotkit.ai/langgraph/deep-agents
