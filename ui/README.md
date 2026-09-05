# Coding Agent UI (TypeScript)

A TypeScript-based web User Interface (UI) for interacting with the coding agent, providing a modern chat experience with a session sidebar and local chat history.

## Features

- **Sidebar**: View, select, create, or delete past coding sessions and tasks.
- **Workspace Selector**: Specify the working directory for the agent.
- **Main Chat Window**: Chat interface for submitting tasks and viewing agent responses.
- **Live Streaming**: Real-time streaming of agent output via Server-Sent Events (SSE).
- **Local History**: Stored automatically in `.ui_data/sessions.json`.

## Installation

1. Ensure you have Node.js installed.
2. Install dependencies:
   ```bash
   cd ui
   npm install
   ```
   *(Ensure main project requirements are also installed: `pip install -r requirements.txt`)*

## Running the UI

1. Build the TypeScript application:
   ```bash
   npm run build
   ```
2. Start the server:
   ```bash
   npm start
   ```
3. Open your browser to [http://localhost:3000](http://localhost:3000).

## Development

To run in development mode with automatic rebuilding (requires `tsx`):
```bash
npx tsx src/server.ts
```
Or use `tsc --watch` in one terminal and `node dist/server.js` in another.
