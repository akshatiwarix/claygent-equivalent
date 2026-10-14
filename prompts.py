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
- Prefer primary sources: the company's own site (blog, newsroom, press releases, changelog, careers page or its Greenhouse/Lever/Ashby job board). Next best: major press (e.g. TechCrunch, Reuters, Bloomberg, WSJ, The Information).
- Avoid job aggregators, scraped company-data sites, and SEO blogs for triggers. Use them only for context, and say so.
- For each event, include a short VERBATIM quote (under 30 words) copied exactly, character for character, from the source text you were shown. Do not paraphrase inside the quote. If you cannot quote it, do not report it as an event.

Output plain-text notes. Every fact MUST end with its date (YYYY-MM-DD if known) and its source URL in brackets, like [2026-09-01, https://example.com/post]. Put each event's quote on the line after it, as: QUOTE: "..."
If you could not find or verify something, list it under a heading UNVERIFIED. Do not guess."""


def synthesis_prompt(notes: str, today: str, cutoff: str,
                     persona: str | None = None, offer: str | None = None) -> str:
    return f"""You turn research notes into a one-page sales account brief. Use ONLY the notes below.
Never invent facts, dates, or URLs. Today is {today}.

Rules:
- triggers: only events dated on or after {cutoff}. Each needs a date (YYYY-MM-DD), a source_url copied exactly from the notes, and an evidence_quote copied exactly from that event's QUOTE line in the notes. Do not edit or paraphrase the quote. Skip events that have no QUOTE.
- Prefer triggers backed by the company's own site or major press over aggregator sites.
- pains: exactly 3. Each references one trigger by its index (0-based) in the triggers list.
- talk_tracks: exactly 3, one per pain (pain_index is 0-based). Each is a cold-email opener under 25 words, specific to this company, no generic flattery.
- confidence_notes: anything missing, undated, conflicting, or unverified.
{_angle(persona, offer)}

Research notes:
{notes}"""
