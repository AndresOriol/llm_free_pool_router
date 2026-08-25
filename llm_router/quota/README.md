# quota — what the pool has spent

A reader over the router's usage ledger: a terminal table, JSON for an agent,
and a self-contained, filterable HTML panel. Standard library only, and it never
calls a provider or spends a request.

By default a model is one line, summed over every account that serves it — *how
much Gemini is left*, rather than a tour of one console per key. Filter to an
account to see its members apart.

The concepts are in [14. Quota panel](../../docs/14-quota-panel.md); this file is
how to run it.

## Use

```bash
python -m llm_router.quota status                     # every model, over all its accounts
python -m llm_router.quota status --account groq_1    # one account, member by member
python -m llm_router.quota status --json              # the whole report, as data
python -m llm_router.quota panel                      # writes the HTML, prints its path
```

`--dir PATH` reads a ledger from somewhere else (default `llm_router/.usage/`,
or `LLM_ROUTER_USAGE_DIR`); `--out PATH` puts the panel somewhere else.

Every figure comes off `ledger.jsonl`, so nothing here costs a request and an
agent may ask as often as it likes. The price is that the ledger sees only what
went through this router — a key used elsewhere is under-counted, which the panel
says out loud.

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
| [ledger.py](ledger.py) | *What is on disk?* — the two files, and how a torn line is treated |
| [report.py](report.py) | *How close is each account to the wall?* — the windows, when they reset, and how a refusal is counted |
| [html.py](html.py) | *What does a person see?* — the panel, as one file, filters included |
| [cli.py](cli.py) | *How is it asked?* — `status`, `status --json`, `panel`, `--account` |
