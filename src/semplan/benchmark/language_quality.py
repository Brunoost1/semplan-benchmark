"""Automated bilingual surface-quality checks for benchmark utterances."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from semplan.benchmark.validator import load_benchmark_cases
from semplan.contracts import BenchmarkCase, DatasetSplit, Locale
from semplan.data_generation.writer import canonical_json

FORBIDDEN_PT_BR_TERMS: tuple[tuple[str, str], ...] = (
    ("net revenue", "metric"),
    ("gross revenue", "metric"),
    ("contribution margin percent", "metric"),
    ("contribution margin pct", "metric"),
    ("contribution margin", "metric"),
    ("order count", "metric"),
    ("average order value", "metric"),
    ("active customer count", "metric"),
    ("budget variance percent", "metric"),
    ("budget variance pct", "metric"),
    ("budget variance", "metric"),
    ("expense amount", "metric"),
    ("budget amount", "metric"),
    ("active contract value", "metric"),
    ("customer segment", "dimension"),
    ("payment method", "dimension"),
    ("cost center", "dimension"),
    ("expense category", "dimension"),
    ("contract risk", "dimension"),
    ("by quarter", "template_fragment"),
    ("by month", "template_fragment"),
    ("por quarter", "template_fragment"),
    ("por month", "template_fragment"),
    ("quarter", "dimension"),
    ("month", "dimension"),
    ("region", "dimension"),
    ("channel", "dimension"),
    ("department", "dimension"),
    ("North", "enum_value"),
    ("South", "enum_value"),
    ("East", "enum_value"),
    ("West", "enum_value"),
    ("consumer", "enum_value"),
    ("small_business", "enum_value"),
    ("mid_market", "enum_value"),
    ("enterprise", "enum_value"),
    ("retail", "enum_value"),
    ("wholesale", "enum_value"),
    ("partner", "enum_value"),
    ("card", "enum_value"),
    ("wallet", "enum_value"),
    ("bank_transfer", "enum_value"),
    ("invoice", "enum_value"),
)

FORBIDDEN_PT_BR_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"(?<![A-Za-z0-9_])department_[0-9]{2}(?![A-Za-z0-9_])", "opaque_value", "department_*"),
    (r"(?<![A-Za-z0-9_])expense_[0-9]{2}(?![A-Za-z0-9_])", "opaque_value", "expense_*"),
    (r"(?<![A-Za-z0-9_])category_[0-9]{2}(?![A-Za-z0-9_])", "opaque_value", "category_*"),
)

INTENTIONAL_PT_BR_LOANWORDS_AND_OPAQUE_TOKENS = {
    "Northstar": "fictional benchmark brand; not the North region enum",
    "online": "common pt-BR analytics/channel loanword",
    "mobile": "common pt-BR channel loanword",
    "marketplace": "common pt-BR commerce loanword",
    "voucher": "common pt-BR payment loanword",
    "cc_###": "opaque synthetic cost-center code with stable canonical meaning",
    "SQL keywords/table names in adversarial prompts": "intentional safety payload surface",
}

SCIENTIFIC_EVALUATION_SPLITS = {
    DatasetSplit.TEST_PUBLIC,
    DatasetSplit.TEST_HIDDEN,
    DatasetSplit.MULTI_TURN,
    DatasetSplit.ADVERSARIAL,
}


def audit_benchmark_language_quality(benchmark_dir: Path) -> dict[str, Any]:
    return audit_cases_language_quality(load_benchmark_cases(benchmark_dir))


def audit_cases_language_quality(cases: list[BenchmarkCase]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    affected_case_ids: set[str] = set()
    term_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()

    pt_cases = [case for case in cases if case.language is Locale.PT_BR]
    en_cases = [case for case in cases if case.language is Locale.EN_US]
    for case in pt_cases:
        text = case.utterance
        for term, category in FORBIDDEN_PT_BR_TERMS:
            matches = _term_pattern(term).findall(text)
            if not matches:
                continue
            affected_case_ids.add(case.case_id)
            term_counts[term] += len(matches)
            category_counts[category] += len(matches)
            findings.append(
                {
                    "severity": "error",
                    "code": f"unlocalized_pt_br_{category}",
                    "case_id": case.case_id,
                    "term": term,
                    "utterance": text,
                }
            )
        for pattern, category, label in FORBIDDEN_PT_BR_PATTERNS:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            if not matches:
                continue
            affected_case_ids.add(case.case_id)
            term_counts[label] += len(matches)
            category_counts[category] += len(matches)
            findings.append(
                {
                    "severity": "error",
                    "code": f"unlocalized_pt_br_{category}",
                    "case_id": case.case_id,
                    "term": label,
                    "utterance": text,
                }
            )

    pair_report = bilingual_pair_report(cases)
    findings.extend(pair_report["findings"])
    errors = sum(1 for finding in findings if finding["severity"] == "error")
    return {
        "schema_version": "1.0",
        "status": "passed" if errors == 0 else "failed",
        "case_count": len(cases),
        "pt_br_case_count": len(pt_cases),
        "en_us_case_count": len(en_cases),
        "affected_pt_br_case_count": len(affected_case_ids),
        "affected_terms": dict(sorted(term_counts.items())),
        "affected_categories": dict(sorted(category_counts.items())),
        "allowlist": INTENTIONAL_PT_BR_LOANWORDS_AND_OPAQUE_TOKENS,
        "bilingual_equivalence": pair_report,
        "summary": {"errors": errors, "warnings": 0},
        "findings": findings,
    }


def validate_benchmark_language_quality(
    benchmark_dir: Path,
    *,
    write_report: bool = True,
) -> dict[str, Any]:
    report = audit_benchmark_language_quality(benchmark_dir)
    if write_report:
        _write_language_quality_report(benchmark_dir, report)
    if report["summary"]["errors"]:
        raise ValueError(
            f"Benchmark language-quality validation failed with "
            f"{report['summary']['errors']} error(s)"
        )
    return report


def bilingual_pair_report(cases: list[BenchmarkCase]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    groups: dict[tuple[DatasetSplit, int], list[BenchmarkCase]] = {}
    for case in cases:
        ordinal = int(case.case_id.rsplit("-", 1)[1])
        groups.setdefault((case.split, (ordinal + 1) // 2), []).append(case)

    for (_split, _pair_index), pair in groups.items():
        languages = Counter(case.language for case in pair)
        if languages != {Locale.EN_US: 1, Locale.PT_BR: 1}:
            findings.append(
                {
                    "severity": "error",
                    "code": "bilingual_pair_language_mismatch",
                    "case_id": ",".join(sorted(case.case_id for case in pair)),
                    "term": "pair",
                    "utterance": "",
                }
            )
            continue
        en_case = next(case for case in pair if case.language is Locale.EN_US)
        pt_case = next(case for case in pair if case.language is Locale.PT_BR)
        comparable_fields = (
            "split",
            "expected_operation",
            "intent",
            "difficulty",
            "requires_clarification",
            "expected_policy",
            "tags",
            "template_family",
            "semantic_fingerprint",
        )
        for field in comparable_fields:
            if getattr(en_case, field) != getattr(pt_case, field):
                findings.append(
                    {
                        "severity": "error",
                        "code": f"bilingual_pair_{field}_mismatch",
                        "case_id": f"{en_case.case_id},{pt_case.case_id}",
                        "term": field,
                        "utterance": "",
                    }
                )

    errors = sum(1 for finding in findings if finding["severity"] == "error")
    return {
        "status": "passed" if errors == 0 else "failed",
        "pair_count": len(groups),
        "errors": errors,
        "findings": findings,
    }


def scientific_evaluation_case_count(cases: list[BenchmarkCase]) -> int:
    return sum(1 for case in cases if case.split in SCIENTIFIC_EVALUATION_SPLITS)


def _write_language_quality_report(benchmark_dir: Path, report: dict[str, Any]) -> None:
    review_dir = benchmark_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "language_quality_report.json").write_text(
        canonical_json(report) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Language Quality Report",
        "",
        f"Status: {report['status']}",
        f"Cases: {report['case_count']}",
        f"pt-BR cases: {report['pt_br_case_count']}",
        f"en-US cases: {report['en_us_case_count']}",
        f"Affected pt-BR cases: {report['affected_pt_br_case_count']}",
        f"Errors: {report['summary']['errors']}",
        "",
        "## Affected Terms",
        "",
    ]
    if report["affected_terms"]:
        for term, count in sorted(report["affected_terms"].items()):
            lines.append(f"- `{term}`: {count}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Allowlist", ""])
    for token, reason in sorted(report["allowlist"].items()):
        lines.append(f"- `{token}`: {reason}")
    lines.append("")
    (review_dir / "language_quality_report.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])",
        flags=re.IGNORECASE,
    )
