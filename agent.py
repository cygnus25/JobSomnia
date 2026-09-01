# agent.py — CLI shim; the real code lives in the jobscraper/ package.
import logging

from jobscraper import run

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
