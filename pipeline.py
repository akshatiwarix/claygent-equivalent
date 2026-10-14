"""The whole pipeline as one pure function: domain in, validated brief + stats out.

No files are written here (Vercel's filesystem is read-only). The CLI and the web API both call
run_brief and decide for themselves what to do with the result.
"""
import time
import uuid
from datetime import date
from typing import Callable
from urllib.parse import urlparse

import llm
from checks import apply_checks, source_tier
from repair import repair_brief

Stage = Callable[[str, dict], None]


def add_tiers(result: dict) -> dict:
    """Label each trigger first-/second-/third-party from its URL (idempotent)."""
    for t in result["brief"]["triggers"]:
        t["tier"] = source_tier(t["source_url"], result["domain"])
    return result


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
        orig_idx = [p.trigger_index for p in brief.pains]  # before any trigger is dropped
        brief = apply_checks(brief, cited, domain)
        brief = repair_brief(client, brief, domain, orig_idx, cited)  # auto-fix what the checks find
        return add_tiers({"domain": domain, "generated": date.today().isoformat(), "persona": persona,
                          "offer": offer, "brief": brief.model_dump(), "stats": stats()})
    except Exception as e:
        e.run_stats = stats()
        raise
    finally:
        llm.RUN_KEY.reset(token)


def clean_domain(raw: str) -> str:
    """'https://www.Notion.so/pricing ' -> 'notion.so'"""
    raw = raw.strip().lower()
    host = urlparse(raw if "://" in raw else "//" + raw).hostname or ""
    return host.removeprefix("www.")
