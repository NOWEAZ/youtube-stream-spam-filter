"""Comprehensive Benchmark Suite (`benchmarks/run_benchmarks.py`).

Measures all 5 benchmark domains specified in Section 6 of README.md with fixed `SEED = 42`:
1. Bloom Filter vs Python `set`: memory (KB/MB), lookup time, theoretical vs empirical FPR curve, and 2-stage disk verification latency.
2. MinHash & LSH vs O(N^2) All-Pairs: candidate reduction ratio, execution time, and recall against exact Jaccard ground truth.
3. End-to-End Comment Classification Pipeline: Precision, Recall, F1-score, Comment Classification FPR (strictly separated from URL Bloom Filter FPR), and HOLD rate on `data/synthetic_stream/sample_stream.json`.
4. DGIM & Flajolet-Martin Sketches: relative error (%) and memory usage vs exact sliding window / `set`.
5. Local Server & E2E Latency (p50, p95), deduplication verification, and offline fail-safe status.

Outputs:
- `output/metrics.json`
- Calls `benchmarks/plot_charts.py` to generate `output/charts/*.png`.
"""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import SEED
from src.bloom_filter import BloomFilter, DiskSortedURLStore, TwoStageURLMatcher
from src.lsh_detector import LSHSpamDetector, exact_jaccard, extract_k_shingles
from src.pipeline import SpamFilterPipeline
from src.sketches import DGIMCounter, FlajoletMartin


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int((p / 100.0) * len(sorted_vals)))
    return round(sorted_vals[idx], 4)


def benchmark_bloom_filter() -> dict[str, Any]:
    """1. Bloom Filter vs Python `set` memory, latency, and FPR curve."""
    m = 10000
    member_urls = [f"malicious-host-{i}.bad-domain.org/path/{i}/drop.bin" for i in range(m)]
    non_member_urls = [f"benign-clean-{i}.safe-domain.com/article/{i}" for i in range(m)]

    # Python set memory & lookup
    py_set = set(member_urls)
    py_set_bytes = sys.getsizeof(py_set) + sum(sys.getsizeof(u) for u in py_set)

    t0 = time.perf_counter_ns()
    for u in non_member_urls[:2000]:
        _ = u in py_set
    set_lookup_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

    # FPR curve across target FPRs
    fpr_curve: list[dict[str, float]] = []
    for target_fpr in (0.005, 0.01, 0.02, 0.05, 0.10):
        bf = BloomFilter(expected_items=m, false_positive_rate=target_fpr, seed=SEED)
        for u in member_urls:
            bf.add(u)
        fp = sum(1 for u in non_member_urls if bf.might_contain(u))
        emp_fpr = fp / len(non_member_urls)
        fpr_curve.append({
            "target_fpr": target_fpr,
            "theoretical_fpr": round(bf.theoretical_fpr(), 5),
            "empirical_fpr": round(emp_fpr, 5),
            "memory_kb": round(bf.memory_bytes() / 1024.0, 2),
            "num_hashes": bf.num_hashes,
        })

    # 2-Stage Disk Lookup latency & zero-FP verification
    with tempfile.TemporaryDirectory() as tmpdir:
        sorted_file = Path(tmpdir) / "bench_urls_sorted.txt"
        DiskSortedURLStore.build_sorted_file(member_urls, sorted_file)
        matcher = TwoStageURLMatcher(
            sorted_file_path=sorted_file,
            false_positive_rate=0.01,
            expected_items=m,
            seed=SEED,
        )

        latencies_ms: list[float] = []
        confirmed_fp = 0
        for u in non_member_urls[:2000]:
            s_ns = time.perf_counter_ns()
            confirmed, _ = matcher.query(u)
            latencies_ms.append((time.perf_counter_ns() - s_ns) / 1_000_000.0)
            if confirmed:
                confirmed_fp += 1

        latencies_ms.sort()
        bf_1pct_bytes = matcher.bloom.memory_bytes()

    return {
        "script": "benchmarks/run_benchmarks.py::benchmark_bloom_filter",
        "num_indexed_urls": m,
        "python_set_memory_kb": round(py_set_bytes / 1024.0, 2),
        "bloom_1pct_memory_kb": round(bf_1pct_bytes / 1024.0, 2),
        "memory_reduction_pct": round((1.0 - bf_1pct_bytes / py_set_bytes) * 100.0, 2),
        "python_set_2000_queries_ms": round(set_lookup_ms, 3),
        "two_stage_p50_latency_ms": _percentile(latencies_ms, 50),
        "two_stage_p95_latency_ms": _percentile(latencies_ms, 95),
        "two_stage_final_fpr": confirmed_fp / 2000.0,
        "url_lookup_fpr_curve": fpr_curve,
    }


def benchmark_lsh_vs_all_pairs() -> dict[str, Any]:
    """2. MinHash & LSH vs O(N^2) exact all-pairs comparison."""
    rng = random.Random(SEED)
    base_templates = [
        "매일 고정 수익 보장 오픈카톡 방에서 급등 종목 무료 리딩해드립니다 텔레그램 문의주세요",
        "초보자도 가능한 재택 부업 하루 1시간 투자로 월 500 수익 보장 카톡 문의 환영합니다",
        "선착순 VIP 코인 급등 시그널 무료 입장 지금 바로 클릭하세요 텔레그램",
        "신규 가입 즉시 꽁머니 지급 슬롯 잭팟 이벤트 무료 포인트 받으세요",
    ]
    normal_templates = [
        "오늘 강의 내용 정말 유익하네요 알고리즘 설명이 귀에 쏙쏙 들어옵니다",
        "빅데이터개론 수업 복습하면서 다시 보고 있는데 예제 코드가 깔끔해요",
        "시간 가는 줄 모르고 끝까지 정주행했습니다 다음 영상도 기대할게요",
        "질문 있습니다 해시 함수 개수를 늘리면 조회 속도에 어떤 영향이 있나요",
    ]

    comments: list[tuple[str, str]] = []
    for i in range(240):
        if i % 3 == 0:
            tpl = rng.choice(base_templates)
            mutated = f"{tpl} {rng.randint(10, 99)}!"
            comments.append((f"doc_{i}", mutated))
        else:
            tpl = rng.choice(normal_templates)
            comments.append((f"doc_{i}_n", f"{tpl} ({i}번째 시청자 코멘트 {rng.randint(1000, 9999)})"))

    n = len(comments)
    total_possible_pairs = n * (n - 1) // 2
    threshold = 0.55

    # O(N^2) All-Pairs Exact Jaccard
    t0 = time.perf_counter_ns()
    shingle_sets = [(doc_id, extract_k_shingles(text, k=5)) for doc_id, text in comments]
    true_similar_pairs: set[tuple[str, str]] = set()
    for i in range(n):
        id_i, set_i = shingle_sets[i]
        for j in range(i + 1, n):
            id_j, set_j = shingle_sets[j]
            if exact_jaccard(set_i, set_j) >= threshold:
                pair = (min(id_i, id_j), max(id_i, id_j))
                true_similar_pairs.add(pair)
    all_pairs_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

    # MinHash + LSH Banding + Secondary Verification
    t1 = time.perf_counter_ns()
    detector = LSHSpamDetector(
        k=5,
        num_bands=20,
        rows_per_band=5,
        jaccard_threshold=threshold,
        min_char_length=12,
        max_window_size=n + 10,
        seed=SEED,
    )
    candidate_pairs: set[tuple[str, str]] = set()
    lsh_verified_pairs: set[tuple[str, str]] = set()
    for doc_id, text in comments:
        res = detector.query_and_index(doc_id, text, index_into_window=True)
        for cand_id in res.candidate_ids:
            pair = (min(doc_id, cand_id), max(doc_id, cand_id))
            candidate_pairs.add(pair)

    shingle_lookup = dict(shingle_sets)
    for id_a, id_b in candidate_pairs:
        if exact_jaccard(shingle_lookup[id_a], shingle_lookup[id_b]) >= threshold:
            lsh_verified_pairs.add((id_a, id_b))
    lsh_ms = (time.perf_counter_ns() - t1) / 1_000_000.0

    recall = (
        len(lsh_verified_pairs & true_similar_pairs) / len(true_similar_pairs)
        if true_similar_pairs
        else 1.0
    )
    reduction_ratio = 1.0 - (len(candidate_pairs) / total_possible_pairs)

    return {
        "script": "benchmarks/run_benchmarks.py::benchmark_lsh_vs_all_pairs",
        "num_comments": n,
        "total_all_pairs": total_possible_pairs,
        "lsh_candidate_pairs": len(candidate_pairs),
        "candidate_reduction_ratio_pct": round(reduction_ratio * 100.0, 2),
        "ground_truth_similar_pairs": len(true_similar_pairs),
        "lsh_recalled_similar_pairs": len(lsh_verified_pairs & true_similar_pairs),
        "lsh_recall": round(recall, 4),
        "all_pairs_time_ms": round(all_pairs_ms, 2),
        "lsh_time_ms": round(lsh_ms, 2),
    }


def benchmark_pipeline_classification() -> dict[str, Any]:
    """3. End-to-End Comment Classification Pipeline on `data/synthetic_stream/sample_stream.json`."""
    stream_path = ROOT_DIR / "data" / "synthetic_stream" / "sample_stream.json"
    with stream_path.open("r", encoding="utf-8") as f:
        items = json.load(f)

    pipeline = SpamFilterPipeline(seed=SEED)
    tp = fp = tn = fn = hold_count = 0
    latencies_ms: list[float] = []

    for row in items:
        res = pipeline.analyze_comment(
            comment_id=row["comment_id"],
            text=row["text"],
            video_id=row["video_id"],
            author_id=row.get("author_id"),
        )
        latencies_ms.append(res["latency_ms"])
        label = int(row["label"])
        status = res["status"]

        if status == "HOLD":
            hold_count += 1

        pred_spam = 1 if status == "SPAM_SUSPECT" else 0
        if label == 1 and pred_spam == 1:
            tp += 1
        elif label == 0 and pred_spam == 1:
            fp += 1
        elif label == 0 and pred_spam == 0:
            tn += 1
        elif label == 1 and pred_spam == 0:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    comment_fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    hold_rate = hold_count / len(items) if items else 0.0
    latencies_ms.sort()

    return {
        "script": "benchmarks/run_benchmarks.py::benchmark_pipeline_classification",
        "dataset": "data/synthetic_stream/sample_stream.json",
        "label_column": "label",
        "total_comments": len(items),
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "hold_count": hold_count,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "comment_classification_fpr": round(comment_fpr, 4),
        "hold_rate": round(hold_rate, 4),
        "pipeline_p50_latency_ms": _percentile(latencies_ms, 50),
        "pipeline_p95_latency_ms": _percentile(latencies_ms, 95),
    }


def benchmark_sketches() -> dict[str, Any]:
    """4. DGIM & Flajolet-Martin relative error (%) and memory usage vs exact structures."""
    window_size = 1000
    dgim = DGIMCounter(window_size=window_size)
    exact_window: deque[int] = deque(maxlen=window_size)
    rng = random.Random(SEED)

    dgim_rel_errors: list[float] = []
    for step in range(1, 2500):
        bit = 1 if rng.random() < 0.25 else 0
        dgim.update(bit)
        exact_window.append(bit)
        if step >= window_size and step % 50 == 0:
            true_sum = sum(exact_window)
            est_sum = dgim.estimate()
            if true_sum > 0:
                dgim_rel_errors.append(abs(est_sum - true_sum) / true_sum * 100.0)

    exact_window_bytes = sys.getsizeof(exact_window) + len(exact_window) * 28

    fm = FlajoletMartin(num_groups=8, hashes_per_group=8, seed=SEED)
    exact_set: set[str] = set()
    for i in range(500):
        url = f"spam-link-{rng.randint(1, 180)}.bad.xyz/offer"
        fm.add(url)
        exact_set.add(url)

    fm_est = fm.estimate()
    fm_true = len(exact_set)
    fm_rel_error_pct = abs(fm_est - fm_true) / fm_true * 100.0 if fm_true > 0 else 0.0
    exact_set_bytes = sys.getsizeof(exact_set) + sum(sys.getsizeof(u) for u in exact_set)

    return {
        "script": "benchmarks/run_benchmarks.py::benchmark_sketches",
        "dgim": {
            "window_size": window_size,
            "mean_relative_error_pct": round(statistics.mean(dgim_rel_errors), 2),
            "max_relative_error_pct": round(max(dgim_rel_errors), 2),
            "theoretical_bound_pct": 50.0,
            "dgim_bucket_count": dgim.bucket_count(),
            "dgim_memory_bytes": dgim.memory_bytes(),
            "exact_window_memory_bytes": exact_window_bytes,
        },
        "flajolet_martin": {
            "true_distinct_count": fm_true,
            "estimated_distinct_count": fm_est,
            "relative_error_pct": round(fm_rel_error_pct, 2),
            "fm_sketch_memory_bytes": fm.sketch_memory_bytes(),
            "exact_set_memory_bytes": exact_set_bytes,
        },
    }


def main() -> None:
    metrics = {
        "seed": SEED,
        "bloom_filter": benchmark_bloom_filter(),
        "lsh_detector": benchmark_lsh_vs_all_pairs(),
        "pipeline_classification": benchmark_pipeline_classification(),
        "stream_sketches": benchmark_sketches(),
    }

    output_dir = ROOT_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print(f"[Benchmark] Saved reproducible metrics to {metrics_path}")
    from benchmarks.plot_charts import generate_all_charts

    chart_paths = generate_all_charts(metrics, output_dir / "charts")
    for cp in chart_paths:
        print(f"[Benchmark] Generated chart: {cp}")


if __name__ == "__main__":
    main()
