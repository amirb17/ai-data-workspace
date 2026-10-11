"""Strict semantic intent; clients and Gemini cannot supply executable code or results."""
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, model_validator, field_validator
from app.schemas.semantic_suggestion import Strict
from app.schemas.metrics import ColumnRef

QuestionType = Literal['SCALAR', 'COUNT', 'GROUPED', 'TIME_SERIES', 'RANKING', 'COMPARISON', 'TREND', 'BREAKDOWN']
AnswerStatus = Literal['ANSWERED', 'CLARIFICATION_REQUIRED', 'REFRESH_REQUIRED', 'UNSUPPORTED', 'NO_DATA', 'FAILED']


class QuestionPeriod(Strict):
    relative: Literal['TODAY', 'YESTERDAY', 'THIS_WEEK', 'LAST_WEEK', 'THIS_MONTH', 'LAST_MONTH', 'THIS_QUARTER', 'LAST_QUARTER', 'THIS_YEAR', 'LAST_YEAR'] | None = None
    start: date | None = None
    end: date | None = None

    @field_validator('start', 'end', mode='before')
    @classmethod
    def iso_date(cls, value):
        # Stored JSON and API/provider JSON use ISO dates; reject epochs and datetimes.
        if isinstance(value, str):
            if len(value) != 10:
                raise ValueError('Use YYYY-MM-DD')
            return date.fromisoformat(value)
        return value

    @model_validator(mode='after')
    def exclusive_range(self):
        if self.relative:
            if self.start or self.end:
                raise ValueError('Choose a relative or explicit range')
        elif not self.start or not self.end or self.start >= self.end:
            raise ValueError('Explicit range must be increasing and half-open')
        return self


class QuestionIntent(Strict):
    question_type: QuestionType = 'SCALAR'
    context_mode: Literal['NEW', 'FOLLOW_UP'] = 'NEW'
    metric_candidate_id: int | None = Field(default=None, gt=0)
    base_dataset_id: int | None = Field(default=None, gt=0)
    aggregation: Literal['COUNT', 'COUNT_DISTINCT', 'SUM', 'AVG', 'MIN', 'MAX'] | None = None
    measure: ColumnRef | None = None
    dimensions: list[ColumnRef] = Field(default_factory=list, max_length=2)
    time_field: ColumnRef | None = None
    time_grain: Literal['DAY', 'WEEK', 'MONTH', 'QUARTER', 'YEAR'] | None = None
    periods: list[QuestionPeriod] = Field(default_factory=list, max_length=2)
    relationship_ids: list[int] = Field(default_factory=list, max_length=1)
    top_n: int = Field(default=10, ge=1, le=20, description='Integer from 1 to 20. Use 10 when this is not a ranking question; never use zero.')
    direction: Literal['DESC', 'ASC'] = 'DESC'
    meaning: Literal['LITERAL', 'BUSINESS_AMBIGUOUS', 'UNRESOLVED_CATEGORY'] = 'LITERAL'


class WorkspaceQuestionPlan(Strict):
    status: Literal['ANSWERABLE', 'CLARIFICATION_REQUIRED', 'UNSUPPORTED', 'NOT_RELEVANT']
    intent: QuestionIntent


class WorkspaceQuestionRequest(Strict):
    question: str = Field(min_length=1, max_length=1000)
    previous_answer_id: int | None = Field(default=None, gt=0)
    clarification_answer_id: int | None = Field(default=None, gt=0)
    choice_id: int | None = Field(default=None, ge=0, le=9)
    timezone: str | None = Field(default=None, max_length=80)

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Question cannot be blank')
        return value

    @field_validator('timezone')
    @classmethod
    def timezone_exists(cls, value):
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('Unknown timezone') from None
        return value

    @model_validator(mode='after')
    def choice_pair(self):
        if (self.choice_id is None) != (self.clarification_answer_id is None):
            raise ValueError('A choice requires its owned clarification answer')
        return self


class AnswerGroup(Strict):
    dimensions: list[str | int | float | bool | None]
    value: str | int | float | None


class WorkspaceAnswer(Strict):
    answer_id: int
    workspace_id: int
    question: str
    intent: QuestionIntent | None
    status: AnswerStatus
    freshness: Literal['FRESH', 'STALE', 'UNAVAILABLE']
    computed_at: str
    primary_answer: str
    result_kind: Literal['scalar', 'grouped', 'comparison'] | None = None
    value: str | int | float | None = None
    groups: list[AnswerGroup] = Field(default_factory=list, max_length=100)
    comparison: dict | None = None
    metric_reference: dict | None = None
    dimensions: list[str] = Field(default_factory=list)
    provenance: list[dict] = Field(default_factory=list)
    relationship_versions: list[dict] = Field(default_factory=list)
    interpretation: str
    explanation: str
    caveats: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list, max_length=4)
    choices: list[dict] = Field(default_factory=list, max_length=10)
    reused: bool = False
