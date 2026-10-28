"""
FastAPI starter service for render-pool.
Includes health check endpoint and local persistent storage demo.
"""

import os
import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="Render Pool Backend",
    version="1.0.0",
    description="FastAPI service running on pooled Render instances with Firebase persistence."
)

DATA_DIR = Path(os.getenv("PERSISTENT_DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Note(BaseModel):
    title: str
    content: str


@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "render-pool-instance",
        "hostname": os.getenv("RENDER_SERVICE_NAME", "local-dev"),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }


@app.get("/healthz")
def healthcheck():
    """Health check endpoint polled by render-pool during handoffs."""
    return {"status": "ok", "healthy": True}


@app.get("/api/notes")
def list_notes():
    """Demonstrates reading persistent files stored in /data."""
    notes = []
    for filepath in DATA_DIR.glob("*.txt"):
        try:
            content = filepath.read_text(encoding="utf-8")
            notes.append({"filename": filepath.name, "content": content})
        except Exception:
            pass
    return {"count": len(notes), "notes": notes}


@app.post("/api/notes")
def create_note(note: Note):
    """Demonstrates writing persistent files stored in /data."""
    clean_title = "".join(c for c in note.title if c.isalnum() or c in ("-", "_")).rstrip()
    if not clean_title:
        clean_title = "note"
    filename = f"{clean_title}_{int(datetime.datetime.now().timestamp())}.txt"
    filepath = DATA_DIR / filename
    filepath.write_text(note.content, encoding="utf-8")
    return {"message": "Note saved to persistent storage", "filename": filename}
