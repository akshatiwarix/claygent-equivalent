"""Auto-repair: fix problems the checks find instead of only reporting them.

1. Triggers whose link is really dead (404/410) are dropped.
2. Triggers whose source text does not support the claim are dropped; ones where it supports only part of
   the claim are trimmed to what the source states (one cheap model call).
3. Pains and openers that rested on a dropped or trimmed trigger are rewritten from the evidence left.
4. Generic openers (stock phrases, or no specific fact) are rewritten with one extra model call.
Every repair is best-effort: if a fix fails, the original stays and a confidence note says so.
"""
import re
import sys

import httpx

import llm
from pydantic import ValidationError

from checks import rebuild_triggers
from models import AccountBrief, Pain, TalkTrack

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


def check_triggers(client, brief: AccountBrief, domain: str,
                   excerpts: dict[str, str]) -> tuple[AccountBrief, set[int]]:
    """A quote can be on the page and the summary still say more than the page does.
    Unsupported triggers are dropped; partly supported ones are trimmed to what the source states.
    Returns (brief, indexes of pains that rested on a trimmed trigger and need rewriting)."""
    if not brief.triggers:
        return brief, set()
    notes = list(brief.confidence_notes)
    try:
        verdict = llm.check_support(client, domain, brief, excerpts)
        if set(verdict) != set(range(len(brief.triggers))):
            raise ValueError("incomplete verdicts")
    except Exception as e:
        print(f"  support check failed for {domain}: {type(e).__name__}", file=sys.stderr)
        notes.append("Automatic check that each quote supports its claim was unavailable for this brief.")
        return brief.model_copy(update={"confidence_notes": notes}), set()
    survivors, trimmed = [], set()
    for i, t in enumerate(brief.triggers):
        kind, short = verdict[i]
        if kind == "unsupported":
            notes.append(f"Auto-fix: dropped trigger, its source text does not support the claim ({t.source_url}): {t.summary}")
        elif kind == "partial" and short:
            notes.append(f"Auto-fix: trimmed a summary to what its source states ({t.source_url}). Removed detail: {t.summary}")
            survivors.append((i, t.model_copy(update={"summary": short})))
            trimmed.add(i)
        else:
            survivors.append((i, t))
    touched = {k for k, p in enumerate(brief.pains) if p.trigger_index in trimmed}
    if len(survivors) == len(brief.triggers):
        return brief.model_copy(update={"triggers": [t for _, t in survivors], "confidence_notes": notes}), touched
    return rebuild_triggers(brief, survivors, notes, domain), touched


def fix_orphans(client, brief: AccountBrief, orig_idx: list[int], domain: str,
                touched: set[int] = frozenset()) -> AccountBrief:
    """Rewrite pains (and their openers) whose trigger was dropped or trimmed, so no opener states
    a claim that failed verification."""
    orphans = [i for i, p in enumerate(brief.pains)
               if (orig_idx[i] >= 0 and p.trigger_index == -1) or i in touched]
    if not orphans:
        return brief
    company = brief.snapshot.company_name
    pains, tracks, notes = list(brief.pains), list(brief.talk_tracks), list(brief.confidence_notes)
    fixed: set[int] = set()
    try:
        for r in llm.rewrite_orphans(client, domain, brief, orphans):
            i = r.pain_index
            if i not in orphans or i in fixed or not -1 <= r.trigger_index < len(brief.triggers):
                continue
            try:
                new_pain = Pain(pain=r.pain, trigger_index=r.trigger_index, reasoning=r.reasoning)
                new_track = TalkTrack(pain_index=i, opener=r.opener)  # enforces under 25 words
            except ValidationError:
                continue
            if opener_flags(new_track.opener, company):
                continue
            old = pains[i].pain
            notes = [n for n in notes if not n.startswith(f"Pain '{old}' was tied")]
            pains[i] = new_pain
            tracks = [new_track if t.pain_index == i else t for t in tracks]
            fixed.add(i)
            if new_pain.trigger_index == -1:
                notes.append(f"Pain {i + 1} rests on the company snapshot, not a recent event.")
    except Exception as e:
        print(f"  orphan repair failed for {domain}: {type(e).__name__}", file=sys.stderr)
    for i in orphans:
        if i not in fixed:
            notes.append(f"Pain {i + 1} and its opener rest on a claim that failed verification "
                         "and could not be replaced automatically. Check them before sending.")
    return brief.model_copy(update={"pains": pains, "talk_tracks": tracks, "confidence_notes": notes})


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


def repair_brief(client, brief: AccountBrief, domain: str, orig_idx: list[int],
                 excerpts: dict[str, str]) -> AccountBrief:
    """orig_idx = each pain's trigger_index as the model wrote it, before any trigger was dropped."""
    brief = drop_dead_links(brief, domain)
    brief, touched = check_triggers(client, brief, domain, excerpts)
    brief = fix_orphans(client, brief, orig_idx, domain, touched)
    return fix_openers(client, brief, domain)
