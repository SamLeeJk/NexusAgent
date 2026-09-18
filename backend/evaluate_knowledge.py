"""Reproducible local lexical evaluation; does not call a model or mutate the app DB."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from database import Database
from knowledge import KnowledgeStore
from knowledge_retrieval import ALGORITHM, MIN_COVERAGE, rank_chunks
from rank_bm25 import BM25Okapi
from retrieval import tokenize


def legacy_baseline(sources, query):
    """Stage-two tokenizer/overlap gate on the same chunks; not a full old-system replay."""
    corpus = [tokenize(s["heading"] + " " + s["text"]) for s in sources]
    terms = tokenize(query)
    scores = BM25Okapi(corpus).get_scores(terms)
    candidates = [i for i, c in enumerate(corpus) if set(terms).intersection(c)]
    candidates.sort(key=lambda i: (-float(scores[i]), sources[i]["chunk_id"]))
    return {"sources": [sources[i] for i in candidates[:3]]}


def measure(sources, cases, ranker):
    records, hits, reciprocal, rejected, elapsed = [], 0, 0.0, 0, []
    for case in cases:
        start = perf_counter()
        found = ranker(sources, case["query"])["sources"]
        elapsed.append((perf_counter() - start) * 1000)
        gold = [
            s["chunk_id"]
            for s in sources
            if case["heading"] and case["heading"] in s["heading"].split(" / ")
        ]
        if case["heading"] and not gold:
            raise ValueError(f"Missing annotated section for {case['id']}")
        rank = next(
            (i + 1 for i, item in enumerate(found) if item["chunk_id"] in gold), None
        )
        hits += bool(rank)
        reciprocal += 1 / rank if rank else 0
        rejected += not case["heading"] and not found
        records.append(
            {
                "id": case["id"],
                "query": case["query"],
                "gold_section": case["heading"],
                "relevant_rank": rank,
                "rejected": not found,
                "returned_sections": [s["heading"] for s in found],
            }
        )
    answerable = sum(bool(c["heading"]) for c in cases)
    unanswerable = len(cases) - answerable
    return {
        "answerable_count": answerable,
        "unanswerable_count": unanswerable,
        "hit_at_3": hits / answerable,
        "mrr_at_3": reciprocal / answerable,
        "no_answer_rejection": rejected / unanswerable,
        "mean_ranking_ms": sum(elapsed) / len(elapsed),
        "cases": records,
    }


def evaluate(output: Path):
    root = Path(__file__).parent
    manifest = root / "evals" / "knowledge_cases.json"
    spec = json.loads(manifest.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="knowledge-eval-", dir=output.parent) as folder:
        db = Database("sqlite:///" + str(Path(folder) / "eval.db"))
        try:
            db.migrate()
            db.seed_demo()
            corpus = KnowledgeStore(db).active_chunks()
            baseline = measure(corpus, spec["cases"], legacy_baseline)
            current = measure(corpus, spec["cases"], rank_chunks)
        finally:
            db.engine.dispose()
    corpus_text = "\n".join(s["heading"] + "\n" + s["text"] for s in corpus)
    gate_results = {
        key: current[key] >= target for key, target in spec["gates"].items()
    }
    report = {
        "dataset_version": spec["dataset_version"],
        "dataset_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus_text.encode()).hexdigest(),
        "chunk_count": len(corpus),
        "algorithm": ALGORITHM,
        "min_coverage": MIN_COVERAGE,
        "python": platform.python_version(),
        "scope": "Synthetic regression only; both rankers use the same chunks. Timing excludes DB I/O.",
        "gates": spec["gates"],
        "gate_results": gate_results,
        "passed": all(gate_results.values()),
        "baseline": baseline,
        "current": current,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 知识检索基线验收",
        "",
        f"数据集：{spec['dataset_version']}；20 个可回答问题、8 个无答案问题。",
        "",
        "两个排序器使用相同的已发布分块。旧分词基线并非完整旧系统回放；此合成集不代表线上准确率。",
        "",
    ]
    for label, values in (
        ("旧分词与重叠门限基线", baseline),
        ("中英文词法检索", current),
    ):
        lines += [
            f"## {label}",
            "",
            f"- Hit@3：{values['hit_at_3']:.1%}",
            f"- MRR@3：{values['mrr_at_3']:.1%}",
            f"- 无答案拒绝率：{values['no_answer_rejection']:.1%}",
            "",
        ]
    lines += [
        f"预设门槛：{'通过' if report['passed'] else '未全部通过'}。",
        "",
        f"用例 SHA256：`{report['dataset_sha256']}`",
        "",
        "完整逐题结果、门槛和内容哈希见同名 JSON。真实模型与向量检索未参与本实验。",
    ]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1]
        / "docs"
        / "validation"
        / "stage3a-retrieval.json",
    )
    args = parser.parse_args()
    report = evaluate(args.output)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "metrics": {k: report["current"][k] for k in report["gates"]},
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)
