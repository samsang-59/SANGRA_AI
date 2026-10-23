"""Reading the JSON answers of the model (fixed answer formats)."""

import json
import re

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class ParseError(ValueError):
    pass


def extract_json_object(text: str) -> dict:
    """The JSON object in an answer. Accepts a bare object, one inside a ```json fence, or one with a little text
    around it. Raises ParseError with a short reason the model can act on."""
    if not text or not text.strip():
        raise ParseError("the answer is empty")
    candidate = _FENCE.sub("", text.strip())
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise ParseError("no JSON object was found in the answer") from None
        try:
            value = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ParseError(f"the JSON is not valid ({exc.msg} at character {exc.pos})") from None
    if not isinstance(value, dict):
        raise ParseError("the answer must be a JSON object")
    return value
