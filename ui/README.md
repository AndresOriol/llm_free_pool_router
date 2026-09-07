# Deep Agents Coding UI (`dcode`)

A web UI for the coding agent in [`agent/code`](../agent/code/), styled after
[`deepagents-code`](https://github.com/langchain-ai/deepagents/tree/main/libs/code).
It runs one agent at a time against a workspace folder, streams the run, and
keeps the conversation as a session on disk.

The server spawns `python -m agent.code <workspace>` with the conversation on
stdin, so the agent — and the router pool behind it — is unchanged by anything
here.

## Tech stack

- **`marked`** (v12.x) for markdown rendering of agent replies.
- **`highlight.js`** (v11.x), core build with languages registered explicitly:
  TypeScript, JavaScript, Python, Bash, JSON, Markdown, YAML, HTML/XML, CSS,
  diff, SQL, Rust, Go, C++, Java, C#, Ruby, Dockerfile.
- **`DOMPurify`** (v3.1.x) sanitises every rendered markdown output.
- **`esbuild`** (v0.21.5) bundles the client to browser ESM (`dist/client/app.js`).
- **`express`** (v4.19.x) serves the REST endpoints and the SSE run stream.

No framework and no CDN: the page loads only its own `style.css` and bundle, so
it works offline. Colours are Catppuccin Macchiato tokens defined in `:root`,
including the `highlight.js` token colours.

## Features

- **Sessions** — sidebar list with message counts and relative timestamps;
  create, select, clear and delete. Stored in `.ui_data/sessions.json`.
- **Workspace picker** — the folder the agent is pointed at, chosen by
  browsing the filesystem rather than typed as a path. A browser cannot hand a
  server an absolute path — a native folder dialog gives the page a name and a
  sandboxed handle, never a location on disk — so the picker walks the machine
  the server runs on, through `GET /api/browse`, and what you click through is
  the filesystem the agent will actually see. Drive roots on Windows, a
  clickable breadcrumb, and the choice remembered in `localStorage`.
- **Live run stream** — elapsed timer, a collapsible drawer of raw
  stdout/stderr, and a status line that follows the run by matching the tool
  names and router lines that appear in that output.
- **Model indicator** — shows the pool member the router last routed to, read
  from the run log. It reads `router pool` until a run reports one.
- **Stop** — `POST /api/sessions/:id/stop` kills the child; only an explicit
  stop records "Stopped by the user."
- **Runs survive their viewer** — closing the tab or refreshing leaves the child
  running; reopening the session re-attaches to it and catches up on the output
  it missed.
- **Markdown, code and diffs** — copyable code blocks with syntax highlighting,
  and a unified-diff viewer with line-number gutters used for any ` ```diff `
  block.
- **Slash commands** — `/help`, `/diff`, `/model`, `/clear`, with an
  autocomplete popup and sidebar chips. Except for `/clear`, these render in the
  transcript locally: they are never stored in the session and never sent to the
  agent.
  - `/diff` runs `git diff` in the workspace and renders it in the diff viewer.

## Installation & running

1. Install dependencies:
   ```bash
   npm install
   ```

2. Build the client bundle:
   ```bash
   npm run build
   ```

3. Start the server:
   ```bash
   npm start
   ```
   Open [http://localhost:3000](http://localhost:3000).

The workspace defaults to `.`, which is this `ui/` directory, until you pick a
folder; picking one stores an absolute path, so it no longer depends on where
the server was started.

The Python side must be importable from the repository root — the server puts
the root on `PYTHONPATH` and calls `python`, overridable with
`PYTHON_EXECUTABLE`.

## Verification

That the client bundle is browser ESM with no CommonJS leaking into it:
```bash
npm run verify-client
```

Types:
```bash
npm run typecheck
```
