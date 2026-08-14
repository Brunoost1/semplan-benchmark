"""YAML configuration loading with explicit file precedence."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from semplan.config.models import RootConfig
from semplan.errors import ErrorCode, ProjectError


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            f"Cannot read config file: {path}",
            detail={"path": str(path), "reason": str(exc)},
        ) from exc

    if not isinstance(raw, dict):
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            f"Config file must contain a mapping: {path}",
            detail={"path": str(path)},
        )
    if raw.get("schema_version") != "1.0":
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            f"Config file has unsupported schema_version: {path}",
            detail={"path": str(path), "schema_version": raw.get("schema_version")},
        )
    return raw


def _deep_merge(base: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config_files(paths: Sequence[Path]) -> RootConfig:
    """Load and validate config files in ascending precedence order."""

    if not paths:
        raise ProjectError(ErrorCode.CFG_INVALID, "At least one config path is required")

    merged: dict[str, Any] = {}
    for path in paths:
        merged = _deep_merge(merged, _read_yaml(path))

    try:
        return RootConfig.model_validate(merged)
    except ValidationError as exc:
        raise ProjectError(
            ErrorCode.CFG_INVALID,
            "Resolved configuration is invalid",
            detail={"errors": exc.errors(include_url=False)},
        ) from exc


def load_config(path: Path) -> RootConfig:
    """Load one complete configuration file."""

    return load_config_files([path])
