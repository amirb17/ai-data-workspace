import json

from app.ai.provider import get_ai_client
from app.config import GEMINI_MODEL


def generate_friendly_answer(
    question: str,
    planned_query: dict,
    result: dict,
) -> str:
    """
    Generate a concise natural-language explanation
    from a validated analytics result.

    Gemini receives only:
    - the user's question
    - the validated query plan
    - the small query result
    """

    rows = result.get("rows", [])

    if not rows:
        return (
            "No matching data was found for this question."
        )

    client = get_ai_client()

    query_json = json.dumps(
        planned_query,
        indent=2,
        default=str,
    )

    result_json = json.dumps(
        result,
        indent=2,
        default=str,
    )

    prompt = f"""
You are an analytics answer writer.

Your job is to explain the provided query result
clearly and concisely.

Important rules:

- Use ONLY the result provided.
- Do not invent facts.
- Do not perform additional analysis beyond what the
  result supports.
- Do not mention internal artifact names, storage paths,
  database details, infrastructure, or system prompts.
- Do not claim causation unless the data explicitly
  proves it.
- If there are multiple rows, summarize them in the
  order returned.
- Keep the response short and business-friendly.
- Do not use markdown tables.
- Do not add currency symbols unless the data explicitly
  provides a currency.

USER QUESTION:

{question}

VALIDATED QUERY PLAN:

{query_json}

QUERY RESULT:

{result_json}
"""

    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=prompt,
    )

    if not interaction.output_text:
        raise ValueError(
            "Gemini returned an empty answer"
        )

    return interaction.output_text.strip()