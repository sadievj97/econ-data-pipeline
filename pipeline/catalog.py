"""Keyword search over a source's indicator catalog. Deterministic: the same
query on the same cached catalog always gives the same ranked list."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .connectors.base import IndicatorInfo

STOPWORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "per", "the", "to", "with"})


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


def search(entries: Iterable[IndicatorInfo], query: str, limit: int = 15) -> list[IndicatorInfo]:
    terms = list(dict.fromkeys(tokens(query)))
    if not terms:
        return []
    phrase = " ".join(tokens(query))
    scored = []
    for e in entries:
        name_text = " ".join(tokens(e.name))
        code_parts = set(re.split(r"[._]", e.code.lower())) | {e.code.lower()}
        name_words = set(tokens(e.name))
        definition_words = set(tokens(e.definition))
        in_code = sum(t in code_parts for t in terms)
        in_name = sum(t in name_words for t in terms)
        if in_code + in_name == 0:
            continue
        in_definition = sum(t in definition_words for t in terms)
        score = 3 * in_code + 2 * in_name + in_definition + (3 if in_name == len(terms) else 0)
        if len(terms) > 1 and phrase in name_text:
            score += 4 if name_text.startswith(phrase) else 2
        scored.append((-score, len(e.name), e.code, e))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored[:limit]]
