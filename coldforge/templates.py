"""Template loading and variable rendering.

Templates are plain ``.md`` files with YAML front-matter, shipped in
``coldforge/templates/``. Users can drop their own ``.md`` files into
``$COLDFORGE_HOME/templates`` to extend the pack without touching the install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

_BUILTIN_DIR = Path(__file__).parent / "templates"
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_FRONT_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class Template:
    id: str
    name: str
    category: str
    subject: str
    body: str
    persona: str = ""
    use_case: str = ""
    deliverability_notes: str = ""
    variables: list[str] = field(default_factory=list)
    path: Path | None = None

    def required_vars(self) -> set[str]:
        """Variables actually referenced in subject + body."""
        return set(_VAR_RE.findall(self.subject)) | set(_VAR_RE.findall(self.body))


def _parse(path: Path) -> Template | None:
    text = path.read_text(encoding="utf-8")
    m = _FRONT_RE.match(text)
    if not m:
        return None
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip("\n")
    return Template(
        id=str(meta.get("id", path.stem)),
        name=str(meta.get("name", path.stem)),
        category=str(meta.get("category", "general")),
        subject=str(meta.get("subject", "")),
        body=body,
        persona=str(meta.get("persona", "")),
        use_case=str(meta.get("use_case", "")),
        deliverability_notes=str(meta.get("deliverability_notes", "")),
        variables=list(meta.get("variables", []) or []),
        path=path,
    )


def _user_dir() -> Path | None:
    from .config import get_settings

    d = get_settings().home / "templates"
    return d if d.is_dir() else None


@lru_cache(maxsize=1)
def load_all() -> dict[str, Template]:
    """Map of ``id → Template`` from built-in pack and the user dir."""
    out: dict[str, Template] = {}
    dirs = [_BUILTIN_DIR]
    if (ud := _user_dir()) is not None:
        dirs.append(ud)
    for directory in dirs:
        for path in sorted(directory.rglob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            tpl = _parse(path)
            if tpl:
                out[tpl.id] = tpl
    return out


def get(template_id: str) -> Template:
    templates = load_all()
    if template_id in templates:
        return templates[template_id]
    # forgiving lookup: match on a suffix / loose id
    for tid, tpl in templates.items():
        if tid.endswith(template_id) or template_id in tid:
            return tpl
    raise KeyError(f"Unknown template '{template_id}'. Try: coldforge templates")


def by_category(category: str | None = None) -> list[Template]:
    items = sorted(load_all().values(), key=lambda t: (t.category, t.id))
    return [t for t in items if not category or t.category == category]


class _SafeDict(dict):
    """Leave unknown ``{{vars}}`` untouched instead of raising."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return "{{" + key + "}}"


def render(template_text: str, variables: dict[str, str]) -> str:
    """Replace ``{{var}}`` with values; unknown vars are left as-is so missing
    personalization is visible at review time rather than silently blank."""
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _VAR_RE.sub(repl, template_text)


def missing_vars(template: Template, variables: dict[str, str]) -> list[str]:
    return sorted(v for v in template.required_vars() if not variables.get(v))
