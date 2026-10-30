"""Reviewed business-rule answers and exact numeric contracts."""

import re

from pydantic import BaseModel, Field, model_validator

from app.processing.exact_decimal import ExactDecimalSpec


LEGACY_ANSWERS = frozenset({
    "YES", "NO", "ALLOW", "KEEP_FIRST",
    "KEEP_LATEST", "QUARANTINE",
    "DECIMAL", "DATETIME", "DATE",
})

EXACT_PATTERN = re.compile(
    r"^DECIMAL_EXACT:P([1-9][0-9]?):S(0|[1-9][0-9]?)$"
)


def exact_decimal_answer_config(answer: str) -> dict | None:
    match = EXACT_PATTERN.fullmatch(answer)

    if match is None:
        return None

    spec = ExactDecimalSpec(
        precision=int(match.group(1)),
        scale=int(match.group(2)),
    )

    return {
        "type": "DECIMAL",
        "mode": "EXACT",
        "precision": spec.precision,
        "scale": spec.scale,
    }


def answer_matches_options(
    rule_type: str,
    answer: str,
    options: list[str],
) -> bool:
    if answer in options:
        return True

    if rule_type != "DATA_TYPE" or "DECIMAL" not in options:
        return False

    try:
        return exact_decimal_answer_config(answer) is not None
    except ValueError:
        return False


def validate_answer(rule_type: str, answer: str) -> None:
    if answer in LEGACY_ANSWERS:
        return

    if answer_matches_options(
        rule_type,
        answer,
        ["DECIMAL"],
    ):
        return

    raise ValueError("Unsupported business-rule answer")


class BusinessRuleAnswer(BaseModel):
    column_name: str
    rule_type: str
    answer: str = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def check_answer(self):
        validate_answer(self.rule_type, self.answer)
        return self


class BusinessRuleSubmission(BaseModel):
    answers: list[BusinessRuleAnswer]
    expected_rule_version: int | None = Field(default=None, ge=0)


class BusinessRuleUpdate(BaseModel):
    expected_rule_version: int | None = Field(default=None, ge=0)
    column_name: str
    rule_type: str
    answer: str = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def check_answer(self):
        validate_answer(self.rule_type, self.answer)
        return self