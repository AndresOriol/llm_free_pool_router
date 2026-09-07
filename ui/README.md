# Coding Agent UI (TypeScript)

A TypeScript-based web User Interface (UI) for interacting with the coding agent, providing a modern chat experience with a session sidebar, local chat history, markdown rendering, code syntax highlighting, live streaming progress with stop control, and separated raw run logs.

## Chat UI & Rendering Library Choice

As researched and documented in `/research/final_report.md` (delegated to the `explore` agent), introducing heavy React component libraries would violate the zero-bundler ESM setup or require complex build tooling. Therefore, we utilize:
- **`marked`** (v12.x) for high-performance markdown parsing.
- **`highlight.js`** (v11.x) for syntax highlighting in code blocks.
- **`DOMPurify`** (v3.4.x) for robust XSS sanitization of rendered HTML.
- **`esbuild`** (v0.21.5) to bundle TypeScript modules into a native browser ESM bundle (`dist/client/app.js`), preventing any `exports is not defined` runtime errors.

## Features

- **Sidebar**: View, select, create, or delete past coding sessions and tasks.
- **Workspace Selector**: Specify the working directory for the agent.
- **Main Chat Window**: Chat interface for submitting tasks and viewing agent responses.
- **Markdown & Syntax Highlighting**: Assistant responses render formatted markdown with highlighted code blocks.
- **Live Streaming & Metrics**: Live execution stream displaying elapsed time, active model routing, and step progress, with a **Stop** button.
- **Separated Run Logs**: Raw agent execution stdout/stderr is stored separately and can be toggled via an expandable log drawer, keeping message history clean.
- **Local History**: Stored automatically in `.ui_data/sessions.json`.

## Installation

1. Ensure you have Node.js and Python installed.
2. Install UI dependencies and build:
   ```bash
   cd ui
   npm install
   ```
   *(Ensure main project requirements are also installed: `pip install -r requirements.txt`)*

## Running the UI

1. Build and start the server:
   ```bash
   npm start
   ```
2. Open your browser to [http://localhost:3000](http://localhost:3000).

## Verification

To verify that the client bundle is correctly formatted as an ES module without CommonJS artifacts (`exports`/`require`):
```bash
npm run verify-client
```
