"""Reading the pool's consumption back.

[usage.py](../usage.py) writes what the router spends; this package reads it
back and renders it as a table, as JSON, or as a self-contained HTML panel.
Nothing here calls a provider or spends a request.

It also answers one question for the router itself: [budget.py](budget.py) says
which members have spent their requests-per-day, so a daily ceiling can be
routed around instead of rediscovered by refusal. That is the only part of this
package anything upstream depends on, and it is advisory
([What it deliberately doesn't do](../../docs/pool/quota.md#what-it-deliberately-doesnt-do)).

    python -m llm_router.quota status [--json] [--account NAME]
    python -m llm_router.quota panel [--out PATH] [--account NAME]

The reasoning is in [Quota panel](../../docs/pool/quota.md).
"""
