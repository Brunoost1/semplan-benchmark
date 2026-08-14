"""Validate JSON Schema placeholders and checked-in YAML schema versions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "schemas"
YAML_DIRS = [PROJECT_ROOT / "configs", PROJECT_ROOT / "catalog", PROJECT_ROOT / "prompts"]

EXPECTED_SCHEMAS = {
    "benchmark_manifest.schema.json",
    "budget_check.schema.json",
    "canonical_response.schema.json",
    "direct_sql.schema.json",
    "semantic_request.schema.json",
    "semantic_plan.schema.json",
    "benchmark_case.schema.json",
    "gold_answer.schema.json",
    "prompt_metadata.schema.json",
    "price_table.schema.json",
    "provider_request.schema.json",
    "provider_response.schema.json",
    "run_manifest.schema.json",
    "result_record.schema.json",
    "tool_agent_turn.schema.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return raw


def _check_json_schemas(errors: list[str]) -> None:
    schema_files = {path.name for path in SCHEMA_DIR.glob("*.schema.json")}
    missing = sorted(EXPECTED_SCHEMAS.difference(schema_files))
    extra = sorted(schema_files.difference(EXPECTED_SCHEMAS))
    if missing:
        errors.append(f"Missing schema files: {', '.join(missing)}")
    if extra:
        errors.append(f"Unexpected schema files: {', '.join(extra)}")

    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        try:
            schema = _read_json(path)
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"{path.relative_to(PROJECT_ROOT)} is not a valid JSON Schema: {exc}")
            continue

        if schema.get("additionalProperties") is not False:
            errors.append(
                f"{path.relative_to(PROJECT_ROOT)} must set additionalProperties to false"
            )
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"{path.relative_to(PROJECT_ROOT)} must define object properties")
            continue
        schema_version = properties.get("schema_version")
        if not isinstance(schema_version, dict) or schema_version.get("const") != "1.0":
            errors.append(f"{path.relative_to(PROJECT_ROOT)} must const schema_version to 1.0")


def _check_yaml_schema_versions(errors: list[str]) -> None:
    yaml_files: list[Path] = []
    for directory in YAML_DIRS:
        yaml_files.extend(directory.rglob("*.yaml"))
        yaml_files.extend(directory.rglob("*.yml"))

    for path in sorted(yaml_files):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.relative_to(PROJECT_ROOT)} is invalid YAML: {exc}")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{path.relative_to(PROJECT_ROOT)} must contain a YAML mapping")
            continue
        if raw.get("schema_version") != "1.0":
            errors.append(f"{path.relative_to(PROJECT_ROOT)} must set schema_version: '1.0'")


def main() -> int:
    errors: list[str] = []
    _check_json_schemas(errors)
    _check_yaml_schema_versions(errors)

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print("schema-check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
