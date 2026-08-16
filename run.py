#!/usr/bin/env python3
"""Run the availability service straight from a clone, with nothing installed.

    python run.py check   --config config.toml
    python run.py publish --config config.toml --strict
    python run.py serve   --config config.toml --with-web web

Installing the package (``python -m pip install -e backend``) gives you an ``availability``
command that does the same thing. This file exists so that step is optional: a tool that needs
its environment prepared before it will start is a tool that gets run wrongly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from availability.cli import main  # noqa: E402  (import follows the path setup, deliberately)

if __name__ == "__main__":
    raise SystemExit(main())
