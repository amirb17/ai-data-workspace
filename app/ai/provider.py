from google import genai

from app.config import GEMINI_API_KEY


def get_ai_client(timeout_ms: int | None = None) -> genai.Client:
    """
    Return the configured Gemini client.
    """

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured"
        )

    return genai.Client(api_key=GEMINI_API_KEY,
        **({'http_options': {'timeout': timeout_ms, 'retry_options': {'attempts': 0}}} if timeout_ms else {}))
