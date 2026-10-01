from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryFilter(BaseModel):
    column: str
    operator: Literal[
        "EQ",
        "NE",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "IN",
    ]
    value: Any


class QuerySort(BaseModel):
    column: str
    direction: Literal[
        "ASC",
        "DESC",
    ] = "ASC"


class AnalyticsQueryRequest(BaseModel):
    artifact_name: str

    select: list[str] = Field(
        default_factory=list
    )

    filters: list[QueryFilter] = Field(
        default_factory=list
    )

    sort: list[QuerySort] = Field(
        default_factory=list
    )

    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
    )