#!/usr/bin/env node
/*
 * 악성 URL 목록을 확장 프로그램 데이터(extension/data)로 만든다.
 *   node tools/build_blocklist.js --input urlhaus.csv [--input list.txt] [--hosts] [--online-only]
 *
 * 입력 형식 (자동 판별)
 *   - URLhaus CSV: 열 이름에 url(과 url_status)가 있는 파일. '#'로 시작하는 줄은 주석.
 *   - 텍스트: 한 줄에 URL 하나, 또는 domain:example.test
 * 출력
 *   extension/data/blocklist.json       정확 조회용 키 목록 (확장 프로그램이 IndexedDB로 가져감)
 *   extension/data/blocklist.bloom.json Bloom Filter (n=키 개수, 오탐률 1%)
 *
 * --hosts : 각 URL의 호스트도 domain: 키로 추가합니다. 정상 호스팅 도메인을 통째로 차단할 위험이 있으니
 *           결과 목록을 한 번 훑어보세요. 기본값은 정확한 URL만 사용합니다.
 * 목록의 출처와 이용 조건은 직접 확인해야 하며, 이 도구는 아무것도 다운로드하지 않습니다.
 */
const fs = require('node:fs');
const path = require('node:path');
const CG = require('../extension/core.js');
const { parseCsv } = require('./csv.js');

const args = process.argv.slice(2);
const inputs = [], opts = { hosts: false, onlineOnly: false, out: path.join(__dirname, '..', 'extension', 'data') };
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--input') inputs.push(args[++i]);
  else if (args[i] === '--hosts') opts.hosts = true;
  else if (args[i] === '--online-only') opts.onlineOnly = true;
  else if (args[i] === '--out') opts.out = args[++i];
  else { console.error('알 수 없는 옵션: ' + args[i]); process.exit(1); }
}

const keys = new Set();
let skipped = 0;
function addUrl(raw) {
  const url = CG.normalizeUrl(raw);
  if (!url) { skipped++; return; }
  keys.add(url);
  if (opts.hosts) keys.add('domain:' + CG.hostOf(url));
}
for (const file of inputs) {
  const text = fs.readFileSync(file, 'utf8');
  const lines = text.split(/\r?\n/);
  // URLhaus CSV는 주석 줄 중 마지막이 "# id,dateadded,url,url_status,..." 헤더다.
  const headerLine = lines.find(l => /^#?\s*"?id"?,.*url/i.test(l));
  if (headerLine) {
    const header = headerLine.replace(/^#\s*/, '').split(',').map(s => s.replace(/"/g, '').trim().toLowerCase());
    const urlCol = header.indexOf('url'), statusCol = header.indexOf('url_status');
    for (const row of parseCsv(lines.filter(l => !l.startsWith('#')).join('\n'))) {
      if (opts.onlineOnly && statusCol >= 0 && row[statusCol] !== 'online') continue;
      if (row[urlCol]) addUrl(row[urlCol]);
    }
  } else {
    for (const line of lines) {
      const v = line.trim();
      if (!v || v.startsWith('#')) continue;
      if (v.startsWith('domain:')) { const h = CG.hostOf(CG.normalizeUrl(v.slice(7)) || ''); if (h) keys.add('domain:' + h); else skipped++; }
      else if (/^https?:\/\//i.test(v)) addUrl(v);
      else skipped++;
    }
  }
}

const list = [...keys].sort();
const version = list.length + '-' + CG.hash32(list.join('\n')).toString(16);
const bloom = new CG.BloomFilter(Math.max(1, list.length), 0.01);
for (const key of list) bloom.add(key);
fs.mkdirSync(opts.out, { recursive: true });
fs.writeFileSync(path.join(opts.out, 'blocklist.json'), JSON.stringify({ version, keys: list }));
fs.writeFileSync(path.join(opts.out, 'blocklist.bloom.json'), JSON.stringify(bloom.toJSON()));
console.log(`키 ${list.length}개 (건너뜀 ${skipped}개) · Bloom m=${bloom.m}비트(${(bloom.bits.length / 1024).toFixed(1)}KB) k=${bloom.k} · 이론 오탐률 ${(bloom.expectedFalsePositiveRate() * 100).toFixed(2)}%`);
console.log('출력: ' + opts.out);
