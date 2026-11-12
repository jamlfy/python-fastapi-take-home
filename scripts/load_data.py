"""CLI entrypoint for the coffee health loader.

Usage:
    uv run python scripts/load_data.py data/coffee_health_raw.csv

The implementation lives in app.coffee.loader and the command-line handling in
app.coffee.cli, so both can be reused and tested without going through this
script.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.coffee.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
