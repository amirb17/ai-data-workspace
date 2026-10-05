from typing import Literal

from pydantic import BaseModel, Field


class BusinessRuleAnswer(BaseModel):
    column_name: str
    rule_type: str

    answer: Literal[
        "YES",
        "NO",
        "ALLOW",
        "KEEP_FIRST",
        "KEEP_LATEST",
        "QUARANTINE",
        "DECIMAL",
        "DATETIME",
    ]


class BusinessRuleSubmission(BaseModel):
    answers: list[BusinessRuleAnswer]
    expected_rule_version: int | None = Field(default=None, ge=0)

class BusinessRuleUpdate(BaseModel):
    expected_rule_version: int | None = Field(default=None, ge=0)
    column_name: str
    rule_type: str
    answer: Literal[
        "YES",
        "NO",
        "ALLOW",
        "KEEP_FIRST",
        "KEEP_LATEST",
        "QUARANTINE",
        "DECIMAL",
        "DATETIME",
    ]
