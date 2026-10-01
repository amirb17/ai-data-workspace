from typing import Literal

from pydantic import BaseModel

from app.schemas.analytics_query import (
    AnalyticsQueryRequest,
)


class AIQueryPlan(BaseModel):
    status: Literal[
        "ANSWERABLE",
        "NOT_RELEVANT",
        "UNSUPPORTED",
        "NEEDS_CLARIFICATION",
    ]

    message: str

    query: AnalyticsQueryRequest | None = None