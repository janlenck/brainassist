"""Translations: every language has exactly the texts of the English file, with the same placeholders."""

import json
from pathlib import Path
import re

import pytest

TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "brainassist" / "translations"
OTHERS = sorted(p.stem for p in TRANSLATIONS.glob("*.json") if p.stem != "en")


def texts(lang: str) -> dict[str, str]:
    """All texts of a language by their path, e.g. config.step.user.title."""

    def flat(node: dict, prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(flat(value, f"{prefix}{key}.") if isinstance(value, dict) else {prefix + key: value})
        return out

    return flat(json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8")))


def test_languages() -> None:
    assert OTHERS == ["de", "uk"]


@pytest.mark.parametrize("lang", OTHERS)
def test_same_texts_and_placeholders_as_english(lang: str) -> None:
    english, other = texts("en"), texts(lang)
    assert set(other) == set(english)
    for key, text in english.items():
        assert sorted(re.findall(r"\{\w+\}", other[key])) == sorted(re.findall(r"\{\w+\}", text)), key
        assert other[key].strip(), key
