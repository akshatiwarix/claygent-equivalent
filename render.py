"""Turn a validated AccountBrief into a one-page Markdown file."""
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
            lines.append(f"{i + 1}. **{t.type.replace('_', ' ').title()}** ({t.date}): {t.summary} [source]({t.source_url})")
    else:
        lines.append("_No verified triggers found._")

    lines += ["", "## Likely pains"]
    for i, p in enumerate(brief.pains):
        lines.append(f"{i + 1}. **{p.pain}** (trigger {p.trigger_index + 1}). {p.reasoning}")

    lines += ["", "## Talk tracks"]
    for t in brief.talk_tracks:
        lines.append(f"- _Pain {t.pain_index + 1}:_ \"{t.opener}\"")

    lines += ["", "## Confidence notes"]
    lines += [f"- {n}" for n in brief.confidence_notes] or ["- None."]
    return "\n".join(lines) + "\n"
