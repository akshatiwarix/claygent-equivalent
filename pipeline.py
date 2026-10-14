"""The whole pipeline as one pure function: domain in, validated brief + stats out.

No files are written here (Vercel's filesystem is read-only). The CLI and the web API both call
run_brief and decide for themselves what to do with the result.
"""
import time
import uuid
from datetime import date
from typing import Callable

import llm
from checks import apply_checks
from repair import repair_brief

Stage = Callable[[str, dict], None]


def run_brief(client, domain: str, persona: str | None = None, offer: str | None = None,
              on_stage: Stage | None = None) -> dict:
    """Returns {domain, generated, persona, offer, brief, stats}. On failure the raised exception
    carries run_stats (tokens, searches, cost_usd, seconds) so callers can still count the spend."""
    emit = on_stage or (lambda *_: None)
    key = f"{domain}#{uuid.uuid4().hex[:8]}"
    token = llm.RUN_KEY.set(key)
    started = time.monotonic()

    def stats() -> dict:
        t = llm.TALLY.pop(key, None) or {"tokens": 0, "searches": 0, "cost": 0.0}
        return {"tokens": t["tokens"], "searches": t["searches"],
                "cost_usd": round(t["cost"], 4), "seconds": round(time.monotonic() - started)}

    try:
        emit("research", {})
        notes, cited, research_usage = llm.research_call(client, domain, persona, offer)
        if not notes.strip():
            raise RuntimeError("research call returned no notes")
        emit("write", {"searches": llm.search_count(research_usage), "sources": len(cited)})
        brief, _ = llm.synthesis_call(client, domain, notes, persona, offer)
        emit("check", {})
        brief = apply_checks(brief, cited, domain)
        brief = repair_brief(client, brief, domain)  # auto-fix dead links and generic openers
        return {"domain": domain, "generated": date.today().isoformat(), "persona": persona,
                "offer": offer, "brief": brief.model_dump(), "stats": stats()}
    except Exception as e:
        e.run_stats = stats()
        raise
    finally:
        llm.RUN_KEY.reset(token)
