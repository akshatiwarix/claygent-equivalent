"""Post-validation guards: stop unverifiable or stale triggers from reaching a sales rep.

A trigger survives only if: it is within 90 days, its URL was returned by the search tool,
and its evidence_quote really appears in the text the search tool returned for that URL.
Survivors are labeled with a source tier (computed here from the URL, not self-reported
by the model) and sorted best source first.
"""
import re
from datetime import date, timedelta
from urllib.parse import urlparse

from models import AccountBrief

TIER_RANK = {"first-party": 0, "second-party": 1, "third-party": 2}

ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
             "workdayjobs.com", "bamboohr.com", "recruitee.com")
# Company-authored content hosted on someone else's platform. Press wires carry the company's
# own releases; independent press (TechCrunch, Reuters...) is third-party.
SECOND_PARTY_HOSTS = ("businesswire.com", "prnewswire.com", "globenewswire.com", "linkedin.com")


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _host_is(host: str, names: tuple[str, ...]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def source_tier(url: str, company_domain: str) -> str:
    host = _host(url)
    parts = host.split(".")
    label = company_domain.lower().removeprefix("www.").split(".")[0]  # notion.so -> notion
    # notion.com, developers.notion.com, notion.co.uk all count as the company's own site.
    own = len(parts) >= 2 and (
        parts[-2] == label
        or (len(parts) >= 3 and parts[-3] == label and parts[-2] in ("co", "com", "org", "net")))
    if own:
        return "first-party"
    # A company's job board on an ATS, or its release on a wire / LinkedIn page.
    if (_host_is(host, ATS_HOSTS) and label in url.lower()) or \
            (_host_is(host, SECOND_PARTY_HOSTS) and label in url.lower()):
        return "second-party"
    return "third-party"


def _clean(text: str) -> str:
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    text = re.sub(r"[*_`#>\[\]]", "", text)  # markdown noise
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_source(quote: str, excerpt: str) -> bool:
    """True if every chunk of the quote appears in the excerpt. '...' in a quote marks a gap."""
    hay = _clean(excerpt)
    chunks = [c for c in re.split(r"\.\.\.|…", _clean(quote)) if len(c.strip()) >= 15]
    return bool(chunks) and all(c.strip(" .\"'") in hay for c in chunks)


def apply_checks(brief: AccountBrief, excerpts: dict[str, str], domain: str) -> AccountBrief:
    """Drop failing triggers (noting each in confidence_notes), sort survivors by source tier.

    Pains point at triggers by index, so we re-point them after dropping and sorting.
    A pain whose trigger was dropped gets trigger_index -1 and is noted.
    """
    cutoff = date.today() - timedelta(days=90)
    by_url = {_norm_url(u): text for u, text in excerpts.items()}
    survivors, notes = [], list(brief.confidence_notes)
    for i, t in enumerate(brief.triggers):
        excerpt = by_url.get(_norm_url(t.source_url))
        if date.fromisoformat(t.date) < cutoff:
            notes.append(f"Dropped trigger older than 90 days ({t.date}): {t.summary}")
        elif excerpt is None:
            notes.append(f"Dropped trigger, source URL not returned by search ({t.source_url}): {t.summary}")
        elif not quote_in_source(t.evidence_quote, excerpt):
            notes.append(f"Dropped trigger, quote not found in source text ({t.source_url}): {t.summary}")
        else:
            survivors.append((i, t))
    survivors.sort(key=lambda it: TIER_RANK[source_tier(it[1].source_url, domain)])  # stable
    old_to_new = {old: new for new, (old, _) in enumerate(survivors)}
    for p in brief.pains:
        if p.trigger_index in old_to_new:
            p.trigger_index = old_to_new[p.trigger_index]
        elif p.trigger_index != -1:
            notes.append(f"Pain '{p.pain}' was tied to a trigger that failed verification.")
            p.trigger_index = -1  # renderer shows no trigger link
    return brief.model_copy(update={"triggers": [t for _, t in survivors], "confidence_notes": notes})
