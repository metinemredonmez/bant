import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import Field

from btgen.models import Coach, CoachInput, Episode, EpisodeInput, Model


class BTError(RuntimeError):
    """Komut satırında gösterilecek hata."""


class Settings(Model):
    default_coach: str = "deniz"
    routes: dict[str, str] = Field(default_factory=dict)
    providers: dict[str, dict[str, str | float | int | bool | None]] = Field(default_factory=dict)
    music: str = "mock"
    sleep_tail_seconds: int = Field(default=20, ge=0)
    music_gain_db: float = Field(default=-24, le=0)


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise BTError(f"{path.name} dosyasında bir YAML nesnesi bekleniyor.")
    return {key: None if value == "" else value for key, value in data.items()}


def slug(value: str) -> str:
    value = value.casefold().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


@dataclass(frozen=True)
class Workspace:
    root: Path
    settings: Settings
    source: str

    @classmethod
    def find(cls, path: Path) -> "Workspace":
        candidates = (*path.resolve().parents, *Path.cwd().resolve().parents, Path.cwd())
        for root in dict.fromkeys(candidates):
            config = root / "config.yaml"
            source = root / "prompts/sistem_prompt.md"
            if config.is_file() and source.is_file():
                return cls(root, Settings.model_validate(read_yaml(config)), source.read_text())
        raise BTError("config.yaml ve prompts/sistem_prompt.md bulunan çalışma klasörünü aç.")

    @property
    def coaches(self) -> list[Coach]:
        return [
            Coach.model_validate(yaml.safe_load(path.read_text()))
            for path in sorted((self.root / "koclar").glob("*.yaml"))
        ]

    def secret(self, name: str) -> str:
        return dotenv_values(self.root / ".env", interpolate=False).get(name) or ""

    def prompt(self, name: str) -> str:
        return (self.root / "prompts" / f"{name}.md").read_text(encoding="utf-8")


class ContentGuide:
    def __init__(self, source: str):
        category_block = source.split("KATEGORİLER VE ALT KATEGORİLER", 1)[1]
        category_block = category_block.split("ÖNERİLEN SÜRELER", 1)[0]
        self.categories: dict[str, list[str]] = {}
        for line in category_block.splitlines():
            if line.startswith("- "):
                category, raw_values = line[2:].rstrip(".").split(": ", 1)
                values = [re.sub(r" \(\d[^)]*\)", "", entry) for entry in raw_values.split(", ")]
                self.categories[category] = values
        self.levels = self._options(source, "SEVİYE")
        self.formats = self._options(source, "BİÇİM")
        recommendations = source.split("ÖNERİLEN SÜRELER", 1)[1]
        recommendations = recommendations.split("GÜVENLİK VE DİL KURALLARI", 1)[0]
        self.durations = self._durations(recommendations)
        self.subcategory_durations: dict[tuple[str, str], float] = {}
        for match in re.finditer(r"([\w\s]+) \((\d+)[–-](\d+) dakika\)", category_block):
            category, _, remainder = match.group(1).strip().rpartition(":")
            subcategory = remainder.strip() if category else match.group(1).strip()
            for item, values in self.categories.items():
                if subcategory in values:
                    self.subcategory_durations[item, subcategory] = float(match.group(2))

    @staticmethod
    def _options(source: str, name: str) -> list[str]:
        expression = rf"(?m)^- {name}: \(([^\n()]+)\)"
        match = re.search(expression, source)
        if match is None:
            raise BTError(f"Sistem kurallarında {name} seçenekleri bulunamadı.")
        return match.group(1).split(" / ")

    def _durations(self, recommendations: str) -> dict[str, float]:
        durations: dict[str, float] = {}
        for match in re.finditer(r"([^\n.]+?)\s+(\d+[\d /–-]*)\s*dk\.", recommendations):
            label, raw = (part.strip() for part in match.groups())
            values = [int(value) for value in re.findall(r"\d+", raw)]
            if "–" in raw or "-" in raw:
                preferred = sum(values) / len(values)
            else:
                preferred = float(values[0])
            for category in self.categories:
                if category.casefold() in label.casefold():
                    durations[category] = preferred
        return durations

    @staticmethod
    def choose(value: str | None, options: list[str], label: str) -> str:
        normalized = slug(value or "")
        for option in options:
            alias = option.partition(" (")[0]
            if normalized and normalized in {slug(option), slug(alias)}:
                return option
        if value and value.strip():
            raise BTError(f"{label} geçersiz ({value}). Seçenekler: {', '.join(options)}.")
        return options[0]


def read_episode(path: Path, workspace: Workspace) -> Episode:
    raw = read_yaml(path)
    if isinstance(raw.get("koc"), str):
        raw["koc"] = {"ad": raw["koc"]}
    fields = EpisodeInput.model_validate(raw)
    guide = ContentGuide(workspace.source)
    category = guide.choose(fields.kategori or "Nefes", list(guide.categories), "Kategori")
    subcategory = guide.choose(fields.alt_kategori, guide.categories[category], "Alt kategori")
    level = guide.choose(fields.seviye, guide.levels, "Seviye")
    default_format = guide.formats[1] if category == "Ses manzarası" else guide.formats[0]
    if category == "Podcast":
        default_format = guide.formats[2]
    media_format = guide.choose(fields.bicim or default_format, guide.formats, "Biçim")
    minutes = fields.sure_dk or guide.subcategory_durations.get(
        (category, subcategory), guide.durations[category]
    )
    topic = (fields.konu or "").strip() or subcategory
    count = fields.bolum_sayisi or 1
    coach_input = fields.koc or CoachInput()
    coach_name = coach_input.ad or workspace.settings.default_coach
    coach = next((item for item in workspace.coaches if slug(item.name) == slug(coach_name)), None)
    if coach is None:
        raise BTError(f"Koç kaydı bulunamadı: {coach_name}. Koç dosyasını koclar/ altına ekle.")
    coach = Coach.model_validate(
        coach.model_dump()
        | {
            output: value.strip()
            for output, value in (
                ("gender", coach_input.cinsiyet),
                ("personality", coach_input.kisilik),
                ("focus", coach_input.odak),
                ("voice_id", coach_input.elevenlabs_ses_kimligi),
                ("avatar", coach_input.avatar),
            )
            if value and value.strip()
        }
    )
    choices = [
        f"{label} {value}"
        for provided, label, value in (
            (fields.kategori, "kategori", category),
            (fields.alt_kategori, "alt kategori", subcategory),
            (fields.seviye, "seviye", level),
            (fields.bicim, "biçim", media_format),
            (fields.sure_dk, "süre", f"{minutes:g} dk"),
            (fields.konu, "konu", topic),
            (fields.bolum_sayisi, "bölüm sayısı", count),
            (coach_input.ad, "koç", coach.name),
        )
        if provided is None or (isinstance(provided, str) and not provided.strip())
    ]
    if coach.placeholder:
        choices.append(f"koç {coach.name} yer tutucu")
    return Episode(
        category=category,
        subcategory=subcategory,
        level=level,
        minutes=minutes,
        topic=topic,
        format=media_format,
        coach=coach,
        count=count,
        defaults=choices,
    )
