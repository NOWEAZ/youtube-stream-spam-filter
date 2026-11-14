"""URL and Comment Text Normalization & Rule-based Signal Extraction.

Pure Python 3 standard library implementation (`re`, `unicodedata`, `urllib.parse`).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit

TRACKING_PARAMS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "si",
    "feature",
    "ref",
})

SHORTENER_DOMAINS = frozenset({
    "bit.ly",
    "tinyurl.com",
    "han.gl",
    "me2.do",
    "is.gd",
    "vo.la",
    "url.kr",
    "t.co",
    "goo.gl",
})

# Matches http(s)://..., www...., or well-known shortener/messenger domains with paths
_URL_REGEX = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'`]+|"
    r"\b(?:bit\.ly|tinyurl\.com|t\.me|open\.kakao\.com|han\.gl|me2\.do|vo\.la|url\.kr)/[^\s<>\"'`]+",
    re.IGNORECASE,
)

_TRAILING_PUNCT = ".,!?;:)'\"]>}"
_WHITESPACE_RE = re.compile(r"\s+")

_TELEGRAM_RE = re.compile(r"(?:t\.me/|텔레그램|텔래그램|telegram)", re.IGNORECASE)
_KAKAO_RE = re.compile(r"(?:open\.kakao\.com|오픈\s*카톡|오픈\s*채팅|카톡\s*문의|카카오톡\s*문의)", re.IGNORECASE)
_FINANCE_PROMO_RE = re.compile(
    r"(?:수익\s*보장|급등\s*종목|급등주|무료\s*리딩|에어드랍|무료\s*코인|재택\s*부업|재택\s*알바|"
    r"꽁머니|당일\s*대출|선착순\s*모집|선착순\s*VIP|자동\s*매매|무료\s*로벅스|"
    r"free\s+usdt|free\s+robux|crypto\s+airdrop|giveaway)",
    re.IGNORECASE,
)
_GAMBLING_RE = re.compile(r"(?:슬롯\s*잭팟|카지노\s*가입|바카라|꽁머니\s*지급)", re.IGNORECASE)


def normalize_url(raw_url: str) -> str:
    """Normalize a URL into a canonical form for Bloom Filter and disk lookup.

    Steps:
    1. Strip whitespace and trailing punctuation.
    2. Ensure scheme for `urlsplit`, then drop `http`/`https` scheme.
    3. Lowercase hostname, strip leading `www.` and default ports (`:80`, `:443`).
    4. Remove common tracking query parameters and sort remaining query keys.
    5. Strip trailing slash `/` and URL fragment `#...`.
    """
    cleaned = raw_url.strip().rstrip(_TRAILING_PUNCT)
    if not cleaned:
        return ""

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned_with_scheme = "http://" + cleaned
    else:
        cleaned_with_scheme = cleaned

    try:
        parsed = urlsplit(cleaned_with_scheme)
    except ValueError:
        return cleaned.lower()

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80"):
        host = host[:-3]
    elif host.endswith(":443"):
        host = host[:-4]

    path = re.sub(r"/+", "/", parsed.path or "")
    if path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    query_pairs.sort()
    query_str = urlencode(query_pairs)

    normalized = f"{host}{path}"
    if query_str:
        normalized = f"{normalized}?{query_str}"
    return normalized


def extract_urls(text: str) -> list[str]:
    """Extract and normalize all URLs from a comment string (order-preserving, deduplicated)."""
    if not text:
        return []
    matches = _URL_REGEX.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for raw in matches:
        norm = normalize_url(raw)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


def normalize_text(text: str) -> str:
    """Normalize comment text with NFKC Unicode normalization and collapsed whitespace."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = _WHITESPACE_RE.sub(" ", norm).strip()
    return norm


def extract_promo_signals(text: str, normalized_urls: list[str] | None = None) -> list[str]:
    """Detect rule-based promotional and external contact inducement signals."""
    if not text:
        return []
    urls = normalized_urls if normalized_urls is not None else extract_urls(text)
    signals: list[str] = []

    if _TELEGRAM_RE.search(text) or any(u.startswith("t.me/") for u in urls):
        signals.append("CONTACT_TELEGRAM")

    if _KAKAO_RE.search(text) or any(u.startswith("open.kakao.com/") for u in urls):
        signals.append("CONTACT_KAKAO")

    for u in urls:
        domain = u.split("/", 1)[0]
        if domain in SHORTENER_DOMAINS:
            signals.append("SHORT_URL")
            break

    if _FINANCE_PROMO_RE.search(text):
        signals.append("PROMO_FINANCE")

    if _GAMBLING_RE.search(text):
        signals.append("GAMBLING_ADULT")

    return signals
