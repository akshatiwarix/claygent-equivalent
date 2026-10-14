"""Spend protection for the public demo: N live briefs per visitor per day plus a global daily cap.

Counters live in Redis (Upstash, added from the Vercel Marketplace) so they hold across serverless
instances. If Redis is missing or errors on Vercel, live runs are refused: the safe failure is
"no spend". The in-memory fallback only exists for local development.
"""
import hashlib
import os
from datetime import datetime, timezone

import httpx

PER_VISITOR = int(os.getenv("LIVE_RUNS_PER_VISITOR", "1"))
GLOBAL_CAP = int(os.getenv("LIVE_RUNS_PER_DAY", "25"))
SALT = os.getenv("VISITOR_SALT", "dev-salt")
DAY_SECONDS = 90_000  # a little over 24h so keys outlive their UTC day

_memory: dict[str, int] = {}


class LimitUnavailable(Exception):
    """Counters can't be reached, so live runs are turned off."""


def _redis_conf() -> tuple[str, str] | None:
    url = os.getenv("KV_REST_API_URL") or os.getenv("UPSTASH_REDIS_REST_URL")
    token = os.getenv("KV_REST_API_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN")
    return (url, token) if url and token else None


def _cmd(*args):
    conf = _redis_conf()
    if conf is None:
        if os.getenv("VERCEL"):
            raise LimitUnavailable("Redis is not configured")
        return None  # local dev: use memory
    try:
        r = httpx.post(conf[0], headers={"Authorization": f"Bearer {conf[1]}"}, json=list(args), timeout=5)
        r.raise_for_status()
        return r.json()["result"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise LimitUnavailable(str(e)) from e


def _incr(key: str) -> int:
    n = _cmd("INCR", key)
    if n is None:
        _memory[key] = _memory.get(key, 0) + 1
        return _memory[key]
    if n == 1:
        _cmd("EXPIRE", key, DAY_SECONDS)
    return n


def _decr(key: str) -> None:
    if _cmd("DECR", key) is None:
        _memory[key] = max(0, _memory.get(key, 0) - 1)


def _keys(visitor_id: str) -> tuple[str, str]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    who = hashlib.sha256(f"{SALT}:{visitor_id}".encode()).hexdigest()[:24]
    return f"brief:v:{day}:{who}", f"brief:g:{day}"


def claim(visitor_id: str) -> str | None:
    """Reserve one live run. Returns None if allowed, else 'visitor' or 'global'. May raise LimitUnavailable."""
    vkey, gkey = _keys(visitor_id)
    if _incr(vkey) > PER_VISITOR:
        return "visitor"  # leave the count as is: they are over the limit either way
    if _incr(gkey) > GLOBAL_CAP:
        _decr(vkey)  # not their fault, give the visitor's run back
        return "global"
    return None


def refund(visitor_id: str) -> None:
    """Give a run back when a failure cost nothing."""
    vkey, gkey = _keys(visitor_id)
    try:
        _decr(vkey)
        _decr(gkey)
    except LimitUnavailable:
        pass
