"""Make the package importable without installing it first.

The tests import ``availability`` directly, so running them straight from a clone -- no editable
install, no PYTHONPATH -- has to work. Requiring either would mean the suite passes for whoever set
their shell up and fails for everyone else, which is precisely how a green build stops meaning
anything.

This lives in conftest.py so it applies however pytest is invoked and from whatever directory.
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
