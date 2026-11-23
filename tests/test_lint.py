from types import SimpleNamespace

import pytest

from btgen.lint import (
    lint_audio,
    lint_plan,
    lint_section,
    lint_shot_direction,
    lint_shots,
    lint_sleep,
    pauses,
    video_policy,
)
from btgen.lint.content import lint_checklist, lint_description, lint_tags, lint_title
from btgen.lint.text import (
    lint_abbreviations,
    lint_brackets,
    lint_emoji,
    lint_hold_duration,
    lint_introduction,
    lint_medical_claims,
    lint_numerals,
    lint_parentheses,
    lint_safety_intro,
    lint_stage_directions,
)
from btgen.models import (
    BreathCue,
    CharacterCard,
    Coach,
    Episode,
    NarrationSection,
    Plan,
    PlanSection,
    Pose,
)


def episode(category: str = "Nefes", subcategory: str = "Sakinleş", minutes: float = 3) -> Episode:
    return Episode(
        category=category,
        subcategory=subcategory,
        level="Başlangıç",
        minutes=minutes,
        topic="Gün ortasında mola",
        format="Video + anlatım",
        coach=Coach(
            name="Deniz",
            gender="kadın",
            personality="sakin",
            focus="nefes",
            character=CharacterCard(
                age_range="30–35", hair="Koyu", clothing="Mavi", setting="Stüdyo", light="Gün ışığı"
            ),
            character_description="A coach in a light blue top.",
        ),
    )


def section(seconds: int = 180, key: str = "s1", **kwargs: object) -> PlanSection:
    return PlanSection.model_validate(
        {"key": key, "seconds": seconds, "title_tr": "Giriş", "title_en": "Intro", **kwargs}
    )


def plan(*sections: PlanSection) -> Plan:
    return Plan(sections=list(sections), music_prompt="Soft ambient tones, no percussion.")


def rule_codes(issues: list) -> set[str]:
    return {issue.rule for issue in issues}


@pytest.mark.parametrize(
    ("check", "good", "bad"),
    [
        (lint_numerals, "Dört saniye ver. [duraklama 8 sn]", "Nefesini 4 saniye ver."),
        (lint_abbreviations, "Yavaşça al ve ver.", "Örn. burundan al, vb. TTS kullan."),
        (lint_brackets, "Omuzlarını indir. [duraklama 2 sn]", "Omuzlarını indir. [gülme]"),
        (
            lint_stage_directions,
            "Gözlerini yavaşça kapat.",
            "Fısıldayarak söyle. Yavaşça nefes al.",
        ),
        (
            lint_medical_claims,
            "Gevşemeni destekleyebilir.",
            "Kaygını tedavi edecek ve ağrını geçirir.",
        ),
        (
            lint_hold_duration,
            "Nefesini dört saniye tut. [duraklama 4 sn]",
            "Nefesini altı saniye tut.",
        ),
        (lint_hold_duration, "Nefesini tutma. [duraklama 8 sn]", "Nefesini tut. [duraklama 8 sn]"),
    ],
)
def test_turkish_spoken_rules(check, good: str, bad: str) -> None:
    assert not check(good, "s1", "tr")
    issues = check(bad, "s1", "tr")
    assert issues
    assert all(issue.section == "s1" and issue.fragment and issue.suggestion for issue in issues)


@pytest.mark.parametrize(
    ("check", "good", "bad"),
    [
        (lint_numerals, "Breathe out. [pause 4 s]", "Count to 4."),
        (lint_abbreviations, "Breathe out for four seconds.", "Exhale for four sec."),
        (lint_brackets, "Let go. [pause 4 s]", "Let go. [duraklama 4 sn]"),
        (lint_stage_directions, "Keep your eyes closed.", "Whisper: keep your eyes closed."),
        (lint_medical_claims, "This may help you ease into the evening.", "This cures asthma."),
        (
            lint_hold_duration,
            "Hold your breath for four seconds.",
            "Hold your breath for half a minute.",
        ),
        (
            lint_hold_duration,
            "Do not hold your breath. [pause 6 s]",
            "Hold your breath. [pause 6 s]",
        ),
    ],
)
def test_english_spoken_rules(check, good: str, bad: str) -> None:
    assert not check(good, "s1", "en")
    assert check(bad, "s1", "en")


@pytest.mark.parametrize(
    ("check", "bad"), [(lint_emoji, "🧘 Nefes al."), (lint_parentheses, "Nefes al (derin).")]
)
def test_forbidden_punctuation(check, bad: str) -> None:
    assert not check("Nefes al.", "s1")
    assert check(bad, "s1")


@pytest.mark.parametrize(
    ("language", "good", "bad"),
    [
        ("tr", "Merhaba, ben Deniz. Yavaşlayalım.", "Günaydın. Merhaba, ben Deniz."),
        ("en", "Hi, I’m Deniz. Let's slow down.", "Hello. I am Deniz."),
    ],
)
def test_one_sentence_coach_introduction(language: str, good: str, bad: str) -> None:
    assert not lint_introduction(good, "s1", language, "Deniz", first=True)
    assert lint_introduction(bad, "s1", language, "Deniz", first=True)
    assert lint_introduction(good, "s2", language, "Deniz", first=False)


@pytest.mark.parametrize(
    ("language", "warning"),
    [
        (
            "tr",
            "Hamileysen, yakın zamanda ameliyat olduysan ya da bel, boyun veya diz sorunun varsa önce "
            "doktoruna danış. Ağrı hissedersen dur.",
        ),
        (
            "en",
            "If you are pregnant, have had recent surgery, or have a lower back, neck or knee issue, "
            "consult your doctor. Stop if you feel pain.",
        ),
    ],
)
def test_movement_safety(language: str, warning: str) -> None:
    assert not lint_safety_intro(warning, "s1", language)
    assert (
        lint_safety_intro(warning.split(". ")[0], "s1", language)[0].fragment
        == "ağrı durumunda durma"
    )


def test_plan_breath_time_and_fast_rules() -> None:
    safe = BreathCue(inhale=4, exhale=6, hold=4)
    assert not lint_plan(episode(), plan(section(breaths=[safe])))
    risky = BreathCue(inhale=1, exhale=1, hold=5, fast=True)
    issues = lint_plan(episode(subcategory="AKŞAM"), plan(section(120, breaths=[risky])))
    assert rule_codes(issues) == {"breath_hold", "fast_breath", "plan_duration"}
    assert rule_codes(lint_plan(episode(), plan(section()))) == {"breath_plan"}


def test_breath_narration_dizziness_and_pause_alignment() -> None:
    fast = section(breaths=[BreathCue(inhale=1, exhale=1, fast=True)])
    text = NarrationSection(key="s1", text="Merhaba, ben Deniz. Hızlı nefes al. [duraklama 3 sn]")
    correct = text.model_copy(
        update={"text": text.text + " Başın dönerse dur ve kendi nefesine dön."}
    )
    assert not lint_section(
        episode(), fast, correct, "tr", first=True, require_fast_warning=True, expected_pauses=[3]
    )
    assert pauses(text.text, "tr") == [3]
    assert rule_codes(
        lint_section(
            episode(), fast, text, "tr", first=True, require_fast_warning=True, expected_pauses=[4]
        )
    ) == {
        "dizziness_warning",
        "pause_match",
    }
    assert rule_codes(
        lint_section(episode(subcategory="Kaygı"), fast, correct, "tr", first=True)
    ) == {"fast_breath"}


def test_movement_pose_balance_alignment_and_beginner_poses() -> None:
    left = Pose(
        name_tr="Ağaç duruşu",
        sanskrit="Vrksasana",
        alignment=["Omuzlarını indir", "Belini uzat"],
        side="left",
        seconds=15,
    )
    right = left.model_copy(update={"side": "right"})
    yoga = episode("Yoga", "denge", 10)
    assert not lint_plan(yoga, plan(section(600, poses=[left, right])))
    headstand = Pose(name_tr="Baş duruşu", alignment=["Gevşe"], seconds=20)
    right = right.model_copy(update={"seconds": 30})
    issues = lint_plan(yoga, plan(section(600, poses=[left, right, headstand])))
    assert rule_codes(issues) == {"pose_balance", "pose_alignment", "beginner_pose"}
    assert rule_codes(lint_plan(yoga, plan(section(600)))) == {"pose_plan"}


def test_sleep_spoken_tempo_and_shorter_ending() -> None:
    outline = plan(section(60, "earlier"), section(180, "ending"))
    short = "Uzakta göl sessizce ışığı yansıtıyor. "
    long = "Serin kıyıda suyun sesi uzayan gölgelerin arasından geçip birlikte ilerliyor. "
    start = NarrationSection(key="earlier", text=long * 12)
    finish = NarrationSection(key="ending", text=(short + "[duraklama 1 sn] ") * 72)
    assert not lint_sleep(outline, [start, finish], "tr")
    long_finish = finish.model_copy(update={"text": long * 36})
    assert rule_codes(lint_sleep(outline, [start, long_finish], "tr")) == {"sleep_ending"}
    slow = finish.model_copy(update={"text": short})
    assert rule_codes(lint_sleep(outline, [start, slow], "tr")) == {"sleep_tempo"}


def test_publish_title_description_tags_and_checklist() -> None:
    assert not lint_title("A" * 60, "TR")
    assert lint_title("A" * 61, "TR")
    assert not lint_description("Kısaca dur. Ardından nefesini yavaşlat.", "tr")
    assert lint_description("Kısaca dur. Sonra dinle. Dikkatini toparla.", "tr")
    assert not lint_tags([f"etiket {index}" for index in range(10)])
    assert lint_tags(["etiket"] * 10)
    assert not lint_checklist(["Ses", "Görüntü", "Alt yazı"])
    assert lint_checklist(["Ses"])


@pytest.mark.parametrize(
    ("action", "camera", "rule"),
    [
        ("Her fingers touch gently.", "A tight shot of her fingers.", "video_close_up"),
        ("A mirror reflects the coach.", "A wide shot.", "video_mirror"),
        ("Text appears beside the coach.", "A wide shot.", "video_on_screen_text"),
        ("The coach stays seated.", "Quick pan left.", "video_fast_motion"),
    ],
)
def test_video_restrictions(action: str, camera: str, rule: str) -> None:
    assert not lint_shot_direction(
        "Gentle waves touch the shore. No mirrors.", "Static wide shot.", "s1"
    )
    assert rule in rule_codes(lint_shot_direction(action, camera, "s1"))


def test_video_character_and_shot_timeline() -> None:
    character = "A coach wearing a light blue top."
    action, camera = "The coach sits quietly.", "A still wide shot."
    shot = SimpleNamespace(
        number=1,
        start=0,
        end=7,
        section="s1",
        action=action,
        camera=camera,
        has_person=True,
        prompt=f"{character} {action} {camera} {video_policy().negative_prompt}",
    )
    assert not lint_shots([shot], character)
    broken = SimpleNamespace(
        **{**vars(shot), "number": 2, "start": 8, "end": 12, "prompt": character.lower()}
    )
    assert rule_codes(lint_shots([shot, broken], character)) == {
        "video_character",
        "video_timeline",
        "video_duration",
    }


def test_measured_audio_duration_and_breath_timing() -> None:
    breath = SimpleNamespace(section="s1", phase="inhale", start=10, end=14.5, target_seconds=4)
    good = SimpleNamespace(languages={"tr": SimpleNamespace(seconds=198, breaths=[breath])})
    assert not lint_audio(episode(), good)
    breath.end = 14.51
    bad = SimpleNamespace(languages={"tr": SimpleNamespace(seconds=198.01, breaths=[breath])})
    assert rule_codes(lint_audio(episode(), bad)) == {"audio_duration", "audio_breath_timing"}
    assert rule_codes(lint_audio(episode(), bad, "en")) == {"audio_timing_missing"}
