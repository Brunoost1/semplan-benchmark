"""Load, hash, and render immutable prompt templates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from semplan.contracts import Approach, PromptMetadata
from semplan.errors import ErrorCode, ProjectError


@dataclass(frozen=True)
class RegisteredPrompt:
    metadata: PromptMetadata
    template: str
    sha256: str
    output_schema_sha256: str | None

    def render(self, variables: dict[str, Any]) -> str:
        try:
            return self.template.format(**variables)
        except KeyError as exc:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Prompt rendering variable is missing",
                detail={"prompt_id": self.metadata.prompt_id, "variable": str(exc)},
            ) from exc


class PromptRegistry:
    """In-memory index of checked-in prompt metadata and template hashes."""

    def __init__(self, prompts: dict[str, RegisteredPrompt]) -> None:
        self._prompts = prompts

    @classmethod
    def load(cls, root: Path) -> PromptRegistry:
        prompts: dict[str, RegisteredPrompt] = {}
        schema_root = root.parent / "schemas"
        for metadata_path in sorted(root.rglob("metadata.yaml")):
            try:
                raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
                metadata = PromptMetadata.model_validate(raw)
            except (OSError, ValidationError, TypeError) as exc:
                raise ProjectError(
                    ErrorCode.CFG_INVALID,
                    "Prompt metadata validation failed",
                    detail={"path": str(metadata_path), "reason": str(exc)},
                ) from exc
            template_path = metadata_path.parent / metadata.template_file
            try:
                template = template_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ProjectError(
                    ErrorCode.CFG_INVALID,
                    "Prompt template cannot be read",
                    detail={"path": str(template_path), "reason": str(exc)},
                ) from exc
            prompt_hash = "sha256:" + hashlib.sha256(template.encode("utf-8")).hexdigest()
            if metadata.prompt_id in prompts:
                raise ProjectError(
                    ErrorCode.CFG_INVALID,
                    "Duplicate prompt ID",
                    detail={"prompt_id": metadata.prompt_id},
                )
            schema_hash = _schema_hash(schema_root / metadata.expected_output_schema)
            prompts[metadata.prompt_id] = RegisteredPrompt(
                metadata,
                template,
                prompt_hash,
                schema_hash,
            )
        if not prompts:
            raise ProjectError(ErrorCode.CFG_INVALID, "No prompts are registered")
        return cls(prompts)

    def get(self, prompt_id: str) -> RegisteredPrompt:
        try:
            return self._prompts[prompt_id]
        except KeyError as exc:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Unknown prompt ID",
                detail={"prompt_id": prompt_id},
            ) from exc

    def for_approach(self, approach: Approach) -> RegisteredPrompt:
        matches = [
            prompt for prompt in self._prompts.values() if prompt.metadata.approach is approach
        ]
        if len(matches) != 1:
            raise ProjectError(
                ErrorCode.CFG_INVALID,
                "Expected exactly one prompt for approach",
                detail={"approach": approach.value, "matches": len(matches)},
            )
        return matches[0]


def _schema_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"
