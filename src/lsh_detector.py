"""k-Shingling, MinHash Signature, and LSH Banding Near-Duplicate Spam Detector.

Implemented strictly with Python 3 standard library (`collections`, ` dataclasses`, `hashlib`, `random`, `struct`).
Follows Section 4.2 of the specification:
- Character-level k-shingling (default k=5) after whitespace normalization.
- Length-n MinHash signatures via universal hash permutations: h_i(x) = (a_i * x + b_i) mod p.
- LSH banding (b bands * r rows = n, threshold t ≈ (1/b)^(1/r)) for candidate generation.
- Secondary verification using exact Jaccard similarity on the original shingle sets.
- Minimum character length threshold to protect short reactions ("ㅋㅋㅋ", "Good", "1등").
- Bounded LSH index size: recent W comments in current video + fixed reference spam set.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import hashlib
import random
import struct

from src import SEED
from src.normalizer import normalize_text

# Smallest prime greater than 2^32 - 1
MERSENNE_PRIME_P = 4294967311
MAX_SHINGLE_HASH = (1 << 32) - 1


def extract_k_shingles(text: str, k: int = 5) -> set[int]:
    """Convert normalized comment text into a set of 32-bit hashed character k-shingles."""
    norm = normalize_text(text).lower()
    if not norm:
        return set()
    if len(norm) < k:
        digest = hashlib.blake2b(norm.encode("utf-8"), digest_size=4).digest()
        return {struct.unpack("<I", digest)[0]}

    shingles: set[int] = set()
    for i in range(len(norm) - k + 1):
        token = norm[i : i + k].encode("utf-8")
        digest = hashlib.blake2b(token, digest_size=4).digest()
        shingles.add(struct.unpack("<I", digest)[0])
    return shingles


def exact_jaccard(set_a: set[int], set_b: set[int]) -> float:
    """Compute exact Jaccard similarity |A ∩ B| / |A ∪ B| between two shingle sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection_size = len(set_a & set_b)
    union_size = len(set_a) + len(set_b) - intersection_size
    return intersection_size / union_size if union_size > 0 else 0.0


class MinHash:
    """MinHash signature generator using `n` universal hash permutations `(a_i * x + b_i) mod p`."""

    def __init__(self, num_hashes: int = 100, seed: int = SEED) -> None:
        if num_hashes <= 0:
            raise ValueError("num_hashes must be > 0")
        self.num_hashes: int = num_hashes
        self.seed: int = seed
        self.prime_p: int = MERSENNE_PRIME_P

        rng = random.Random(seed)
        self._coeffs: list[tuple[int, int]] = [
            (rng.randint(1, self.prime_p - 1), rng.randint(0, self.prime_p - 1))
            for _ in range(num_hashes)
        ]

    def signature(self, shingle_set: set[int]) -> list[int]:
        """Compute length-`n` MinHash signature for a set of integer shingle hashes."""
        if not shingle_set:
            return [self.prime_p] * self.num_hashes

        p = self.prime_p
        sig: list[int] = []
        for a, b in self._coeffs:
            min_hash = min(((a * x + b) % p) for x in shingle_set)
            sig.append(min_hash)
        return sig

    @staticmethod
    def estimate_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
        """Estimate Jaccard similarity from two MinHash signatures of equal length."""
        if len(sig_a) != len(sig_b) or not sig_a:
            raise ValueError("Signatures must be non-empty and have equal length")
        matches = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
        return matches / len(sig_a)


@dataclass
class IndexedComment:
    """Internal entry stored in the bounded LSH index."""

    doc_id: str
    shingles: set[int]
    signature: list[int]
    is_reference_spam: bool = False
    band_keys: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class LSHQueryResult:
    """Result of querying and verifying a comment against the LSH index."""

    is_short_exempt: bool
    candidate_ids: set[str]
    best_match_id: str | None
    best_jaccard: float
    matched_reference_spam: bool
    duplicate_count_above_threshold: int


class LSHSpamDetector:
    """LSH Banding + Exact Jaccard Verification with bounded sliding window memory."""

    def __init__(
        self,
        k: int = 5,
        num_bands: int = 20,
        rows_per_band: int = 5,
        jaccard_threshold: float = 0.55,
        min_char_length: int = 12,
        max_window_size: int = 500,
        seed: int = SEED,
    ) -> None:
        if k <= 0:
            raise ValueError("k must be > 0")
        if num_bands <= 0 or rows_per_band <= 0:
            raise ValueError("num_bands and rows_per_band must be > 0")

        self.k: int = k
        self.num_bands: int = num_bands
        self.rows_per_band: int = rows_per_band
        self.num_hashes: int = num_bands * rows_per_band
        self.jaccard_threshold: float = jaccard_threshold
        self.min_char_length: int = min_char_length
        self.max_window_size: int = max_window_size
        self.seed: int = seed

        self.minhash = MinHash(num_hashes=self.num_hashes, seed=seed)
        self._reference_entries: dict[str, IndexedComment] = {}
        self._window_entries: dict[str, IndexedComment] = {}
        self._window_order: deque[str] = deque()
        # Band buckets: band_idx -> bucket_hash -> set of doc_ids
        self._buckets: list[dict[int, set[str]]] = [
            defaultdict(set) for _ in range(self.num_bands)
        ]

    @property
    def theoretical_threshold(self) -> float:
        """Approximate LSH S-curve threshold t ≈ (1 / b) ** (1 / r)."""
        return (1.0 / self.num_bands) ** (1.0 / self.rows_per_band)

    def _compute_band_keys(self, signature: list[int]) -> list[tuple[int, int]]:
        r = self.rows_per_band
        keys: list[tuple[int, int]] = []
        for band_idx in range(self.num_bands):
            start = band_idx * r
            sub_sig = tuple(signature[start : start + r])
            bucket_key = hash(sub_sig)
            keys.append((band_idx, bucket_key))
        return keys

    def _evict_oldest_window_entry(self) -> None:
        while len(self._window_order) >= self.max_window_size:
            oldest_id = self._window_order.popleft()
            entry = self._window_entries.pop(oldest_id, None)
            if entry is not None:
                for band_idx, bucket_key in entry.band_keys:
                    bucket = self._buckets[band_idx].get(bucket_key)
                    if bucket is not None:
                        bucket.discard(oldest_id)
                        if not bucket:
                            del self._buckets[band_idx][bucket_key]

    def add_reference_spam(self, doc_id: str, text: str) -> None:
        """Register a known spam template into the permanent reference index."""
        norm = normalize_text(text)
        if len(norm) < self.min_char_length:
            return
        shingles = extract_k_shingles(norm, k=self.k)
        sig = self.minhash.signature(shingles)
        band_keys = self._compute_band_keys(sig)
        entry = IndexedComment(
            doc_id=doc_id,
            shingles=shingles,
            signature=sig,
            is_reference_spam=True,
            band_keys=band_keys,
        )
        self._reference_entries[doc_id] = entry
        for band_idx, bucket_key in band_keys:
            self._buckets[band_idx][bucket_key].add(doc_id)

    def reset_window(self) -> None:
        """Clear current video's sliding window while preserving reference spam entries."""
        for doc_id, entry in list(self._window_entries.items()):
            for band_idx, bucket_key in entry.band_keys:
                bucket = self._buckets[band_idx].get(bucket_key)
                if bucket is not None:
                    bucket.discard(doc_id)
                    if not bucket:
                        del self._buckets[band_idx][bucket_key]
        self._window_entries.clear()
        self._window_order.clear()

    def query_and_index(
        self,
        doc_id: str,
        text: str,
        index_into_window: bool = True,
    ) -> LSHQueryResult:
        """Query LSH bands, verify candidates with exact Jaccard, and update window index."""
        norm = normalize_text(text)
        if len(norm) < self.min_char_length:
            return LSHQueryResult(
                is_short_exempt=True,
                candidate_ids=set(),
                best_match_id=None,
                best_jaccard=0.0,
                matched_reference_spam=False,
                duplicate_count_above_threshold=0,
            )

        shingles = extract_k_shingles(norm, k=self.k)
        sig = self.minhash.signature(shingles)
        band_keys = self._compute_band_keys(sig)

        candidate_ids: set[str] = set()
        for band_idx, bucket_key in band_keys:
            bucket = self._buckets[band_idx].get(bucket_key)
            if bucket:
                for cand_id in bucket:
                    if cand_id != doc_id:
                        candidate_ids.add(cand_id)

        best_match_id: str | None = None
        best_jaccard: float = 0.0
        matched_reference_spam: bool = False
        dup_count: int = 0

        for cand_id in candidate_ids:
            cand_entry = self._reference_entries.get(cand_id) or self._window_entries.get(cand_id)
            if cand_entry is None:
                continue
            jaccard = exact_jaccard(shingles, cand_entry.shingles)
            if jaccard > best_jaccard:
                best_jaccard = jaccard
                best_match_id = cand_id
            if jaccard >= self.jaccard_threshold:
                dup_count += 1
                if cand_entry.is_reference_spam:
                    matched_reference_spam = True

        if index_into_window and doc_id not in self._window_entries:
            self._evict_oldest_window_entry()
            entry = IndexedComment(
                doc_id=doc_id,
                shingles=shingles,
                signature=sig,
                is_reference_spam=False,
                band_keys=band_keys,
            )
            self._window_entries[doc_id] = entry
            self._window_order.append(doc_id)
            for band_idx, bucket_key in band_keys:
                self._buckets[band_idx][bucket_key].add(doc_id)

        return LSHQueryResult(
            is_short_exempt=False,
            candidate_ids=candidate_ids,
            best_match_id=best_match_id,
            best_jaccard=round(best_jaccard, 4),
            matched_reference_spam=matched_reference_spam,
            duplicate_count_above_threshold=dup_count,
        )

    def stats(self) -> dict[str, int | float]:
        """Return current LSH detector configuration and index sizes."""
        return {
            "k": self.k,
            "num_bands": self.num_bands,
            "rows_per_band": self.rows_per_band,
            "num_hashes": self.num_hashes,
            "theoretical_threshold": round(self.theoretical_threshold, 4),
            "jaccard_threshold": self.jaccard_threshold,
            "min_char_length": self.min_char_length,
            "max_window_size": self.max_window_size,
            "reference_count": len(self._reference_entries),
            "window_count": len(self._window_entries),
        }
