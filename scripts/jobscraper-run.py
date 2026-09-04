# Run the AI job scraper pipeline headlessly and print the raw summary.
# stdout is injected into the Hermes agent's prompt as context; the agent
# turns it into a digest for delivery (see README "Scheduled runs").
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run(
    [sys.executable, "-m", "jobscraper.schedule"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    timeout=1200,
)
print(result.stdout)
if result.returncode != 0:
    print("SCHEDULED RUN FAILED (exit %d):" % result.returncode)
    print(result.stderr[-2000:])
