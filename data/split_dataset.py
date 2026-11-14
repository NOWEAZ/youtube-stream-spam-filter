"""UCI YouTube Spam Collection Near-Duplicate Splitter (`data/split_dataset.py`).

Complies strictly with AGENTS.md rules:
1. Pure Python 3 standard library implementation (`csv`, `json`, `random`, `pathlib`).
2. Fixed SEED = 42 for all random shuffling and cluster allocations.
3. Does NOT modify raw data files in `data/`.
4. Prevents Data Leakage:
   - Groups comments into near-duplicate clusters using character 5-shingles and Jaccard similarity.
   - Assigns entire clusters (not individual comments) to Reference, Validation, or Test splits.
5. Handles both raw UCI CSVs (Youtube01-Psy.csv, etc.) and fallback synthetic stream data.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import SEED
from src.lsh_detector import exact_jaccard, extract_k_shingles
from src.normalizer import normalize_text

UCI_DIR = ROOT_DIR / "data" / "uci_spam"
SYNTHETIC_PATH = ROOT_DIR / "data" / "synthetic_stream" / "sample_stream.json"


def load_raw_comments() -> tuple[list[dict[str, Any]], str]:
    """Load comments from raw UCI CSVs if available, otherwise from synthetic stream."""
    uci_csv_files = sorted(UCI_DIR.glob("Youtube*.csv"))
    records: list[dict[str, Any]] = []

    if uci_csv_files:
        for csv_path in uci_csv_files:
            video_prefix = csv_path.stem.replace("Youtube", "").lower()
            with csv_path.open("r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for idx, row in enumerate(reader):
                    content = row.get("CONTENT", "").strip()
                    if not content:
                        continue
                    cid = row.get("COMMENT_ID") or f"{video_prefix}_{idx}"
                    author = row.get("AUTHOR", "")
                    try:
                        label = int(row.get("CLASS", 0))
                    except ValueError:
                        label = 0
                    records.append({
                        "comment_id": cid,
                        "video_id": video_prefix,
                        "author_id": author,
                        "text": content,
                        "label": label,
                        "source_file": csv_path.name,
                    })
        return records, f"UCI YouTube Spam Collection ({len(uci_csv_files)} files)"

    # Fallback to sample_stream.json
    if SYNTHETIC_PATH.exists():
        with SYNTHETIC_PATH.open("r", encoding="utf-8") as f:
            items = json.load(f)
            return items, f"Synthetic Stream ({SYNTHETIC_PATH.name})"

    return [], "None"


def cluster_near_duplicates(
    comments: list[dict[str, Any]],
    jaccard_threshold: float = 0.55,
    k: int = 5,
) -> list[list[dict[str, Any]]]:
    """Group comments into near-duplicate clusters via Union-Find on pairwise Jaccard."""
    n = len(comments)
    if n == 0:
        return []

    norms = [normalize_text(item["text"]) for item in comments]
    shingles_list = [extract_k_shingles(norm, k=k) for norm in norms]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for i in range(n):
        norm_i = norms[i]
        shingles_i = shingles_list[i]
        len_i = len(shingles_i)
        is_long_i = len(norm_i) >= 12
        for j in range(i + 1, n):
            norm_j = norms[j]
            if is_long_i and len(norm_j) >= 12:
                shingles_j = shingles_list[j]
                len_j = len(shingles_j)
                if len_i and len_j:
                    max_len = len_i if len_i >= len_j else len_j
                    min_len = len_j if len_i >= len_j else len_i
                    if (min_len / max_len) < jaccard_threshold:
                        continue
                if exact_jaccard(shingles_i, shingles_j) >= jaccard_threshold:
                    union(i, j)
            elif norm_i.lower() == norm_j.lower():
                union(i, j)

    grouped: dict[int, list[dict[str, Any]]] = {}
    for idx in range(n):
        root = find(idx)
        grouped.setdefault(root, []).append(comments[idx])

    return list(grouped.values())


def split_clusters(
    clusters: list[list[dict[str, Any]]],
    ref_ratio: float = 0.20,
    val_ratio: float = 0.35,
    seed: int = SEED,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split clusters into stratified Reference (spam), Validation, and Test sets."""
    rng = random.Random(seed)
    shuffled_clusters = list(clusters)
    rng.shuffle(shuffled_clusters)

    spam_clusters = [c for c in shuffled_clusters if any(item.get("label") == 1 for item in c)]
    normal_clusters = [c for c in shuffled_clusters if not any(item.get("label") == 1 for item in c)]

    ref_split: list[dict[str, Any]] = []
    val_split: list[dict[str, Any]] = []
    test_split: list[dict[str, Any]] = []

    num_spam = len(spam_clusters)
    if num_spam >= 3:
        n_ref = max(1, min(num_spam - 2, round(num_spam * 0.30)))
        n_val = max(1, min(num_spam - n_ref - 1, round(num_spam * 0.35)))
        for c in spam_clusters[:n_ref]:
            ref_split.extend(c)
        for c in spam_clusters[n_ref : n_ref + n_val]:
            val_split.extend(c)
        for c in spam_clusters[n_ref + n_val :]:
            test_split.extend(c)
    else:
        for idx, c in enumerate(spam_clusters):
            if idx == 0:
                ref_split.extend(c)
            elif idx == 1:
                val_split.extend(c)
            else:
                test_split.extend(c)

    num_normal = len(normal_clusters)
    if num_normal >= 2:
        n_norm_val = max(1, min(num_normal - 1, round(num_normal * 0.45)))
        for c in normal_clusters[:n_norm_val]:
            val_split.extend(c)
        for c in normal_clusters[n_norm_val:]:
            test_split.extend(c)
    elif num_normal == 1:
        test_split.extend(normal_clusters[0])

    return ref_split, val_split, test_split


def run_split() -> dict[str, Any]:
    """Execute data loading, near-duplicate clustering, and non-leaking dataset split."""
    comments, source_desc = load_raw_comments()
    if not comments:
        print("[Splitter] No comments found to split.")
        return {}

    clusters = cluster_near_duplicates(comments, jaccard_threshold=0.55, k=5)
    multi_item_clusters = sum(1 for c in clusters if len(c) > 1)

    ref_split, val_split, test_split = split_clusters(clusters, ref_ratio=0.20, val_ratio=0.35, seed=SEED)

    out_ref_path = UCI_DIR / "split_reference.json"
    out_val_path = UCI_DIR / "split_validation.json"
    out_test_path = UCI_DIR / "split_test.json"
    out_summary_path = UCI_DIR / "split_summary.json"

    with out_ref_path.open("w", encoding="utf-8") as f:
        json.dump(ref_split, f, ensure_ascii=False, indent=2)

    with out_val_path.open("w", encoding="utf-8") as f:
        json.dump(val_split, f, ensure_ascii=False, indent=2)

    with out_test_path.open("w", encoding="utf-8") as f:
        json.dump(test_split, f, ensure_ascii=False, indent=2)

    summary = {
        "seed": SEED,
        "source": source_desc,
        "total_comments": len(comments),
        "total_clusters": len(clusters),
        "multi_comment_near_duplicate_clusters": multi_item_clusters,
        "splits": {
            "reference": {
                "count": len(ref_split),
                "spam_count": sum(1 for x in ref_split if x.get("label") == 1),
                "path": str(out_ref_path.relative_to(ROOT_DIR)),
            },
            "validation": {
                "count": len(val_split),
                "spam_count": sum(1 for x in val_split if x.get("label") == 1),
                "path": str(out_val_path.relative_to(ROOT_DIR)),
            },
            "test": {
                "count": len(test_split),
                "spam_count": sum(1 for x in test_split if x.get("label") == 1),
                "path": str(out_test_path.relative_to(ROOT_DIR)),
            },
        },
    }

    with out_summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[Splitter] Source: {source_desc}")
    print(f"[Splitter] Total Comments: {len(comments)}, Clusters: {len(clusters)} (Near-duplicate groups: {multi_item_clusters})")
    print(f"[Splitter] Reference: {len(ref_split)}, Validation: {len(val_split)}, Test: {len(test_split)}")
    print(f"[Splitter] Saved summary to {out_summary_path}")

    return summary


if __name__ == "__main__":
    run_split()
