from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class AskDataRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=1000,
    )

    @field_validator("question")
    @classmethod
    def validate_question(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "Question cannot be empty"
            )

        return value