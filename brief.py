"""CLI entry point. v1: one domain in, one Markdown brief out.

Usage: python brief.py notion.so [--persona "VP Sales"] [--offer "sales AI"]
"""
import argparse
import sys
from datetime import date
from pathlib import Path

from checks import apply_checks
from llm import get_client, research_call, search_count, synthesis_call
from render import render_markdown


def build_brief(client, domain: str, persona: str | None, offer: str | None) -> Path:
    """Run both steps for one domain and write briefs/<domain>.md. Raises on any failure."""
    notes, cited, research_usage = research_call(client, domain, persona, offer)
    if not notes.strip():
        raise RuntimeError("research call returned no notes")
    brief, _ = synthesis_call(client, domain, notes, persona, offer)
    brief = apply_checks(brief, cited, domain)
    out = Path("briefs") / f"{domain}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(render_markdown(brief, domain, date.today().isoformat()))
    print(f"searches: {search_count(research_usage)} | wrote {out}", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="AI account research brief")
    ap.add_argument("domain", help="company domain, e.g. notion.so")
    ap.add_argument("--persona", help="who you sell to, e.g. 'VP Sales'")
    ap.add_argument("--offer", help="what you sell, e.g. 'AI outbound tooling'")
    args = ap.parse_args()
    try:
        build_brief(get_client(), args.domain.lower().strip(), args.persona, args.offer)
    except Exception as e:  # CSV mode (v2) will catch this per domain instead
        sys.exit(f"{args.domain}: failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
