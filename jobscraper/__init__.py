# jobscraper/__init__.py
"""JobScraper: scrape + analyze pipeline (no cover letters)."""
from dotenv import load_dotenv

load_dotenv()

from .config import THRESHOLD
from .pipeline import run, run_pipeline

__all__ = ["run", "run_pipeline", "THRESHOLD"]
