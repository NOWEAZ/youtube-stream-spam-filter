"""Optional 3rd-Stage OpenCode API Verifier for `HOLD` Borderline Comments.

Implemented strictly with the Python 3 standard library (`json`, `os`, `pathlib`,
`re`, `urllib.error`, `urllib.request`). Only invoked when 1st/2nd stage stream
algorithms classify a comment as `HOLD` and `OPENCODE_API_KEY` is configured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

DEFAULT_OPENCODE_API_URL = "https://opencode.ai/zen/v1/chat/completions"
DEFAULT_OPENCODE_MODEL = "deepseek-v4-flash-free"
VALID_VERDICTS = frozenset({"SPAM_SUSPECT", "NORMAL", "HOLD"})

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def load_dotenv_if_present(dotenv_path: Path | None = None) -> None:
    """Load `KEY=VALUE` pairs from `.env` into `os.environ` without overwriting existing vars."""
    path = dotenv_path or (Path(__file__).resolve().parent.parent / ".env")
    if not path.is_file():
        return

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
                val = val[1:-1]
            if key:
                os.environ.setdefault(key, val)


class OpenCodeHoldVerifier:
    """3rd-stage OpenAI-compatible Chat Completions verifier for `HOLD` comments."""

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        load_dotenv_if_present()
        self.api_key: str = (
            api_key if api_key is not None else os.environ.get("OPENCODE_API_KEY", "")
        ).strip()
        self.api_url: str = (
            api_url
            if api_url is not None
            else os.environ.get("OPENCODE_API_URL", DEFAULT_OPENCODE_API_URL)
        ).strip()
        self.model: str = (
            model if model is not None else os.environ.get("OPENCODE_MODEL", DEFAULT_OPENCODE_MODEL)
        ).strip()
        raw_timeout = timeout_sec if timeout_sec is not None else os.environ.get("OPENCODE_TIMEOUT_SEC", "2.5")
        self.timeout_sec: float = float(raw_timeout)

        self.total_calls: int = 0
        self.success_calls: int = 0
        self.upgraded_to_spam: int = 0
        self.cleared_to_normal: int = 0
        self.fallback_holds: int = 0

    @property
    def is_enabled(self) -> bool:
        """Return True when an OpenCode API key is available."""
        return bool(self.api_key)

    def verify_hold_comment(
        self,
        text: str,
        hold_reason: str,
        promo_signals: list[str],
        best_jaccard: float,
    ) -> dict[str, str]:
        """Verify a borderline `HOLD` comment via OpenCode API with fail-safe fallback."""
        if not self.is_enabled:
            return {"verdict": "HOLD", "reason": hold_reason, "used_llm": "false"}

        self.total_calls += 1
        system_prompt = (
            "You are a precise YouTube comment spam classifier. "
            "Classify the borderline comment into exactly one of: SPAM_SUSPECT, NORMAL, or HOLD. "
            "Respond strictly in valid JSON with keys 'verdict' and 'reason' (short Korean explanation)."
        )
        user_prompt = json.dumps(
            {
                "comment_text": text,
                "stage1_2_hold_reason": hold_reason,
                "promo_signals": promo_signals,
                "lsh_best_jaccard": best_jaccard,
                "output_schema": {"verdict": "SPAM_SUSPECT | NORMAL | HOLD", "reason": "string"},
            },
            ensure_ascii=False,
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        req = Request(
            self.api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "youtube-stream-spam-filter/0.1",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=self.timeout_sec) as resp:
                raw_body = resp.read().decode("utf-8")
            parsed_resp = json.loads(raw_body)
            content = str(parsed_resp["choices"][0]["message"]["content"]).strip()
            cleaned_content = _CODE_FENCE_RE.sub("", content).strip()
            verdict_obj = json.loads(cleaned_content)

            verdict = str(verdict_obj.get("verdict", "")).strip().upper()
            if verdict not in VALID_VERDICTS:
                raise ValueError(f"Unsupported verdict from OpenCode API: {verdict!r}")
            reason = str(verdict_obj.get("reason") or hold_reason).strip()

            self.success_calls += 1
            if verdict == "SPAM_SUSPECT":
                self.upgraded_to_spam += 1
            elif verdict == "NORMAL":
                self.cleared_to_normal += 1
            else:
                self.fallback_holds += 1

            return {"verdict": verdict, "reason": reason, "used_llm": "true"}

        except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
            self.fallback_holds += 1
            return {"verdict": "HOLD", "reason": hold_reason, "used_llm": "error"}

    def stats(self) -> dict[str, Any]:
        """Return configuration and call metrics without exposing the API key."""
        return {
            "enabled": self.is_enabled,
            "model": self.model,
            "api_url": self.api_url,
            "timeout_sec": self.timeout_sec,
            "total_calls": self.total_calls,
            "success_calls": self.success_calls,
            "upgraded_to_spam": self.upgraded_to_spam,
            "cleared_to_normal": self.cleared_to_normal,
            "fallback_holds": self.fallback_holds,
        }
