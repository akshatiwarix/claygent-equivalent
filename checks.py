"""Post-validation guards: stop unverifiable or stale triggers from reaching a sales rep."""
from datetime import date, timedelta

from models import AccountBrief


def apply_checks(brief: AccountBrief, cited_urls: set[str]) -> AccountBrief:
    """Drop triggers with uncited URLs or dates older than 90 days; log each drop in confidence_notes.

    Pains/talk tracks point at triggers by index, so we re-point them after dropping.
    A pain whose trigger was dropped keeps working but loses its trigger link (index -> first survivor).
    """
    cutoff = date.today() - timedelta(days=90)
    keep, notes = [], list(brief.confidence_notes)
    old_to_new: dict[int, int] = {}
    for i, t in enumerate(brief.triggers):
        if date.fromisoformat(t.date) < cutoff:
            notes.append(f"Dropped trigger older than 90 days ({t.date}): {t.summary}")
        elif t.source_url not in cited_urls:
            notes.append(f"Dropped trigger, source URL not returned by search ({t.source_url}): {t.summary}")
        else:
            old_to_new[i] = len(keep)
            keep.append(t)
    for p in brief.pains:
        if p.trigger_index in old_to_new:
            p.trigger_index = old_to_new[p.trigger_index]
        else:
            notes.append(f"Pain '{p.pain}' was tied to a trigger that failed verification.")
            p.trigger_index = 0
    return brief.model_copy(update={"triggers": keep, "confidence_notes": notes})
