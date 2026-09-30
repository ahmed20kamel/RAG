"""Download the optional cross-encoder reranker, once.

    pip install -r requirements-reranker.txt
    python scripts/download_reranker.py            # bge-reranker-v2-m3, int8 (~590 MB)
    python scripts/download_reranker.py --small    # gte-multilingual-reranker-base (~360 MB)

Then set RERANKER=cross (and RERANKER_MODEL_DIR for the small one) in .env and restart.

Both are Apache-2.0 and multilingual, Arabic included. The weights come from the
ONNX conversions published by the onnx-community organisation on Hugging Face. Only
the model is downloaded; no document or question ever leaves the machine, before or
after — inference runs locally on the CPU.

Measured on the reference deployment (6-core laptop CPU, 34-question set):

    model   section rank   evidence recall   added latency
    bge     +0.044         unchanged         ~18 s / question
    gte     +0.020         unchanged         ~7 s / question

which is why the default is RERANKER=feature. Revisit with a faster CPU, a GPU for
inference, or a much larger corpus, where lexical noise grows and ranking matters more.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODELS = {
    "bge": ("onnx-community/bge-reranker-v2-m3-ONNX", "models/reranker"),
    "gte": ("onnx-community/gte-multilingual-reranker-base", "models/reranker-gte"),
}
FILES = ("onnx/model_int8.onnx", "tokenizer.json", "config.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--small", action="store_true", help="the smaller, faster gte model")
    args = parser.parse_args()
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("pip install -r requirements-reranker.txt first", file=sys.stderr)
        return 1

    repo, target = MODELS["gte" if args.small else "bge"]
    destination = ROOT / target
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        path = Path(hf_hub_download(repo, name, local_dir=str(destination)))
        print(f"  {name:24} {path.stat().st_size / 1e6:8.1f} MB")
    print(f"\ndownloaded to {destination}")
    print("set in .env:  RERANKER=cross" + ("" if not args.small else f"\n              RERANKER_MODEL_DIR={target}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
