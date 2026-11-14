"""Benchmark Chart Generator (`benchmarks/plot_charts.py`).

Generates comparison charts into `output/charts/*.png` from `output/metrics.json`.
Uses `matplotlib` if installed in the environment, and automatically falls back to a
built-in pure-Python standard-library PNG bar-chart renderer (`zlib`, `struct`) so
chart generation succeeds in any standard Python 3 environment.
"""

from __future__ import annotations

import json
from pathlib import Path
import struct
from typing import Any
import zlib


class PurePythonPNGCanvas:
    """Minimal pure-Python RGB canvas that writes valid PNG files using `zlib` & `struct`."""

    def __init__(self, width: int, height: int, bg_color: tuple[int, int, int] = (24, 25, 28)) -> None:
        self.width = width
        self.height = height
        r, g, b = bg_color
        row = bytes([r, g, b] * width)
        self.pixels = [bytearray(row) for _ in range(height)]

    def fill_rect(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
    ) -> None:
        r, g, b = color
        xa = max(0, min(self.width, min(x0, x1)))
        xb = max(0, min(self.width, max(x0, x1)))
        ya = max(0, min(self.height, min(y0, y1)))
        yb = max(0, min(self.height, max(y0, y1)))
        for y in range(ya, yb):
            row = self.pixels[y]
            for x in range(xa, xb):
                idx = x * 3
                row[idx] = r
                row[idx + 1] = g
                row[idx + 2] = b

    def save_png(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_data = bytearray()
        for row in self.pixels:
            raw_data.append(0)  # Filter type 0 (None)
            raw_data.extend(row)

        def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw_data), level=6)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + make_chunk(b"IHDR", ihdr)
            + make_chunk(b"IDAT", idat)
            + make_chunk(b"IEND", b"")
        )
        path.write_bytes(png_bytes)


def _render_bar_chart_pure_png(
    output_path: Path,
    values: list[float],
    colors: list[tuple[int, int, int]],
) -> Path:
    width, height = 640, 400
    canvas = PurePythonPNGCanvas(width, height, bg_color=(24, 25, 28))
    margin_left, margin_right, margin_top, margin_bottom = 70, 50, 50, 60
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    # Draw axes
    canvas.fill_rect(margin_left, margin_top, margin_left + 2, height - margin_bottom, (140, 145, 155))
    canvas.fill_rect(
        margin_left,
        height - margin_bottom,
        width - margin_right,
        height - margin_bottom + 2,
        (140, 145, 155),
    )

    max_val = max(max(values, default=1.0), 1e-6)
    n = max(1, len(values))
    slot_w = plot_w // n
    bar_w = max(16, int(slot_w * 0.55))

    for i, val in enumerate(values):
        ratio = min(1.0, max(0.0, val / max_val))
        bar_h = max(4, int(plot_h * ratio))
        x0 = margin_left + i * slot_w + (slot_w - bar_w) // 2
        x1 = x0 + bar_w
        y1 = height - margin_bottom
        y0 = y1 - bar_h
        canvas.fill_rect(x0, y0, x1, y1, colors[i % len(colors)])

    canvas.save_png(output_path)
    return output_path


def generate_all_charts(metrics: dict[str, Any], charts_dir: Path) -> list[Path]:
    """Generate benchmark comparison PNG charts in `charts_dir`."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    bf = metrics["bloom_filter"]
    lsh = metrics["lsh_detector"]
    pipe = metrics["pipeline_classification"]
    sk = metrics["stream_sketches"]

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 1. Bloom Filter vs Python set Memory
        p1 = charts_dir / "bloom_vs_set_memory.png"
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(
            ["Python set (KB)", "Bloom Filter 1% (KB)"],
            [bf["python_set_memory_kb"], bf["bloom_1pct_memory_kb"]],
            color=["#ff6b6b", "#8ab4f8"],
        )
        ax.set_title("URL Memory Usage: Python set vs Bloom Filter (KB)")
        ax.set_ylabel("Memory (KB)")
        fig.tight_layout()
        fig.savefig(p1, dpi=120)
        plt.close(fig)
        generated.append(p1)

        # 2. LSH Candidate Pair Reduction
        p2 = charts_dir / "lsh_pair_reduction.png"
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(
            ["O(N^2) All Pairs", "LSH Candidate Pairs"],
            [lsh["total_all_pairs"], lsh["lsh_candidate_pairs"]],
            color=["#fbbc04", "#81c995"],
        )
        ax.set_title("Pairwise Comparisons: All-Pairs vs MinHash LSH")
        ax.set_ylabel("Number of Pairs")
        fig.tight_layout()
        fig.savefig(p2, dpi=120)
        plt.close(fig)
        generated.append(p2)

        # 3. Pipeline Classification Metrics
        p3 = charts_dir / "pipeline_metrics.png"
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(
            ["Precision", "Recall", "F1-Score", "FPR", "HOLD Rate"],
            [
                pipe["precision"],
                pipe["recall"],
                pipe["f1_score"],
                pipe["comment_classification_fpr"],
                pipe["hold_rate"],
            ],
            color=["#8ab4f8", "#81c995", "#c58af9", "#ff6b6b", "#fbbc04"],
        )
        ax.set_ylim(0, 1.05)
        ax.set_title("End-to-End Spam Classification Pipeline Metrics")
        fig.tight_layout()
        fig.savefig(p3, dpi=120)
        plt.close(fig)
        generated.append(p3)

        # 4. Stream Sketches Memory Comparison
        p4 = charts_dir / "sketch_memory_comparison.png"
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(
            ["Exact Window", "DGIM Sketch", "Exact URL Set", "FM Sketch"],
            [
                sk["dgim"]["exact_window_memory_bytes"],
                sk["dgim"]["dgim_memory_bytes"],
                sk["flajolet_martin"]["exact_set_memory_bytes"],
                sk["flajolet_martin"]["fm_sketch_memory_bytes"],
            ],
            color=["#ff6b6b", "#81c995", "#fbbc04", "#8ab4f8"],
        )
        ax.set_title("Stream Sketch Memory vs Exact Structures (Bytes)")
        ax.set_ylabel("Bytes")
        fig.tight_layout()
        fig.savefig(p4, dpi=120)
        plt.close(fig)
        generated.append(p4)

    except ImportError:
        # Pure-Python standard-library PNG fallback
        generated.append(
            _render_bar_chart_pure_png(
                charts_dir / "bloom_vs_set_memory.png",
                [bf["python_set_memory_kb"], bf["bloom_1pct_memory_kb"]],
                [(255, 107, 107), (138, 180, 248)],
            )
        )
        generated.append(
            _render_bar_chart_pure_png(
                charts_dir / "lsh_pair_reduction.png",
                [float(lsh["total_all_pairs"]), float(lsh["lsh_candidate_pairs"])],
                [(251, 188, 4), (129, 201, 149)],
            )
        )
        generated.append(
            _render_bar_chart_pure_png(
                charts_dir / "pipeline_metrics.png",
                [
                    pipe["precision"],
                    pipe["recall"],
                    pipe["f1_score"],
                    pipe["comment_classification_fpr"],
                    pipe["hold_rate"],
                ],
                [
                    (138, 180, 248),
                    (129, 201, 149),
                    (197, 138, 249),
                    (255, 107, 107),
                    (251, 188, 4),
                ],
            )
        )
        generated.append(
            _render_bar_chart_pure_png(
                charts_dir / "sketch_memory_comparison.png",
                [
                    float(sk["dgim"]["exact_window_memory_bytes"]),
                    float(sk["dgim"]["dgim_memory_bytes"]),
                    float(sk["flajolet_martin"]["exact_set_memory_bytes"]),
                    float(sk["flajolet_martin"]["fm_sketch_memory_bytes"]),
                ],
                [(255, 107, 107), (129, 201, 149), (251, 188, 4), (138, 180, 248)],
            )
        )

    return generated


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    metrics_path = root_dir / "output" / "metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(
            f"{metrics_path} not found. Run `python benchmarks/run_benchmarks.py` first."
        )
    with metrics_path.open("r", encoding="utf-8") as f:
        metrics = json.load(f)
    paths = generate_all_charts(metrics, root_dir / "output" / "charts")
    for p in paths:
        print(f"[Plot] Saved {p}")


if __name__ == "__main__":
    main()
