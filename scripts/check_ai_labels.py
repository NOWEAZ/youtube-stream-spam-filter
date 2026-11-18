"""Run the local rules against a versioned AI-draft snapshot, without API keys or calls."""
import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.blocklist import Blocklist
from backend.engine import Engine
from backend.opencode import OpenCodeAgent


def load_snapshot(path):
    manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('공유 데이터의 체크섬이 manifest와 다릅니다.')
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(rows) != manifest['records'] or len({r['sample_id'] for r in rows}) != len(rows):
        raise ValueError('공유 데이터의 건수/중복 ID를 확인해 주세요.')
    for row in rows:
        if (row.get('label_source') != 'ai' or row.get('review_status') != 'unreviewed'
                or row.get('human_label') is not None or row.get('eligible_for_gold_evaluation') is not False
                or row.get('status') != 'completed' or row.get('ai_label') not in ('normal', 'spam', 'uncertain')):
            raise ValueError('AI 초벌 라벨의 상태가 올바르지 않습니다.')
    return rows, manifest


def check(path):
    rows, manifest = load_snapshot(path)
    root = ROOT/'.local/test-temp'
    root.mkdir(parents=True, exist_ok=True)
    predictions, differences, agreed, resolved = Counter(), [], 0, 0
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        blocklist = Blocklist(Path(temporary)/'empty.sqlite3')
        try:
            # Explicitly disabled even if the teammate has set an API key in their shell.
            engine = Engine(blocklist, OpenCodeAgent(False))
            for row in rows:
                # Isolate samples so index history or snapshot order cannot alter the comparison.
                result = engine.analyze(row['sample_id'], [{'id': row['sample_id'], 'text': row['text']}], False)['results'][0]
                verdict = {'safe': 'normal', 'spam': 'spam', 'review': 'uncertain'}[result['verdict']]
                predictions[verdict] += 1
                if verdict != 'uncertain' and row['ai_label'] != 'uncertain':
                    resolved += 1
                    agreed += verdict == row['ai_label']
                if verdict != row['ai_label']:
                    differences.append({'sample_id': row['sample_id'], 'ai_label': row['ai_label'],
                                        'rule_label': verdict, 'rule_reason': result['reason']})
        finally:
            blocklist.db.close()
    return {'dataset_id': manifest['dataset_id'], 'records': len(rows), 'api_requests': 0,
            'ai_label_counts': dict(Counter(r['ai_label'] for r in rows)), 'rule_label_counts': dict(predictions),
            'resolved_comparisons': resolved, 'agreed_on_resolved': agreed,
            'agreement_with_ai_on_resolved': agreed / resolved if resolved else None,
            'differences': differences,
            'note': 'Agreement with unreviewed AI labels, NOT accuracy against human gold. Empty blocklist; no OpenCode calls; each comment has an isolated session.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=ROOT/'data/ai_labels/pilot-v1.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT/'reports/ai-label-comparison.json')
    args = parser.parse_args()
    report = check(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
