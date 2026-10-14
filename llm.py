"""OpenRouter calls: step 1 (research with web search) and step 2 (structured synthesis)."""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from models import AccountBrief, brief_json_schema
from prompts import research_prompt, synthesis_prompt

load_dotenv()

USAGE_LOG = Path("briefs/usage.log")

WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"engine": "exa", "max_results": 5, "max_uses": 5},
}


def get_client() -> OpenAI:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY missing. Copy .env.example to .env and fill it in.")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def get_model() -> str:
    model = os.getenv("MODEL")
    if not model:
        sys.exit("MODEL missing in .env (e.g. anthropic/claude-haiku-5.5).")
    return model


def log_usage(step: str, domain: str, usage: dict) -> None:
    """Print the usage object to stderr and append it to briefs/usage.log."""
    line = json.dumps({"domain": domain, "step": step, "usage": usage}, default=str)
    print(f"[usage] {line}", file=sys.stderr)
    USAGE_LOG.parent.mkdir(exist_ok=True)
    with USAGE_LOG.open("a") as f:
        f.write(line + "\n")


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
                  offer: str | None = None) -> tuple[str, set[str], dict]:
    """Step 1. Returns (research notes, set of URLs the search tool actually returned, usage)."""
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
    cited = set()
    # The SDK exposes annotations as a typed attribute, not in model_extra.
    for ann in getattr(msg, "annotations", None) or []:
        ann = ann if isinstance(ann, dict) else ann.model_dump()
        url = (ann.get("url_citation") or {}).get("url")
        if url:
            cited.add(url)
    usage = _usage_dict(resp)
    log_usage("research", domain, usage)
    return notes, cited, usage


def synthesis_call(client: OpenAI, domain: str, notes: str, persona: str | None = None,
                   offer: str | None = None) -> tuple[AccountBrief, dict]:
    """Step 2. No tools; strict JSON schema; validated with Pydantic."""
    today = date.today()
    prompt = synthesis_prompt(notes, today.isoformat(),
                              (today - timedelta(days=90)).isoformat(), persona, offer)
    resp = client.chat.completions.create(
        model=get_model(),
        messages=[{"role": "user", "content": prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "account_brief", "strict": True, "schema": brief_json_schema()},
        },
        # Only route to providers that really support response_format.
        extra_body={"provider": {"require_parameters": True}},
    )
    usage = _usage_dict(resp)
    log_usage("synthesis", domain, usage)
    brief = AccountBrief.model_validate_json(resp.choices[0].message.content or "")
    return brief, usage
