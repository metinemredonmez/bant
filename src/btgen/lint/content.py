from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from statistics import mean
from typing import TYPE_CHECKING

from .policy import Language, policy
from .text import (
    Violation,
    fast_breath_fragment,
    folded,
    lint_abbreviations,
    lint_brackets,
    lint_dizziness_warning,
    lint_emoji,
    lint_hold_duration,
    lint_introduction,
    lint_medical_claims,
    lint_numerals,
    lint_parentheses,
    lint_safety_intro,
    lint_stage_directions,
    pause_pattern,
    pauses,
    sentences,
    spoken_text,
    word_count,
)

if TYPE_CHECKING:
    from btgen.models import Episode, Metadata, NarrationSection, Plan, PlanSection

_MOVEMENT = {"Yoga", "Pilates"}


def lint_plan_duration(episode: Episode, plan: Plan) -> list[Violation]:
    total = sum(section.seconds for section in plan.sections)
    target = round(episode.minutes * 60)
    if total == target:
        return []
    return [
        Violation(
            rule="plan_duration",
            section="plan",
            fragment=f"{total} sn",
            suggestion=f"Bölüm sürelerinin toplamını {target} saniye yap.",
        )
    ]


def lint_pose_alignment(plan: Plan) -> list[Violation]:
    rules = policy().pose
    violations: list[Violation] = []
    for section in plan.sections:
        for pose in section.poses:
            clues = {folded(clue.strip()) for clue in pose.alignment if clue.strip()}
            if pose.name_tr.strip() and rules.min_alignment <= len(clues) <= rules.max_alignment:
                continue
            violations.append(
                Violation(
                    rule="pose_alignment",
                    section=section.key,
                    fragment=pose.name_tr,
                    suggestion=f"Duruşa Türkçe adını, varsa Sanskritçe adını ve {rules.min_alignment}–"
                    f"{rules.max_alignment} farklı hizalama ipucu ekle.",
                )
            )
    return violations


def lint_pose_balance(plan: Plan) -> list[Violation]:
    sides: dict[str, dict[str, int]] = defaultdict(lambda: {"left": 0, "right": 0})
    sections: dict[str, str] = {}
    for section in plan.sections:
        for pose in section.poses:
            if pose.side in {"left", "right"}:
                # Tarafı ayrı sütunda tutan planlarda ad tek başına duruşu belirler.
                key = re.sub(r"^(?:sol|sağ)\s+|\s+(?:sol|sağ)$", "", folded(pose.name_tr.strip()))
                sides[key][pose.side] += pose.seconds
                sections.setdefault(key, section.key)
    return [
        Violation(
            rule="pose_balance",
            section=sections[pose],
            fragment=f"{pose}: sol {counts['left']} sn, sağ {counts['right']} sn",
            suggestion="Duruşta sol ve sağ taraflara eşit süre ayır.",
        )
        for pose, counts in sides.items()
        if counts["left"] != counts["right"]
    ]


def lint_beginner_pose(text: str, section: str, language: Language) -> list[Violation]:
    # Sanskrit adları iki dilde de geçebilir; metni kendi dilinin yazım kurallarıyla eşleştir.
    normalized = folded(text, language)
    pattern = policy().languages[language].beginner_poses
    return [
        Violation(
            rule="beginner_pose",
            section=section,
            fragment=match[0],
            suggestion="Duruşu başlangıç seviyesine uygun bir seçenekle değiştir.",
        )
        for match in re.finditer(rf"\b(?:{pattern})\b", normalized)
    ]


def _fast_forbidden(episode: Episode) -> bool:
    return folded(episode.subcategory) in map(folded, policy().breath.fast_excluded_subcategories)


def lint_breath_plan(episode: Episode, plan: Plan) -> list[Violation]:
    rules = policy().breath
    violations: list[Violation] = []
    for section in plan.sections:
        for cue in section.breaths:
            if cue.hold > rules.maximum_hold_seconds:
                violations.append(
                    Violation(
                        rule="breath_hold",
                        section=section.key,
                        fragment=f"{cue.hold:g} sn",
                        suggestion=f"Nefes tutuşunu {rules.maximum_hold_seconds:g} saniyeyi geçirmeden planla.",
                    )
                )
            if cue.fast and _fast_forbidden(episode):
                violations.append(
                    Violation(
                        rule="fast_breath",
                        section=section.key,
                        fragment=episode.subcategory,
                        suggestion="Bu alt kategoride yavaş, rahat bir nefes ritmi kullan.",
                    )
                )
    return violations


def lint_plan(episode: Episode, plan: Plan) -> list[Violation]:
    violations = lint_plan_duration(episode, plan)
    violations.extend(lint_breath_plan(episode, plan))
    if episode.category == "Nefes" and not any(section.breaths for section in plan.sections):
        violations.append(
            Violation(
                rule="breath_plan",
                section="plan",
                fragment="",
                suggestion="Alış, veriş ve tekrar sayısı için bir nefes planı ekle.",
            )
        )
    if episode.category not in _MOVEMENT:
        return violations
    if not any(section.poses for section in plan.sections):
        violations.append(
            Violation(
                rule="pose_plan",
                section="plan",
                fragment="",
                suggestion="Planlanan hareket ve duruşları ekle.",
            )
        )
    violations.extend(lint_pose_alignment(plan))
    violations.extend(lint_pose_balance(plan))
    if episode.level == "Başlangıç":
        for section in plan.sections:
            for pose in section.poses:
                violations.extend(lint_beginner_pose(pose.name_tr, section.key, "tr"))
                if pose.sanskrit:
                    violations.extend(lint_beginner_pose(pose.sanskrit, section.key, "tr"))
    return violations


def lint_pause_match(
    text: str,
    section: str,
    language: Language,
    expected: Sequence[int],
) -> list[Violation]:
    actual = pauses(text, language)
    if actual == list(expected):
        return []
    return [
        Violation(
            rule="pause_match",
            section=section,
            fragment=str(actual),
            suggestion=f"Duraklamaların sırasını ve sürelerini koru: {list(expected)}.",
        )
    ]


def lint_section(
    episode: Episode,
    plan_section: PlanSection,
    narration_section: NarrationSection,
    language: Language,
    *,
    first: bool = False,
    expected_pauses: Sequence[int] | None = None,
    require_fast_warning: bool = False,
) -> list[Violation]:
    text, section = narration_section.text, plan_section.key
    violations = lint_numerals(text, section, language)
    violations.extend(lint_abbreviations(text, section, language))
    violations.extend(lint_emoji(text, section))
    violations.extend(lint_parentheses(text, section))
    violations.extend(lint_brackets(text, section, language))
    violations.extend(lint_medical_claims(text, section, language))
    violations.extend(lint_stage_directions(text, section, language))
    violations.extend(lint_introduction(text, section, language, episode.coach.name, first=first))
    violations.extend(lint_hold_duration(text, section, language))
    if episode.category in _MOVEMENT:
        if first:
            violations.extend(lint_safety_intro(text, section, language))
        if episode.level == "Başlangıç":
            violations.extend(lint_beginner_pose(text, section, language))
    fast_fragment = fast_breath_fragment(text, language)
    if fast_fragment and _fast_forbidden(episode):
        violations.append(
            Violation(
                rule="fast_breath",
                section=section,
                fragment=fast_fragment,
                suggestion="Bu alt kategoride hızlı nefes kullanma.",
            )
        )
    elif require_fast_warning or (
        fast_fragment and not any(cue.fast for cue in plan_section.breaths)
    ):
        violations.extend(lint_dizziness_warning(text, section, language))
    if expected_pauses is not None:
        violations.extend(lint_pause_match(text, section, language, expected_pauses))
    return violations


def lint_sleep_tempo(
    plan: Plan, sections: Sequence[NarrationSection], language: Language
) -> list[Violation]:
    rules = policy().sleep
    budgets = {section.key: section.seconds for section in plan.sections}
    total_seconds = sum(budgets.values())
    word_counts = {
        section.key: word_count(spoken_text(section.text, language)) for section in sections
    }
    overall = sum(word_counts.values()) * 60 / total_seconds if total_seconds else 0
    if rules.min_words_per_minute <= overall <= rules.max_words_per_minute:
        return []
    faster = overall > rules.max_words_per_minute
    violations: list[Violation] = []
    for key, seconds in budgets.items():
        local = word_counts.get(key, 0) * 60 / seconds if seconds else 0
        if (faster and local > rules.max_words_per_minute) or (
            not faster and local < rules.min_words_per_minute
        ):
            violations.append(
                Violation(
                    rule="sleep_tempo",
                    section=key,
                    fragment=f"{local:.0f} kelime/dk",
                    suggestion=f"Bölümü dakikada {rules.min_words_per_minute}–{rules.max_words_per_minute} "
                    f"kelimeye yaklaştır. Toplam tempo {overall:.0f} kelime/dk.",
                )
            )
    return violations


def _sentence_midpoints(text: str, language: Language, seconds: int) -> list[tuple[float, int]]:
    pause = pause_pattern(language)
    words = word_count(spoken_text(text, language))
    speaking_seconds = max(0, seconds - sum(pauses(text, language)))
    seconds_per_word = speaking_seconds / words if words else 0
    elapsed: float = 0
    positions: list[tuple[float, int]] = []
    sentence_words = 0
    start = 0.0
    for match in re.finditer(rf"{pause.pattern}|[^.!?…\[\]\n]+|[.!?…\n]+", text):
        if match[1]:
            elapsed += int(match[1])
        elif match[0][0] in ".!?…\n":
            if sentence_words:
                positions.append(((start + elapsed) / 2, sentence_words))
                sentence_words = 0
        else:
            if not sentence_words:
                start = elapsed
            count = word_count(match[0])
            sentence_words += count
            elapsed += count * seconds_per_word
    if sentence_words:
        positions.append(((start + elapsed) / 2, sentence_words))
    return positions


def lint_sleep_ending(
    plan: Plan, sections: Sequence[NarrationSection], language: Language
) -> list[Violation]:
    ending_start = sum(section.seconds for section in plan.sections) - policy().sleep.ending_seconds
    if ending_start <= 0:
        return []
    by_key = {section.key: section.text for section in sections}
    earlier: list[int] = []
    ending: list[int] = []
    ending_keys: list[str] = []
    elapsed = 0
    for section in plan.sections:
        for midpoint, length in _sentence_midpoints(
            by_key.get(section.key, ""), language, section.seconds
        ):
            if elapsed + midpoint < ending_start:
                earlier.append(length)
            else:
                ending.append(length)
                if section.key not in ending_keys:
                    ending_keys.append(section.key)
        elapsed += section.seconds
    if earlier and ending and mean(ending) < mean(earlier):
        return []
    return [
        Violation(
            rule="sleep_ending",
            section=key,
            fragment=f"{mean(ending):.1f} kelime/cümle" if ending else "",
            suggestion="Son üç dakikada önceki kısımlardakinden daha kısa cümleler kullan.",
        )
        for key in ending_keys or [plan.sections[-1].key]
    ]


def lint_sleep(
    plan: Plan, sections: Sequence[NarrationSection], language: Language
) -> list[Violation]:
    return lint_sleep_tempo(plan, sections, language) + lint_sleep_ending(plan, sections, language)


def lint_title(text: str, field: str) -> list[Violation]:
    limit = policy().publish.max_title_characters
    if text.strip() and len(text) <= limit:
        return []
    return [
        Violation(
            rule="title_length",
            section="paket",
            fragment=f"{field}: {text}",
            suggestion=f"Başlığı en fazla {limit} karakterle yaz.",
        )
    ]


def lint_description(text: str, language: Language) -> list[Violation]:
    limit = policy().publish.max_description_sentences
    if 0 < len(sentences(text, language)) <= limit:
        return []
    return [
        Violation(
            rule="description_length",
            section="paket",
            fragment=text,
            suggestion=f"Kısa açıklamayı en fazla {limit} cümleyle yaz.",
        )
    ]


def lint_tags(tags: Sequence[str]) -> list[Violation]:
    count = policy().publish.tags
    if len(tags) == count and len({folded(tag.strip()) for tag in tags if tag.strip()}) == count:
        return []
    return [
        Violation(
            rule="tag_count",
            section="paket",
            fragment=", ".join(tags),
            suggestion=f"Tam {count} farklı ve boş olmayan etiket yaz.",
        )
    ]


def lint_checklist(items: Sequence[str]) -> list[Violation]:
    rules = policy().publish
    if rules.min_checklist <= len(items) <= rules.max_checklist and all(
        item.strip() for item in items
    ):
        return []
    return [
        Violation(
            rule="checklist_size",
            section="paket",
            fragment=str(len(items)),
            suggestion=f"{rules.min_checklist}–{rules.max_checklist} gözden geçirme maddesi yaz.",
        )
    ]


def lint_metadata(metadata: Metadata, episode: Episode | None = None) -> list[Violation]:
    violations = lint_title(metadata.title_tr, "TR")
    violations.extend(lint_title(metadata.title_en, "EN"))
    violations.extend(lint_title(metadata.youtube_title, "YouTube"))
    violations.extend(lint_description(metadata.description_tr, "tr"))
    violations.extend(lint_description(metadata.description_en, "en"))
    violations.extend(lint_tags(metadata.tags))
    violations.extend(lint_checklist(metadata.checklist))
    for text in (
        metadata.title_tr,
        metadata.description_tr,
        metadata.youtube_title,
        metadata.youtube_summary,
        metadata.youtube_safety,
        *metadata.tags,
    ):
        violations.extend(lint_medical_claims(text, "paket", "tr"))
    for text in (metadata.title_en, metadata.description_en):
        violations.extend(lint_medical_claims(text, "paket", "en"))
    if (
        len(sentences(metadata.youtube_summary.strip(), "tr")) != 1
        or "\n" in metadata.youtube_summary.strip()
    ):
        violations.append(
            Violation(
                rule="youtube_summary",
                section="paket",
                fragment=metadata.youtube_summary,
                suggestion="YouTube açıklaması için tek satırda tek cümlelik özet yaz.",
            )
        )
    if episode and episode.category in _MOVEMENT:
        violations.extend(lint_safety_intro(metadata.youtube_safety, "paket", "tr"))
    return violations
