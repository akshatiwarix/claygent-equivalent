"""OpenRouter calls: step 1 (research with web search) and step 2 (structured synthesis)."""
import json
import os
import sys
from typing import Literal
from contextvars import ContextVar
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from models import AccountBrief, Pain, TalkTrack, brief_json_schema
from prompts import (orphan_prompt, research_prompt, rewrite_openers_prompt, support_prompt,
                     synthesis_prompt)

load_dotenv()

USAGE_LOG = Path("briefs/usage.log")

# Running totals per domain, filled by log_usage so failed domains still report what they spent.
TALLY: dict[str, dict] = {}
# Set per run by pipeline.run_brief so concurrent runs of the same domain don't share a tally.
RUN_KEY: ContextVar[str | None] = ContextVar("run_key", default=None)

WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"engine": "exa", "max_results": 5, "max_uses": 5},
}


def get_client() -> OpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY missing. Copy .env.example to .env and fill it in.")
    # Timeout so one stuck request can't freeze an unattended batch.
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=240, max_retries=2)


def get_model() -> str:
    model = os.getenv("MODEL")
    if not model:
        sys.exit("MODEL missing in .env (e.g. anthropic/claude-haiku-5.5).")
    return model


def log_usage(step: str, domain: str, usage: dict) -> None:
    """Print the usage object to stderr and append it to briefs/usage.log."""
    t = TALLY.setdefault(RUN_KEY.get() or domain, {"tokens": 0, "cost": 0.0, "searches": 0})
    t["tokens"] += usage.get("total_tokens") or 0
    t["cost"] += usage.get("cost") or 0
    t["searches"] += search_count(usage)
    line = json.dumps({"domain": domain, "step": step, "usage": usage}, default=str)
    print(f"[usage] {line}", file=sys.stderr)
    try:  # best effort: the filesystem is read-only on Vercel
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _usage_dict(resp) -> dict:
    # model_dump keeps OpenRouter's extra fields (cost, server_tool_use) alongside token counts.
    return resp.usage.model_dump() if resp.usage else {}


def search_count(usage: dict) -> int:
    # OpenRouter reports this under server_tool_use_details (older docs say server_tool_use).
    for key in ("server_tool_use_details", "server_tool_use"):
        n = (usage.get(key) or {}).get("web_search_requests")
        if n is not None:
            return n
    return 0


def research_call(client: OpenAI, domain: str, persona: str | None = None,
                  offer: str | None = None) -> tuple[str, dict[str, str], dict]:
    """Step 1. Returns (research notes, {url: search excerpt text}, usage)."""
    today = date.today()
    prompt = research_prompt(domain, today.isoformat(),
                             (today - timedelta(days=90)).isoformat(), persona, offer)
    resp = client.chat.completions.create(
        model=get_model(),
        messages=[{"role": "user", "content": prompt}],
        # The SDK passes unknown tool types through at runtime, so no extra_body needed.
        tools=[WEB_SEARCH_TOOL],
    )
    msg = resp.choices[0].message
    notes = msg.content or ""
    # url_citation annotations are OpenRouter's record of pages the search really returned.
    cited: dict[str, str] = {}
    # The SDK exposes annotations as a typed attribute, not in model_extra.
    for ann in getattr(msg, "annotations", None) or []:
        ann = ann if isinstance(ann, dict) else ann.model_dump()
        uc = ann.get("url_citation") or {}
        if uc.get("url"):
            # Several annotations can share a URL; keep all of their excerpt text.
            cited[uc["url"]] = cited.get(uc["url"], "") + "\n" + (uc.get("content") or "")
    usage = _usage_dict(resp)
    log_usage("research", domain, usage)
    return notes, cited, usage


def synthesis_call(client: OpenAI, domain: str, notes: str, persona: str | None = None,
                   offer: str | None = None) -> tuple[AccountBrief, dict]:
    """Step 2. No tools; strict JSON schema; validated with Pydantic."""
    today = date.today()
    prompt = synthesis_prompt(notes, today.isoformat(),
                              (today - timedelta(days=90)).isoformat(), persona, offer)
    messages = [{"role": "user", "content": prompt}]
    total: dict = {}
    for attempt in (1, 2):
        resp = client.chat.completions.create(
            model=get_model(),
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "account_brief", "strict": True, "schema": brief_json_schema()},
            },
            # Only route to providers that really support response_format.
            extra_body={"provider": {"require_parameters": True}},
        )
        usage = _usage_dict(resp)
        log_usage("synthesis" if attempt == 1 else "synthesis_retry", domain, usage)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            total[k] = total.get(k, 0) + (usage.get(k) or 0)
        content = resp.choices[0].message.content or ""
        try:
            return AccountBrief.model_validate_json(content), total
        except ValidationError as e:
            if attempt == 2:
                raise
            # Strict schema can't enforce word counts etc., so show the model what failed.
            problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"That failed validation: {problems}. Return the corrected JSON."}]


class _Rewrites(BaseModel):
    model_config = ConfigDict(extra="forbid")
    talk_tracks: list[TalkTrack]


def rewrite_openers(client: OpenAI, domain: str, brief: AccountBrief,
                    flagged: dict[int, list[str]]) -> list[TalkTrack]:
    """Auto-fix call: rewrite only the openers a quality check flagged. One attempt; may raise."""
    pains = "\n".join(f"{i}: {p.pain}" for i, p in enumerate(brief.pains))
    triggers = "\n".join(f"- {t.date} {t.summary} (quote: {t.evidence_quote})" for t in brief.triggers)
    bad = "\n".join(f'pain_index {i}: "{brief.talk_tracks[i].opener}" <- {"; ".join(f)}'
                    for i, f in flagged.items())
    resp = client.chat.completions.create(
        model=get_model(),
        messages=[{"role": "user", "content": rewrite_openers_prompt(
            brief.snapshot.company_name, pains, triggers or "(none)", bad)}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "rewrites", "strict": True, "schema": _Rewrites.model_json_schema()}},
        extra_body={"provider": {"require_parameters": True}},
    )
    log_usage("repair", domain, _usage_dict(resp))
    return _Rewrites.model_validate_json(resp.choices[0].message.content or "").talk_tracks


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    verdict: Literal["supported", "partial", "unsupported"]
    trimmed_summary: str


class _Verdicts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[_Verdict]


def _strict_call(client: OpenAI, domain: str, step: str, prompt: str, model_cls):
    resp = client.chat.completions.create(
        model=get_model(),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {
            "name": step, "strict": True, "schema": model_cls.model_json_schema()}},
        extra_body={"provider": {"require_parameters": True}},
    )
    log_usage(step, domain, _usage_dict(resp))
    return model_cls.model_validate_json(resp.choices[0].message.content or "")


def check_support(client: OpenAI, domain: str, brief: AccountBrief,
                  excerpts: dict[str, str]) -> dict[int, tuple[str, str]]:
    """Does each trigger's source text support its summary? One call for all triggers.
    Returns {index: (verdict, trimmed_summary)}."""
    by_url = {u.strip().rstrip("/"): text for u, text in excerpts.items()}
    parts = []
    for i, t in enumerate(brief.triggers):
        text = by_url.get(t.source_url.strip().rstrip("/"), "").strip()[:5000] or "(source text not available)"
        parts.append(f'{i}. Claim: {t.summary}\n   Quote shown to the rep: "{t.evidence_quote}"\n   SOURCE TEXT:\n{text}')
    out = _strict_call(client, domain, "support", support_prompt("\n\n".join(parts)), _Verdicts)
    return {v.index: (v.verdict, v.trimmed_summary.strip()) for v in out.verdicts}


class _Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pain_index: int
    trigger_index: int
    reasoning: str
    pain: str
    opener: str


class _Replacements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacements: list[_Replacement]


def rewrite_orphans(client: OpenAI, domain: str, brief: AccountBrief,
                    orphan_idx: list[int]) -> list[_Replacement]:
    """Rewrite pains (and their openers) whose trigger failed verification."""
    s = brief.snapshot
    snapshot = f"Sells: {s.what_they_sell}\nSells to: {s.who_they_sell_to}\nStage: {s.size_and_stage}"
    triggers = "\n".join(f'{i}. {t.date} {t.summary} (quote: "{t.evidence_quote}")'
                         for i, t in enumerate(brief.triggers)) or "(none)"
    kept = "\n".join(f"{i}: {p.pain}" for i, p in enumerate(brief.pains) if i not in orphan_idx) or "(none)"
    old = "\n".join(f"{i}: {brief.pains[i].pain}" for i in orphan_idx)
    return _strict_call(client, domain, "orphans",
                        orphan_prompt(s.company_name, snapshot, triggers, kept, old),
                        _Replacements).replacements
