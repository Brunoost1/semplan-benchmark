from __future__ import annotations

from collections import Counter

from semplan.benchmark.templates import f3_smoke_templates
from semplan.contracts import ExpectedPolicy, Locale
from semplan.executor.sql_guard import validate_select_sql


def test_f3_smoke_templates_are_exactly_bilingual_case_count() -> None:
    templates = f3_smoke_templates()

    assert len(templates) == 25
    assert sum(len(template.utterances) for template in templates) == 50
    for template in templates:
        assert set(template.utterances) == {Locale.EN_US, Locale.PT_BR}


def test_f3_smoke_templates_cover_key_classes_and_splits() -> None:
    templates = f3_smoke_templates()
    class_counts = Counter(template.question_class.value for template in templates)
    split_counts = Counter(template.split.value for template in templates)

    assert {"lookup", "grouped_aggregation", "ranking", "comparison"}.issubset(class_counts)
    assert {"variance", "trend", "share_ratio", "contract_status"}.issubset(class_counts)
    assert class_counts["ambiguity"] == 2
    assert class_counts["adversarial"] == 1
    assert split_counts["test_hidden"] == 0
    assert split_counts["adversarial"] == 1


def test_executable_template_sql_passes_guard() -> None:
    executable = [
        template
        for template in f3_smoke_templates()
        if template.expected_policy is ExpectedPolicy.ALLOW
    ]

    assert len(executable) == 22
    for template in executable:
        assert template.sql is not None
        validate_select_sql(template.sql)


def test_policy_templates_do_not_include_sql() -> None:
    policy_templates = [
        template
        for template in f3_smoke_templates()
        if template.expected_policy is not ExpectedPolicy.ALLOW
    ]

    assert policy_templates
    assert all(template.sql is None for template in policy_templates)
