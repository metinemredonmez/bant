from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel

Language = Literal["tr", "en"]


class BreathPolicy(BaseModel):
    maximum_hold_seconds: float
    fast_excluded_subcategories: list[str]
    timing_tolerance_seconds: float


class SleepPolicy(BaseModel):
    min_words_per_minute: int
    max_words_per_minute: int
    ending_seconds: int


class PublishPolicy(BaseModel):
    max_title_characters: int
    max_description_sentences: int
    tags: int
    min_checklist: int
    max_checklist: int


class PosePolicy(BaseModel):
    min_alignment: int
    max_alignment: int


class VideoPolicy(BaseModel):
    min_seconds: int
    max_seconds: int
    palette: str
    negative_prompt: str
    prohibited: dict[str, str]


class LanguagePolicy(BaseModel):
    pause: str
    abbreviations: list[str]
    medical_claims: list[str]
    stage_directions: list[str]
    introduction: str
    safety: dict[str, str]
    dizziness: str
    fast_breath: str
    hold_instruction: str
    negative_hold: str
    duration_words: dict[str, float]
    seconds: str
    minutes: str
    beginner_poses: str


class Policy(BaseModel):
    sources: list[str]
    breath: BreathPolicy
    sleep: SleepPolicy
    publish: PublishPolicy
    pose: PosePolicy
    audio_duration_tolerance: float
    video: VideoPolicy
    languages: dict[Language, LanguagePolicy]


@cache
def policy() -> Policy:
    return Policy.model_validate_json(files(__package__).joinpath("policy.json").read_text("utf-8"))


def video_policy() -> VideoPolicy:
    return policy().video
