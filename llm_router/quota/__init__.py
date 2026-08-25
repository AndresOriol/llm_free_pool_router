"""Reading the pool's consumption back.

[usage.py](../usage.py) writes what the router spends; this package reads it,
asks the vendors what they think when told to ([probe.py](probe.py)), and
renders the answer as a table, as JSON, or as a self-contained HTML panel.

    python -m llm_router.quota status [--json] [--probe]
    python -m llm_router.quota panel [--out PATH] [--probe]

The reasoning is in [14. Quota panel](../../docs/14-quota-panel.md).
"""

from .ledger import read_ledger, read_pool, read_vendor, usage_dir
from .report import Report, Row, build_report

__all__ = [
    "Report",
    "Row",
    "build_report",
    "read_ledger",
    "read_pool",
    "read_vendor",
    "usage_dir",
]
