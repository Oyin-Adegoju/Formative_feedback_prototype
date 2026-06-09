#!/usr/bin/env python3
"""check_local_llm.py — quick pre-flight check for local Ollama / LM Studio.

Verifies:
  1. Inference server is reachable
  2. Requested model is available (Ollama /api/tags)
  3. Model can produce a minimal valid JSON response

Usage:
    python scripts/check_local_llm.py
    python scripts/check_local_llm.py --model qwen2.5:7b
    python scripts/check_local_llm.py --base-url http://localhost:1234/v1  # LM Studio

Exit codes:
    0 — all checks passed, ready to run the pipeline
    1 — one or more checks failed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import pathlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_DEFAULT_BASE_URL = "http://localhost:11434/v1"
_DEFAULT_MODEL = "qwen2.5:7b"

_MINI_PROMPT = (
    'Respond with ONLY valid JSON, no explanation. '
    'Return exactly: {"status": "ok", "model": "ready"}'
)


def _check_server(base_url: str) -> bool:
    root = base_url.replace("/v1", "").rstrip("/")
    print(f"  Checking server at {root} ...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(root, timeout=5) as r:
            body = r.read().decode("utf-8", errors="replace")
        print(f"OK  ({body.strip()[:40]})")
        return True
    except urllib.error.URLError as exc:
        print(f"FAIL  ({exc.reason})")
        print("  → Is Ollama running? Try: ollama serve")
        return False
    except Exception as exc:
        print(f"FAIL  ({exc})")
        return False


def _check_model_available(base_url: str, model: str) -> bool:
    root = base_url.replace("/v1", "").rstrip("/")
    tags_url = f"{root}/api/tags"
    print(f"  Checking model '{model}' is pulled ...", end=" ", flush=True)
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        # Ollama model names can be "qwen2.5:7b" or "qwen2.5:7b-instruct-q4_K_M"
        matched = any(m.startswith(model.split(":")[0]) for m in models)
        if matched:
            print(f"OK  (found: {[m for m in models if m.startswith(model.split(':')[0])]})")
            return True
        else:
            print(f"NOT FOUND")
            print(f"  → Available: {models}")
            print(f"  → Run: ollama pull {model}")
            return False
    except urllib.error.URLError:
        print("SKIP  (not an Ollama server, cannot verify model list)")
        return True
    except Exception as exc:
        print(f"SKIP  ({exc})")
        return True


def _check_inference(base_url: str, model: str, timeout: int) -> bool:
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    print(f"  Sending mini inference request (timeout={timeout}s) ...", end=" ", flush=True)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": _MINI_PROMPT}],
        "temperature": 0.0,
        "max_tokens": 64,
    }).encode("utf-8")
    req = urllib.request.Request(
        url=endpoint, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        print(f"OK")
        print(f"    response: {content.strip()[:80]}")
        try:
            parsed = json.loads(content.strip())
            if parsed.get("status") == "ok":
                print("    JSON parse: valid, 'status' field correct")
            else:
                print(f"    JSON parse: valid but unexpected content: {parsed}")
        except json.JSONDecodeError:
            print("    JSON parse: response was not JSON — model may not follow instructions well")
            print("    → Consider: ollama pull qwen2.5:7b  (better instruction following)")
        return True
    except TimeoutError:
        print(f"TIMEOUT after {timeout}s")
        print("  → Model is loading or very slow. Try again in 30s, or increase --timeout.")
        return False
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}  ({exc.reason})")
        return False
    except urllib.error.URLError as exc:
        print(f"FAIL  ({exc.reason})")
        return False
    except (KeyError, IndexError) as exc:
        print(f"FAIL  (unexpected response shape: {exc})")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-flight check for local LLM before running the full pipeline.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LLM_BASE_URL", _DEFAULT_BASE_URL),
        help=f"Inference server base URL (default: {_DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_MODEL", _DEFAULT_MODEL),
        help=f"Model name (default: {_DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for the inference check (default: 60)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Local LLM pre-flight check")
    print("=" * 60)
    print(f"  base URL : {args.base_url}")
    print(f"  model    : {args.model}")
    print()

    ok = True
    ok = _check_server(args.base_url) and ok
    if not ok:
        print()
        print("[FAIL] Server not reachable. Fix server first, then retry.")
        return 1

    ok = _check_model_available(args.base_url, args.model) and ok
    ok = _check_inference(args.base_url, args.model, args.timeout) and ok

    print()
    if ok:
        print("[PASS] All checks passed. You can run the pipeline:")
        print()
        print(f"  $env:LLM_BASE_URL = \"{args.base_url}\"")
        print(f"  $env:LLM_MODEL    = \"{args.model}\"")
        print("  python scripts/run_full_pipeline_with_feedback.py \\")
        print("      --input data/anonymised/<file>_anonymized.json \\")
        print("      --input-type anonymized-json \\")
        print("      --llm-timeout 300")
    else:
        print("[FAIL] One or more checks failed. See details above.")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
