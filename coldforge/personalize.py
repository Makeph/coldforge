"""Draft generation: template + lead vars + researched signal → an email.

Two modes, chosen automatically:

* **Template fill** (always available, no key): render ``{{vars}}`` from the
  lead, and if a researched signal exists, weave it into the opener variable so
  the first line is concrete. Deterministic and offline.
* **Claude rewrite** (when ``ANTHROPIC_API_KEY`` is set): the template becomes a
  *brief*. Claude rewrites the body around the signal while honouring the
  template's constraints (length, one CTA, plaintext, deliverability notes).

The Claude path falls back to template fill on any error, so a draft is always
produced.
"""

from __future__ import annotations

import re

from .config import Settings, get_settings
from .models import Draft, Lead, Signal
from .templates import Template, get as get_template, missing_vars, render

# Template variables that conventionally hold the personalized opener / hook.
_OPENER_VARS = ("observation", "candidate_signal", "candidate_work", "specific_thing",
                "overlap", "reason", "original_hook")

_SYSTEM = (
    "You are an expert cold-email copywriter. You write plaintext emails that get "
    "replies: specific, human, never salesy. Hard rules: under 120 words; exactly one "
    "call to action; no greetings like 'I hope this finds you well'; no buzzwords "
    "(synergy, revolutionary, game-changing, rockstar, 10x); no markdown, no links "
    "unless given. Open on something true about the recipient, not about the sender."
)


def _inject_signal(template: Template, variables: dict[str, str],
                   signal: Signal | None) -> dict[str, str]:
    """Fill the template's opener variable from research when it's otherwise empty.

    Looks at the variables the *template* actually references, finds the first
    conventional opener slot among them that has no value, and sets it to the
    signal. Falls back to a generic ``{{signal}}`` var if the template uses one.
    """
    if not signal or not signal.text:
        return variables
    out = dict(variables)
    referenced = template.required_vars()
    for var in _OPENER_VARS:
        if var in referenced and not out.get(var):
            out[var] = signal.text
            return out
    if "signal" in referenced:
        out.setdefault("signal", signal.text)
    return out


def _template_fill(template: Template, variables: dict[str, str]) -> Draft:
    subject = render(template.subject, variables)
    body = render(template.body, variables)
    missing = missing_vars(template, variables)
    notes = ""
    if missing:
        notes = "Unfilled variables (left as {{…}} for review): " + ", ".join(missing)
    return Draft(subject=subject, body=body, template_id=template.id,
                 personalized=False, notes=notes)


def _claude_rewrite(template: Template, variables: dict[str, str],
                    signal: Signal | None, settings: Settings) -> Draft | None:
    try:
        import anthropic
    except ImportError:
        return None

    known = "\n".join(f"- {k}: {v}" for k, v in variables.items())
    signal_block = f'\nResearched signal about the recipient:\n"{signal.text}"' if signal else ""
    prompt = (
        f"Rewrite this cold-email template into a finished, ready-to-send email.\n\n"
        f"Template intent: {template.use_case or template.name}\n"
        f"Deliverability rules to respect:\n{template.deliverability_notes or '—'}\n\n"
        f"Template subject: {template.subject}\n"
        f"Template body:\n{template.body}\n\n"
        f"Known variables:\n{known or '(none)'}"
        f"{signal_block}\n\n"
        "Use the signal to make the first line concrete and specific to this person. "
        "Keep every remaining {{variable}} that you don't have a value for as a literal "
        "{{placeholder}} so the sender can fill it. Return exactly:\n"
        "SUBJECT: <one line>\n<blank line>\n<body>"
    )
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model,
            max_tokens=600,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as exc:  # noqa: BLE001 — any API failure → fall back
        return Draft(subject="", body="", template_id=template.id, personalized=False,
                     notes=f"Claude unavailable ({exc.__class__.__name__}); used template fill.")

    m = re.match(r"\s*SUBJECT:\s*(?P<subj>.+?)\n(?P<body>.*)$", text, re.DOTALL | re.I)
    if not m:
        return Draft(subject=render(template.subject, variables), body=text.strip(),
                     template_id=template.id, personalized=True,
                     notes="Model output had no SUBJECT line; used template subject.")
    return Draft(subject=m.group("subj").strip(), body=m.group("body").strip(),
                 template_id=template.id, personalized=True,
                 notes=f"Personalized by {settings.model}.")


def draft_email(
    template_id: str,
    lead: Lead,
    *,
    signal: Signal | None = None,
    extra_vars: dict[str, str] | None = None,
    settings: Settings | None = None,
    force_template_fill: bool = False,
) -> Draft:
    """Produce a :class:`Draft` for *lead* from *template_id*.

    *signal* is an optional researched fact used to personalize the opener.
    *extra_vars* override/supplement the lead's own variables.
    """
    settings = settings or get_settings()
    template = get_template(template_id)

    variables = lead.as_vars()
    if settings.from_name:
        variables.setdefault("sender_name", settings.from_name)
    if extra_vars:
        variables.update({k: v for k, v in extra_vars.items() if v})
    variables = _inject_signal(template, variables, signal)

    if settings.has_ai and not force_template_fill:
        drafted = _claude_rewrite(template, variables, signal, settings)
        if drafted and drafted.body:
            return drafted
        # else: fall through to deterministic fill (note carried via fill below)

    return _template_fill(template, variables)
