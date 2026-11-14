"""Automated Unit & Theoretical Verification Suite (`test_tasks.py`).

Runs strictly with the Python 3 standard library (`unittest`, `random`, `tempfile`, `collections`).
Verifies:
1. Bloom Filter: False Negative (FN) == 0%, empirical FPR vs theoretical FPR, and 2-stage disk lookup.
2. Shingling / MinHash / LSH: Candidate extraction recall, exact Jaccard verification, short comment exemption, and bounded window eviction.
3. DGIM: Relative estimation error strictly <= 50% across sliding window queries.
4. Flajolet-Martin: Distinct cardinality estimation within expected variance bounds.
5. Integrated Pipeline: SPAM_SUSPECT / NORMAL / HOLD verdicts and comment deduplication.
"""

from __future__ import annotations

from collections import deque
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from data.split_dataset import cluster_near_duplicates, load_raw_comments, split_clusters
from src import SEED
from src.bloom_filter import BloomFilter, DiskSortedURLStore, TwoStageURLMatcher
from src.lsh_detector import LSHSpamDetector, MinHash, exact_jaccard, extract_k_shingles
from src.normalizer import extract_promo_signals, extract_urls, normalize_text, normalize_url
from src.pipeline import SpamFilterPipeline
from src.server import StreamServerContext, make_request_handler
from src.sketches import DGIMCounter, FlajoletMartin


class TestSpamFilterAlgorithms(unittest.TestCase):
    """Comprehensive verification suite for all core big-data stream algorithms."""

    def test_1_bloom_filter_and_two_stage_disk_lookup(self) -> None:
        """① Verify Bloom Filter FN == 0%, theoretical vs empirical FPR, and 2-stage disk lookup."""
        m = 2000
        target_fpr = 0.02
        bf = BloomFilter(expected_items=m, false_positive_rate=target_fpr, seed=SEED)

        inserted_urls = [f"malicious-site-{i}.example.com/payload/{i}" for i in range(m)]
        for u in inserted_urls:
            bf.add(u)

        # 1-A. False Negative must be strictly 0%
        false_negatives = sum(1 for u in inserted_urls if not bf.might_contain(u))
        self.assertEqual(false_negatives, 0, "Bloom Filter False Negative count must be 0")

        # 1-B. Empirical FPR on non-member queries vs theoretical FPR
        num_neg_queries = 5000
        non_member_urls = [f"clean-benign-site-{i}.org/page/{i}" for i in range(num_neg_queries)]
        fp_count = sum(1 for u in non_member_urls if bf.might_contain(u))
        empirical_fpr = fp_count / num_neg_queries
        theo_fpr = bf.theoretical_fpr()
        self.assertAlmostEqual(
            empirical_fpr,
            theo_fpr,
            delta=0.015,
            msg=f"Empirical FPR ({empirical_fpr:.4f}) deviated too far from theoretical ({theo_fpr:.4f})",
        )

        # 1-C. 2-Stage Disk Verification eliminates all Bloom Filter False Positives
        with tempfile.TemporaryDirectory() as tmpdir:
            sorted_path = Path(tmpdir) / "urls_sorted.txt"
            DiskSortedURLStore.build_sorted_file(inserted_urls, sorted_path)
            matcher = TwoStageURLMatcher(
                sorted_file_path=sorted_path,
                false_positive_rate=0.05,
                expected_items=m,
                seed=SEED,
            )
            for u in inserted_urls[:300]:
                confirmed, bloom_pos = matcher.query(f"https://www.{u}/?utm_source=test")
                self.assertTrue(bloom_pos)
                self.assertTrue(confirmed)

            for u in non_member_urls[:500]:
                confirmed, _ = matcher.query(u)
                self.assertFalse(confirmed, "2-stage disk lookup must eliminate all False Positives")

    def test_2_shingling_minhash_and_lsh_recall(self) -> None:
        """② Verify k-Shingling, MinHash estimation, LSH candidate recall, and bounded window."""
        base_spam = "매일 고정 수익 보장! 오픈카톡 방에서 급등 종목 무료 리딩해드립니다 텔레그램 문의주세요"
        variants = [
            "🔥매일 고정 수익 보장🔥 오픈카톡 방에서 급등 종목 무료 리딩해드립니다! 텔레그램 문의주세요",
            "[긴급] 매일 고정 수익 보장! 오픈카톡 방에서 급등 종목 무료로 리딩해드립니다 텔레그램 문의주세요",
            "매일 고정 수익 보장합니다~ 오픈카톡 방에서 급등 종목 무료 리딩해드립니다 텔레그램 문의주세요!",
        ]

        s_base = extract_k_shingles(base_spam, k=5)
        mh = MinHash(num_hashes=100, seed=SEED)
        sig_base = mh.signature(s_base)

        for var in variants:
            s_var = extract_k_shingles(var, k=5)
            true_j = exact_jaccard(s_base, s_var)
            est_j = mh.estimate_jaccard(sig_base, mh.signature(s_var))
            self.assertGreater(true_j, 0.55)
            self.assertAlmostEqual(true_j, est_j, delta=0.15)

        detector = LSHSpamDetector(
            k=5,
            num_bands=20,
            rows_per_band=5,
            jaccard_threshold=0.50,
            min_char_length=12,
            max_window_size=10,
            seed=SEED,
        )
        # Short comment exemption check
        short_res = detector.query_and_index("short_1", "ㅋㅋㅋㅋㅋ")
        self.assertTrue(short_res.is_short_exempt)

        # Index base spam and check recall on variants
        detector.query_and_index("spam_0", base_spam)
        recalled = 0
        for idx, var in enumerate(variants, start=1):
            res = detector.query_and_index(f"spam_{idx}", var)
            if "spam_0" in res.candidate_ids and res.best_jaccard >= 0.50:
                recalled += 1
        recall = recalled / len(variants)
        self.assertEqual(recall, 1.0, "LSH must recall all high-similarity spam variants")

        # Verify sliding window bound (max_window_size=10)
        for i in range(25):
            detector.query_and_index(f"fill_{i}", f"일반적인 정상 댓글 테스트 문장입니다 번호 {i}")
        self.assertLessEqual(detector.stats()["window_count"], 10)

    def test_3_dgim_error_bound_within_50_percent(self) -> None:
        """③ Verify DGIM sliding window estimate relative error is always <= 50%."""
        window_size = 200
        dgim = DGIMCounter(window_size=window_size)
        exact_window: deque[int] = deque(maxlen=window_size)
        rng = random.Random(SEED)

        for step in range(1, 600):
            bit = 1 if rng.random() < 0.30 else 0
            dgim.update(bit)
            exact_window.append(bit)

            if step >= 20:
                true_ones = sum(exact_window)
                est_ones = dgim.estimate()
                if true_ones > 0:
                    rel_error = abs(est_ones - true_ones) / true_ones
                    self.assertLessEqual(
                        rel_error,
                        0.500001,
                        f"DGIM relative error {rel_error:.4f} exceeded 50% at step {step}",
                    )

    def test_4_flajolet_martin_cardinality_estimation(self) -> None:
        """④ Verify Flajolet-Martin distinct count estimation on stream with duplicates."""
        fm = FlajoletMartin(num_groups=12, hashes_per_group=8, seed=SEED)
        true_unique = 250
        # Insert 250 unique items repeated multiple times
        for repeat in range(5):
            for i in range(true_unique):
                fm.add(f"https://spam-domain-{i}.xyz/path")

        est = fm.estimate()
        self.assertEqual(fm.exact_count(), true_unique)
        # Median-of-means 2^R estimate should be within the same order of magnitude
        self.assertGreater(est, true_unique * 0.35)
        self.assertLess(est, true_unique * 2.5)

    def test_5_integrated_pipeline_and_normalizer(self) -> None:
        """⑤ Verify URL/text normalizer and end-to-end SpamFilterPipeline verdicts."""
        norm_u = normalize_url("HTTPS://WWW.Bit.ly/free-usdt-drop/?utm_source=youtube&fbclid=123#top")
        self.assertEqual(norm_u, "bit.ly/free-usdt-drop")

        urls = extract_urls("여기 클릭 https://airdrop-crypto-bonus.net/claim?si=abc 확인!")
        self.assertEqual(urls, ["airdrop-crypto-bonus.net/claim"])

        signals = extract_promo_signals("무료 코인 에어드랍 텔레그램 문의 https://t.me/crypto_vip_pump_99")
        self.assertIn("CONTACT_TELEGRAM", signals)
        self.assertIn("PROMO_FINANCE", signals)

        pipeline = SpamFilterPipeline(seed=SEED)
        r_normal = pipeline.analyze_comment("c1", "오늘 영상 설명이 정말 이해하기 쉽네요!", video_id="v1")
        self.assertEqual(r_normal["status"], "NORMAL")

        r_mal_url = pipeline.analyze_comment(
            "c2",
            "무료 수령 https://airdrop-crypto-bonus.net/claim?utm_medium=comment",
            video_id="v1",
            author_id="/@spammer_01",
        )
        self.assertEqual(r_mal_url["status"], "SPAM_SUSPECT")
        self.assertEqual(r_mal_url["badge_type"], "MALICIOUS_URL")

        # Duplicate comment_id must be deduplicated without incrementing observed_count
        r_dup = pipeline.analyze_comment("c2", "무료 수령", video_id="v1")
        self.assertTrue(r_dup["deduplicated"])
        stats = pipeline.get_or_create_video_state("v1").snapshot_stats()
        self.assertEqual(stats["observed_count"], 2)
        self.assertEqual(stats["spam_suspect_count"], 1)

    def test_6_local_http_server_endpoints(self) -> None:
        """⑥ Verify local HTTP API server endpoints (/health, /analyze, /reset)."""
        ctx = StreamServerContext()
        handler_cls = make_request_handler(ctx)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            base_url = f"http://127.0.0.1:{port}"
            # 1. GET /health
            with urlopen(f"{base_url}/health") as resp:
                self.assertEqual(resp.status, 200)
                health = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(health["status"], "ok")

            # 2. POST /analyze
            payload = {
                "video_id": "test_vid_99",
                "comments": [
                    {"comment_id": "k1", "text": "좋은 영상 감사합니다!"},
                    {
                        "comment_id": "k2",
                        "text": "무료 에어드랍 클릭 https://airdrop-crypto-bonus.net/claim",
                        "author_id": "/@spam_bot",
                    },
                ],
            }
            req = Request(
                f"{base_url}/analyze",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(body["results"]), 2)
                self.assertEqual(body["results"][0]["status"], "NORMAL")
                self.assertEqual(body["results"][1]["status"], "SPAM_SUSPECT")
                self.assertEqual(body["stream_stats"]["observed_count"], 2)

            # 3. POST /reset
            reset_req = Request(
                f"{base_url}/reset",
                data=json.dumps({"video_id": "test_vid_99"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(reset_req) as resp:
                self.assertEqual(resp.status, 200)
                reset_body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(reset_body["stream_stats"]["observed_count"], 0)
        finally:
            server.shutdown()
            server.server_close()

    def test_7_dataset_splitter_no_leakage(self) -> None:
        """⑦ Verify near-duplicate clustering & stratified splitting prevent data leakage."""
        comments, _ = load_raw_comments()
        self.assertGreater(len(comments), 0)

        clusters = cluster_near_duplicates(comments, jaccard_threshold=0.55, k=5)
        ref_split, val_split, test_split = split_clusters(clusters, seed=SEED)

        self.assertGreater(len(ref_split), 0)
        self.assertGreater(len(val_split), 0)
        self.assertGreater(len(test_split), 0)

        for split_name, split_items in (("validation", val_split), ("test", test_split)):
            labels = {item.get("label") for item in split_items}
            self.assertIn(1, labels, f"{split_name} split must contain spam comments")
            self.assertIn(0, labels, f"{split_name} split must contain normal comments")

        split_pairs = (
            ("reference", ref_split, "validation", val_split),
            ("reference", ref_split, "test", test_split),
            ("validation", val_split, "test", test_split),
        )
        for name_a, items_a, name_b, items_b in split_pairs:
            for item_a in items_a:
                norm_a = normalize_text(item_a["text"])
                if len(norm_a) < 12:
                    continue
                shingles_a = extract_k_shingles(norm_a, k=5)
                for item_b in items_b:
                    norm_b = normalize_text(item_b["text"])
                    if len(norm_b) < 12:
                        continue
                    sim = exact_jaccard(shingles_a, extract_k_shingles(norm_b, k=5))
                    self.assertLess(
                        sim,
                        0.55,
                        f"Near-duplicate leakage detected between {name_a} and {name_b} (Jaccard={sim:.4f})",
                    )


if __name__ == "__main__":
    print("=" * 72)
    print("[YouTube Stream Spam Filter] Running Core Algorithm Verification Suite")
    print("=" * 72)
    unittest.main(verbosity=2)
