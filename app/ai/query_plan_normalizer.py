import re

from app.schemas.analytics_query import (
    AnalyticsQueryRequest,
)


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _extract_requested_limit(
    question: str,
) -> int | None:
    """
    Detect explicit top/bottom N requests such as:
    - top 2
    - top two
    - bottom 5
    """

    normalized = question.lower()

    digit_match = re.search(
        r"\b(?:top|bottom)\s+(\d+)\b",
        normalized,
    )

    if digit_match:
        return int(digit_match.group(1))

    word_match = re.search(
        r"\b(?:top|bottom)\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        normalized,
    )

    if word_match:
        return NUMBER_WORDS[
            word_match.group(1)
        ]

    return None


def normalize_query_plan(
    question: str,
    query: AnalyticsQueryRequest,
) -> AnalyticsQueryRequest:
    """
    Apply deterministic corrections to an AI-generated
    query plan before semantic validation.
    """

    data = query.model_dump()

    requested_limit = _extract_requested_limit(
        question
    )

    if requested_limit is not None:
        data["limit"] = requested_limit

    # Remove duplicate sort clauses while
    # preserving the original order.
    unique_sort = []
    seen = set()

    for sort_item in data["sort"]:
        key = (
            sort_item["column"],
            sort_item["direction"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_sort.append(sort_item)

    data["sort"] = unique_sort

    return AnalyticsQueryRequest(
        **data
    )