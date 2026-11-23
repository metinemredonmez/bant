from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from .policy import Language, policy, video_policy
from .text import Violation, folded

if TYPE_CHECKING:
    from btgen.models import Episode


class ShotRecord(Protocol):
    number: int
    start: float
    end: float
    section: str
    action: str
    camera: str
    prompt: str
    has_person: bool


class BreathRecord(Protocol):
    @property
    def section(self) -> str: ...

    @property
    def phase(self) -> str: ...

    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def target_seconds(self) -> float: ...


class LanguageTimingRecord(Protocol):
    @property
    def seconds(self) -> float: ...

    @property
    def breaths(self) -> Sequence[BreathRecord]: ...


class AudioTimingRecord(Protocol):
    @property
    def languages(self) -> dict[str, LanguageTimingRecord]: ...


_NEGATIVE_CLAUSE = re.compile(
    r"(?:(?<=^)|(?<=[.?!;,])|\b(?:and|with)\s+)\s*"
    r"(?:no\b|without\b|avoid\b|do not\b|don't\b|never\b)"
    r"(?:(?!\b(?:but|however|then)\b)[^.!?;])*"
)


def _video_fragments(text: str, section: str) -> list[Violation]:
    rules = video_policy()
    normalized = folded(text, "en").replace(folded(rules.negative_prompt, "en"), "")
    positives = _NEGATIVE_CLAUSE.sub(" ", normalized)
    return [
        Violation(
            rule=f"video_{name}",
            section=section,
            fragment=match[0],
            suggestion="Yasaklı görsel öğeyi veya hareketi istemden çıkar.",
        )
        for name, pattern in rules.prohibited.items()
        for match in re.finditer(pattern, positives)
    ]


def lint_shot_direction(action: str, camera: str, section: str) -> list[Violation]:
    return _video_fragments(action, section) + _video_fragments(camera, section)


def lint_shot_character(shot: ShotRecord, character_description: str) -> list[Violation]:
    if not shot.has_person or (character_description and character_description in shot.prompt):
        return []
    return [
        Violation(
            rule="video_character",
            section=shot.section,
            fragment=f"{shot.number}. çekim",
            suggestion="Kişili çekime koçun tam karakter tarifini birebir ekle.",
        )
    ]


def lint_shot_timeline(shots: Sequence[ShotRecord]) -> list[Violation]:
    rules = video_policy()
    violations: list[Violation] = []
    previous_end = 0.0
    tolerance = 1e-6
    for shot in shots:
        duration = shot.end - shot.start
        if not math.isclose(shot.start, previous_end, abs_tol=tolerance, rel_tol=0):
            violations.append(
                Violation(
                    rule="video_timeline",
                    section=shot.section,
                    fragment=f"{shot.number}. çekim / {shot.start:g} sn",
                    suggestion="Çekimi bir öncekinin bittiği anda başlat.",
                )
            )
        if not rules.min_seconds - tolerance <= duration <= rules.max_seconds + tolerance:
            violations.append(
                Violation(
                    rule="video_duration",
                    section=shot.section,
                    fragment=f"{shot.number}. çekim / {duration:g} sn",
                    suggestion=f"Çekim süresini {rules.min_seconds}–{rules.max_seconds} saniyeye getir.",
                )
            )
        previous_end = shot.end
    return violations


def lint_shots(shots: Sequence[ShotRecord], character_description: str) -> list[Violation]:
    violations = lint_shot_timeline(shots)
    for shot in shots:
        violations.extend(lint_shot_character(shot, character_description))
        direction = lint_shot_direction(shot.action, shot.camera, shot.section)
        for violation in _video_fragments(shot.prompt, shot.section):
            if violation not in direction:
                direction.append(violation)
        violations.extend(direction)
    return violations


def lint_audio_duration(episode: Episode, seconds: float, language: Language) -> list[Violation]:
    target = episode.minutes * 60
    tolerance = policy().audio_duration_tolerance
    if abs(seconds - target) <= target * tolerance:
        return []
    return [
        Violation(
            rule="audio_duration",
            section=f"ses/{language}",
            fragment=f"{seconds:.2f} sn",
            suggestion=f"Toplam sesi {target:g} saniyenin yüzde {tolerance:.0%} aralığına getir.".replace(
                "%", ""
            ),
        )
    ]


def lint_audio_breaths(breaths: Sequence[BreathRecord]) -> list[Violation]:
    rules = policy().breath
    names = {"inhale": "alış", "exhale": "veriş", "hold": "tutuş"}
    violations: list[Violation] = []
    for breath in breaths:
        measured = breath.end - breath.start
        if abs(measured - breath.target_seconds) > rules.timing_tolerance_seconds + 1e-6:
            violations.append(
                Violation(
                    rule="audio_breath_timing",
                    section=breath.section,
                    fragment=f"{names[breath.phase]} {measured:.2f} sn",
                    suggestion=f"Ölçülen süreyi plandaki {breath.target_seconds:g} saniyeye "
                    f"{rules.timing_tolerance_seconds:g} saniye içinde yaklaştır.",
                )
            )
        if breath.phase == "hold" and measured > rules.maximum_hold_seconds + 1e-6:
            violations.append(
                Violation(
                    rule="breath_hold",
                    section=breath.section,
                    fragment=f"{measured:.2f} sn",
                    suggestion=f"Tutuşu en çok {rules.maximum_hold_seconds:g} saniye yap.",
                )
            )
    return violations


def lint_audio(
    episode: Episode, timing: AudioTimingRecord, language: Language = "tr"
) -> list[Violation]:
    track = timing.languages.get(language)
    if track is None:
        return [
            Violation(
                rule="audio_timing_missing",
                section=f"ses/{language}",
                fragment=language,
                suggestion="Bu dil için zaman damgalarını seslendirmeden yeniden üret.",
            )
        ]
    return lint_audio_duration(episode, track.seconds, language) + lint_audio_breaths(track.breaths)
