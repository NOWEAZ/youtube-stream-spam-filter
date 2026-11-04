"""Deterministic course algorithms. No model or third-party dependency required."""
import hashlib
import math
import re
import statistics
from collections import OrderedDict, defaultdict
from urllib.parse import parse_qs, urlsplit, urlunsplit


def hash64(value, salt=0):
    return int.from_bytes(hashlib.blake2b(str(value).encode('utf-8'), digest_size=8,
                                        person=salt.to_bytes(8, 'little')).digest(), 'big')


def normalize_url(raw):
    raw = raw.strip().rstrip('.,!;。，')
    if raw.startswith('//'):
        raw = 'https:' + raw
    if '://' not in raw:
        raw = 'https://' + raw
    try:
        parts = urlsplit(raw)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            return None
        host = parts.hostname.encode('idna').decode('ascii').lower()
        port = parts.port
        if host in ('www.youtube.com', 'youtube.com') and parts.path == '/redirect':
            target = parse_qs(parts.query).get('q', [''])[0]
            if target.startswith(('http://', 'https://')):
                return normalize_url(target)
        if ':' in host:
            host = '[' + host + ']'
        if port and not (parts.scheme.lower() == 'http' and port == 80 or
                         parts.scheme.lower() == 'https' and port == 443):
            host += ':' + str(port)
        return urlunsplit((parts.scheme.lower(), host, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError):
        return None


def extract_urls(text, links=()):
    found = re.findall(r'https?://[^\s<>"\u200b]+|www\.[^\s<>"\u200b]+|(?:t\.me|bit\.ly)/[\w/?=&%.-]+', text, re.I)
    return list(dict.fromkeys(u for raw in list(links) + found
                             if isinstance(raw, str) and (u := normalize_url(raw))))[:20]


class BloomFilter:
    def __init__(self, capacity, error_rate=0.01):
        capacity = max(1, capacity)
        if not 0 < error_rate < 1:
            raise ValueError('error_rate must be between zero and one')
        self.n = max(8, math.ceil(-capacity * math.log(error_rate) / math.log(2) ** 2))
        self.k = max(1, round(self.n / capacity * math.log(2)))
        self.bits = bytearray((self.n + 7) // 8)
        self.count = 0

    def indexes(self, value):
        a, b = hash64(value), hash64(value, 1) | 1
        return ((a + i * b) % self.n for i in range(self.k))

    def add(self, value):
        for i in self.indexes(value):
            self.bits[i // 8] |= 1 << (i % 8)
        self.count += 1

    def __contains__(self, value):
        return all(self.bits[i // 8] & (1 << (i % 8)) for i in self.indexes(value))

    @property
    def predicted_error(self):
        return (1 - math.exp(-self.k * self.count / self.n)) ** self.k


def shingles(text, k=3):
    normalized = re.sub(r'\s+', ' ', text.casefold()).strip()[:1500]
    return {normalized[i:i + k] for i in range(max(0, len(normalized) - k + 1))}


def jaccard(left, right):
    union = left | right
    return len(left & right) / len(union) if union else 0.0


class MinHashLSH:
    """48 hash signature, 16 bands of 3; exact reranking, bounded rolling index."""
    def __init__(self, limit=500, bands=16, rows=3):
        self.limit, self.bands, self.rows = limit, bands, rows
        self.records = OrderedDict()
        self.buckets = defaultdict(set)
        self.comparisons = 0

    def signature(self, elements):
        return tuple(min(hash64(e, salt) for e in elements)
                     for salt in range(self.bands * self.rows)) if elements else ()

    def keys(self, signature):
        if signature:
            for band in range(self.bands):
                yield (band, signature[band * self.rows:(band + 1) * self.rows])

    def query(self, elements, threshold=0.55):
        signature = self.signature(elements)
        ids = set()
        for key in self.keys(signature):
            ids.update(self.buckets.get(key, ()))
        matches = []
        for item_id in ids:
            record = self.records[item_id]
            score = jaccard(elements, record['elements'])
            self.comparisons += 1
            if score >= threshold:
                matches.append({'similarity': round(score, 4), 'verdict': record['verdict'],
                                'snippet': record['text'][:160]})
        return sorted(matches, key=lambda item: item['similarity'], reverse=True)[:5], signature

    def add(self, item_id, elements, signature, text, verdict):
        if not signature or item_id in self.records:
            return
        self.records[item_id] = dict(elements=elements, signature=signature, text=text[:1000], verdict=verdict)
        for key in self.keys(signature):
            self.buckets[key].add(item_id)
        while len(self.records) > self.limit:
            old_id, old = self.records.popitem(last=False)
            for key in self.keys(old['signature']):
                self.buckets[key].discard(old_id)
                if not self.buckets[key]:
                    del self.buckets[key]


class DGIM:
    """Newest bucket first; (size, last position). Timestamp position is a Python int."""
    def __init__(self, window=1000):
        self.window, self.position, self.buckets = window, 0, []

    def add(self, bit):
        self.position += 1
        self.buckets = [b for b in self.buckets if b[1] > self.position - self.window]
        if not bit:
            return
        self.buckets.insert(0, (1, self.position))
        size = 1
        while True:
            indexes = [i for i, bucket in enumerate(self.buckets) if bucket[0] == size]
            if len(indexes) <= 2:
                break
            recent, oldest = indexes[-2:]
            merged = (size * 2, self.buckets[recent][1])
            self.buckets[recent:oldest + 1] = [merged]
            size *= 2

    def estimate(self):
        if not self.buckets:
            return 0.0
        if self.position <= self.window:
            return float(sum(size for size, _ in self.buckets))
        return sum(size for size, _ in self.buckets[:-1]) + self.buckets[-1][0] / 2


class FlajoletMartin:
    """Textbook maximum-tail estimates, mean per group then median across groups."""
    def __init__(self, groups=5, per_group=8):
        self.groups, self.per_group = groups, per_group
        self.tails = [-1] * (groups * per_group)

    def add(self, value):
        for salt in range(len(self.tails)):
            hashed = hash64(value, salt)
            tail = (hashed & -hashed).bit_length() - 1 if hashed else 64
            self.tails[salt] = max(self.tails[salt], tail)

    def estimate(self):
        if self.tails[0] < 0:
            return 0
        means = [sum(2 ** r for r in self.tails[i:i + self.per_group]) / self.per_group
                 for i in range(0, len(self.tails), self.per_group)]
        return round(statistics.median(means))
