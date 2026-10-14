"""Turn a validated AccountBrief into a one-page Markdown file."""
from checks import source_tier
from models import AccountBrief


def render_markdown(brief: AccountBrief, domain: str, today: str) -> str:
    s = brief.snapshot
    lines = [
        f"# {s.company_name} ({domain}): Account Brief",
        f"_Generated {today}_",
        "",
        "## Snapshot",
        f"- **What they sell:** {s.what_they_sell}",
        f"- **To whom:** {s.who_they_sell_to}",
        f"- **Size / stage:** {s.size_and_stage}",
        "",
        "## Recent triggers (last 90 days)",
    ]
    if brief.triggers:
        for i, t in enumerate(brief.triggers):
            tier = source_tier(t.source_url, domain)
            lines.append(f"{i + 1}. **{t.type.replace('_', ' ').title()}** ({t.date}): {t.summary} [source: {tier}]({t.source_url})")
            lines.append(f"   > \"{t.evidence_quote}\"")
    else:
        lines.append("_No verified triggers found._")

    lines += ["", "## Likely pains"]
    for i, p in enumerate(brief.pains):
        ref = f" (trigger {p.trigger_index + 1})" if p.trigger_index < len(brief.triggers) else ""
        lines.append(f"{i + 1}. **{p.pain.rstrip(".")}**{ref}. {p.reasoning}")

    lines += ["", "## Talk tracks"]
    for t in brief.talk_tracks:
        lines.append(f"- _Pain {t.pain_index + 1}:_ \"{t.opener}\"")

    lines += ["", "## Confidence notes"]
    lines += [f"- {n}" for n in brief.confidence_notes] or ["- None."]
    return "\n".join(lines) + "\n"
