Read resume.md to understand the candidate's full profile — profession, skills, experience, seniority, location, and preferences. The candidate can be in any profession; judge every job against what the resume actually shows.
The jobs to score are provided as a JSON array below this prompt, in the same message — not in a file.

For each job, score it 0–100 based on how well it matches THIS specific candidate:

Scoring factors:
- Skills match: award high points if the job requires skills, tools, or services the candidate's resume demonstrates
- Seniority fit: infer the candidate's level from their years of experience and role history in the resume; weight roles at or slightly above that level favorably, avoid roles far below or far above it
- Remote-first signals: explicit "remote" in title or description, async culture mentioned, timezone compatible with the candidate's location (from the resume)
- Posting freshness: award points if the job was posted within the last 30 days (use today's date provided at the end of this prompt) and the role is still open; penalize or skip listings that are expired, closed, or posted more than 30 days ago
- Preferences: honor any preferences stated in the resume (industries, company types or sizes, tools or stacks to avoid)
- Red flags: requires physical presence or relocation, citizenship or work-authorization restrictions the candidate doesn't meet, core requirements entirely outside the candidate's skill set, posting is closed or older than 30 days

Include only jobs with score >= 70.

Your entire response must be only the raw JSON array — no markdown fences, no explanation, nothing before or after it:

[{
  "title": "",
  "company": "",
  "url": "",
  "location": "",
  "salary": "",
  "score": 0,
  "verdict": "apply|review|skip",
  "match_reasons": [],
  "red_flags": [],
  "suggested_angle": ""
}]

suggested_angle: one sentence on how the candidate should frame their application for this specific role, based on their resume.
location: the job's location, copied from the input job's location field (use "" only if missing).
salary: the job's pay, copied from the input job's salary field if present (use "" if missing); if the input job has no salary field but the description states pay (e.g. "$25 per hour", "$60k-$70k plus benefits"), extract it concisely from the description; otherwise "".
Never invent or estimate a salary that appears nowhere in the input.
