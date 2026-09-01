# conftest.py — make the repo root importable when tests live in tests/.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
