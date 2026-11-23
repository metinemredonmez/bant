from collections.abc import Mapping
from pathlib import Path

from btgen.models import Episode, Metadata, Model, Narration, NarrationSection, Plan
from btgen.state import write_json, write_text


class Package(Model):
    files: list[str]


def timestamp(seconds: float) -> str:
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def plan_starts(plan: Plan) -> dict[str, float]:
    elapsed: float = 0
    starts = {}
    for section in plan.sections:
        starts[section.key] = elapsed
        elapsed += section.seconds
    return starts


def _script(
    plan: Plan, sections: list[NarrationSection], language: str, starts: Mapping[str, float]
) -> str:
    titles = {section.key: getattr(section, f"title_{language}") for section in plan.sections}
    if not sections:
        return "Bu bölüm ses manzarası olarak planlandı.\n"
    return "\n\n".join(
        f"### {timestamp(starts[section.key])} {titles[section.key]}\n\n{section.text}"
        for section in sections
    )


def write_package(
    directory: Path,
    episode: Episode,
    plan: Plan,
    tr: Narration,
    en: Narration,
    metadata: Metadata,
    *,
    tr_starts: Mapping[str, float] | None = None,
    en_starts: Mapping[str, float] | None = None,
    shots: list[dict] | None = None,
    media: list[str] | None = None,
) -> Package:
    predicted = plan_starts(plan)
    tr_starts = predicted | (tr_starts or {})
    en_starts = predicted | (en_starts or {})
    chapters = [f"{timestamp(tr_starts[s.key])} {s.title_tr}" for s in plan.sections]
    disclaimer = (
        "Evet (gerçekçi yapay zekâ kişi kullanıldığında)"
        if episode.has_video
        else "Görsel yüklenirse içeriğe göre işaretle"
    )
    settings = {
        "Görünürlük": "Liste dışı",
        "Kitle": "Çocuklar için yapılmadı",
        "Yerleştirmeye izin ver": "Açık",
        "Değiştirilmiş ya da yapay içerik": disclaimer,
    }
    description = (
        f"{metadata.youtube_summary}\n\nGüvenlik — {metadata.youtube_safety}\n\n"
        f"Bölümler\n{chr(10).join(chapters)}\n\nDaha fazlası Blue Therapy uygulamasında."
    )
    youtube = {
        "baslik": metadata.youtube_title,
        "aciklama": description,
        "etiketler": metadata.tags,
        "ayar_hatirlatmalari": settings,
    }
    body = []
    if episode.defaults:
        body.append("> Seçilen varsayılanlar ve yer tutucular — " + " · ".join(episode.defaults))
    body.extend(
        [
            f"# {metadata.title_tr}",
            f"## 1. Başlık\n\n- TR — {metadata.title_tr}\n- EN — {metadata.title_en}",
            f"## 2. Kısa açıklama\n\n- TR — {metadata.description_tr}\n- EN — {metadata.description_en}",
            "## 3. Bilgi satırı\n\n"
            f"{episode.category} · {episode.subcategory} · {episode.level} · {episode.minutes:g} dk · "
            f"Ekipman — {', '.join(plan.equipment) or 'yok'}",
            f"## 4. Anlatım metni (Türkçe)\n\n{_script(plan, tr.sections, 'tr', tr_starts)}",
            f"## 5. Anlatım metni (İngilizce)\n\n{_script(plan, en.sections, 'en', en_starts)}",
            f"## 6. Video planı\n\n{_video(episode, shots, plan.music_prompt)}",
            f"## 7. Podcast sürümü\n\n{_podcast(episode, plan, tr, en, metadata, predicted)}",
            f"## 8. YouTube bilgileri\n\n**{metadata.youtube_title}**\n\n{description}\n\n"
            f"Etiketler — {', '.join(metadata.tags)}\n\n"
            + "\n".join(f"- {label} — {value}" for label, value in settings.items()),
            "## 9. Kapak görseli istemleri\n\n"
            f"**16:9**\n\n```text\n{metadata.cover_wide}\n```\n\n"
            f"**1:1**\n\n```text\n{metadata.cover_square}\n```",
            "## 10. Kontrol listesi\n\n"
            + "\n".join(f"- [ ] {item}" for item in metadata.checklist),
        ]
    )
    if plan.layers:
        body[3] += "\n\nSes katmanları — " + ", ".join(plan.layers)
    if media:
        body[3] += "\n\n" + "\n".join(f"- [{item}]({item})" for item in media)
    write_text(directory / "paket.md", "\n\n".join(body) + "\n")
    write_json(directory / "youtube.json", youtube)
    return Package(files=["paket.md", "youtube.json"])


def _video(episode: Episode, shots: list[dict] | None, music: str) -> str:
    if not episode.has_video:
        return "Seçilen biçim yalnız ses içeriği içeriyor."
    coach = episode.coach
    character = coach.character
    body = [
        "### Karakter kartı",
        f"- Yaş aralığı — {character.age_range}\n- Saç — {character.hair}\n"
        f"- Kıyafet — {character.clothing}\n- Ortam — {character.setting}\n- Işık — {character.light}",
        f"```text\n{coach.character_description}\n```",
        f"Müzik — {music}",
    ]
    if shots is None:
        body.append("Çekim listesi ölçülmüş ses zamanları hazır olduğunda eklenir.")
        return "\n\n".join(body)
    for shot in shots:
        number = shot["number"]
        status = (
            f" · **ELLE KONTROL** — {shot['review_reason']}" if shot.get("review_reason") else ""
        )
        body.append(
            f"**{number:02} · {timestamp(shot['start'])}–{timestamp(shot['end'])} · "
            f"{shot.get('reference', '')}**{status}\n\n```text\n{shot['prompt']}\n```"
        )
    return "\n\n".join(body)


def _podcast(
    episode: Episode,
    plan: Plan,
    tr: Narration,
    en: Narration,
    metadata: Metadata,
    starts: Mapping[str, float],
) -> str:
    if not episode.has_podcast:
        return "Podcast için bölüm istendiğinde bu alana anlatım ve bölüm notları eklenir."
    notes = "\n".join(f"- {note}" for note in metadata.podcast_notes)
    return (
        f"### Türkçe\n\n{_script(plan, tr.podcast_sections, 'tr', starts)}\n\n"
        f"### İngilizce\n\n{_script(plan, en.podcast_sections, 'en', starts)}\n\n"
        f"### Bölüm notları\n\n{notes}"
    )
