import json
import logging
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from btgen.cli import app
from btgen.config import Workspace, read_episode
from btgen.models import NarrationSection
from btgen.providers.llm import TextEngine

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "config.yaml", tmp_path)
    for name in ("prompts", "koclar", "girdiler"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def test_package_is_bilingual_and_reusable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(workspace)
    result = runner.invoke(app, ["run", "girdiler/ornek.yaml", "--dry-run"])
    assert result.exit_code == 0, result.output
    run = workspace / "runs/ornek"
    package = (run / "paket.md").read_text()
    assert "Merhaba, ben Deniz." in package
    assert "Hi, I'm Deniz." in package
    assert "## 10. Kontrol listesi" in package
    assert len(json.loads((run / "youtube.json").read_text())["etiketler"]) == 10
    assert runner.invoke(app, ["lint", "runs/ornek"]).exit_code == 0
    old_plan = (run / "plan.json").stat().st_mtime_ns
    resumed = runner.invoke(app, ["run", "girdiler/ornek.yaml", "--dry-run", "--from", "denetim"])
    assert resumed.exit_code == 0, resumed.output
    assert "plan · önbellekten" in resumed.output
    assert "denetim · çalışıyor" in resumed.output
    assert old_plan == (run / "plan.json").stat().st_mtime_ns


def test_empty_input_uses_guide_and_placeholder(workspace: Path) -> None:
    source = workspace / "empty.yaml"
    source.write_text("kategori: Nefes\nalt_kategori: Hızlı\nsure_dk:\n")
    episode = read_episode(source, Workspace.find(source))
    assert episode.minutes == 1
    assert episode.coach.name == "Deniz"
    assert "süre 1 dk" in episode.defaults


def test_schema_errors_are_retried(workspace: Path) -> None:
    class WrongThenCorrect:
        calls = 0

        def generate(self, task: str, system: str, prompt: str, schema: dict, payload: dict) -> str:
            self.calls += 1
            if self.calls < 3:
                return '{"key":"giris"}'
            assert "Önceki JSON geçersiz" in prompt
            return '{"key":"giris","text":"Merhaba, ben Deniz."}'

    provider = WrongThenCorrect()
    engine = TextEngine(
        Workspace.find(workspace / "config.yaml"), lambda task: provider, logging.getLogger("test")
    )
    assert engine.generate("anlatim_tr", NarrationSection, {}).key == "giris"
    assert provider.calls == 3
