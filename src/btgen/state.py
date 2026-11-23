import hashlib
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from btgen.config import BTError


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, encoding="utf-8", delete=False
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, data: Any) -> None:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def digest(*parts: Any) -> str:
    def value(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(type(item).__name__)

    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=value).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class JsonLogs(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "zaman": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "seviye": record.levelname,
            "mesaj": record.getMessage(),
        }
        for key in ("response", "command", "stage"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


@contextmanager
def run_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".kilit").open("a") as handle:
        flock(handle, LOCK_EX)
        try:
            yield
        finally:
            flock(handle, LOCK_UN)


@contextmanager
def run_logger(directory: Path) -> Iterator[logging.Logger]:
    now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    logger = logging.getLogger(f"btgen.{uuid.uuid4().hex[:10]}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(
        directory / f"gunluk-{now}-{uuid.uuid4().hex[:8]}.jsonl", encoding="utf-8"
    )
    handler.setFormatter(JsonLogs())
    logger.addHandler(handler)
    try:
        yield logger
    finally:
        logger.removeHandler(handler)
        handler.close()


class Checkpoints:
    def __init__(
        self,
        directory: Path,
        stages: tuple[str, ...],
        logger: logging.Logger,
        report: Callable[[str], None],
        *,
        force: bool = False,
        start: str | None = None,
    ):
        self.directory = directory
        self.stages = stages
        self.logger = logger
        self.report = report
        self.status_path = directory / "durum.json"
        self.records = read_json(self.status_path) if self.status_path.exists() else {}
        if force or start:
            invalidated = stages[stages.index(start) :] if start else stages
            for name in invalidated:
                self.records.pop(name, None)
            write_json(self.status_path, self.records)

    def save(self, name: str, status: str, **values: Any) -> None:
        self.records[name] = {"durum": status, **values}
        write_json(self.status_path, self.records)

    def skip(self, name: str, note: str) -> None:
        self.save(name, "atlandı", notlar=note)

    def step[T: BaseModel](
        self,
        name: str,
        key: str,
        schema: type[T],
        action: Callable[[], T],
        *,
        files: Callable[[T], list[str]] | None = None,
        cost: Callable[[T], float | None] | None = None,
        cache: bool = True,
    ) -> T:
        record = self.records.get(name, {})
        path = self.directory / f"{name}.json"
        if cache and record.get("durum") == "tamam" and record.get("girdi_ozeti") == key:
            with suppress(OSError, ValidationError):
                result = schema.model_validate_json(path.read_bytes())
                existing = [path.name, *(files(result) if files else [])]
                hashes = {item: file_digest(self.directory / item) for item in existing}
                if None not in hashes.values() and hashes == record.get("dosyalar"):
                    self.report(f"{name} · önbellekten")
                    self.logger.info("%s önbellekten okundu", name)
                    return result
        clock = perf_counter()
        self.save(name, "çalışıyor", girdi_ozeti=key)
        self.report(f"{name} · çalışıyor")
        try:
            result = action()
            write_json(path, result)
            outputs = [path.name, *(files(result) if files else [])]
            hashes = {item: file_digest(self.directory / item) for item in outputs}
            if None in hashes.values():
                raise BTError(f"{name} adımında beklenen bir çıktı dosyası eksik.")
            self.save(
                name,
                "tamam",
                girdi_ozeti=key,
                dosyalar=hashes,
                sure=round(perf_counter() - clock, 3),
                maliyet=cost(result) if cost else 0,
            )
        except Exception as exc:
            self.save(
                name,
                "hata",
                girdi_ozeti=key,
                sure=round(perf_counter() - clock, 3),
                notlar=str(exc),
            )
            self.logger.error("%s başarısız: %s", name, exc)
            raise
        return result
