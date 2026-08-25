# quota — what the pool has spent

A reader over the router's usage ledger: a terminal table, a JSON feed for an
agent, and a self-contained HTML panel. TypeScript, no runtime dependencies, no
build step — Node runs the `.ts` sources directly.

The concepts are in [14. Quota panel](../../docs/14-quota-panel.md); this file
is how to run it.

## Use

```bash
node llm_router/quota/src/cli.ts status          # the table
node llm_router/quota/src/cli.ts status --json   # the same report, as data
node llm_router/quota/src/cli.ts panel           # writes the HTML, prints its path
```

`--dir <path>` reads a ledger from somewhere else (default `llm_router/.usage/`,
or `LLM_ROUTER_USAGE_DIR`); `--out <path>` puts the panel somewhere else.

Nothing here calls a provider or reads an API key. Both inputs are written by
[`llm_router/usage.py`](../usage.py): `ledger.jsonl`, one line per attempt, and
`pool.json`, the configured pool with each model's declared limits. If the panel
says it has no limits to measure against, the router has not loaded since the
ledger directory was created — `python -m llm_router` writes the snapshot
without making a call.

## Develop

```bash
node --test "llm_router/quota/test/**/*.test.ts"   # the report's arithmetic
```

Typechecking is optional and the only thing that needs an install:

```bash
npm --prefix llm_router/quota install && npm --prefix llm_router/quota run typecheck
```

`tsconfig.json` sets `erasableSyntaxOnly`, which keeps the code inside the
subset Node can strip — no enums, no namespaces, no parameter properties. That
constraint is what buys the missing build step, so don't relax it.

| File | What it answers |
| --- | --- |
| [src/ledger.ts](src/ledger.ts) | *What did the router write down?* — the two file formats, and where they live |
| [src/report.ts](src/report.ts) | *How close is each account to the wall?* — the windows and the gauges |
| [src/html.ts](src/html.ts) | *What does a person see?* — the panel, as one file |
| [src/cli.ts](src/cli.ts) | *How is it asked?* — `status`, `status --json`, `panel` |
