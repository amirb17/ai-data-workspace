from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.auth.security import normalize_email


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Email(Payload):
    email: str = Field(max_length=320)
    @field_validator('email')
    @classmethod
    def normalized(cls, value):
        return normalize_email(value)


class Login(Email):
    password: str = Field(min_length=1, max_length=128)


class Password(Payload):
    password: str = Field(min_length=12, max_length=128)
    confirm_password: str = Field(max_length=128)
    @model_validator(mode='after')
    def confirmation(self):
        if self.password != self.confirm_password:
            raise ValueError('Passwords do not match')
        return self


class Signup(Password, Email):
    display_name: str = Field(min_length=1, max_length=100)
    @field_validator('display_name')
    @classmethod
    def name(cls, value):
        if not value.strip():
            raise ValueError('Enter your name')
        return value.strip()


class Challenge(Payload):
    challenge_id: UUID


class Verify(Challenge):
    code: str = Field(pattern=r'^\d{6}$')
    purpose: str = Field(pattern=r'^(VERIFY_EMAIL|RESET_PASSWORD)$')


class Reset(Password):
    reset_token: str = Field(min_length=32, max_length=128)


class Change(Password):
    current_password: str = Field(min_length=1, max_length=128)


class Profile(Payload):
    display_name: str = Field(min_length=1, max_length=100)
    _name = field_validator('display_name')(Signup.name.__func__)
