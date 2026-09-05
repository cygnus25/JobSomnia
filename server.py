# server.py
import json
import queue
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from jobscraper import store
from jobscraper.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[0]

app = FastAPI()

STATUS_FILE = ROOT / "output/status.json"
JOBS_FILE = ROOT / "output/jobs.json"
RUNS_DB = ROOT / "output/jobs.db"

run_lock = threading.Lock()


def _read_status() -> dict:
    if STATUS_FILE.exists():
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    return {}


def _write_status(data: dict) -> None:
    STATUS_FILE.parent.mkdir(exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/")
async def index():
    return FileResponse(ROOT / "ui/index.html")


@app.get("/api/jobs")
async def get_jobs():
    if not JOBS_FILE.exists():
        return JSONResponse([])
    jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    status = _read_status()
    for job in jobs:
        job["status"] = status.get(job.get("url", ""), "none")
    return JSONResponse(jobs)


@app.get("/api/runs")
async def get_runs():
    """Recent pipeline runs from output/jobs.db (store.py), newest first —
    a run-history view the jobs.json export can't provide."""
    if not RUNS_DB.exists():
        return JSONResponse([])
    return JSONResponse(store.run_history(RUNS_DB))


class StatusUpdate(BaseModel):
    url: str
    status: str


@app.post("/api/status")
async def update_status(body: StatusUpdate):
    if body.status not in ("applied", "skipped", "none"):
        raise HTTPException(status_code=400, detail="status must be applied, skipped, or none")
    data = _read_status()
    if body.status == "none":
        data.pop(body.url, None)
    else:
        data[body.url] = body.status
    _write_status(data)
    return {"ok": True}


@app.get("/api/run")
async def run_agent():
    if not run_lock.acquire(blocking=False):
        async def _busy():
            yield f"data: {json.dumps({'step': 'busy'})}\n\n"
        return StreamingResponse(_busy(), media_type="text/event-stream")

    q: queue.Queue = queue.Queue()

    def _on_progress(step: int, label: str, status: str) -> None:
        q.put({"step": step, "label": label, "status": status})

    def _pipeline_thread() -> None:
        try:
            result = run_pipeline(on_progress=_on_progress)
            q.put({"step": "complete", **result})
        except Exception as exc:
            q.put({"step": "error", "message": str(exc)})
        finally:
            run_lock.release()
            q.put(None)

    threading.Thread(target=_pipeline_thread, daemon=True).start()

    async def _event_stream():
        import asyncio
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, q.get)
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
