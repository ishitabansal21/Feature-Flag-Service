from __future__ import annotations

import hashlib
import json
import logging
import os

import psycopg2
import redis as redis_lib
from fastapi import FastAPI

app = FastAPI(title="Evaluation Service")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

redis_client = redis_lib.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
)

_CACHE_TTL_SECONDS = 60


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "evaluation-service"}


# ---------------------------------------------------------------------------
# Evaluation endpoint
# ---------------------------------------------------------------------------

@app.get("/evaluate/{flag_name}")
def evaluate(flag_name: str, user_id: str):
    """
    Returns whether flag_name is ON for the given user_id.

    Flow:
      1. Check Redis cache (fast path — must be under 5ms)
      2. On cache miss, read from Postgres and cache the result
      3. Apply rollout_percentage: same user always gets the same result (deterministic hash)
    """
    flag_data = _get_cached(flag_name) or _get_from_db(flag_name)

    if not flag_data:
        return {"flag": flag_name, "enabled": False, "user_id": user_id, "reason": "flag not found"}

    if not flag_data["enabled"]:
        return {"flag": flag_name, "enabled": False, "user_id": user_id}

    # Partial rollout: hash(flag+user) gives a stable 0-99 bucket per user
    if flag_data["rollout_percentage"] < 100:
        bucket = int(hashlib.md5(f"{flag_name}{user_id}".encode()).hexdigest(), 16) % 100
        if bucket >= flag_data["rollout_percentage"]:
            return {
                "flag": flag_name,
                "enabled": False,
                "user_id": user_id,
                "reason": "not in rollout",
            }

    return {"flag": flag_name, "enabled": True, "user_id": user_id}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _get_cached(flag_name: str) -> dict | None:
    try:
        raw = redis_client.get(f"flag:{flag_name}")
        if raw:
            logger.info("CACHE HIT  — flag '%s' served from Redis", flag_name)
            return json.loads(raw)
        logger.info("CACHE MISS — flag '%s' not in Redis, going to DB", flag_name)
        return None
    except Exception as e:
        logger.warning("Redis read failed, falling back to DB: %s", e)
        return None


def _cache(flag_name: str, flag_data: dict):
    try:
        redis_client.setex(f"flag:{flag_name}", _CACHE_TTL_SECONDS, json.dumps(flag_data))
    except Exception as e:
        logger.warning("Redis write failed (evaluation still works): %s", e)


# ---------------------------------------------------------------------------
# DB helper (cold path — only hit on cache miss)
# ---------------------------------------------------------------------------

def _get_from_db(flag_name: str) -> dict | None:
    conn = None
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name, enabled, rollout_percentage FROM flags WHERE name = %s",
            (flag_name,),
        )
        row = cur.fetchone()
        if not row:
            return None
        flag_data = {"name": row[0], "enabled": row[1], "rollout_percentage": row[2]}
        _cache(flag_name, flag_data)
        return flag_data
    except Exception as e:
        logger.error("DB read failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()
