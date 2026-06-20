"""Tests for src/llm/json_extract.py — unwrapping fenced/prose-wrapped LLM JSON."""

from __future__ import annotations

import json

from src.llm.json_extract import extract_json


def test_bare_json_unchanged():
    s = '{"a": 1}'
    assert json.loads(extract_json(s)) == {"a": 1}


def test_strips_json_code_fence():
    s = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert json.loads(extract_json(s)) == {"a": 1, "b": [2, 3]}


def test_strips_plain_code_fence():
    s = '```\n{"a": 1}\n```'
    assert json.loads(extract_json(s)) == {"a": 1}


def test_strips_fence_with_surrounding_whitespace():
    s = '\n\n   ```JSON\n{"a": 1}\n```   \n'
    assert json.loads(extract_json(s)) == {"a": 1}


def test_isolates_json_amid_prose():
    s = 'Here is the result:\n{"a": 1}\nHope that helps!'
    assert json.loads(extract_json(s)) == {"a": 1}


def test_real_qwen32b_preview():
    """The exact failure shape from qwen2.5:32b: a ```json fence."""
    s = (
        '```json\n'
        '{\n  "document_id": "2f4d9d91",\n'
        '  "criteria": {"beperking": {"criterion_judgement": "strong"}}\n}\n'
        '```'
    )
    data = json.loads(extract_json(s))
    assert data["document_id"] == "2f4d9d91"
    assert data["criteria"]["beperking"]["criterion_judgement"] == "strong"
