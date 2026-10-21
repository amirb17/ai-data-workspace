"""Reviewed semantic metadata. No inferred grain, keys or executable transforms."""
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


ColumnName = Annotated[str, Field(min_length=1, max_length=255)]
Columns = Annotated[tuple[ColumnName, ...], Field(max_length=64)]
IdentityColumns = Annotated[tuple[ColumnName, ...], Field(min_length=1, max_length=32)]


class TimePolicy(Contract):
    column: ColumnName
    interpretation: Literal['UTC', 'SOURCE_TIMEZONE']
    timezone: str | None = None

    @model_validator(mode='after')
    def timezone_policy(self):
        if self.interpretation == 'UTC':
            if self.timezone is not None:
                raise ValueError('UTC interpretation does not take a source timezone')
        else:
            if not self.timezone:
                raise ValueError('SOURCE_TIMEZONE requires an explicit IANA timezone')
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError('Unknown IANA timezone') from error
        return self


class Grain(Contract):
    description: str = Field(min_length=1, max_length=500)
    identity_columns: IdentityColumns

    @field_validator('description')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Grain description is required')
        return value


class FactConfiguration(Contract):
    schema_version: Literal[1] = 1
    model_type: Literal['FACT']
    grain: Grain
    event_time: TimePolicy | None = None
    identity_null_policy: Literal['REJECT'] = 'REJECT'


class DimensionConfiguration(Contract):
    schema_version: Literal[1] = 1
    model_type: Literal['DIMENSION']
    business_keys: IdentityColumns
    scd_type: Literal['SCD_TYPE_1', 'SCD_TYPE_2']
    tracked_attributes: Columns = ()
    nonhistorical_attributes: Columns = ()
    effective_time: TimePolicy | None = None
    history_coverage: Literal['FROM_FIRST_OBSERVED_EVENT'] = 'FROM_FIRST_OBSERVED_EVENT'
    missing_member_policy: Literal['REJECT', 'INFER_MEMBER'] = 'REJECT'
    null_business_key_policy: Literal['REJECT'] = 'REJECT'
    snapshot_absence_policy: Literal['KEEP', 'DEACTIVATE_ON_COMPLETE'] = 'KEEP'

    @model_validator(mode='after')
    def scd_contract(self):
        if not self.tracked_attributes and not self.nonhistorical_attributes:
            raise ValueError('Dimension requires explicit business attributes')
        if self.scd_type == 'SCD_TYPE_2' and (self.effective_time is None or not self.tracked_attributes):
            raise ValueError('SCD2 requires effective time and tracked attributes')
        return self


ModelConfiguration = Annotated[FactConfiguration | DimensionConfiguration, Field(discriminator='model_type')]


class ModelingRequest(Contract):
    source_policy_id: int = Field(gt=0)
    source_rule_version: int = Field(gt=0)
    configuration: ModelConfiguration


class SourceColumn(Contract):
    name: ColumnName
    data_type: Literal['STRING', 'INTEGER', 'DECIMAL', 'FLOAT', 'BOOLEAN', 'DATE', 'TIMESTAMP']
    nullable: bool | None = None  # Unknown schema nullability is not a non-null guarantee.


class SourceSchema(Contract):
    schema_version: Literal[1] = 1
    columns: tuple[SourceColumn, ...] = Field(min_length=1, max_length=400)
