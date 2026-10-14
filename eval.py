"""Eval harness: automated pre-screen of briefs + a review sheet for the human hand-check.

  python eval.py check evals/before   -> writes evals/before/review.md and auto.json
  (you tick boxes in review.md: a ticked box = a problem)
  python eval.py tally evals/before   -> counts problems, adds cost and seconds from summary.csv

Three things are counted: wrong facts, dead links, generic openers.
Machines only pre-screen. The final numbers come from your ticks.
"""
import csv
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx

from checks import quote_in_source
from repair import opener_flags

UA = {"User-Agent": "Mozilla/5.0 (compatible; brief-eval/1.0)"}
BLOCKED = {401, 403, 429, 999}  # site refuses bots; not proof the link is dead

TRIGGER_RE = re.compile(r"^\d+\. \*\*(.+?)\*\* \((\d{4}-\d{2}-\d{2})\): (.*) \[source: (.+?)\]\((.+?)\)$")
QUOTE_RE = re.compile(r'^\s+> "(.*)"$')
TRACK_RE = re.compile(r'^- _Pain (\d+):_ "(.*)"$')


def parse_brief(path: Path) -> dict:
    """Read back the Markdown our renderer wrote."""
    b = {"domain": path.stem, "snapshot": {}, "triggers": [], "openers": [], "company": ""}
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            b["company"] = line[2:].split(" (")[0]
        elif m := re.match(r"^- \*\*(What they sell|To whom|Size / stage):\*\* (.*)$", line):
            b["snapshot"][m[1]] = m[2]
        elif m := TRIGGER_RE.match(line):
            b["triggers"].append({"type": m[1], "date": m[2], "summary": m[3], "tier": m[4], "url": m[5], "quote": ""})
        elif (m := QUOTE_RE.match(line)) and b["triggers"]:
            b["triggers"][-1]["quote"] = m[1]
        elif m := TRACK_RE.match(line):
            b["openers"].append(m[2])
    return b


def date_variants(iso: str) -> list[str]:
    d = date.fromisoformat(iso)
    return [iso, f"{d:%B} {d.day}, {d.year}", f"{d:%b} {d.day}, {d.year}",
            f"{d.day} {d:%B} {d.year}", f"{d.day} {d:%b} {d.year}"]


def check_link(t: dict) -> dict:
    """Fetch the cited page. Returns link status plus whether quote/date appear on it."""
    try:
        r = httpx.get(t["url"], headers=UA, timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        return {"link": "dead", "detail": type(e).__name__, "quote_on_page": None, "date_on_page": None}
    if r.status_code in BLOCKED:
        return {"link": "blocked", "detail": str(r.status_code), "quote_on_page": None, "date_on_page": None}
    if r.status_code >= 400:
        return {"link": "dead", "detail": str(r.status_code), "quote_on_page": None, "date_on_page": None}
    text = re.sub(r"(?s)<(script|style).*?</\1>", " ", r.text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return {"link": "ok", "detail": str(r.status_code),
            "quote_on_page": quote_in_source(t["quote"], text),
            "date_on_page": any(v.lower() in text.lower() for v in date_variants(t["date"]))}


def cmd_check(folder: Path) -> None:
    briefs = [parse_brief(p) for p in sorted(folder.glob("*.md")) if p.name != "review.md"]
    auto, out = {}, [
        "# Hand-check sheet",
        "Tick a box `[x]` when there is a PROBLEM. Unticked = fine. Open each link and compare to the claim.",
        "Tags: {fact} wrong or unsupported claim, {link} dead link, {opener} generic opener.\n"]
    for b in briefs:
        out += [f"## {b['domain']}", "", "**Snapshot** (check each against the company's own site / Crunchbase):"]
        for k, v in b["snapshot"].items():
            out.append(f"- [ ] {{fact}} {k}: {v}")
        out += ["", "**Triggers:**"]
        auto[b["domain"]] = {"links": [], "openers": []}
        for t in b["triggers"]:
            res = check_link(t)
            auto[b["domain"]]["links"].append({**res, "url": t["url"]})
            dead = "x" if res["link"] == "dead" else " "
            notes = f"link={res['link']} ({res['detail']})"
            if res["link"] == "ok":
                notes += f", quote_on_page={res['quote_on_page']}, date_on_page={res['date_on_page']}"
            out += [f"- [ ] {{fact}} ({t['tier']}, {t['date']}) {t['summary']}",
                    f"  - [{dead}] {{link}} {t['url']}  ← auto: {notes}"]
        out += ["", "**Openers:**"]
        for o in b["openers"]:
            flags = opener_flags(o, b["company"])
            auto[b["domain"]]["openers"].append({"opener": o, "flags": flags})
            out.append(f"- [ ] {{opener}} \"{o}\"" + (f"  ← auto-flag: {'; '.join(flags)}" if flags else ""))
        out.append("")
        print(f"checked {b['domain']}", file=sys.stderr)
    (folder / "review.md").write_text("\n".join(out))
    (folder / "auto.json").write_text(json.dumps(auto, indent=2))
    print(f"wrote {folder / 'review.md'}. Tick the boxes, then: python eval.py tally {folder}")


def cmd_tally(folder: Path) -> None:
    lines = (folder / "review.md").read_text().splitlines()
    counts = {t: [0, 0] for t in ("fact", "link", "opener")}  # [problems, checked]
    for line in lines:
        m = re.match(r"^\s*- \[([ x])\] \{(fact|link|opener)\}", line)
        if m:
            counts[m[2]][1] += 1
            counts[m[2]][0] += m[1] == "x"
    n = sum(1 for line in lines if line.startswith("## "))
    print(f"briefs checked: {n}")
    for tag, label in (("fact", "wrong facts"), ("link", "dead links"), ("opener", "generic openers")):
        bad, total = counts[tag]
        print(f"{label}: {bad}/{total} ({bad / total:.0%})" if total else f"{label}: n/a")
    summary = folder / "summary.csv"
    if summary.exists():
        rows = [r for r in csv.DictReader(summary.open()) if r["status"].startswith("ok")]
        print(f"cost per brief: ${sum(float(r['cost_usd']) for r in rows) / len(rows):.4f} (n={len(rows)})")
        if rows and "seconds" in rows[0]:
            print(f"seconds per brief: {sum(float(r['seconds']) for r in rows) / len(rows):.0f}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("check", "tally"):
        sys.exit(__doc__)
    {"check": cmd_check, "tally": cmd_tally}[sys.argv[1]](Path(sys.argv[2]))
