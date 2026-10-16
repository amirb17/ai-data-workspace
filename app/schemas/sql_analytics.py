from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.processing.sql_policy import LIMITS


class SqlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    sql_text: str = Field(min_length=1, max_length=LIMITS.text_length)
    saved_query_id: int | None = Field(default=None, gt=0)


class SaveQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=1000)
    sql_text: str = Field(min_length=1, max_length=LIMITS.text_length)
    expected_version: int | None = Field(default=None, gt=0)


class DeleteQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: Literal[True]
    expected_version: int = Field(gt=0)


class BuilderFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str = Field(min_length=1,max_length=240)
    operator: Literal['EQ','NE','GT','GE','LT','LE','BETWEEN','CONTAINS','STARTS','IS_NULL','IS_NOT_NULL']
    value: str | int | float | bool | None = None
    end: str | int | float | None = None


class BuilderSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str | None = Field(default=None, max_length=240)
    aggregation: Literal['COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX'] | None = None
    alias: str | None = Field(default=None, min_length=1, max_length=120)


class BuilderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset: str = Field(min_length=1,max_length=120)
    selections: list[BuilderSelection] = Field(default_factory=list, max_length=30)
    columns: list[str] = Field(default_factory=list,max_length=30)
    aggregation: Literal['COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX'] | None = None
    measure: str | None = Field(default=None,max_length=240)
    grouping: list[str] = Field(default_factory=list,max_length=3)
    filters: list[BuilderFilter] = Field(default_factory=list,max_length=4)
    sort_by: str | None = Field(default=None,max_length=240)
    descending: bool = False
    limit: int = Field(default=100,ge=1,le=LIMITS.output_rows)
    relationship_id: int | None = Field(default=None,gt=0)
