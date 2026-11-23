import re

from btgen.config import BTError
from btgen.lint import Language, lint_metadata, lint_plan, lint_section, lint_sleep, pauses
from btgen.models import Episode, Metadata, Narration, NarrationSection, Plan, SeriesPlan
from btgen.providers.llm import TextEngine


def plan_series(engine: TextEngine, episode: Episode) -> SeriesPlan:
    def check(result: SeriesPlan) -> list[object]:
        if len(result.episodes) == episode.count:
            return []
        return [f"Seri tam {episode.count} bölüm içermeli."]

    return engine.generate("seri", SeriesPlan, {"episode": episode.for_model()}, check)


def plan_episode(engine: TextEngine, episode: Episode) -> Plan:
    return engine.generate(
        "plan", Plan, {"episode": episode.for_model()}, lambda plan: list(lint_plan(episode, plan))
    )


def narration(
    engine: TextEngine,
    episode: Episode,
    plan: Plan,
    language: Language,
    source: Narration | None = None,
    *,
    unvalidated: bool = False,
) -> Narration:
    if not episode.has_narration:
        return Narration(sections=[])
    variants = [False, True] if episode.has_podcast else [False]
    outputs: list[list[NarrationSection]] = []
    for podcast in variants:
        outputs.append(
            _variant(
                engine, episode, plan, language, source, podcast=podcast, unvalidated=unvalidated
            )
        )
    return Narration(sections=outputs[0], podcast_sections=outputs[1] if len(outputs) == 2 else [])


def _variant(
    engine: TextEngine,
    episode: Episode,
    plan: Plan,
    language: Language,
    source: Narration | None,
    *,
    podcast: bool,
    unvalidated: bool,
) -> list[NarrationSection]:
    task = f"{'podcast' if podcast else 'anlatim'}_{language}"
    originals = (source.podcast_sections if podcast else source.sections) if source else []
    output: list[NarrationSection] = []
    for index, section in enumerate(plan.sections):
        original = originals[index].text if originals else None
        context = _context(episode, plan, index, output, original)
        validate = None
        if not unvalidated:
            validate = lambda result, i=index, src=original: _section_issues(  # noqa: E731
                episode, plan, i, result, language, src, podcast
            )
        result = engine.generate(task, NarrationSection, context, validate)
        if result.key != section.key:
            raise BTError(f"{task} bölüm anahtarı beklenen değerden farklı: {section.key}.")
        output.append(result)
    return (
        output
        if unvalidated
        else _repair_sleep(engine, episode, plan, language, output, task, originals)
    )


def _context(
    episode: Episode, plan: Plan, index: int, output: list[NarrationSection], original: str | None
) -> dict:
    previous = output[index - 1].text if index else ""
    sentences = re.split(r"(?<=[.!?])\s+", previous)
    return {
        "episode": episode.for_model(),
        "plan": plan.model_dump(mode="json"),
        "section": plan.sections[index].model_dump(mode="json"),
        "previous": " ".join(sentences[-3:]),
        "source_text": original,
        "first": index == 0,
        "last": index == len(plan.sections) - 1,
    }


def _section_issues(
    episode: Episode,
    plan: Plan,
    index: int,
    result: NarrationSection,
    language: Language,
    original: str | None,
    podcast: bool,
) -> list[object]:
    section = plan.sections[index]
    if result.key != section.key:
        return [f"Bölümün anahtarı {section.key} olmalı."]
    issues: list[object] = list(
        lint_section(
            episode,
            section,
            result,
            language,
            first=index == 0,
            expected_pauses=pauses(original, "tr") if original is not None else None,
            require_fast_warning=index == 0
            and any(cue.fast for item in plan.sections for cue in item.breaths),
        )
    )
    if section.breaths:
        expected = sum((2 + bool(cue.hold)) * cue.repetitions for cue in section.breaths)
        if expected != len(pauses(result.text, language)):
            issues.append(
                f"{section.key} bölümünde her nefes evresine bir komut ve duraklama yaz. Toplam {expected} duraklama gerekli."
            )
    if podcast and re.search(
        r"ekrana bak|(?:look|see) (?:at |on )?(?:the |your )?screen", result.text, re.I
    ):
        issues.append("Podcast anlatımında dinleyiciden ekrana bakmasını isteme.")
    return issues


def _repair_sleep(
    engine: TextEngine,
    episode: Episode,
    plan: Plan,
    language: Language,
    sections: list[NarrationSection],
    task: str,
    originals: list[NarrationSection],
) -> list[NarrationSection]:
    if episode.category != "Uyku hikâyesi" or not sections:
        return sections
    by_key = {section.key: index for index, section in enumerate(plan.sections)}
    for _ in range(2):
        issues = lint_sleep(plan, sections, language)
        if not issues:
            return sections
        for key in dict.fromkeys(issue.section for issue in issues):
            idx = by_key.get(key, len(sections) - 1)
            original = originals[idx].text if originals else None
            context = _context(episode, plan, idx, sections, original)
            context["revision"] = [issue.model_dump() for issue in issues if issue.section == key]
            context["previous_draft"] = sections[idx].text
            sections[idx] = engine.generate(
                task,
                NarrationSection,
                context,
                lambda result, i=idx, src=original: _section_issues(
                    episode,
                    plan,
                    i,
                    result,
                    language,
                    src,
                    task.startswith("podcast"),
                ),
                attempts=1,
            )
    if issues := lint_sleep(plan, sections, language):
        raise BTError(
            "Uyku hikâyesi denetimi başarısız: " + ", ".join(issue.rule for issue in issues)
        )
    return sections


def revise_turkish(
    engine: TextEngine, episode: Episode, plan: Plan, previous: Narration
) -> Narration:
    output: dict[str, list[NarrationSection]] = {}
    for task, variant in [
        ("anlatim_tr", previous.sections),
        ("podcast_tr", previous.podcast_sections),
    ]:
        sections: list[NarrationSection] = []
        for index, draft in enumerate(variant):
            issues = _section_issues(
                episode,
                plan,
                index,
                draft,
                "tr",
                None,
                task == "podcast_tr",
            )
            if issues:
                context = _context(episode, plan, index, sections, None)
                context["revision"] = [
                    issue.model_dump() if hasattr(issue, "model_dump") else issue
                    for issue in issues
                ]
                context["previous_draft"] = draft.text
                draft = engine.generate(
                    task,
                    NarrationSection,
                    context,
                    lambda result, i=index, pod=task == "podcast_tr": _section_issues(
                        episode,
                        plan,
                        i,
                        result,
                        "tr",
                        None,
                        pod,
                    ),
                    attempts=2,
                )
            sections.append(draft)
        output[task] = _repair_sleep(engine, episode, plan, "tr", sections, task, [])
    return Narration(sections=output["anlatim_tr"], podcast_sections=output["podcast_tr"])


def publishing(engine: TextEngine, episode: Episode, plan: Plan) -> Metadata:
    return engine.generate(
        "metadata",
        Metadata,
        {"episode": episode.for_model(), "plan": plan.model_dump(mode="json")},
        lambda metadata: list(lint_metadata(metadata, episode=episode)),
    )
