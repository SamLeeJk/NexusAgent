"""Compare frozen Stage 3B lexical, vector and hybrid retrieval modes."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from types import SimpleNamespace

from database import Database
from hybrid_retrieval import HybridRetriever
from knowledge import KnowledgeStore


def settings(mode, qdrant_url, collection, cache_dir, threshold):
    return SimpleNamespace(
        retrieval_mode=mode,
        qdrant_url=qdrant_url,
        qdrant_collection=collection,
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_revision="faf4aa4225822f3bc6376869cb1164e8e3feedd0",
        embedding_cache_dir=cache_dir,
        vector_score_threshold=threshold,
    )


def measure(sources, cases, retriever):
    records, hits, reciprocal, rejected, latencies, fallbacks = [], 0, 0.0, 0, [], 0
    for case in cases:
        started = perf_counter()
        result = retriever.search(sources, case["query"], 3)
        latencies.append((perf_counter() - started) * 1000)
        found = result["sources"]
        gold = [
            s["chunk_id"]
            for s in sources
            if case["heading"] and case["heading"] in s["heading"].split(" / ")
        ]
        rank = next(
            (i + 1 for i, item in enumerate(found) if item["chunk_id"] in gold), None
        )
        hits += bool(rank)
        reciprocal += 1 / rank if rank else 0
        rejected += not case["heading"] and not found
        fallbacks += bool(result.get("fallback"))
        records.append(
            {
                "id": case["id"],
                "gold_section": case["heading"],
                "relevant_rank": rank,
                "rejected": not found,
                "returned_sections": [s["heading"] for s in found],
                "returned_scores": [
                    s.get("vector_score", s.get("score")) for s in found
                ],
                "fallback": result.get("fallback", False),
            }
        )
    answerable = sum(bool(c["heading"]) for c in cases)
    unanswerable = len(cases) - answerable
    ordered = sorted(latencies)
    return {
        "answerable_count": answerable,
        "unanswerable_count": unanswerable,
        "hit_at_3": hits / answerable,
        "mrr_at_3": reciprocal / answerable,
        "no_answer_rejection": rejected / unanswerable,
        "mean_ms": sum(latencies) / len(latencies),
        "p95_ms": ordered[max(0, int(len(ordered) * 0.95) - 1)],
        "fallback_count": fallbacks,
        "cases": records,
    }


def evaluate(output, qdrant_url, cache_dir, threshold):
    root = Path(__file__).parent
    manifest = root / "evals" / "knowledge_cases_v2.json"
    spec = json.loads(manifest.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="hybrid-eval-", dir=output.parent) as folder:
        db = Database("sqlite:///" + str(Path(folder) / "eval.db"))
        try:
            db.migrate()
            db.seed_demo()
            sources = KnowledgeStore(db).active_chunks()
            results = {}
            for mode in ("lexical", "vector", "hybrid"):
                retriever = HybridRetriever(
                    settings(
                        mode,
                        qdrant_url,
                        "nexus_stage3b_eval",
                        cache_dir,
                        threshold,
                    )
                )
                results[mode] = measure(sources, spec["cases"], retriever)
        finally:
            db.engine.dispose()
    hybrid, lexical = results["hybrid"], results["lexical"]
    observed = {
        "hybrid_hit_at_3": hybrid["hit_at_3"],
        "hybrid_mrr_at_3": hybrid["mrr_at_3"],
        "hybrid_no_answer_rejection": hybrid["no_answer_rejection"],
        "hit_at_3_gain_over_lexical": hybrid["hit_at_3"] - lexical["hit_at_3"],
    }
    gate_results = {key: observed[key] >= target for key, target in spec["gates"].items()}
    corpus_text = "\n".join(s["heading"] + "\n" + s["text"] for s in sources)
    report = {
        "dataset_version": spec["dataset_version"],
        "dataset_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus_text.encode()).hexdigest(),
        "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "model_revision": "faf4aa4225822f3bc6376869cb1164e8e3feedd0",
        "vector_size": 384,
        "vector_score_threshold": threshold,
        "python": platform.python_version(),
        "scope": (
            "Frozen synthetic hard set; local ONNX embeddings and Qdrant; excludes DB "
            "I/O; each mode includes its first lazy model/index initialization."
        ),
        "gates": spec["gates"],
        "observed": observed,
        "gate_results": gate_results,
        "passed": all(gate_results.values()),
        "modes": results,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 阶段 3B 混合检索验收",
        "",
        f"冻结数据集：{spec['dataset_version']}（48 个可回答问题、32 个无答案问题）。",
        "",
        "| 模式 | Hit@3 | MRR@3 | 无答案拒绝率 | 平均延迟 | P95 延迟 | 降级次数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("lexical", "vector", "hybrid"):
        value = results[mode]
        lines.append(
            f"| {mode} | {value['hit_at_3']:.1%} | {value['mrr_at_3']:.1%} | "
            f"{value['no_answer_rejection']:.1%} | {value['mean_ms']:.1f} ms | "
            f"{value['p95_ms']:.1f} ms | {value['fallback_count']} |"
        )
    lines += [
        "",
        f"预设门槛：{'通过' if report['passed'] else '未全部通过'}。",
        "",
        f"数据集 SHA256：`{report['dataset_sha256']}`",
        "",
        "逐题结果、内容哈希和门槛见同名 JSON。该合成集用于回归，不代表线上准确率。",
    ]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "docs" / "validation" / "stage3b-hybrid.json",
    )
    args = parser.parse_args()
    report = evaluate(args.output, args.qdrant_url, args.cache_dir or None, args.threshold)
    print(json.dumps({"passed": report["passed"], "observed": report["observed"]}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
