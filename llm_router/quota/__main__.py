"""Entry point: `python -m llm_router.quota`. The commands live in cli.py."""

import sys

from .cli import main

sys.exit(main())
