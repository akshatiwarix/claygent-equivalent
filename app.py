"""Web API. On Vercel this file is the Python function; static files in public/ come from the CDN.

  GET  /api/examples          saved briefs (free, unlimited)
  GET  /api/examples/{domain} one saved brief
  POST /api/brief             run a live brief, streamed as newline-delimited JSON
"""
import json
import os
import queue
import re
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import limits
import llm
from checks import source_tier
from pipeline import clean_domain, run_brief

app = FastAPI(title="Account Brief")
EXAMPLES = Path(__file__).parent / "data" / "examples"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


class BriefRequest(BaseModel):
    domain: str
    persona: str | None = None
    offer: str | None = None


def tidy(text: str | None) -> str | None:
    """Free-text fields go into a prompt: one line, no control characters, short."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:80] or None


def visitor_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/api/examples")
def list_examples():
    rows = []
    for path in sorted(EXAMPLES.glob("*.json")):
        r = json.loads(path.read_text())
        trig = r["brief"]["triggers"]
        rows.append({"domain": r["domain"], "company": r["brief"]["snapshot"]["company_name"],
                     "generated": r["generated"], "triggers": len(trig),
                     "first_party": sum(source_tier(t["source_url"], r["domain"]) == "first-party" for t in trig),
                     "cost_usd": r["stats"]["cost_usd"]})
    return rows


@app.get("/api/examples/{domain}")
def get_example(domain: str):
    if not DOMAIN_RE.match(domain):
        raise HTTPException(404)
    path = EXAMPLES / f"{domain}.json"
    if not path.exists():
        raise HTTPException(404, "No saved example for that domain")
    return json.loads(path.read_text())


def friendly(e: BaseException) -> str:
    """Never show raw errors to visitors; they can include account details."""
    name = type(e).__name__
    if name in ("AuthenticationError", "PermissionDeniedError", "SystemExit"):
        return "Live runs are not set up correctly right now. Try a saved example."
    if "402" in str(e) or "credit" in str(e).lower():
        return "Live runs are paused because the demo's budget is used up. Try a saved example."
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError"):
        return "The research service is busy. Try again in a minute, or open a saved example."
    return "Could not build a brief for that domain. Check the spelling, or open a saved example."


@app.post("/api/brief")
def create_brief(body: BriefRequest, request: Request):
    domain = clean_domain(body.domain)
    if not DOMAIN_RE.match(domain):
        raise HTTPException(422, "Enter a company domain like notion.so")
    persona, offer = tidy(body.persona), tidy(body.offer)
    vid = visitor_id(request)

    try:
        denied = limits.claim(vid)
    except limits.LimitUnavailable as e:
        print(f"limits unavailable: {e}", file=sys.stderr)
        return JSONResponse({"error": "Live runs are switched off right now. Open a saved example."}, 503)
    if denied == "visitor":
        return JSONResponse({"error": "You've used today's live run. Saved examples are unlimited, or come back tomorrow."}, 429)
    if denied == "global":
        return JSONResponse({"error": "Today's live runs for everyone are used up. Open a saved example, or come back tomorrow."}, 429)

    events: queue.Queue = queue.Queue()

    def work() -> None:
        try:
            client = llm.get_client()
            result = run_brief(client, domain, persona, offer,
                               on_stage=lambda stage, info: events.put({"type": "stage", "stage": stage, **info}))
            events.put({"type": "result", **result})
        except BaseException as e:  # SystemExit comes from get_client when the key is missing
            print(f"brief failed for {domain}: {type(e).__name__}: {e}", file=sys.stderr)
            if getattr(e, "run_stats", {}).get("cost_usd", 0) == 0:
                limits.refund(vid)  # nothing was spent, so the visitor keeps their run
            events.put({"type": "error", "message": friendly(e)})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                item = events.get(timeout=10)
            except queue.Empty:
                yield '{"type":"ping"}\n'  # keeps the connection open through quiet stretches
                continue
            if item is None:
                return
            yield json.dumps(item) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


if not os.getenv("VERCEL"):  # on Vercel, public/ is served by the CDN
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory="public", html=True), name="public")
