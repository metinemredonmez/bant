from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from functools import cache

from pydantic import BaseModel

from .policy import Language, policy

_TR_CASE = str.maketrans({"I": "ı", "İ": "i", "ı": "ı"})
_EMOJI = re.compile(
    r"[\u2194-\u2199\u21A9-\u21AA\u2300-\u23FA\u25AA-\u25FE\u2600-\u27BF"
    r"\u2B05-\u2B55\u3030\u303D\u3297\u3299\uFE0F\u20E3"
    r"\U0001F000-\U0001FAFF]"
)
_PARENTHESES = re.compile(r"\([^()\n]*\)|（[^（）\n]*）|[()（）]")
_BRACKETS = re.compile(r"\[[^\[\]\n]*\]|[\[\]【】［］]")
_ACRONYMS = re.compile(r"\b(?:[A-ZÇĞİÖŞÜ]{2,}|(?:[A-Za-zÇĞİÖŞÜçğıöşü]\.){2,})\b")
_WORDS = re.compile(r"[^\W_]+(?:[’'\-][^\W_]+)*")
_SENTENCES = re.compile(r"[^.!?…\n]+(?:[.!?…]+[”\"’']*|$|(?=\n))")


class Violation(BaseModel):
    rule: str
    section: str
    fragment: str
    suggestion: str


def folded(text: str, language: Language = "tr") -> str:
    text = unicodedata.normalize("NFC", text)
    if language == "tr":
        text = text.translate(_TR_CASE)
    return text.casefold()


@cache
def pause_pattern(language: Language) -> re.Pattern[str]:
    return re.compile(r"\[" + policy().languages[language].pause + r"\]")


def pauses(text: str, language: Language) -> list[int]:
    return [int(match[1]) for match in pause_pattern(language).finditer(text)]


def spoken_text(text: str, language: Language) -> str:
    return pause_pattern(language).sub(" ", text)


def sentences(text: str, language: Language) -> list[str]:
    text = re.sub(r"(?<=\d)\.(?=\d)", "·", text)
    for abbreviation in policy().languages[language].abbreviations:
        text = re.sub(
            rf"\b{re.escape(abbreviation)}\.(?=\s+[^\W\d_])",
            lambda match: match[0].replace(".", "·"),
            text,
            flags=re.IGNORECASE,
        )
    return [match[0].strip() for match in _SENTENCES.finditer(text) if _WORDS.search(match[0])]


def word_count(text: str) -> int:
    return len(_WORDS.findall(text))


def lint_numerals(text: str, section: str, language: Language) -> list[Violation]:
    return [
        Violation(
            rule="numerals",
            section=section,
            fragment=match[0],
            suggestion="Anlatımdaki sayıyı yazıyla yaz.",
        )
        for match in re.finditer(r"\d+|[²³¹⁰⁴-⁹]", spoken_text(text, language))
    ]


@cache
def _abbreviations(language: Language) -> re.Pattern[str]:
    alternatives = "|".join(map(re.escape, policy().languages[language].abbreviations))
    return re.compile(rf"(?<!\w)(?:{alternatives})\.?(?!\w)")


def lint_abbreviations(text: str, section: str, language: Language) -> list[Violation]:
    spoken = spoken_text(text, language)
    matches = dict.fromkeys(
        [match[0] for match in _ACRONYMS.finditer(spoken)]
        + [match[0] for match in _abbreviations(language).finditer(folded(spoken, language))]
    )
    return [
        Violation(
            rule="abbreviations",
            section=section,
            fragment=match,
            suggestion="Kısaltmayı telaffuz edilecek açık biçimiyle yaz.",
        )
        for match in matches
    ]


def lint_emoji(text: str, section: str) -> list[Violation]:
    return [
        Violation(
            rule="emoji", section=section, fragment=match[0], suggestion="Emojiyi anlatımdan çıkar."
        )
        for match in _EMOJI.finditer(text)
    ]


def lint_parentheses(text: str, section: str) -> list[Violation]:
    return [
        Violation(
            rule="parentheses",
            section=section,
            fragment=match[0],
            suggestion="Parantez içi ifadeyi kaldır veya doğal bir cümleye dönüştür.",
        )
        for match in _PARENTHESES.finditer(text)
    ]


def lint_brackets(text: str, section: str, language: Language) -> list[Violation]:
    spans = {match.span() for match in pause_pattern(language).finditer(text)}
    return [
        Violation(
            rule="brackets",
            section=section,
            fragment=match[0],
            suggestion="Köşeli parantezleri yalnızca dilin duraklama işareti için kullan.",
        )
        for match in _BRACKETS.finditer(text)
        if match.span() not in spans
    ]


@cache
def _claims(language: Language) -> re.Pattern[str]:
    alternatives = "|".join(policy().languages[language].medical_claims)
    return re.compile(rf"\b(?:{alternatives})\b")


def lint_medical_claims(text: str, section: str, language: Language) -> list[Violation]:
    return [
        Violation(
            rule="medical_claims",
            section=section,
            fragment=match[0],
            suggestion="Tıbbi iddiayı çıkar ve deneyimi yumuşak bir dille anlat.",
        )
        for match in _claims(language).finditer(folded(text, language))
    ]


def lint_stage_directions(text: str, section: str, language: Language) -> list[Violation]:
    normalized = folded(text, language)
    stage_directions = policy().languages[language].stage_directions
    return [
        Violation(
            rule="stage_directions",
            section=section,
            fragment=fragment,
            suggestion="Efekt, gülme veya fısıltı yönergesini anlatımdan çıkar.",
        )
        for fragment in stage_directions
        if fragment in normalized
    ]


def lint_introduction(
    text: str,
    section: str,
    language: Language,
    name: str,
    *,
    first: bool,
) -> list[Violation]:
    identity = re.escape(folded(name, language))
    introduction = re.compile(policy().languages[language].introduction.format(name=identity))
    statements = sentences(folded(spoken_text(text, language), language), language)
    positions = [index for index, sentence in enumerate(statements) if introduction.match(sentence)]
    if positions == ([0] if first else []):
        return []
    suggestion = (
        "Açılıştaki ilk cümlede koç kendini adıyla tek kez tanıtsın."
        if first
        else "Koçun kendini tanıttığı cümleyi açılış bölümünde bırak."
    )
    return [
        Violation(
            rule="coach_introduction",
            section=section,
            fragment=statements[0] if statements else "",
            suggestion=suggestion,
        )
    ]


def lint_safety_intro(text: str, section: str, language: Language) -> list[Violation]:
    normalized = folded(spoken_text(text, language), language)
    names = {
        "pregnancy": "hamilelik",
        "recent_surgery": "yakın zamanda ameliyat",
        "lower_back": "bel sorunu",
        "neck": "boyun sorunu",
        "knee": "diz sorunu",
        "doctor": "doktora danışma",
        "pain_stop": "ağrı durumunda durma",
    }
    missing = [
        names[name]
        for name, pattern in policy().languages[language].safety.items()
        if not re.search(pattern, normalized)
    ]
    if not missing:
        return []
    return [
        Violation(
            rule="safety_intro",
            section=section,
            fragment=", ".join(missing),
            suggestion="Girişte belirtilen güvenlik uyarılarını tamamla.",
        )
    ]


def lint_dizziness_warning(text: str, section: str, language: Language) -> list[Violation]:
    normalized = folded(spoken_text(text, language), language)
    if re.search(policy().languages[language].dizziness, normalized):
        return []
    return [
        Violation(
            rule="dizziness_warning",
            section=section,
            fragment="",
            suggestion="Hızlı nefes öncesinde başı dönerse durmasını söyle.",
        )
    ]


def fast_breath_fragment(text: str, language: Language) -> str | None:
    normalized = folded(spoken_text(text, language), language)
    pattern = policy().languages[language].fast_breath
    for sentence in sentences(normalized, language):
        match = re.search(pattern, sentence)
        if not match:
            continue
        if language == "tr" and re.search(
            r"(?:alma|verme|yapma|kullanma|önermiyoruz|yok)\b", sentence
        ):
            continue
        if language == "en" and re.search(
            r"(?:don't|don’t|do not|without|avoid|skip|\bno\b)", sentence
        ):
            continue
        return match[0]
    return None


@cache
def _durations(language: Language) -> re.Pattern[str]:
    rules = policy().languages[language]
    alternatives = "|".join(rules.duration_words)
    number = rf"(?:{alternatives})"
    quantity = rf"(?:\d+(?:[.,]\d+)?|{number}(?:[ -](?:and[ -]|a[ -]|ve[ -])?{number})*)"
    return re.compile(
        rf"(?<!\w)(?P<quantity>{quantity})(?: a)?[ -]+"
        rf"(?:(?P<minutes>{rules.minutes})|(?:{rules.seconds}))\b"
    )


def _read_duration(quantity: str, language: Language) -> float:
    if quantity[0].isdecimal():
        return float(quantity.replace(",", "."))
    values = policy().languages[language].duration_words
    return sum(values.get(word, 0) for word in re.split(r"[ -]+", quantity))


def hold_intervals(text: str, language: Language) -> Iterator[tuple[float, str]]:
    rules = policy().languages[language]
    normalized = folded(text, language)
    for match in re.finditer(r"[^\n.!?;]+[.!?;]*", normalized):
        sentence = spoken_text(match[0], language).strip()
        clauses = re.split(r",\s+(?:sonra|then)\s+|\b(?:ve sonra|and then)\s+", sentence)
        for index, clause in enumerate(clauses):
            if not re.search(rules.hold_instruction, clause) or re.search(
                rules.negative_hold, clause
            ):
                continue
            periods = list(_durations(language).finditer(clause))
            if periods:
                for period in periods:
                    value = _read_duration(period["quantity"], language)
                    yield value * (60 if period["minutes"] else 1), clause
            elif index == len(clauses) - 1:
                pause = pause_pattern(language).match(normalized[match.end() :].lstrip())
                if pause:
                    yield float(pause[1]), clause + " " + pause[0]


def lint_hold_duration(text: str, section: str, language: Language) -> list[Violation]:
    limit = policy().breath.maximum_hold_seconds
    return [
        Violation(
            rule="breath_hold",
            section=section,
            fragment=fragment,
            suggestion=f"Nefes tutuşunu {limit:g} saniyeyi geçmeyecek şekilde düzelt.",
        )
        for seconds, fragment in hold_intervals(text, language)
        if seconds > limit
    ]
