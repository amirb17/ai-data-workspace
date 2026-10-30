import pytest

from app.schemas.business_rules import (
    BusinessRuleAnswer,
    answer_matches_options,
    exact_decimal_answer_config,
)
from app.services.business_rule_service import (
    convert_answer_to_rule_config,
)
from app.services.rule_reuse_service import (
    approved_rules_cover_questions,
)


def test_exact_decimal_rule_contract():
    answer = BusinessRuleAnswer(
        column_name="amount",
        rule_type="DATA_TYPE",
        answer="DECIMAL_EXACT:P20:S4",
    )

    assert answer.answer == "DECIMAL_EXACT:P20:S4"

    assert exact_decimal_answer_config(answer.answer) == {
        "type": "DECIMAL",
        "mode": "EXACT",
        "precision": 20,
        "scale": 4,
    }


def test_legacy_decimal_unchanged():
    assert convert_answer_to_rule_config(
        "DATA_TYPE",
        "DECIMAL",
    ) == {"type": "DECIMAL"}


@pytest.mark.parametrize(
    "answer",
    [
        "DECIMAL_EXACT:P77:S4",
        "DECIMAL_EXACT:P20:S39",
        "DECIMAL_EXACT:P2:S4",
        "DECIMAL_EXACT:P00:S2",
        "DECIMAL_EXACT:P20:S04",
        "DECIMAL_EXACT:P20",
    ],
)
def test_invalid_contract_rejected(answer):
    with pytest.raises(ValueError):
        BusinessRuleAnswer(
            column_name="amount",
            rule_type="DATA_TYPE",
            answer=answer,
        )


def test_exact_answer_requires_decimal_question():
    assert answer_matches_options(
        "DATA_TYPE",
        "DECIMAL_EXACT:P20:S4",
        ["DECIMAL", "NO"],
    )

    assert not answer_matches_options(
        "NOT_NULL",
        "DECIMAL_EXACT:P20:S4",
        ["YES", "NO"],
    )


def test_approved_rule_reuse_checks_precision_and_scale():
    answer = "DECIMAL_EXACT:P20:S4"

    configuration = exact_decimal_answer_config(answer)

    approval = (42, 1, 1, None, False, True)

    questions = [{
        "column_name": "amount",
        "suggested_rule_type": "DATA_TYPE",
        "options": ["DECIMAL", "NO"],
    }]

    answers = [
        (1, 42, "amount", "DATA_TYPE", answer),
    ]

    active_rules = [
        (1, 42, "amount", "DATA_TYPE", configuration, True),
    ]

    assert approved_rules_cover_questions(
        approval,
        questions,
        answers,
        active_rules,
    )

    changed_config = {
        **configuration,
        "scale": 2,
    }

    assert not approved_rules_cover_questions(
        approval,
        questions,
        answers,
        [
            (1, 42, "amount", "DATA_TYPE", changed_config, True),
        ],
    )