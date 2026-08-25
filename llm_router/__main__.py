"""Load the pool and write its snapshot, without calling anything.

    python -m llm_router

The snapshot is what the quota panel measures usage against, and it is normally
written as a side effect of the router loading (loader.py). This exists for the
case where the panel is opened before any agent has run: the limits are in the
config either way, and nobody should have to spend a request to see them.
"""

import logging

from .loader import load_providers_from_config
from .usage import pool_path

logging.basicConfig(level=logging.INFO, format="%(message)s")
providers = load_providers_from_config()
print(f"{len(providers)} pool members written to {pool_path()}")
