"""Test package.

Puts ./src on sys.path so the suite runs from a clean checkout without installing:

    python -B -m unittest discover -s tests -t . -v
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
