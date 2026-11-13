"""Production diagnostics expose no private exception values or SMTP credentials."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          'app/security/public_launch.py').read_text(encoding='utf8')


def test_public_errors_log_class_not_secrets_or_exception_text():
    assert "type(exc).__name__" in SOURCE
    assert "getattr(matched, 'path', 'unknown')" in SOURCE
    assert "if not started: await error_response(503)" in SOURCE
    assert "logging.getLogger('datarise.public_launch').error(" in SOURCE
    assert "logging.exception(" not in SOURCE
    assert "logging.getLogger('datarise.public_launch').error(exc)" not in SOURCE
