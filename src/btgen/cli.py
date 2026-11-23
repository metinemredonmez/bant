from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from btgen.config import BTError, Workspace
from btgen.lint import lint_metadata, lint_plan, lint_sleep
from btgen.models import Episode, Metadata, Narration, Plan
from btgen.pipeline import STAGES, RunOptions, run
from btgen.stages.text import _section_issues
from btgen.state import read_json

console = Console()
errors = Console(stderr=True)
app = typer.Typer(no_args_is_help=True, help="Blue Therapy içerik üreticisi.")
coach_app = typer.Typer(no_args_is_help=True, help="Koç listesi.")
app.add_typer(coach_app, name="koc")


def cli_errors[**P](action: Callable[P, None]) -> Callable[P, None]:
    @wraps(action)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            action(*args, **kwargs)
        except (OSError, ValueError, ValidationError, yaml.YAMLError, RuntimeError) as exc:
            errors.print(f"Hata — {exc}", markup=False)
            raise typer.Exit(1) from exc

    return wrapped


def report(message: str) -> None:
    console.print(message, markup=False)


@app.command("run", help="Girdiden bölüm veya seri paketi üret.")
@cli_errors
def run_command(
    input_path: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Bölüm YAML dosyası.")
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Ağ ve servis anahtarları kullanmadan çalıştır.")
    ] = False,
    start: Annotated[
        str | None, typer.Option("--from", help="Bu adımdan itibaren çıktıları yenile.")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Bütün adımları yeniden çalıştır.")
    ] = False,
) -> None:
    workspace = Workspace.find(input_path)
    report("Yerel deneme · metin üretimi" if dry_run else "Metin üretimi · yerel sağlayıcı")
    for target in run(input_path, workspace, RunOptions(force=force, start=start), report):
        report(f"Paket hazır · {target / 'paket.md'}")


@app.command("status", help="Her adımın durumunu, süresini ve maliyetini göster.")
@cli_errors
def status(
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Bölüm klasörü.")],
) -> None:
    records = read_json(directory / "durum.json")
    table = Table("Adım", "Durum", "Süre", "Tahmini maliyet", "Not")
    for name in dict.fromkeys((*STAGES, *records)):
        info = records.get(name, {})
        duration = f"{info['sure']:.2f} sn" if "sure" in info else "—"
        cost = info.get("maliyet")
        table.add_row(
            name,
            info.get("durum", "bekliyor"),
            duration,
            f"${cost:.4f}" if cost is not None else "—",
            info.get("notlar", ""),
        )
    console.print(table, markup=False)


@app.command("lint", help="Önceden üretilen bölümün içerik kurallarını denetle.")
@cli_errors
def lint(
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Bölüm klasörü.")],
) -> None:
    episode = Episode.model_validate(read_json(directory / "girdi.json"))
    plan = Plan.model_validate(read_json(directory / "plan.json"))
    metadata = Metadata.model_validate(read_json(directory / "metadata.json"))
    violations = [*lint_plan(episode, plan), *lint_metadata(metadata, episode=episode)]
    if episode.has_narration:
        tr = Narration.model_validate(read_json(directory / "denetim.json"))
        en = Narration.model_validate(read_json(directory / "anlatim_en.json"))
        for pod, tr_sections, en_sections in (
            (False, tr.sections, en.sections),
            (True, tr.podcast_sections, en.podcast_sections),
        ):
            if not pod or episode.has_podcast:
                if len(tr_sections) != len(plan.sections) or len(en_sections) != len(plan.sections):
                    raise BTError("Anlatım bölümleri planla eşleşmiyor.")
                for index, (tr_section, en_section) in enumerate(
                    zip(tr_sections, en_sections, strict=True)
                ):
                    violations.extend(
                        _section_issues(episode, plan, index, tr_section, "tr", None, pod)
                    )
                    violations.extend(
                        _section_issues(
                            episode, plan, index, en_section, "en", tr_section.text, pod
                        )
                    )
                if episode.category == "Uyku hikâyesi":
                    violations.extend(lint_sleep(plan, tr_sections, "tr"))
                    violations.extend(lint_sleep(plan, en_sections, "en"))
    if violations:
        for violation in violations:
            report(
                violation.model_dump_json()
                if hasattr(violation, "model_dump_json")
                else str(violation)
            )
        raise typer.Exit(1)
    report("Kural denetimi geçti.")


@coach_app.command("list", help="Kayıtlı koçları listele.")
@cli_errors
def list_coaches() -> None:
    for coach in Workspace.find(Path.cwd() / "config.yaml").coaches:
        label = " · yer tutucu" if coach.placeholder else ""
        report(f"{coach.name} · {coach.personality} · {coach.focus}{label}")
