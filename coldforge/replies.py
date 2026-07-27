"""Reply triage — classify inbound replies so the pipeline can react.

A reply isn't just "stop the sequence": an *interested* reply should surface
first thing, an *unsubscribe* must land on the suppression list immediately,
and an out-of-office shouldn't count as engagement at all.

Categories: ``interested`` | ``not_interested`` | ``unsubscribe`` | ``ooo`` | ``other``.

Keyword heuristics (French + English) are the zero-key default; with
``ANTHROPIC_API_KEY`` set the ambiguous cases are upgraded by a one-line LLM
call that must answer with one of the category words. Any failure falls back to
the heuristic, so triage never crashes a scan.
"""

from __future__ import annotations

from .config import Settings, get_settings

CATEGORIES = ("interested", "not_interested", "unsubscribe", "ooo", "other")

_UNSUBSCRIBE = (
    "unsubscribe", "remove me", "take me off", "stop emailing", "stop contacting",
    "désinscri", "desinscri", "me désabonner", "me retirer", "retirez-moi",
    "retirez moi", "plus de mail", "plus d'email", "ne plus recevoir",
    "arrêtez de m'écrire",
)
_OOO = (
    "out of office", "on vacation", "annual leave", "maternity leave", "auto-reply",
    "automatic reply", "absent du bureau", "absente du bureau", "en congé",
    "congés", "réponse automatique", "de retour le", "currently away",
)
_NEGATIVE = (
    "not interested", "no thanks", "no thank you", "we already have", "not a fit",
    "not the right time", "no budget", "please don't", "pas intéressé",
    "pas interesse", "pas intéressée", "non merci", "pas le bon moment",
    "pas de budget", "déjà équipé", "deja equipe", "ce n'est pas pour nous",
)
_POSITIVE = (
    "interested", "tell me more", "more info", "sounds good", "let's talk",
    "book a call", "schedule", "demo", "send me", "how much", "pricing",
    "intéressé", "interesse", "intéressée", "en savoir plus", "dites-m'en plus",
    "on peut échanger", "un appel", "un rdv", "rendez-vous", "combien",
    "tarif", "envoyez-moi", "essai",
)


def _heuristic(text: str) -> str:
    """Order matters: an OOO auto-reply often *contains* polite positives, and an
    unsubscribe outranks everything."""
    lower = text.lower()
    if any(k in lower for k in _UNSUBSCRIBE):
        return "unsubscribe"
    if any(k in lower for k in _OOO):
        return "ooo"
    if any(k in lower for k in _NEGATIVE):
        return "not_interested"
    if any(k in lower for k in _POSITIVE):
        return "interested"
    return "other"


def _llm(text: str, settings: Settings) -> str | None:
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.model, max_tokens=10,
            system=(
                "Classify a reply to a cold outreach email. Answer with exactly one "
                "word from: interested, not_interested, unsubscribe, ooo, other."
            ),
            messages=[{"role": "user", "content": text[:2000]}],
        )
        word = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip().lower()
        return word if word in CATEGORIES else None
    except Exception:  # triage must never crash a scan
        return None


def classify_reply(subject: str, body: str, settings: Settings | None = None) -> str:
    """Classify one reply into a category from :data:`CATEGORIES`."""
    text = f"{subject}\n{body}".strip()
    if not text:
        return "other"
    guess = _heuristic(text)
    # The heuristic is precise when it matches; only ambiguity is worth a model call.
    if guess == "other":
        settings = settings or get_settings()
        if settings.has_ai:
            return _llm(text, settings) or guess
    return guess
