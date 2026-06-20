"""json_extract.py — pull a JSON object out of raw LLM text.

Some models (notably qwen2.5:32b) wrap their JSON answer in a markdown code
fence (```json ... ```), sometimes with a line of prose before or after, even
when the prompt says "only valid JSON". The validators expect a bare JSON
string, so this helper strips the fence and, as a fallback, isolates the
outermost {...} / [...] block before json.loads runs.

LLM-free and dependency-light so both quality_validator and feedback_validator
can reuse it without importing the LLM client.
"""

from __future__ import annotations


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence (```/```json … ```), if present."""
    t = text.strip()
    if not t.startswith("```"):
        return t
    # Drop the opening fence line (``` or ```json / ```JSON).
    t = t.split("\n", 1)[1] if "\n" in t else t[3:]
    # Drop the trailing fence.
    t = t.rstrip()
    if t.endswith("```"):
        t = t[: -3]
    return t.strip()


def extract_json(text: str) -> str:
    """Best-effort: return the JSON payload from raw LLM output.

    Strips a markdown code fence and, if the result still doesn't start with a
    JSON token, isolates the outermost object/array. Returns the original
    (stripped) text when no JSON-looking block is found, so the caller's
    json.loads still raises a clear error.
    """
    t = _strip_code_fence(text)
    if t.startswith(("{", "[")):
        return t
    # Prose around the JSON: take from the first opener to the matching last closer.
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    ends = [i for i in (t.rfind("}"), t.rfind("]")) if i != -1]
    if starts and ends:
        start, end = min(starts), max(ends)
        if end > start:
            return t[start : end + 1].strip()
    return t
