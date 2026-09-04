# Project instructions — ai-job-scraper

## Shell output compression (RTK)

When running shell/terminal commands, prefix them with `rtk` so output is
compressed before reaching your context. Examples:

- `rtk pytest tests/ -q` — failures only, tracebacks trimmed
- `rtk git diff` — reduced context, headers stripped
- `rtk git log --oneline -10` — hash + subject only
- `rtk grep -rn "pattern" jobscraper/` — grouped by file
- `rtk ls` — tree format with counts

Do NOT wrap: commands whose full raw output you genuinely need verbatim
(e.g. `python -c` JSON dumps you must parse precisely, or anything writing
files). If `rtk` is not on PATH, run commands plain — never fail a task
over the prefix.

## CodeGraph

A `.codegraph/` index exists for this repo (Python). Prefer CodeGraph MCP
tools (symbol lookup, callers/callees, impact analysis) over re-reading
whole files when you need to understand structure or locate definitions.
Files change on disk constantly — the MCP server auto-syncs; trust it over
stale in-memory knowledge of file contents.

## Style

Be concise during implementation. Avoid narrating obvious actions. Do not
repeat plans or findings. Preserve full technical detail for errors,
commands, code, debugging, and important decisions.
