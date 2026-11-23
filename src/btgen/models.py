from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SectionKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,48}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CharacterCard(Model):
    age_range: str
    hair: str
    clothing: str
    setting: str
    light: str


class Coach(Model):
    name: Nonempty
    gender: Nonempty
    personality: Nonempty
    focus: Nonempty
    voice_id: str = ""
    voice_settings: dict[str, float | bool] = Field(default_factory=dict)
    avatar: str = ""
    character: CharacterCard
    character_description: Nonempty
    placeholder: bool = False


class CoachInput(Model):
    ad: str | None = None
    cinsiyet: str | None = None
    kisilik: str | None = None
    odak: str | None = None
    elevenlabs_ses_kimligi: str | None = None
    avatar: str | None = None


class EpisodeInput(Model):
    kategori: str | None = None
    alt_kategori: str | None = None
    seviye: str | None = None
    sure_dk: float | None = Field(default=None, ge=1, le=60)
    konu: str | None = None
    bicim: str | None = None
    bolum_sayisi: int | None = Field(default=None, ge=1)
    koc: CoachInput | None = None


class Episode(Model):
    category: Nonempty
    subcategory: Nonempty
    level: Nonempty
    minutes: float = Field(ge=1, le=60)
    topic: Nonempty
    format: Nonempty
    coach: Coach
    count: int = Field(default=1, ge=1)
    index: int = Field(default=1, ge=1)
    defaults: list[str] = Field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return self.format in {"Video + anlatım", "Hepsi"}

    @property
    def has_narration(self) -> bool:
        return self.category != "Ses manzarası"

    @property
    def has_podcast(self) -> bool:
        return self.has_narration and (
            self.category == "Podcast" or self.format in {"Podcast", "Hepsi"}
        )

    @property
    def target_seconds(self) -> int:
        return round(self.minutes * 60)

    def for_model(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"coach": {"voice_id", "voice_settings", "avatar"}},
        )


class Pose(Model):
    name_tr: Nonempty
    sanskrit: str | None = None
    alignment: list[Nonempty]
    side: Literal["left", "right", "both"] = "both"
    seconds: int = Field(gt=0)


class BreathCue(Model):
    inhale: float = Field(gt=0)
    exhale: float = Field(gt=0)
    hold: float = Field(default=0, ge=0)
    repetitions: int = Field(default=1, ge=1)
    fast: bool = False


class PlanSection(Model):
    key: SectionKey
    title_tr: Nonempty
    title_en: Nonempty
    seconds: int = Field(gt=0)
    poses: list[Pose] = Field(default_factory=list)
    breaths: list[BreathCue] = Field(default_factory=list)


class Plan(Model):
    sections: list[PlanSection] = Field(min_length=1)
    equipment: list[Nonempty] = Field(default_factory=list)
    layers: list[Nonempty] = Field(default_factory=list)
    music_prompt: Nonempty

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if len({section.key for section in self.sections}) != len(self.sections):
            raise ValueError("Plan bölüm kimlikleri benzersiz olmalı.")
        return self


class NarrationSection(Model):
    key: SectionKey
    text: Nonempty


class Narration(Model):
    sections: list[NarrationSection]
    podcast_sections: list[NarrationSection] = Field(default_factory=list)


class Metadata(Model):
    title_tr: Nonempty
    title_en: Nonempty
    description_tr: Nonempty
    description_en: Nonempty
    youtube_title: Nonempty
    youtube_summary: Nonempty
    youtube_safety: Nonempty
    tags: list[Nonempty]
    cover_wide: Nonempty
    cover_square: Nonempty
    checklist: list[Nonempty]
    podcast_notes: list[Nonempty] = Field(default_factory=list)


class SeriesEpisode(Model):
    title: Nonempty
    topic: Nonempty
    minutes: float = Field(ge=1, le=60)
    level: Nonempty
    progression: Nonempty


class SeriesPlan(Model):
    episodes: list[SeriesEpisode] = Field(min_length=1)
