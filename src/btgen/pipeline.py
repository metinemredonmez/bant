from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from btgen.config import BTError, Workspace, read_episode, slug
from btgen.models import Episode, Metadata, Narration, Plan, SeriesPlan
from btgen.providers.llm import LLMProvider, TextEngine
from btgen.providers.mock_text import MockLLMProvider
from btgen.render.package import Package, write_package
from btgen.stages.text import narration, plan_episode, plan_series, publishing, revise_turkish
from btgen.state import Checkpoints, digest, run_lock, run_logger, write_text

STAGES = ("girdi", "plan", "anlatim_tr", "denetim", "anlatim_en", "metadata", "paket")


@dataclass(frozen=True)
class RunOptions:
    force: bool = False
    start: str | None = None


def run(
    input_path: Path,
    workspace: Workspace,
    options: RunOptions,
    report: Callable[[str], None],
    *,
    select: Callable[[str], LLMProvider] | None = None,
    destination: Path | None = None,
    text_key: object = "mock",
) -> list[Path]:
    if options.start is not None and options.start not in STAGES:
        raise BTError(f"Adım geçersiz. Seçenekler: {', '.join(STAGES)}.")
    episode = read_episode(input_path, workspace)
    target = destination or workspace.root / "runs" / slug(input_path.stem or "bolum")
    mock = MockLLMProvider()
    if select is None:

        def select(task: str) -> LLMProvider:
            return mock

    if episode.count == 1:
        _run_episode(workspace, episode, target, options, report, select, text_key)
        return [target]
    with run_lock(target), run_logger(target) as logger:
        engine = TextEngine(workspace, select, logger)
        series_checkpoint = Checkpoints(target, ("seri",), logger, report, force=options.force)
        series = series_checkpoint.step(
            "seri",
            _key(workspace, "seri", text_key, episode),
            SeriesPlan,
            lambda: plan_series(engine, episode),
        )
        write_text(
            target / "seri.md",
            "# Seri planı\n\n"
            + "\n\n".join(
                f"## {index}. {item.title}\n\n{item.topic} · {item.minutes:g} dk · {item.level}\n\n"
                f"{item.progression}"
                for index, item in enumerate(series.episodes, 1)
            )
            + "\n",
        )
        results = []
        for index, item in enumerate(series.episodes, 1):
            branch = target / f"{index:02}-{slug(item.title)[:72] or 'bolum'}"
            current = episode.model_copy(
                update={
                    "index": index,
                    "topic": item.topic,
                    "minutes": item.minutes,
                    "level": item.level,
                }
            )
            _run_episode(workspace, current, branch, options, report, select, text_key)
            results.append(branch)
        return results


def _key(workspace: Workspace, task: str, text_key: object, *inputs: object) -> str:
    return digest(1, workspace.source, workspace.prompt(task), text_key, *inputs)


def _run_episode(
    workspace: Workspace,
    episode: Episode,
    directory: Path,
    options: RunOptions,
    report: Callable[[str], None],
    select: Callable[[str], LLMProvider],
    text_key: object,
) -> None:
    with run_lock(directory), run_logger(directory) as logger:
        engine = TextEngine(workspace, select, logger)
        state = Checkpoints(
            directory, STAGES, logger, report, force=options.force, start=options.start
        )
        state.step("girdi", digest(episode), Episode, lambda: episode)
        plan = state.step(
            "plan",
            _key(workspace, "plan", text_key, episode),
            Plan,
            lambda: plan_episode(engine, episode),
        )
        if episode.has_narration:
            draft_key = _key(
                workspace, "anlatim_tr", text_key, episode, plan, workspace.prompt("podcast_tr")
            )
            draft = state.step(
                "anlatim_tr",
                draft_key,
                Narration,
                lambda: narration(engine, episode, plan, "tr", unvalidated=True),
            )
            tr = state.step(
                "denetim",
                digest(draft_key, draft),
                Narration,
                lambda: revise_turkish(engine, episode, plan, draft),
            )
            en = state.step(
                "anlatim_en",
                _key(
                    workspace,
                    "anlatim_en",
                    text_key,
                    episode,
                    plan,
                    tr,
                    workspace.prompt("podcast_en"),
                ),
                Narration,
                lambda: narration(engine, episode, plan, "en", tr),
            )
        else:
            for name in ("anlatim_tr", "denetim", "anlatim_en"):
                state.skip(name, "Ses manzarası")
            tr = en = Narration(sections=[])
        metadata = state.step(
            "metadata",
            _key(workspace, "metadata", text_key, episode, plan),
            Metadata,
            lambda: publishing(engine, episode, plan),
        )
        state.step(
            "paket",
            digest(1, episode, plan, tr, en, metadata),
            Package,
            lambda: write_package(directory, episode, plan, tr, en, metadata),
            files=lambda item: item.files,
        )
