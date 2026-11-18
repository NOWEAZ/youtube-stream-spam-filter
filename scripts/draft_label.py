"""Import redacted JSONL and optionally spend a bounded number of OpenCode requests."""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import load_env
from backend.labeling import DraftLabeler, LabelStore, PROMPT_VERSION


def main():
    parser = argparse.ArgumentParser(description='OpenCode AI 생성 초벌 라벨링 (정답 데이터와 분리)')
    parser.add_argument('--input', type=Path, help='text와 source를 포함한 JSONL')
    parser.add_argument('--run', action='store_true', help='없으면 가져오기와 대기 건수 확인만 수행')
    parser.add_argument('--max-requests', type=int, default=20)
    parser.add_argument('--requests-per-minute', type=int, default=6)
    parser.add_argument('--retry-errors', action='store_true')
    parser.add_argument('--db', type=Path, default=ROOT/'.local/labels/drafts.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT/'.local/labels/ai-drafts.jsonl')
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 500 or not 1 <= args.requests_per_minute <= 12:
        parser.error('max-requests는 1~500, requests-per-minute은 1~12여야 합니다.')
    # Labels and source text must stay in the ignored local directory.
    local = (ROOT/'.local').resolve()
    for path in (args.db, args.output):
        if not path.resolve().is_relative_to(local):
            parser.error('DB와 출력 파일은 Git·배포 제외 경로 .local 안에 저장해 주세요.')
    load_env(ROOT/'.env')
    labeler = DraftLabeler()
    if args.run and not labeler.agent.enabled:
        parser.error('.env의 OPENCODE_API_KEY 설정을 확인해 주세요.')
    store = LabelStore(args.db)
    try:
        if args.input:
            # Validate the complete input before committing any import.
            records = [json.loads(line) for line in args.input.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
            if any(not isinstance(row, dict) or not isinstance(row.get('text'), str)
                   or not row['text'].strip() or len(row['text']) > 8000 for row in records):
                parser.error('각 줄에는 1~8000자 text가 필요합니다.')
            for record in records:
                store.add(record, labeler.agent)
        pending = store.pending(labeler.agent, args.max_requests, args.retry_errors)
        print(json.dumps({'mode': 'run' if args.run else 'preview', 'pending_in_this_run': len(pending),
                          'provider': labeler.agent.provider, 'model': labeler.agent.model,
                          'prompt_version': PROMPT_VERSION, 'label_source': 'ai'}), flush=True)
        next_request, failures, attempted = 0, 0, 0
        if args.run:
            for sample in pending:
                delay = next_request - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                next_request = time.monotonic() + 60 / args.requests_per_minute
                result, error = labeler.classify(sample['text'])
                store.save(sample['sample_id'], labeler.agent, result, error)
                store.export(args.output)  # Durable export after every successful or failed request.
                attempted += 1
                failures = failures + 1 if error else 0
                print(json.dumps({'completed_request': attempted, 'label': result['label'] if result else None,
                                  'status': 'error' if error else 'completed', 'error_code': error}), flush=True)
                if failures >= 3 or error in ('http_401', 'http_402', 'http_403', 'http_429'):
                    print(json.dumps({'stopped': 'provider_error_or_rate_limit'}), flush=True)
                    break
        rows = store.export(args.output)
        summary = {'ai_label_counts': dict(Counter(row['ai_label'] for row in rows if row['status'] == 'completed')),
                   'status_counts': dict(Counter(row['status'] for row in rows)),
                   'language_counts': dict(Counter(row['language'] for row in rows)),
                   'human_reviewed': sum(row['review_status'] != 'unreviewed' for row in rows),
                   'api_requests_this_run': attempted, 'gold_evaluation_eligible': 0,
                   'output': str(args.output)}
        args.output.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=True), flush=True)
    finally:
        store.db.close()


if __name__ == '__main__':
    main()
