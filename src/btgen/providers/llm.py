import json
import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ValidationError

from btgen.config import BTError, Workspace


class LLMProvider(Protocol):
    def generate(self, task: str, system: str, prompt: str, schema: dict, payload: dict) -> str: ...


class TextEngine:
    def __init__(
        self, workspace: Workspace, select: Callable[[str], LLMProvider], logger: logging.Logger
    ):
        self.workspace = workspace
        self.select = select
        self.logger = logger

    def generate[T: BaseModel](
        self,
        task: str,
        schema: type[T],
        payload: dict,
        validate: Callable[[T], list[object]] | None = None,
        *,
        attempts: int = 3,
    ) -> T:
        provider = self.select(task)
        instructions = self.workspace.prompt(task)
        schema_json = schema.model_json_schema()
        request = f"{instructions}\n\nGirdi JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        errors = ""
        for _ in range(attempts):
            prompt = request
            if errors:
                prompt += f"\n\nÖnceki JSON geçersiz. Şu ihlalleri düzelt:\n{errors}"
            raw = provider.generate(task, self.workspace.source, prompt, schema_json, payload)
            try:
                result = schema.model_validate_json(raw)
                issues = validate(result) if validate else []
                if not issues:
                    return result
                errors = "\n".join(
                    issue.model_dump_json() if isinstance(issue, BaseModel) else str(issue)
                    for issue in issues
                )
            except ValidationError as exc:
                errors = str(exc)
            self.logger.warning(
                "%s adımının doğrulaması başarısız: %s",
                task,
                errors,
                extra={"response": raw[:16_000]},
            )
        raise BTError(f"{task} adımı doğrulamadan geçemedi. {errors}")
