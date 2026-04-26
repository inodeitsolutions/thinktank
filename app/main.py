"""
FastAPI wrapper. Accepts ideas, runs the crew in the background,
persists results to Postgres.
"""
import os
import uuid
import json
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel

from thinktank import run_thinktank

app = FastAPI(title="Think Tank", version="0.1")
DB = os.environ["DATABASE_URL"]


class IdeaIn(BaseModel):
    idea: str


class RunOut(BaseModel):
    run_id: str
    status: str
    idea: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run", response_model=RunOut)
def submit(body: IdeaIn, bg: BackgroundTasks):
    if not body.idea.strip():
        raise HTTPException(400, "idea is empty")
    run_id = str(uuid.uuid4())
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (id, idea, status) VALUES (%s, %s, 'running')",
            (run_id, body.idea),
        )
        c.commit()
    bg.add_task(_execute, run_id, body.idea)
    return RunOut(run_id=run_id, status="running", idea=body.idea)


@app.get("/run/{run_id}", response_model=RunOut)
def status(run_id: str):
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, idea, status, result, error, created_at, completed_at "
            "FROM runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return RunOut(
        run_id=str(row[0]),
        idea=row[1],
        status=row[2],
        result=row[3],
        error=row[4],
        created_at=row[5],
        completed_at=row[6],
    )


@app.get("/runs")
def list_runs(limit: int = 20):
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, idea, status, created_at FROM runs "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {"run_id": str(r[0]), "idea": r[1][:120], "status": r[2], "created_at": r[3]}
        for r in rows
    ]

@app.get("/run/{run_id}/full")
def full_run(run_id: str):
   """Return the idea plus every agent's full output."""
   with psycopg.connect(DB) as c, c.cursor() as cur:
       cur.execute(
           "SELECT idea, agent_outputs FROM runs WHERE id = %s", (run_id,)
       )
       row = cur.fetchone()
   if not row or not row[1]:
       raise HTTPException(404, "not found or no agent outputs stored")
   return {"idea": row[0], **row[1]}

def _execute(run_id: str, idea: str):
   try:
       result_dict = run_thinktank(idea)
       final = result_dict["final"]
       agent_outputs_json = json.dumps(result_dict)
       status_val, error = "done", None
   except Exception as e:  # noqa: BLE001
       final, agent_outputs_json, status_val, error = None, None, "failed", str(e)
   with psycopg.connect(DB) as c, c.cursor() as cur:
       cur.execute(
           "UPDATE runs SET status=%s, result=%s, agent_outputs=%s, "
           "error=%s, completed_at=%s WHERE id=%s",
           (status_val, final, agent_outputs_json, error,
            datetime.now(timezone.utc), run_id),
       )
       c.commit()
