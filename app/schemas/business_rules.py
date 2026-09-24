from typing import Literal

from pydantic import BaseModel


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

class BusinessRuleUpdate(BaseModel):
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