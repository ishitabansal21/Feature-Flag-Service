import json
import logging
import os
from typing import Generator

import boto3
from fastapi import Depends, FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator

from database import get_conn, get_pool, release_conn
from models import FlagCreate, FlagResponse, FlagUpdate

app = FastAPI(title="Feature Flag Service")
Instrumentator().instrument(app).expose(app)

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE_FIELDS = {"enabled", "rollout_percentage", "description"}


# ---------------------------------------------------------------------------
# DB dependency — yields a connection, commits on success, rolls back on error
# ---------------------------------------------------------------------------

def get_db() -> Generator:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "flag-service"}


@app.get("/ready")
def ready():
    # Does its own connection check so it can return 503 if the DB is down
    try:
        conn = get_conn()
        conn.cursor().execute("SELECT 1")
        release_conn(conn)
        return {"status": "ready"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@app.post("/flags", response_model=FlagResponse, status_code=201)
def create_flag(flag: FlagCreate, conn=Depends(get_db)):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO flags (name, description, enabled, rollout_percentage, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        RETURNING id, name, description, enabled, rollout_percentage, created_at, updated_at
        """,
        (flag.name, flag.description, flag.enabled, flag.rollout_percentage),
    )
    row = cur.fetchone()
    _publish_event("FLAG_CREATED", flag.name, flag.enabled)
    return _row_to_flag(row)


@app.get("/flags", response_model=list[FlagResponse])
def list_flags(conn=Depends(get_db)):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, description, enabled, rollout_percentage, created_at, updated_at "
        "FROM flags ORDER BY created_at DESC"
    )
    return [_row_to_flag(row) for row in cur.fetchall()]


@app.get("/flags/{flag_id}", response_model=FlagResponse)
def get_flag(flag_id: int, conn=Depends(get_db)):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, description, enabled, rollout_percentage, created_at, updated_at "
        "FROM flags WHERE id = %s",
        (flag_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")
    return _row_to_flag(row)


@app.patch("/flags/{flag_id}", response_model=FlagResponse)
def update_flag(flag_id: int, updates: FlagUpdate, conn=Depends(get_db)):
    # Only include fields that were explicitly sent (not None)
    fields = {
        k: v
        for k, v in updates.model_dump().items()
        if v is not None and k in _ALLOWED_UPDATE_FIELDS
    }
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields provided")

    # Field names come from the Pydantic model, not user input — safe to interpolate
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [flag_id]

    cur = conn.cursor()
    cur.execute(
        f"UPDATE flags SET {set_clause}, updated_at = NOW() WHERE id = %s "
        "RETURNING id, name, description, enabled, rollout_percentage, created_at, updated_at",
        values,
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")

    _publish_event("FLAG_UPDATED", row[1], row[3])
    return _row_to_flag(row)


@app.delete("/flags/{flag_id}", status_code=204)
def delete_flag(flag_id: int, conn=Depends(get_db)):
    cur = conn.cursor()
    cur.execute("SELECT name FROM flags WHERE id = %s", (flag_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")
    cur.execute("DELETE FROM flags WHERE id = %s", (flag_id,))
    _publish_event("FLAG_DELETED", row[0], False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_flag(row) -> FlagResponse:
    return FlagResponse(
        id=row[0],
        name=row[1],
        description=row[2],
        enabled=row[3],
        rollout_percentage=row[4],
        created_at=row[5],
        updated_at=row[6],
    )


def _publish_event(event_type: str, flag_name: str, enabled: bool):
    topic_arn = os.getenv("SNS_TOPIC_ARN")
    if not topic_arn:
        # SNS not configured — normal for local dev
        return
    try:
        sns = boto3.client("sns", region_name=os.getenv("AWS_REGION", "ap-south-1"))
        sns.publish(
            TopicArn=topic_arn,
            Message=json.dumps({"event": event_type, "flag": flag_name, "enabled": enabled}),
            Subject=f"Feature Flag {event_type}",
        )
    except Exception as e:
        logger.warning("SNS publish failed (flag change still saved): %s", e)
