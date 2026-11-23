import json
from importlib.resources import files

from btgen.models import Episode, PlanSection

_SAMPLE = json.loads(files("btgen").joinpath("data/sample.json").read_text(encoding="utf-8"))

_MOVEMENT_WARNING_TR = (
    "Hamileysen, yakın zamanda ameliyat olduysan, bel, boyun ya da diz "
    "sorunun varsa doktoruna danış. Ağrı hissedersen dur."
)
_MOVEMENT_WARNING_EN = (
    "If you are pregnant, recently had surgery, or have lower back, neck, or knee "
    "problems, consult your doctor first. Stop if you feel pain."
)


class MockLLMProvider:
    def generate(self, task: str, system: str, prompt: str, schema: dict, payload: dict) -> str:
        if task == "cekimler":
            return json.dumps(
                {
                    "action": "Gentle natural motion in a restful scene.",
                    "camera": "Wide shot with a static camera.",
                }
            )
        episode = Episode.model_validate(payload["episode"])
        if task == "seri":
            result = {
                "episodes": [
                    {
                        "title": f"{episode.subcategory} · {index + 1}",
                        "topic": f"{episode.topic} · {index + 1}. bölüm",
                        "minutes": episode.minutes,
                        "level": episode.level,
                        "progression": "Önceki bölümün ritmini takip eden bağımsız çalışma.",
                    }
                    for index in range(episode.count)
                ]
            }
        elif task == "plan":
            result = self._plan(episode)
        elif task == "metadata":
            result = self._metadata(episode)
        else:
            section = PlanSection.model_validate(payload["section"])
            language = task[-2:]
            text = self._narration(episode, section, language)
            result = {"key": section.key, "text": text}
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _sample(episode: Episode) -> bool:
        return (
            episode.category == "Nefes"
            and episode.subcategory == "Sakinleş"
            and episode.target_seconds == 180
        )

    def _plan(self, episode: Episode) -> dict:
        length = episode.target_seconds
        if self._sample(episode):
            rows = [
                ("giris", "Giriş", "Intro", 20),
                ("yerlesme", "Yerleşme", "Settling", 25),
                ("ritim", "Ritim", "The Rhythm", 15),
                ("nefes", "Dört Altı Nefesi", "Four Six Breathing", 80),
                ("kapanis", "Kapanış", "Closing", 40),
            ]
        elif not episode.has_narration:
            rows = [("ambiyans", "Ses manzarası", "Soundscape", length)]
        else:
            opening = min(20, length // 4)
            closing = 180 if episode.category == "Uyku hikâyesi" and length >= 360 else opening
            rows = [
                ("giris", "Giriş", "Intro", opening),
                ("akis", "Akış", "Practice", length - opening - closing),
                ("kapanis", "Kapanış", "Closing", closing),
            ]
        sections = [
            {"key": key, "title_tr": tr, "title_en": en, "seconds": seconds}
            for key, tr, en, seconds in rows
        ]
        if self._sample(episode):
            sections[3]["breaths"] = [{"inhale": 4, "exhale": 6, "repetitions": 8}]
        elif episode.category == "Nefes":
            count, remainder = divmod(sections[1]["seconds"], 10)
            sections[1]["seconds"] -= remainder
            sections[2]["seconds"] += remainder
            sections[1]["breaths"] = [{"inhale": 4, "exhale": 6, "repetitions": count}]
        if episode.category in {"Yoga", "Pilates"}:
            sections[1]["poses"] = [
                {
                    "name_tr": "Rahat oturuş",
                    "sanskrit": "Sukhasana" if episode.category == "Yoga" else None,
                    "alignment": ["Omurganı uzat", "Omuzlarını gevşet"],
                    "side": "both",
                    "seconds": sections[1]["seconds"],
                }
            ]
        return {
            "sections": sections,
            "equipment": ["mat"] if episode.category in {"Yoga", "Pilates"} else [],
            "layers": [episode.subcategory] if not episode.has_narration else [],
            "music_prompt": "Soft instrumental ambient textures, no percussion or voices.",
        }

    def _narration(self, episode: Episode, section: PlanSection, language: str) -> str:
        if self._sample(episode):
            return _SAMPLE[language][section.key].replace("Deniz", episode.coach.name)
        tr = language == "tr"
        if section.breaths:
            cycle = "Nefes al. [duraklama 3 sn] Yavaşça ver. [duraklama 5 sn]"
            if not tr:
                cycle = "Breathe in. [pause 3 s] Breathe out. [pause 5 s]"
            return " ".join([cycle] * section.breaths[0].repetitions)
        if episode.category == "Uyku hikâyesi":
            sentences = (
                [
                    "Gölün kıyısındaki ağaçların gölgesi akşam ışığında suyun üzerinde usulca uzanıyor.",
                    "Bulutlar yavaşça geçiyor.",
                ]
                if tr
                else [
                    "The trees beside the lake cast long shadows across the still surface.",
                    "Clouds drift slowly.",
                ]
            )
            sentence = sentences[section.key == "kapanis"]
            intro = ""
            if section.key == "giris":
                intro = (
                    f"Merhaba, ben {episode.coach.name}. "
                    if tr
                    else f"Hi, I'm {episode.coach.name}. "
                )
            budget = round(section.seconds * 2) - len(intro.split())
            return intro + " ".join([sentence] * max(1, round(budget / len(sentence.split()))))
        if section.key == "giris":
            intro = (
                f"Merhaba, ben {episode.coach.name}." if tr else f"Hi, I'm {episode.coach.name}."
            )
            if episode.category in {"Yoga", "Pilates"}:
                warning = _MOVEMENT_WARNING_TR if tr else _MOVEMENT_WARNING_EN
                return f"{intro} {warning}"
            return f"{intro} Rahatça yerleş." if tr else f"{intro} Get comfortable."
        if section.key == "kapanis":
            return (
                "Kendine ayırdığın bu zaman için teşekkür ederim."
                if tr
                else "Thank you for taking this time for yourself."
            )
        if section.poses:
            name = "Sukhasana, rahat oturuş. " if episode.category == "Yoga" else "Rahat oturuş. "
            return (
                name + "Omurganı uzat. Omuzlarını gevşet."
                if tr
                else "Sit comfortably. Lengthen your spine. Soften your shoulders."
            )
        return (
            "Bakışını yumuşat. Nefesini fark et."
            if tr
            else "Soften your gaze. Notice your breathing."
        )

    def _metadata(self, episode: Episode) -> dict:
        title_tr = (
            "Üç Dakikada Sakinleş: Gün Ortası Nefes Molası"
            if self._sample(episode)
            else f"{episode.category}: {episode.subcategory}"
        )
        title_en = (
            "Calm in Three Minutes: A Midday Breathing Break"
            if self._sample(episode)
            else "A Moment of Stillness"
        )
        return {
            "title_tr": title_tr,
            "title_en": title_en,
            "description_tr": "Günün içinde kendine kısa bir alan aç.",
            "description_en": "Make a little room for yourself during your day.",
            "youtube_title": title_tr,
            "youtube_summary": "Günün içinde kendine zaman ayırabileceğin sakin bir bölüm.",
            "youtube_safety": (
                _MOVEMENT_WARNING_TR
                if episode.category in {"Yoga", "Pilates"}
                else "Rahatsızlık hissedersen dur, kendi ritmine dön."
            ),
            "tags": [
                episode.category,
                episode.subcategory,
                "kısa mola",
                "sakin zaman",
                "doğal ritim",
                "öz bakım",
                "dikkat",
                "gün içinde",
                "sessiz an",
                "başlangıç",
            ],
            "cover_wide": "16:9 wide view of a calm shore, soft blue water, white and sand tones. No text or logos.",
            "cover_square": "1:1 square view of a quiet shoreline in pale blue, white and sand. No text or logos.",
            "checklist": [
                "Koç adı, ses ve avatar eşleşiyor mu?",
                "Süre ve duraklamalar hedefe uyuyor mu?",
                "Türkçe ve İngilizce metin aynı anlama geliyor mu?",
            ],
            "podcast_notes": ["Bölümde kendi ritminde kısa bir mola ver."]
            if episode.has_podcast
            else [],
        }
