from google import genai

from app.config import GEMINI_API_KEY


def get_ai_client() -> genai.Client:
    """
    Return the configured Gemini client.
    """

    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured"
        )

    return genai.Client(
        api_key=GEMINI_API_KEY
    )