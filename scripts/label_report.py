"""Render a local, read-only HTML review report. Text is escaped, no scripts or remote assets."""
import argparse
import html
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(source, output):
    records = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    counts = Counter(row['ai_label'] for row in records if row['status'] == 'completed')
    esc = lambda value: html.escape(str(value if value is not None else '—'), quote=True)
    rows = []
    for index, item in enumerate(records, 1):
        rows.append(f'''<tr><td>{index}</td><td>{esc(item['language'])}<br>{esc(item['source'])}</td>
        <td><details><summary>{esc(item['text'][:100])}</summary><p>{esc(item['text'])}</p></details></td>
        <td><b>{esc(item['ai_label'])}</b><br>{esc(item['status'])}</td><td>{esc(item['ai_reason'])}</td>
        <td>{esc(item['review_status'])}<br>최종 정답: {esc(item['human_label'])}</td></tr>''')
    document = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>AI 생성 초벌 라벨 검토</title>
    <style>body{{font:15px/1.6 system-ui,sans-serif;margin:32px;color:#213047;background:#f7f9fc}}
    h1{{font-size:26px}}.notice{{padding:16px;background:#fff4d8;border-radius:10px}}
    table{{border-collapse:collapse;width:100%;margin:24px 0;background:white}}th,td{{padding:12px;border:1px solid #dce2eb;text-align:left;vertical-align:top}}
    th{{background:#eaf0f7}}td:nth-child(3){{width:35%;overflow-wrap:anywhere}}td:nth-child(5){{width:25%}}summary{{cursor:pointer}}p{{white-space:pre-wrap;overflow-wrap:anywhere}}small{{color:#57677c}}</style>
    <h1>AI 생성 초벌 라벨 검토</h1><p class="notice">이 결과는 OpenCode가 생성한 <b>미검토 라벨</b>입니다.
    사람이 확인한 정답·최종 평가 데이터가 아닙니다. 이 화면은 읽기 전용이며 댓글을 클릭하면 원문 전체가 펼쳐집니다.</p>
    <p>총 {len(records)}건 · AI 정상 {counts['normal']} · AI 스팸 {counts['spam']} · AI 판단 보류 {counts['uncertain']}</p>
    <table><thead><tr><th>번호</th><th>언어·출처</th><th>마스킹된 댓글</th><th>AI 라벨</th><th>AI 근거</th><th>사람 검토</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
    <small>normal = AI 정상 의견 / spam = AI 스팸 의견 / uncertain = AI 판단 보류. 연락처·주소 마스킹은 완전한 익명화가 아니며 문맥 손실이 있을 수 있습니다.
    <br>공개 자료: Alberto &amp; Lochter (2015), YouTube Spam Collection, UCI, DOI 10.24432/C58885, CC BY 4.0.
    제공받은 한국어 댓글은 별도 출처로 구분합니다. 댓글과 라벨은 로컬에만 보관합니다.</small></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    return len(records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=ROOT/'.local/labels/ai-drafts.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT/'.local/labels/ai-drafts.html')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT/'.local').resolve()):
        parser.error('output must stay inside ignored .local')
    print(json.dumps({'rows': render(args.input, args.output), 'output': str(args.output)}))
