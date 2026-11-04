import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from .algorithms import BloomFilter, normalize_url


class Blocklist:
    """SQLite exact lookups: never retain the full blacklist as a Python set."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS threats (key TEXT PRIMARY KEY)')
        self.rebuild()

    def rebuild(self):
        self.count = self.db.execute('SELECT COUNT(*) FROM threats').fetchone()[0]
        self.bloom = BloomFilter(self.count)
        for (key,) in self.db.execute('SELECT key FROM threats'):
            self.bloom.add(key)

    def import_file(self, path):
        imported = 0
        with open(path, encoding='utf-8-sig') as source:
            for line in source:
                value = line.strip()
                if not value or value.startswith('#'):
                    continue
                if value.startswith('domain:'):
                    host = urlsplit(normalize_url(value[7:]) or '').hostname
                    key = 'domain:' + host if host else None
                elif value.startswith(('http://', 'https://')):
                    key = normalize_url(value)
                else:
                    continue
                if key:
                    imported += self.db.execute('INSERT OR IGNORE INTO threats VALUES (?)', (key,)).rowcount
        self.db.commit()
        self.rebuild()
        return imported

    def lookup(self, raw):
        url = normalize_url(raw)
        if not url:
            return {'matched': False, 'bloom_positive': False}
        host = urlsplit(url).hostname
        candidates = [url, 'domain:' + host]
        positive = False
        for key in candidates:
            if key in self.bloom:
                positive = True
                if self.db.execute('SELECT 1 FROM threats WHERE key=?', (key,)).fetchone():
                    return {'matched': True, 'bloom_positive': True, 'key': key}
        return {'matched': False, 'bloom_positive': positive}
