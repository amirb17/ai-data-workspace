import hashlib
import hmac
import secrets
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from email_validator import validate_email, EmailNotValidError
from itsdangerous import URLSafeTimedSerializer, BadSignature
from app.auth.config import require_secret

PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))


def normalize_email(value):
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        raise ValueError('Enter a valid email address.') from None


def password_hash(value):
    if len(value) < 12 or len(value) > 128 or len(value.encode()) > 512:
        raise ValueError('Use a password between 12 and 128 characters.')
    return PASSWORDS.hash(value)


def password_matches(encoded, value):
    try:
        return PASSWORDS.verify(encoded or DUMMY_HASH, value) and bool(encoded)
    except (VerificationError, InvalidHashError):
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def protected_digest(purpose, value):
    return hmac.new(require_secret().secret.encode(), (purpose + ':' + value).encode(), hashlib.sha256).hexdigest()


def new_csrf():
    return URLSafeTimedSerializer(require_secret().secret, salt='datarise-csrf-v1').dumps(secrets.token_urlsafe(32))


def valid_csrf(header, cookie):
    if not header or not cookie or not hmac.compare_digest(header, cookie):
        return False
    try:
        URLSafeTimedSerializer(require_secret().secret, salt='datarise-csrf-v1').loads(header, max_age=86400)
        return True
    except (BadSignature, ValueError):
        return False
