"""Auto-repair: fix problems the checks find instead of only reporting them.

1. Triggers whose link is really dead (404/410) are dropped.
2. Generic openers (stock phrases, or no specific fact) are rewritten with one extra model call.
   If the rewrite is still flagged or invalid, the original stays and a confidence note says so.
"""
import re
import sys

import httpx

import llm
from checks import rebuild_triggers
from models import AccountBrief

STOCK = ("hope this finds you", "quick question", "i noticed", "i came across", "reaching out",
         "i wanted to", "touching base", "congrats on", "congratulations on")
DEAD_STATUS = {404, 410}  # only these prove a link is gone; 403/429/timeouts just mean "can't tell"
UA = {"User-Agent": "Mozilla/5.0 (compatible; brief-eval/1.0)"}


def opener_flags(opener: str, company: str) -> list[str]:
    """Specific = names a number/version or a mid-sentence proper noun beyond the company name."""
    flags = [f"stock phrase '{p}'" for p in STOCK if p in opener.lower()]
    words = opener.split()
    company_words = {w.lower() for w in company.split()}
    specific = any(
        re.sub(r"\W", "", w).lower() not in company_words and
        (any(c.isdigit() for c in w) or (i > 0 and w[:1].isupper() and not words[i - 1].endswith((".", "?", "!"))))
        for i, w in enumerate(words))
    if not specific:
        flags.append("names no specific fact (no number or proper noun)")
    return flags


def link_is_dead(url: str) -> bool:
    try:
        return httpx.get(url, headers=UA, timeout=15, follow_redirects=True).status_code in DEAD_STATUS
    except httpx.HTTPError:
        return False  # network hiccup is not proof the page is gone


def drop_dead_links(brief: AccountBrief, domain: str) -> AccountBrief:
    notes, survivors = list(brief.confidence_notes), []
    for i, t in enumerate(brief.triggers):
        if link_is_dead(t.source_url):
            notes.append(f"Auto-fix: dropped trigger, source link is dead ({t.source_url}): {t.summary}")
        else:
            survivors.append((i, t))
    if len(survivors) == len(brief.triggers):
        return brief
    return rebuild_triggers(brief, survivors, notes, domain)


def fix_openers(client, brief: AccountBrief, domain: str) -> AccountBrief:
    company = brief.snapshot.company_name
    flagged = {i: f for i, t in enumerate(brief.talk_tracks) if (f := opener_flags(t.opener, company))}
    if not flagged:
        return brief
    notes = list(brief.confidence_notes)
    tracks = list(brief.talk_tracks)
    try:
        for new in llm.rewrite_openers(client, domain, brief, flagged):
            if new.pain_index in flagged and not opener_flags(new.opener, company):
                i = next(k for k, t in enumerate(tracks) if t.pain_index == new.pain_index)
                tracks[i] = new
                flagged.pop(new.pain_index)
        fixed = True
    except Exception as e:  # repair is best-effort; never fail the brief
        print(f"  opener repair failed for {domain}: {type(e).__name__}", file=sys.stderr)
        fixed = False
    for i in flagged:  # whatever is still flagged stays, but the rep is told
        notes.append(f"Opener for pain {i + 1} looks generic ({'; '.join(flagged[i])})"
                     + ("; auto-rewrite did not fix it." if fixed else "; auto-rewrite failed."))
    return brief.model_copy(update={"talk_tracks": tracks, "confidence_notes": notes})


def repair_brief(client, brief: AccountBrief, domain: str) -> AccountBrief:
    return fix_openers(client, drop_dead_links(brief, domain), domain)
