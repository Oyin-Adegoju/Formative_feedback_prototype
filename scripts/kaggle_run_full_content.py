#!/usr/bin/env python3
"""kaggle_run_full_content.py — run the full-content prompts through Qwen2.5-32B.

Designed to run on Kaggle (GPU notebook). It loads a Qwen2.5-32B-Instruct model
with vLLM, feeds each prompt.txt produced by build_full_content_experiment.py,
parses the JSON quality diagnosis, and writes it next to the prompt.

────────────────────────────────────────────────────────────────────────────
HARDWARE NOTE (read before running on Kaggle)
    Qwen2.5-32B in fp16 needs ~65 GB VRAM — it does NOT fit on Kaggle's 2×T4
    (2×16 GB). Use a 4-bit quant that fits in ~20 GB:
        Qwen/Qwen2.5-32B-Instruct-AWQ      (default below)
    and split across both T4s with tensor_parallel_size=2. On a single A100
    (40 GB, sometimes offered) the AWQ model fits with tensor_parallel_size=1.

KAGGLE SETUP (in a notebook cell, before running this script):
    !pip install -q vllm
    # Upload prompts: either add this repo's data/full_content_experiment as a
    # Kaggle dataset, or paste each prompt.txt into /kaggle/working/prompts/.

USAGE:
    python kaggle_run_full_content.py \
        --prompts-dir /kaggle/input/full-content-experiment \
        --out-dir /kaggle/working/out \
        --model Qwen/Qwen2.5-32B-Instruct-AWQ \
        --tensor-parallel 2

    Each prompt is expected at <prompts-dir>/<doc_id>/prompt.txt (the layout
    build_full_content_experiment.py produces). A flat directory of *.txt files
    also works.

OUTPUT (per prompt):
    <out-dir>/<doc_id>/quality_diagnostics_32b.json   parsed diagnosis (or raw on parse-fail)
    <out-dir>/<doc_id>/raw_response.txt               the model's raw text
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


def _find_prompts(prompts_dir: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Return [(doc_id, prompt_path)]. Supports <dir>/<doc_id>/prompt.txt and flat *.txt."""
    pairs: list[tuple[str, pathlib.Path]] = []
    for p in sorted(prompts_dir.glob("*/prompt.txt")):
        pairs.append((p.parent.name, p))
    if not pairs:
        for p in sorted(prompts_dir.glob("*.txt")):
            pairs.append((p.stem, p))
    return pairs


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from a model response (handles code fences / prose)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prompts-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="Qwen/Qwen2.5-32B-Instruct-AWQ")
    parser.add_argument("--tensor-parallel", type=int, default=2)
    parser.add_argument("--max-model-len", type=int, default=32768)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args()

    prompts_dir = pathlib.Path(args.prompts_dir)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = _find_prompts(prompts_dir)
    if not pairs:
        print(f"[FATAL] No prompts found under {prompts_dir}")
        return 1
    print(f"Found {len(pairs)} prompt(s): {', '.join(d for d, _ in pairs)}")

    # Imported here so --help works without vLLM installed.
    from vllm import LLM, SamplingParams

    llm = LLM(
        model=args.model,
        tensor_parallel_size=args.tensor_parallel,
        max_model_len=args.max_model_len,
        # AWQ is auto-detected from the model config; left implicit for portability.
        trust_remote_code=True,
    )
    tokenizer = llm.get_tokenizer()
    sampling = SamplingParams(temperature=args.temperature, max_tokens=args.max_tokens)

    for doc_id, prompt_path in pairs:
        print(f"\n=== {doc_id} ===")
        user_prompt = prompt_path.read_text(encoding="utf-8")
        # Wrap in the Qwen chat template so the instruct model behaves correctly.
        chat = tokenizer.apply_chat_template(
            [{"role": "user", "content": user_prompt}],
            tokenize=False, add_generation_prompt=True,
        )
        result = llm.generate([chat], sampling)[0]
        raw = result.outputs[0].text

        doc_out = out_dir / doc_id
        doc_out.mkdir(parents=True, exist_ok=True)
        (doc_out / "raw_response.txt").write_text(raw, encoding="utf-8")

        parsed = _extract_json(raw)
        if parsed is not None:
            parsed.setdefault("document_id", doc_id)
            (doc_out / "quality_diagnostics_32b.json").write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            crits = list((parsed.get("criteria") or {}).keys())
            print(f"  parsed OK — criteria: {crits}")
        else:
            print("  [WARN] could not parse JSON — see raw_response.txt")

    print(f"\n[done] outputs in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
