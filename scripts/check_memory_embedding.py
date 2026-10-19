"""Offline local-weight check with cache/artifacts confined to project .tmp."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True)
    args = parser.parse_args()
    source = Path(args.model_path).resolve()
    before = {str(p.relative_to(source)): (p.stat().st_mtime_ns, p.stat().st_size)
              for p in source.rglob("*") if p.is_file()}
    runtime = ROOT / ".tmp" / ("memory-embedding-" + uuid.uuid4().hex)
    runtime.mkdir(parents=True, exist_ok=False)
    os.environ.update(TMP=str(runtime), TEMP=str(runtime), HF_HOME=str(runtime / "hf"),
                      TORCH_HOME=str(runtime / "torch"), HF_MODULES_CACHE=str(runtime / "modules"),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    from qqbot_core.agent_memory import AgentMemory
    from qqbot_core.group_policy import GroupPolicy

    try:
        import psutil
        rss = lambda: psutil.Process().memory_info().rss
    except ImportError:
        rss = lambda: 0
    before_rss = rss()
    memory = AgentMemory(runtime / "memory", GroupPolicy(), embedding_path=str(source))
    start = time.perf_counter()
    cold = memory.embedder.encode(["周六八点一起打王者"])
    first_return_ms = (time.perf_counter() - start) * 1000
    deadline = time.monotonic() + 90
    while memory.embedder.status in {"loading", "not_loaded"} and time.monotonic() < deadline:
        time.sleep(.2)
    loaded_seconds = time.perf_counter() - start
    begin = time.perf_counter()
    vectors = memory.embedder.encode(["上次约好的排位几点开始", "周六八点开黑打王者荣耀", "咖啡的产地和烘焙"])
    encode_ms = (time.perf_counter() - begin) * 1000
    after = {str(p.relative_to(source)): (p.stat().st_mtime_ns, p.stat().st_size)
             for p in source.rglob("*") if p.is_file()}
    result = {"status": memory.embedder.status, "source_files_unchanged": before == after,
              "cold_call_keyword_fallback": cold is None, "first_return_ms": round(first_return_ms, 2),
              "background_load_seconds": round(loaded_seconds, 2), "encode_three_ms": round(encode_ms, 2),
              "rss_growth_mb": round((rss() - before_rss) / 1024 ** 2, 1),
              "dimension": len(vectors[0]) if vectors is not None else None,
              "relevant_similarity": float(vectors[0] @ vectors[1]) if vectors is not None else None,
              "irrelevant_similarity": float(vectors[0] @ vectors[2]) if vectors is not None else None,
              "network": "offline", "artifact": str(runtime / "result.json")}
    (runtime / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if vectors is not None and before == after else 1


if __name__ == "__main__":
    sys.exit(main())
