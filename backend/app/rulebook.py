"""Единственный экземпляр справочника на процесс. Перечитывается при старте."""

from __future__ import annotations

from .config import settings
from .engine import Rulebook, load_rulebook

_book: Rulebook | None = None


def get_rulebook() -> Rulebook:
    global _book
    if _book is None:
        _book = load_rulebook(settings.rules_dir)
    return _book


def reload_rulebook() -> Rulebook:
    global _book
    _book = load_rulebook(settings.rules_dir)
    return _book
