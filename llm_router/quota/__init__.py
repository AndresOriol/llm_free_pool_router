"""Reading the pool's consumption back.

[usage.py](../usage.py) writes what the router spends; this package reads it
back and renders it as a table, as JSON, or as a self-contained HTML panel.
Nothing here calls a provider or spends a request.

    python -m llm_router.quota status [--json] [--account NAME]
    python -m llm_router.quota panel [--out PATH] [--account NAME]

The reasoning is in [14. Quota panel](../../docs/14-quota-panel.md).
"""

from .ledger import read_ledger, read_pool, usage_dir
from .report import Report, Row, build_report

__all__ = [
    "Report",
    "Row",
    "build_report",
    "read_ledger",
    "read_pool",
    "usage_dir",
]
