"""Prepare deterministic public UCI samples without retaining AUTHOR, IDs, dates or CLASS."""
import argparse
import csv
import io
import json
import random
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.labeling import redact_text

SOURCE = 'https://archive.ics.uci.edu/dataset/380/youtube+spam+collection'
DOWNLOAD = 'https://archive.ics.uci.edu/static/public/380/youtube%2Bspam%2Bcollection.zip'


def prepare(output, per_file=4):
    with urllib.request.urlopen(DOWNLOAD, timeout=30) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError('dataset download exceeded size limit')
    records, rng, seen = [], random.Random(42), set()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 10_000_000:
            raise ValueError('dataset uncompressed size exceeded limit')
        for name in sorted(archive.namelist()):
            if not re.fullmatch(r'Youtube\d+-[^/]+\.csv', Path(name).name):
                continue
            rows = list(csv.DictReader(io.StringIO(archive.read(name).decode('utf-8-sig'))))
            rng.shuffle(rows)  # Do not inspect CLASS when choosing samples or requesting AI labels.
            chosen = 0
            for row in rows:
                text = redact_text(row.get('CONTENT', ''))
                if not text or text.casefold() in seen or len(text) > 8000:
                    continue
                records.append({'text': text, 'source': 'uci-youtube-spam-collection',
                                'source_id': f'{Path(name).stem}:sample-{chosen}', 'source_url': SOURCE})
                seen.add(text.casefold())
                chosen += 1
                if chosen >= per_file:
                    break
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('output already exists; original input is never overwritten')
    output.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
    manifest = {'source_url': SOURCE, 'download_url': DOWNLOAD, 'seed': 42, 'samples': len(records),
                'selection': 'per-video random sample without CLASS or live rule filtering',
                'license': 'CC BY 4.0', 'citation': 'Alberto, T. & Lochter, J. (2015). YouTube Spam Collection. DOI: 10.24432/C58885',
                'original_labels_used': False, 'author_fields_retained': False,
                'redacted': True, 'notice': 'Contact/URL masking is imperfect and may remove classification context.'}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'.local/labels/uci-pilot.jsonl')
    parser.add_argument('--per-video', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.per_video <= 100:
        parser.error('per-video must be 1..100')
    if not args.output.resolve().is_relative_to((ROOT/'.local').resolve()):
        parser.error('output must be inside ignored .local')
    print(json.dumps(prepare(args.output, args.per_video), ensure_ascii=True))
