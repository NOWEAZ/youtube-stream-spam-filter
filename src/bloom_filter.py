"""Bloom Filter and 2-Stage Disk-Backed Malicious URL Matcher.

Implemented strictly with Python 3 standard library (`hashlib`, `math`, `os`, `struct`, `sys`).
Follows Section 4.1 of the specification:
- Set size m, bit array size n, number of hash functions k:
  * Optimal k = round((n / m) * ln 2)
  * Theoretical FPR p ≈ (1 - exp(-k * m / n)) ** k
- Stage 1: In-memory bit array (`bytearray`) lookup in O(k).
  * Any bit == 0 -> Guaranteed Negative (False Negative = 0%).
- Stage 2: Only when all k bits == 1, performs exact O(log(bytes)) binary search
  directly on the sorted URL file on disk to eliminate False Positives without
  keeping the URL strings in memory.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from pathlib import Path
from typing import Iterable

from src import SEED
from src.normalizer import normalize_url


class BloomFilter:
    """Space-efficient probabilistic membership filter using a packed `bytearray`."""

    def __init__(
        self,
        expected_items: int = 10000,
        false_positive_rate: float = 0.01,
        bit_size: int | None = None,
        num_hashes: int | None = None,
        seed: int = SEED,
    ) -> None:
        if expected_items <= 0:
            raise ValueError("expected_items must be > 0")
        if not (0.0 < false_positive_rate < 1.0):
            raise ValueError("false_positive_rate must be between 0 and 1")

        self.expected_items: int = expected_items
        self.target_fpr: float = false_positive_rate
        self.seed: int = seed

        if bit_size is not None:
            if bit_size <= 0:
                raise ValueError("bit_size must be > 0")
            self.bit_size: int = bit_size
        else:
            # n = ceil(-m * ln(p) / (ln 2)^2)
            n_float = -expected_items * math.log(false_positive_rate) / (math.log(2.0) ** 2)
            self.bit_size = max(64, int(math.ceil(n_float)))

        if num_hashes is not None:
            if num_hashes <= 0:
                raise ValueError("num_hashes must be > 0")
            self.num_hashes: int = num_hashes
        else:
            # k = (n / m) * ln 2
            k_opt = (self.bit_size / self.expected_items) * math.log(2.0)
            self.num_hashes = max(1, int(round(k_opt)))

        self._byte_len: int = (self.bit_size + 7) // 8
        self._bits: bytearray = bytearray(self._byte_len)
        self.num_inserted: int = 0
        self._seed_prefix: bytes = struct.pack("<I", self.seed & 0xFFFFFFFF)

    def _hash_positions(self, item: str) -> list[int]:
        """Generate `k` bit positions via Kirsch-Mitzenmacher double hashing over SHA-256."""
        digest = hashlib.sha256(self._seed_prefix + item.encode("utf-8")).digest()
        h1, h2 = struct.unpack("<QQ", digest[:16])
        if h2 % self.bit_size == 0:
            h2 = (h2 | 1)
        n = self.bit_size
        return [(h1 + i * h2) % n for i in range(self.num_hashes)]

    def add(self, item: str) -> None:
        """Insert a string into the Bloom Filter."""
        for pos in self._hash_positions(item):
            self._bits[pos >> 3] |= 1 << (pos & 7)
        self.num_inserted += 1

    def might_contain(self, item: str) -> bool:
        """Return False if `item` is definitely not in the set, True if possibly in the set."""
        for pos in self._hash_positions(item):
            if not (self._bits[pos >> 3] & (1 << (pos & 7))):
                return False
        return True

    def __contains__(self, item: str) -> bool:
        return self.might_contain(item)

    def theoretical_fpr(self, m: int | None = None) -> float:
        """Calculate theoretical false positive rate p = (1 - e^(-k * m / n))^k."""
        items_count = self.num_inserted if m is None else m
        if items_count <= 0:
            return 0.0
        exponent = -self.num_hashes * items_count / self.bit_size
        return (1.0 - math.exp(exponent)) ** self.num_hashes

    def ones_ratio(self) -> float:
        """Fraction of bits currently set to 1 in the bit array."""
        set_bits = sum( b.bit_count() for b in self._bits )
        return set_bits / self.bit_size

    def memory_bytes(self) -> int:
        """Return size of the packed bit array in bytes."""
        return len(self._bits)


class DiskSortedURLStore:
    """Disk-backed exact URL lookup on a newline-sorted UTF-8 file using binary search.

    Does NOT load the URL set into memory; performs O(log(file_bytes)) `seek` lookups on disk.
    """

    def __init__(self, sorted_file_path: str | Path) -> None:
        self.file_path = Path(sorted_file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Sorted URL file not found: {self.file_path}")
        self.file_size: int = self.file_path.stat().st_size
        self.disk_lookups: int = 0

    @staticmethod
    def build_sorted_file(raw_urls: Iterable[str], output_path: str | Path) -> int:
        """Normalize, deduplicate, and lexicographically sort URLs into `output_path`."""
        normalized_set = {normalize_url(u) for u in raw_urls if normalize_url(u)}
        sorted_urls = sorted(normalized_set)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            for u in sorted_urls:
                f.write(u.encode("utf-8") + b"\n")
        return len(sorted_urls)

    def iter_urls(self) -> Iterable[str]:
        """Stream normalized URLs line-by-line from disk without loading all at once."""
        with self.file_path.open("rb") as f:
            for raw_line in f:
                line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="ignore")
                if line:
                    yield line

    def count_urls(self) -> int:
        """Count total non-empty URL lines on disk."""
        return sum(1 for _ in self.iter_urls())

    def contains(self, normalized_url: str) -> bool:
        """Perform byte-offset binary search on the sorted disk file."""
        if not normalized_url or self.file_size == 0:
            return False

        self.disk_lookups += 1
        target = normalized_url.encode("utf-8")
        low = 0
        high = self.file_size

        with self.file_path.open("rb") as f:
            while low < high:
                mid = (low + high) // 2
                if mid > 0:
                    f.seek(mid - 1)
                    f.readline()
                    line_start = f.tell()
                else:
                    f.seek(0)
                    line_start = 0

                if line_start >= high:
                    high = mid
                    continue

                line = f.readline().rstrip(b"\r\n")
                if line == target:
                    return True
                elif line < target:
                    low = f.tell()
                else:
                    high = mid
        return False


class TwoStageURLMatcher:
    """Combines in-memory BloomFilter (Stage 1) with DiskSortedURLStore (Stage 2)."""

    def __init__(
        self,
        sorted_file_path: str | Path,
        false_positive_rate: float = 0.01,
        expected_items: int | None = None,
        seed: int = SEED,
    ) -> None:
        self.disk_store = DiskSortedURLStore(sorted_file_path)
        count = expected_items if expected_items is not None else max(1, self.disk_store.count_urls())
        self.bloom = BloomFilter(
            expected_items=count,
            false_positive_rate=false_positive_rate,
            seed=seed,
        )
        for url in self.disk_store.iter_urls():
            self.bloom.add(url)

        self.total_queries: int = 0
        self.bloom_positives: int = 0
        self.bloom_false_positives: int = 0
        self.confirmed_positives: int = 0

    def query(self, raw_or_normalized_url: str) -> tuple[bool, bool]:
        """Check whether a URL is malicious using 2-stage lookup.

        Returns:
            (is_confirmed_malicious, bloom_positive)
        """
        norm_url = normalize_url(raw_or_normalized_url)
        if not norm_url:
            return False, False

        self.total_queries += 1
        if not self.bloom.might_contain(norm_url):
            # Stage 1 Negative -> Guaranteed clean (FN = 0)
            return False, False

        # Stage 1 Positive -> Stage 2 Disk verification
        self.bloom_positives += 1
        confirmed = self.disk_store.contains(norm_url)
        if confirmed:
            self.confirmed_positives += 1
        else:
            self.bloom_false_positives += 1
        return confirmed, True

    def stats(self) -> dict[str, int | float]:
        """Return operational statistics of the 2-stage URL matcher."""
        return {
            "indexed_urls": self.bloom.num_inserted,
            "bit_size": self.bloom.bit_size,
            "num_hashes": self.bloom.num_hashes,
            "memory_bytes": self.bloom.memory_bytes(),
            "theoretical_fpr": round(self.bloom.theoretical_fpr(), 6),
            "total_queries": self.total_queries,
            "bloom_positives": self.bloom_positives,
            "confirmed_positives": self.confirmed_positives,
            "bloom_false_positives": self.bloom_false_positives,
            "disk_lookups": self.disk_store.disk_lookups,
        }
