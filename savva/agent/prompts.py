"""Versioned prompt registry loaded from package data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PromptSpec:
    role: str
    version: str
    purpose: str
    input_contract: str
    expected_response: str
    estimated_tokens: int
    text: str


@lru_cache(maxsize=1)
def load_registry() -> dict[str, PromptSpec]:
    path = Path(__file__).resolve().parent.parent / "prompts" / "registry.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: PromptSpec(**value) for key, value in raw.items()}


def get_prompt(name: str) -> PromptSpec:
    try:
        return load_registry()[name]
    except KeyError:
        raise ValueError(f"Unknown prompt role: {name}") from None
