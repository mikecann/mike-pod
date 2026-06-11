"""
Webhook runner for Cowork scheduled tasks.
POST /run/{script}  — trigger a whitelisted script
POST /run/log       — append an activity log entry
GET  /run/log       — return today's activity log entries

This is retained only as legacy reference. It deliberately has no endpoint
that reads or returns credentials from the host.
"""
import subprocess
import pathlib
import json
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Header, Request
from typing import Optional
from pydantic import BaseModel

TOKEN_PATH = pathlib.Path.home() / ".config" / "webhook_token"
LOG_DIR = pathlib.Path.home() / "mike-pod" / "logs" / "activity"

SCRIPTS = {
    "research":   ["./venv/bin/python", "research.py"],
    "generate":   ["./venv/bin/python", "generate.py"],
    "ingest":     ["./venv/bin/python", "wiki_ingest.py"],
    "ingest-all": ["./venv/bin/python", "wiki_ingest.py", "--all"],
    "brain-blog":   ["./venv/bin/python", "brain/blog_ingest.py"],
    "brain-github": ["./venv/bin/python", "brain/github_ingest.py"],
    "brain-convex": ["./venv/bin/python", "brain/convex_portfolio_ingest.py"],
}

WORKDIR = pathlib.Path.home() / "mike-pod"

app = FastAPI(title="Webhook Runner", version="1.1")

def load_token() -> str:
    return TOKEN_PATH.read_text().strip()

def check_auth(authorization: Optional[str]):
    token = load_token()
    expected = "Bearer " + token
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

def today_log_path() -> pathlib.Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / (date_str + ".jsonl")

class LogEntry(BaseModel):
    task: str
    status: str        # "ok" | "error" | "skipped"
    summary: str       # human-readable one-liner
    detail: str = ""   # optional longer output

@app.post("/run/log")
async def post_log(entry: LogEntry, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": entry.task,
        "status": entry.status,
        "summary": entry.summary,
        "detail": entry.detail,
    }
    with today_log_path().open("a") as f:
        f.write(json.dumps(record) + "\n")
    return {"ok": True}

@app.get("/run/log")
async def get_log(date: Optional[str] = None, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if date:
        log_path = LOG_DIR / (date + ".jsonl")
    else:
        log_path = today_log_path()
    if not log_path.exists():
        return {"entries": []}
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    return {"entries": entries}

@app.post("/run/{script}")
async def run_script(script: str, authorization: Optional[str] = Header(None)):
    check_auth(authorization)
    if script not in SCRIPTS:
        raise HTTPException(status_code=404, detail="Unknown script: " + repr(script) + ". Available: " + str(list(SCRIPTS)))
    cmd = SCRIPTS[script]
    try:
        result = subprocess.run(cmd, cwd=str(WORKDIR), capture_output=True, text=True, timeout=600)
        status = "ok" if result.returncode == 0 else "error"
        output = (result.stdout + result.stderr).strip()
        return {"status": status, "output": output, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": "Script timed out after 600s", "returncode": -1}
    except Exception as e:
        return {"status": "error", "output": str(e), "returncode": -1}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7846)
