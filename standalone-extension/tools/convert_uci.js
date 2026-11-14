#!/usr/bin/env node
/*
 * UCI "YouTube Spam Collection"(Psy, KatyPerry, LMFAO, Eminem, Shakira CSV)을
 * 평가용 text,label,group CSV로 합친다.
 *   node tools/convert_uci.js --out data/uci.csv Youtube01-Psy.csv Youtube02-KatyPerry.csv ...
 * 데이터는 https://archive.ics.uci.edu/dataset/380/youtube+spam+collection (CC BY 4.0) 에서 직접 받으세요.
 * 같은 문장이 여러 번 들어 있어 정확히 같은 text는 한 번만 남기고, 그 수를 출력합니다.
 */
const fs = require('node:fs');
const path = require('node:path');
const { parseCsv, quoteCsv } = require('./csv.js');

const args = process.argv.slice(2);
let out = 'data/uci.csv'; const files = [];
for (let i = 0; i < args.length; i++) { if (args[i] === '--out') out = args[++i]; else files.push(args[i]); }
if (!files.length) { console.error('사용법: node tools/convert_uci.js --out data/uci.csv <UCI CSV 파일들>'); process.exit(1); }

const clean = s => s.replace(/<br\s*\/?>/gi, ' ').replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&')
  .replace(/﻿/g, '').replace(/\s+/g, ' ').trim();
const seen = new Set(); const rows = []; let dup = 0;
for (const file of files) {
  const table = parseCsv(fs.readFileSync(file, 'utf8'));
  const header = table[0].map(h => h.trim().toUpperCase());
  const c = header.indexOf('CONTENT'), k = header.indexOf('CLASS');
  if (c < 0 || k < 0) { console.error(file + ': CONTENT, CLASS 열이 없습니다.'); process.exit(1); }
  const group = path.basename(file).replace(/\.csv$/i, '');
  for (const row of table.slice(1)) {
    const text = clean(row[c] || ''), label = row[k] === '1' ? 'spam' : row[k] === '0' ? 'ham' : null;
    if (!text || !label) continue;
    if (seen.has(text)) { dup++; continue; }
    seen.add(text); rows.push([text, label, group]);
  }
}
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, 'text,label,group\n' + rows.map(r => r.map(quoteCsv).join(',')).join('\n') + '\n');
const spam = rows.filter(r => r[1] === 'spam').length;
console.log(`${rows.length}건 저장 (spam ${spam}, ham ${rows.length - spam}) · 완전 중복 ${dup}건 제거 → ${out}`);
