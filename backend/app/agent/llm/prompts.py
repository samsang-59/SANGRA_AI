"""The prompts: plain text files in prompts/, filled with string.Template ($name). The filled-in values are never
read as a template again, so text like "$x" or "{x}" in the user's code or bug report stays as it is."""

from functools import cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"


@cache
def _template(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8"))


def render(name: str, **values: object) -> str:
    """Fills a prompt. Every placeholder must get a value (a missing one is a bug, so it raises)."""
    return _template(name).substitute({key: str(value) for key, value in values.items()})


def as_list(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty
