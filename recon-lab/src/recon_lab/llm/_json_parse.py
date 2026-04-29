"""Robust JSON extraction from LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from LLM output.

    Tries, in order:
      1. Direct ``json.loads``
      2. Strip markdown code fences and retry
      3. Extract outermost ``{ … }`` substring
      4. Fix invalid ``\\escape`` sequences and retry

    Raises:
        RuntimeError: If no valid JSON object can be extracted.
    """
    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [line for line in lines if not line.startswith("```")]
        stripped = "\n".join(lines).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # 3. Extract outermost { … }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # 4. Fix invalid \escape sequences (LLMs output raw backslashes
        #    in regex patterns, Windows paths, etc.)
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    raise RuntimeError(f"Failed to parse JSON from LLM response: {text[:300]}")
