"""Integrated Stream Spam Detection Pipeline (Steps A, B, C, D).

Combines:
- Step A: URL Normalization + 2-Stage Bloom Filter & Disk Verification (`src/bloom_filter.py`)
- Step B: k-Shingling + MinHash + LSH Banding + Exact Jaccard + Promo Signals (`src/lsh_detector.py`)
- Step C: Verdict Determination (`SPAM_SUSPECT` | `NORMAL` | `HOLD`) & Reason Generation
- Step D: Per-Video Stream Sketches (`DGIMCounter` & `FlajoletMartin` in `src/sketches.py`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time
from typing import Any

from src import SEED
from src.bloom_filter import TwoStageURLMatcher
from src.llm_verifier import OpenCodeHoldVerifier
from src.lsh_detector import LSHSpamDetector
from src.normalizer import extract_promo_signals, extract_urls, normalize_text
from src.sketches import DGIMCounter, FlajoletMartin

DEFAULT_SORTED_URLS_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "urlhaus_phishtank"
    / "sample_urls_sorted.txt"
)

DEFAULT_REFERENCE_SPAM_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "ref_spam_01",
        "매일 고정 수익 보장 오픈카톡 방에서 급등 종목 무료 리딩해드립니다 텔레그램 문의주세요",
    ),
    (
        "ref_spam_02",
        "초보자도 가능한 재택 부업 하루 1시간 투자로 월 500 수익 보장 카톡 문의 환영",
    ),
    (
        "ref_spam_03",
        "선착순 VIP 코인 급등 시그널 무료 입장 텔레그램 문의",
    ),
    (
        "ref_spam_04",
        "신규 가입 즉시 꽁머니 지급 슬롯 잭팟 이벤트 무료 포인트 받으세요",
    ),
)


@dataclass
class VideoStreamState:
    """Per-video stream state holding LSH window and DGIM / Flajolet-Martin sketches."""

    video_id: str
    dgim_window_size: int = 1000
    lsh_window_size: int = 500
    seed: int = SEED
    observed_count: int = 0
    spam_suspect_count: int = 0
    hold_count: int = 0
    normal_count: int = 0
    seen_comment_ids: set[str] = field(default_factory=set)
    cached_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    dgim: DGIMCounter = field(init=False)
    fm_urls: FlajoletMartin = field(init=False)
    fm_accounts: FlajoletMartin = field(init=False)
    lsh: LSHSpamDetector = field(init=False)

    def __post_init__(self) -> None:
        self.dgim = DGIMCounter(window_size=self.dgim_window_size)
        self.fm_urls = FlajoletMartin(num_groups=8, hashes_per_group=8, seed=self.seed)
        self.fm_accounts = FlajoletMartin(num_groups=8, hashes_per_group=8, seed=self.seed + 1)
        self.lsh = LSHSpamDetector(
            k=5,
            num_bands=20,
            rows_per_band=5,
            jaccard_threshold=0.55,
            min_char_length=12,
            max_window_size=self.lsh_window_size,
            seed=self.seed,
        )
        for ref_id, ref_text in DEFAULT_REFERENCE_SPAM_TEMPLATES:
            self.lsh.add_reference_spam(ref_id, ref_text)

    def snapshot_stats(self) -> dict[str, Any]:
        """Return current video's observation stream statistics."""
        return {
            "video_id": self.video_id,
            "note": "현재 영상의 관측 스트림 기준 통계 (DOM 스크롤 로딩 순서)",
            "observed_count": self.observed_count,
            "spam_suspect_count": self.spam_suspect_count,
            "hold_count": self.hold_count,
            "normal_count": self.normal_count,
            "dgim_window_size": self.dgim_window_size,
            "dgim_suspect_estimate": self.dgim.estimate(),
            "dgim_bucket_count": self.dgim.bucket_count(),
            "fm_unique_spam_urls_estimate": self.fm_urls.estimate(),
            "fm_unique_spam_urls_exact": self.fm_urls.exact_count(),
            "fm_unique_spam_accounts_estimate": self.fm_accounts.estimate(),
            "fm_unique_spam_accounts_exact": self.fm_accounts.exact_count(),
        }


class SpamFilterPipeline:
    """Main orchestrator for real-time YouTube comment stream spam filtering."""

    def __init__(
        self,
        sorted_urls_path: str | Path | None = None,
        bloom_fpr: float = 0.01,
        dgim_window_size: int = 1000,
        lsh_window_size: int = 500,
        seed: int = SEED,
        llm_verifier: OpenCodeHoldVerifier | None = None,
    ) -> None:
        url_path = Path(sorted_urls_path) if sorted_urls_path else DEFAULT_SORTED_URLS_PATH
        self.url_matcher = TwoStageURLMatcher(
            sorted_file_path=url_path,
            false_positive_rate=bloom_fpr,
            seed=seed,
        )
        self.dgim_window_size = dgim_window_size
        self.lsh_window_size = lsh_window_size
        self.seed = seed
        self.llm_verifier = llm_verifier if llm_verifier is not None else OpenCodeHoldVerifier()
        self._videos: dict[str, VideoStreamState] = {}

    def get_or_create_video_state(self, video_id: str) -> VideoStreamState:
        vid = video_id or "default_video"
        if vid not in self._videos:
            self._videos[vid] = VideoStreamState(
                video_id=vid,
                dgim_window_size=self.dgim_window_size,
                lsh_window_size=self.lsh_window_size,
                seed=self.seed,
            )
        return self._videos[vid]

    def reset_video(self, video_id: str) -> dict[str, Any]:
        """Reset the stream state for `video_id`."""
        vid = video_id or "default_video"
        self._videos[vid] = VideoStreamState(
            video_id=vid,
            dgim_window_size=self.dgim_window_size,
            lsh_window_size=self.lsh_window_size,
            seed=self.seed,
        )
        return self._videos[vid].snapshot_stats()

    def analyze_comment(
        self,
        comment_id: str,
        text: str,
        video_id: str = "default_video",
        author_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute Steps A -> B -> C -> D on a single comment."""
        start_ns = time.perf_counter_ns()
        state = self.get_or_create_video_state(video_id)

        # Deduplicate already-observed comments in the same video
        if comment_id and comment_id in state.seen_comment_ids:
            cached = dict(state.cached_results[comment_id])
            cached["deduplicated"] = True
            return cached

        norm_text = normalize_text(text)
        urls = extract_urls(norm_text)

        # Step A: URL Normalization & 2-Stage Bloom Filter + Disk Verification
        confirmed_malicious_urls: list[str] = []
        bloom_positive_urls: list[str] = []
        for u in urls:
            is_malicious, bloom_pos = self.url_matcher.query(u)
            if bloom_pos:
                bloom_positive_urls.append(u)
            if is_malicious:
                confirmed_malicious_urls.append(u)

        # Step B: k-Shingling -> MinHash -> LSH Banding -> Exact Jaccard + Promo Rules
        doc_key = comment_id or f"anon_{state.observed_count + 1}"
        lsh_res = state.lsh.query_and_index(doc_id=doc_key, text=norm_text, index_into_window=True)
        promo_signals = extract_promo_signals(norm_text, urls)

        # Step C: Final Verdict & Reason Generation
        status = "NORMAL"
        badge_type = "NONE"
        reasons: list[str] = []

        if confirmed_malicious_urls:
            status = "SPAM_SUSPECT"
            badge_type = "MALICIOUS_URL"
            reasons.append(f"악성 URL 확인 ({confirmed_malicious_urls[0]})")

        if lsh_res.matched_reference_spam:
            status = "SPAM_SUSPECT"
            if badge_type == "NONE":
                badge_type = "REPEATED_SPAM"
            reasons.append(f"스팸 참조 문구 유사 (Jaccard={lsh_res.best_jaccard:.2f})")
        elif lsh_res.duplicate_count_above_threshold >= 1 and promo_signals:
            status = "SPAM_SUSPECT"
            if badge_type == "NONE":
                badge_type = "REPEATED_SPAM"
            reasons.append(
                f"유사 도배 댓글 + 홍보 신호 (Jaccard={lsh_res.best_jaccard:.2f}, {', '.join(promo_signals)})"
            )
        elif lsh_res.duplicate_count_above_threshold >= 2:
            status = "SPAM_SUSPECT"
            if badge_type == "NONE":
                badge_type = "REPEATED_SPAM"
            reasons.append(
                f"반복 도배 댓글 {lsh_res.duplicate_count_above_threshold}회 감지 (Jaccard={lsh_res.best_jaccard:.2f})"
            )
        elif len(promo_signals) >= 2:
            status = "SPAM_SUSPECT"
            if badge_type == "NONE":
                badge_type = "PROMO_RULE"
            reasons.append(f"다중 홍보/외부 유도 신호 ({', '.join(promo_signals)})")
        elif status == "NORMAL":
            # Check borderline cases -> HOLD (False Positive prevention)
            if lsh_res.duplicate_count_above_threshold == 1 and not promo_signals:
                status = "HOLD"
                badge_type = "HOLD"
                reasons.append(
                    f"유사 댓글 감지되었으나 홍보 신호 없음 - 판단 보류 (Jaccard={lsh_res.best_jaccard:.2f})"
                )
            elif len(promo_signals) == 1:
                status = "HOLD"
                badge_type = "HOLD"
                reasons.append(f"단일 홍보/링크 의심 신호 - 판단 보류 ({promo_signals[0]})")

        llm_verified = False
        if status == "HOLD" and self.llm_verifier.is_enabled:
            llm_res = self.llm_verifier.verify_hold_comment(
                text=norm_text,
                hold_reason=" | ".join(reasons),
                promo_signals=promo_signals,
                best_jaccard=lsh_res.best_jaccard,
            )
            llm_verified = llm_res.get("used_llm") == "true"
            if llm_res["verdict"] == "SPAM_SUSPECT":
                status = "SPAM_SUSPECT"
                badge_type = "PROMO_RULE"
                reasons.append(f"OpenCode 3차 검증 스팸 확정 ({llm_res['reason']})")
            elif llm_res["verdict"] == "NORMAL":
                status = "NORMAL"
                badge_type = "NONE"
                reasons = [f"OpenCode 3차 검증 정상 완화 ({llm_res['reason']})"]

        if not reasons:
            reasons.append("정상 댓글")

        # Step D: Update Stream Sketches (DGIM & Flajolet-Martin)
        state.observed_count += 1
        if status == "SPAM_SUSPECT":
            state.spam_suspect_count += 1
            state.dgim.update(1)
            for u in urls:
                state.fm_urls.add(u)
            if author_id and author_id.strip():
                # Privacy rule: hash stable author/channel ID before feeding into sketch
                anon_author = hashlib.sha256(author_id.strip().encode("utf-8")).hexdigest()[:16]
                state.fm_accounts.add(anon_author)
        elif status == "HOLD":
            state.hold_count += 1
            state.dgim.update(0)
        else:
            state.normal_count += 1
            state.dgim.update(0)

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
        result = {
            "comment_id": comment_id,
            "video_id": state.video_id,
            "status": status,
            "badge_type": badge_type,
            "reason": " | ".join(reasons),
            "urls": urls,
            "confirmed_malicious_urls": confirmed_malicious_urls,
            "bloom_positive_urls": bloom_positive_urls,
            "lsh_best_jaccard": lsh_res.best_jaccard,
            "lsh_candidate_count": len(lsh_res.candidate_ids),
            "promo_signals": promo_signals,
            "llm_verified": llm_verified,
            "latency_ms": round(elapsed_ms, 3),
            "deduplicated": False,
        }

        if comment_id:
            state.seen_comment_ids.add(comment_id)
            state.cached_results[comment_id] = result

        return result

    def analyze_batch(
        self,
        comments: list[dict[str, Any]],
        video_id: str = "default_video",
    ) -> dict[str, Any]:
        """Analyze a list of comment payloads and return verdicts + stream statistics."""
        batch_start_ns = time.perf_counter_ns()
        results: list[dict[str, Any]] = []
        for item in comments:
            cid = str(item.get("comment_id", ""))
            text = str(item.get("text", ""))
            vid = str(item.get("video_id") or video_id or "default_video")
            author_id = item.get("author_id")
            res = self.analyze_comment(
                comment_id=cid,
                text=text,
                video_id=vid,
                author_id=str(author_id) if author_id else None,
            )
            results.append(res)

        state = self.get_or_create_video_state(video_id)
        total_ms = (time.perf_counter_ns() - batch_start_ns) / 1_000_000.0
        return {
            "video_id": state.video_id,
            "results": results,
            "stream_stats": state.snapshot_stats(),
            "batch_latency_ms": round(total_ms, 3),
        }

    def health_info(self) -> dict[str, Any]:
        """Return server & filter configuration and active video summaries."""
        sample_lsh = LSHSpamDetector(
            k=5,
            num_bands=20,
            rows_per_band=5,
            jaccard_threshold=0.55,
            min_char_length=12,
            max_window_size=self.lsh_window_size,
            seed=self.seed,
        )
        return {
            "status": "ok",
            "seed": self.seed,
            "bloom_filter": self.url_matcher.stats(),
            "lsh_config": sample_lsh.stats(),
            "llm_verifier": self.llm_verifier.stats(),
            "active_videos": {
                vid: st.snapshot_stats() for vid, st in self._videos.items()
            },
        }
