# quota — what the pool has spent

A reader over the router's usage ledger and the vendors' own figures: a terminal
table, JSON for an agent, and a self-contained HTML panel. Standard library only.

The concepts are in [14. Quota panel](../../docs/14-quota-panel.md); this file is
how to run it.

## Use

```bash
python -m llm_router.quota status            # the table
python -m llm_router.quota status --json     # the same report, as data
python -m llm_router.quota panel             # writes the HTML, prints its path
python -m llm_router.quota status --probe    # ask the vendors first
```

`--dir PATH` reads a ledger from somewhere else (default `llm_router/.usage/`,
or `LLM_ROUTER_USAGE_DIR`); `--out PATH` puts the panel somewhere else.

**`--probe` is the only thing here that touches the network**, and it costs one
request per pool member that can report — Groq publishes its remaining budget in
response headers, and only on a real completion. It is never implicit, so an
agent polling `status --json` cannot spend the budget it is asking about. Without
it, every figure is this router's own ledger and is labelled as such.

If the panel says it has no limits to measure against, the router has not loaded
since the ledger directory was created; `python -m llm_router` writes the pool
snapshot without making a call.

## Develop

```bash
python -m tests.llm_router.test_quota    # the report's arithmetic and sourcing
python -m tests.llm_router.test_usage    # the ledger the report reads
```

| File | What it answers |
| --- | --- |
| [ledger.py](ledger.py) | *What is on disk?* — the three files, and how a torn line is treated |
| [probe.py](probe.py) | *What does the vendor say?* — the one request that gets an answer, and what each platform will and won't tell you |
| [report.py](report.py) | *How close is each account to the wall?* — the windows, the gauges, and which source won |
| [html.py](html.py) | *What does a person see?* — the panel, as one file |
| [cli.py](cli.py) | *How is it asked?* — `status`, `status --json`, `panel`, `--probe` |
