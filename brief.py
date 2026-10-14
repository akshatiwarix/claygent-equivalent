"""CLI entry point.

Single domain:  python brief.py notion.so [--persona "VP Sales"] [--offer "sales AI"]
Batch from CSV: python brief.py --csv accounts.csv   (column: domain)
"""
import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import llm
from models import AccountBrief
from pipeline import run_brief
from render import render_markdown

SUMMARY_FIELDS = ["domain", "status", "top_trigger", "tokens", "searches", "cost_usd", "seconds"]


def build_brief(client, domain: str, persona: str | None, offer: str | None) -> dict:
    """Run the pipeline for one domain, write briefs/<domain>.md and .json, return the result.

    Raises on any failure; the caller decides whether that stops the run.
    """
    result = run_brief(client, domain, persona, offer)
    brief = AccountBrief.model_validate(result["brief"])
    out = Path("briefs")
    out.mkdir(exist_ok=True)
    (out / f"{domain}.md").write_text(render_markdown(brief, domain, result["generated"]))
    (out / f"{domain}.json").write_text(json.dumps(result, indent=2))
    print(f"wrote {out / domain}.md", file=sys.stderr)
    return result


def clean_domain(raw: str) -> str:
    """'https://www.Notion.so/pricing ' -> 'notion.so'"""
    raw = raw.strip().lower()
    host = urlparse(raw if "://" in raw else "//" + raw).hostname or ""
    return host.removeprefix("www.")


def read_domains(path: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "domain" not in [h.strip().lower() for h in reader.fieldnames]:
            sys.exit(f"{path}: needs a 'domain' column (found: {reader.fieldnames})")
        col = next(h for h in reader.fieldnames if h.strip().lower() == "domain")
        domains = [clean_domain(row[col] or "") for row in reader]
    # drop blanks, keep first occurrence of duplicates, keep order
    return list(dict.fromkeys(d for d in domains if d))


def run_batch(client, domains: list[str], persona: str | None, offer: str | None,
              summary_path: Path) -> None:
    summary_path.parent.mkdir(exist_ok=True)
    ok = failed = 0
    total_cost = 0.0
    with summary_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for i, domain in enumerate(domains, 1):
            print(f"[{i}/{len(domains)}] {domain}", file=sys.stderr)
            top, status = "", "ok"
            stats = {"tokens": 0, "searches": 0, "cost_usd": 0.0, "seconds": 0}
            try:
                result = build_brief(client, domain, persona, offer)
                stats = result["stats"]
                triggers = result["brief"]["triggers"]
                if triggers:  # sorted best source first
                    t = triggers[0]
                    top = f"{t['type'].replace('_', ' ')}: {t['summary']} ({t['date']})"
                else:
                    status = "no_triggers"  # brief written, but nothing verified: don't send as-is
                ok += 1
            except Exception as e:  # skip this domain, keep the batch going
                status = f"error: {type(e).__name__}: {str(e)[:150]}".replace("\n", " ")
                stats = getattr(e, "run_stats", stats)  # failed domains still show what they spent
                print(f"  skipped {domain}: {status}", file=sys.stderr)
                failed += 1
            total_cost += stats["cost_usd"]
            writer.writerow({"domain": domain, "status": status, "top_trigger": top,
                             "tokens": stats["tokens"], "searches": stats["searches"],
                             "cost_usd": stats["cost_usd"], "seconds": stats["seconds"]})
            f.flush()  # keep progress if the run is killed
    print(f"done: {ok} ok, {failed} failed, total cost ${total_cost:.4f}. Summary: {summary_path}",
          file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="AI account research brief")
    ap.add_argument("domain", nargs="?", help="company domain, e.g. notion.so")
    ap.add_argument("--csv", help="CSV file with a 'domain' column; writes one brief per row")
    ap.add_argument("--persona", help="who you sell to, e.g. 'VP Sales'")
    ap.add_argument("--offer", help="what you sell, e.g. 'AI outbound tooling'")
    args = ap.parse_args()
    if bool(args.domain) == bool(args.csv):
        ap.error("give either a domain or --csv FILE")

    client = llm.get_client()
    if args.csv:
        run_batch(client, read_domains(args.csv), args.persona, args.offer,
                  Path("briefs/summary.csv"))
        return
    domain = clean_domain(args.domain)
    try:
        build_brief(client, domain, args.persona, args.offer)
    except Exception as e:
        sys.exit(f"{domain}: failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
