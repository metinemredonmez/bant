# Blue Therapy içerik üretici

İlk sprintte bir bölüm ya da seri girdisinden Türkçe ve İngilizce anlatım, bölüm planı, YouTube bilgileri ve kapak istemleri üretilir. İçerik kuralları `prompts/sistem_prompt.md` dosyasından okunur. Deniz yer tutucu koçtur.

## Başlangıç

Python 3.12 ve `uv` ile:

```sh
uv sync
uv run btgen run girdiler/ornek.yaml --dry-run
uv run btgen lint runs/ornek
uv run btgen status runs/ornek
uv run btgen koc list
uv run ruff check
uv run pytest
```

Örnek yayın taslağı `runs/ornek/paket.md`, YouTube verisi ise `runs/ornek/youtube.json` konumunda oluşur. Her adım JSON çıktı ve girdi özeti bırakır. Aynı girdiler önbellekten okunur. `--from denetim` örneğin planı koruyup denetimden itibaren yeniden çalıştırır. `--force` bütün adımları yeniler.

İlk sprintin varsayılanı ağ erişimi gerektirmeyen yerel metin sağlayıcısıdır. Gemini ve Kaptan bağlantıları, seslendirme, ölçülmüş çekim listesi ve kurgu sonraki sprintlerin kapsamındadır.
