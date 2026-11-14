"""Local HTTP Stream Analysis Server for the Chrome Extension (`127.0.0.1:8000`).

Usage:
    python3 -m src.server --host 127.0.0.1 --port 8000

Endpoints:
- GET  /health   : Server health, filter parameters, and current video stream statistics.
- POST /analyze  : Analyze batch or single comment payload and return verdicts + DGIM/FM stats.
- POST /reset    : Reset per-video stream state when `video_id` changes.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

from src.pipeline import SpamFilterPipeline


class StreamServerContext:
    """Thread-safe holder for the `SpamFilterPipeline` instance."""

    def __init__(self, urls_path: str | Path | None = None) -> None:
        self.lock = threading.Lock()
        self.pipeline = SpamFilterPipeline(sorted_urls_path=urls_path)


def make_request_handler(ctx: StreamServerContext) -> type[BaseHTTPRequestHandler]:
    """Create a request handler class bound to `ctx`."""

    class SpamFilterRequestHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict[str, Any]:
            length_str = self.headers.get("Content-Length", "0")
            length = int(length_str) if length_str.isdigit() else 0
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                query = parse_qs(parsed.query)
                video_id = query.get("video_id", [None])[0]
                with ctx.lock:
                    info = ctx.pipeline.health_info()
                    if video_id:
                        info["current_video_stats"] = (
                            ctx.pipeline.get_or_create_video_state(video_id).snapshot_stats()
                        )
                self._send_json(200, info)
            else:
                self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            try:
                payload = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error": f"Invalid JSON payload: {exc}"})
                return

            if parsed.path == "/analyze":
                video_id = str(payload.get("video_id") or "default_video")
                comments = payload.get("comments")
                if comments is None:
                    # Support single-comment payload shape as well
                    comments = [
                        {
                            "comment_id": payload.get("comment_id", ""),
                            "text": payload.get("text", ""),
                            "video_id": video_id,
                            "author_id": payload.get("author_id"),
                        }
                    ]
                if not isinstance(comments, list):
                    self._send_json(400, {"error": "'comments' must be a list"})
                    return

                with ctx.lock:
                    response = ctx.pipeline.analyze_batch(comments=comments, video_id=video_id)
                st = response["stream_stats"]
                print(
                    f"[ANALYZE] video={video_id} batch={len(comments)} "
                    f"observed={st['observed_count']} suspect={st['spam_suspect_count']} "
                    f"hold={st['hold_count']}"
                )
                self._send_json(200, response)

            elif parsed.path == "/reset":
                video_id = str(payload.get("video_id") or "default_video")
                with ctx.lock:
                    stats = ctx.pipeline.reset_video(video_id=video_id)
                print(f"[RESET] video={video_id}")
                self._send_json(200, {"status": "reset", "stream_stats": stats})

            else:
                self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Keep server stdout concise
            return

    return SpamFilterRequestHandler


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    urls_path: str | Path | None = None,
) -> None:
    """Start the local HTTP stream engine."""
    ctx = StreamServerContext(urls_path=urls_path)
    handler_cls = make_request_handler(ctx)
    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"[YouTube Stream Spam Filter] Listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[YouTube Stream Spam Filter] Shutting down server.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube Stream Spam Filter Local Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--urls-file",
        default=None,
        help="Path to sorted malicious URLs file for 2-stage lookup",
    )
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, urls_path=args.urls_file)


if __name__ == "__main__":
    main()
