"""The two prompts. Kept separate from code so you can tune wording without touching logic."""


def _angle(persona: str | None, offer: str | None) -> str:
    if not (persona or offer):
        return ""
    parts = []
    if persona:
        parts.append(f"The reader is selling to this persona: {persona}.")
    if offer:
        parts.append(f"The reader's offer: {offer}.")
    return " ".join(parts) + " Prioritize signals relevant to that."


def research_prompt(domain: str, today: str, cutoff: str,
                    persona: str | None = None, offer: str | None = None) -> str:
    return f"""You are a B2B account researcher. Today is {today}.
Research the company at {domain} using web search. Only report facts you found in search results.

Find:
1. What they sell, to whom, approximate headcount, and funding stage / total raised.
2. Events since {cutoff} (last 90 days): funding rounds, product launches, leadership hires, and hiring trends (which roles/teams they are hiring for).
{_angle(persona, offer)}

Source rules (a sales rep must be able to trust every link):
- Prefer primary sources: the company's own site (blog, newsroom, press releases, changelog, careers page), then its job board or press wire, then major press. Job boards, aggregators and data sites are acceptable when nothing better exists; the brief labels each source, so report them rather than skipping the event.
- For each event, include a short VERBATIM quote (under 30 words) copied exactly, character for character, from the source text you were shown. Do not paraphrase inside the quote. If you cannot quote it, do not report it as an event.

Output plain-text notes. Every fact MUST end with its date (YYYY-MM-DD if known) and its source URL in brackets, like [2026-09-01, https://example.com/post]. Put each event's quote on the line after it, as: QUOTE: "..."
If you could not find or verify something, list it under a heading UNVERIFIED. Do not guess."""


def synthesis_prompt(notes: str, today: str, cutoff: str,
                     persona: str | None = None, offer: str | None = None) -> str:
    return f"""You turn research notes into a one-page sales account brief. Use ONLY the notes below.
Never invent facts, dates, or URLs. Today is {today}.

Rules:
- triggers: only events dated on or after {cutoff}. Each needs a date (YYYY-MM-DD), a source_url copied exactly from the notes, and an evidence_quote copied exactly from that event's QUOTE line in the notes. Do not edit or paraphrase the quote. Skip events that have no QUOTE.
- Include every verifiable and relevant trigger, including hiring trends from job boards. Prefer a company-owned source when two sources cover the same event.
- Relevance: keep a trigger only if it could change what a seller says to this company. If your own summary of an event would say "not sales", "indirect", or "low relevance", leave it out of triggers and explain it in confidence_notes instead.
- pains: exactly 3. Each references one trigger by its index (0-based) in the triggers list. A pain must follow directly from its trigger: do not use a trigger whose link to the pain you would call indirect. If fewer than 3 strong triggers exist, tie the extra pains to the company snapshot instead and use -1 (say so in confidence_notes).
- talk_tracks: exactly 3, one per pain (pain_index is 0-based). Each is a cold-email opener under 25 words, specific to this company, no generic flattery.
- confidence_notes: anything missing, undated, conflicting, or unverified.
{_angle(persona, offer)}

Research notes:
{notes}"""


def rewrite_openers_prompt(company: str, pains: str, triggers: str, flagged: str) -> str:
    return f"""These cold-email openers for {company} failed a quality check. Rewrite only the ones listed.

Rules for each rewrite:
- Under 25 words, and it must keep the same pain_index.
- Name one specific fact from the triggers below (a product, version, number, or named person). Do not invent facts.
- No stock phrases ("hope this finds you", "quick question", "I noticed", "congrats on").

Pains:
{pains}

Triggers:
{triggers}

Openers to rewrite, with what was wrong:
{flagged}"""


def support_prompt(items: str) -> str:
    return f"""For each numbered claim, decide whether the quote directly supports the claim's main fact.
Answer supported=false if the quote is about a different topic, only loosely related, or does not contain the key fact (a number, name, product, or event) the claim states.
Be strict: a sales rep will repeat the claim to the prospect.

{items}"""


def orphan_prompt(company: str, snapshot: str, triggers: str, kept: str, orphans: str) -> str:
    return f"""Some likely pains for {company} lost their supporting evidence when their trigger failed verification.
Write a replacement for each pain listed under "Pains to replace". Use ONLY the verified triggers and snapshot below. Do not invent facts.

For each replacement:
- pain_index: the same index as the pain it replaces.
- trigger_index: the index of the one verified trigger that supports it, or -1 if none fits (then it must rest on the snapshot).
- reasoning: one or two sentences tying the pain to that evidence. Say plainly if the link is an inference.
- opener: a cold-email opener under 25 words that names one specific fact from that trigger or the snapshot. No stock phrases ("hope this finds you", "quick question", "I noticed").
- The replacement must be different in topic from the pains that are kept.

Snapshot:
{snapshot}

Verified triggers:
{triggers}

Pains that are kept:
{kept}

Pains to replace:
{orphans}"""
