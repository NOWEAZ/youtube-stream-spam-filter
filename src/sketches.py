"""Stream Sketches: DGIM Sliding Window Counter and Flajolet-Martin Distinct Estimator.

Implemented strictly with Python 3 standard library (` dataclasses`, `hashlib`, `statistics`, `struct`, `sys`).
Follows Section 4.3 of the specification:
- DGIMCounter:
  * Converts comment verdicts into a binary stream (SPAM_SUSPECT -> 1, NORMAL/HOLD -> 0).
  * Maintains O(log N) power-of-two buckets in O(log^2 N) bits.
  * Estimates 1-count in the last N observations by summing full buckets + half of the oldest bucket,
    guaranteeing relative error <= 50%.
- FlajoletMartin:
  * Estimates distinct URL count and distinct author/account count in suspected spam comments.
  * Tracks maximum trailing zero count R of binary hash representations (2^R estimate)
    and combines multiple hash functions via group means and median of means to reduce variance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import statistics
import struct
import sys

from src import SEED


@dataclass
class DGIMBucket:
    """Single DGIM bucket with creation/merge timestamp and power-of-two size."""

    timestamp: int
    size: int


class DGIMCounter:
    """DGIM (Datar-Gionis-Indyk-Motwani) algorithm for counting 1s in a sliding window."""

    def __init__(self, window_size: int = 1000) -> None:
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        self.window_size: int = window_size
        self.timestamp: int = 0
        # Ordered from oldest (index 0) to newest (index -1)
        self._buckets: list[DGIMBucket] = []

    def _expire_old_buckets(self) -> None:
        cutoff = self.timestamp - self.window_size
        while self._buckets and self._buckets[0].timestamp <= cutoff:
            self._buckets.pop(0)

    def _merge_buckets(self) -> None:
        """Ensure at most 2 buckets exist for any power-of-two size."""
        i = len(self._buckets) - 1
        while i >= 2:
            if (
                self._buckets[i].size == self._buckets[i - 1].size
                and self._buckets[i - 1].size == self._buckets[i - 2].size
            ):
                # Merge the two older buckets: (i - 2) and (i - 1)
                merged_timestamp = self._buckets[i - 1].timestamp
                merged_size = self._buckets[i - 1].size * 2
                self._buckets[i - 2 : i] = [
                    DGIMBucket(timestamp=merged_timestamp, size=merged_size)
                ]
                i -= 1
            else:
                i -= 1

    def update(self, bit: int) -> None:
        """Record a new stream observation (`1` for SPAM_SUSPECT, `0` otherwise)."""
        if bit not in (0, 1):
            raise ValueError("DGIM bit must be 0 or 1")
        self.timestamp += 1
        self._expire_old_buckets()
        if bit == 1:
            self._buckets.append(DGIMBucket(timestamp=self.timestamp, size=1))
            self._merge_buckets()

    def estimate(self, k: int | None = None) -> float:
        """Estimate the count of 1s within the most recent `k` observations (default: `window_size`)."""
        query_window = self.window_size if k is None else min(k, self.window_size)
        if query_window <= 0 or not self._buckets:
            return 0.0

        cutoff = self.timestamp - query_window
        valid_buckets = [b for b in self._buckets if b.timestamp > cutoff]
        if not valid_buckets:
            return 0.0

        total = sum(b.size for b in valid_buckets[1:])
        oldest_half = valid_buckets[0].size / 2.0
        return total + oldest_half

    def bucket_count(self) -> int:
        """Return current number of active buckets (O(log N))."""
        return len(self._buckets)

    def memory_bytes(self) -> int:
        """Approximate memory usage in bytes for storing the active buckets."""
        # Each bucket holds two integers (timestamp, size)
        return sys.getsizeof(self._buckets) + len(self._buckets) * 16

    def reset(self) -> None:
        """Clear all buckets and reset timestamp."""
        self.timestamp = 0
        self._buckets.clear()


def _count_trailing_zeros_64(value: int) -> int:
    """Return number of trailing zero bits in a 64-bit unsigned integer."""
    if value == 0:
        return 64
    return (value & -value).bit_length() - 1


class FlajoletMartin:
    """Flajolet-Martin distinct element estimator using Median-of-Means over 2^R."""

    def __init__(
        self,
        num_groups: int = 8,
        hashes_per_group: int = 8,
        seed: int = SEED,
    ) -> None:
        if num_groups <= 0 or hashes_per_group <= 0:
            raise ValueError("num_groups and hashes_per_group must be > 0")
        self.num_groups: int = num_groups
        self.hashes_per_group: int = hashes_per_group
        self.total_hashes: int = num_groups * hashes_per_group
        self.seed: int = seed

        self._max_zeros: list[list[int]] = [
            [0 for _ in range(hashes_per_group)] for _ in range(num_groups)
        ]
        self._has_data: bool = False
        self._exact_reference: set[str] = set()

    def _hash_item(self, group_idx: int, hash_idx: int, item: str) -> int:
        prefix = struct.pack("<III", self.seed & 0xFFFFFFFF, group_idx, hash_idx)
        digest = hashlib.blake2b(prefix + item.encode("utf-8"), digest_size=8).digest()
        return struct.unpack("<Q", digest)[0]

    def add(self, item: str, track_exact: bool = True) -> None:
        """Update sketch with `item`."""
        if not item:
            return
        self._has_data = True
        if track_exact:
            self._exact_reference.add(item)

        for g in range(self.num_groups):
            group_zeros = self._max_zeros[g]
            for h in range(self.hashes_per_group):
                hv = self._hash_item(g, h, item)
                tz = _count_trailing_zeros_64(hv)
                if tz > group_zeros[h]:
                    group_zeros[h] = tz

    def estimate(self) -> float:
        """Compute distinct count estimate via Median-of-Means over 2^R."""
        if not self._has_data:
            return 0.0
        group_estimates: list[float] = []
        for g in range(self.num_groups):
            mean_r = statistics.mean(self._max_zeros[g])
            group_estimates.append(2.0 ** mean_r)
        return round(float(statistics.median(group_estimates)), 2)

    def exact_count(self) -> int:
        """Return exact distinct count (tracked for benchmark error comparison)."""
        return len(self._exact_reference)

    def sketch_memory_bytes(self) -> int:
        """Return memory footprint of the FM registers only (excluding benchmark set)."""
        return self.total_hashes * 8

    def reset(self) -> None:
        """Reset all trailing-zero registers."""
        for g in range(self.num_groups):
            for h in range(self.hashes_per_group):
                self._max_zeros[g][h] = 0
        self._has_data = False
        self._exact_reference.clear()
