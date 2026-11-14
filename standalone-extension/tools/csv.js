// 따옴표·줄바꿈이 들어간 CSV(RFC 4180)를 읽는 최소 파서
function parseCsv(text) {
  text = text.replace(/^﻿/, '');
  const rows = []; let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows;
}
const quoteCsv = v => /[",\n\r]/.test(v) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
module.exports = { parseCsv, quoteCsv };
