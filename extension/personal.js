/* Local, bounded example-based personalisation. No network or automatic model training. */
(function(root) {
  'use strict';
  const MAX_LABELS = 500;
  const SEEDS = [
    { text: '원금 보장! 텔레그램 문의하세요', label: 'spam' },
    { text: 'Guaranteed profit! Join our Telegram and make money today.', label: 'spam' },
    { text: '영상 잘 봤습니다. 설명 감사합니다.', label: 'normal' },
    { text: 'Thanks for the clear explanation!', label: 'normal' },
  ];
  const normalize = text => text.normalize('NFKC').toLowerCase()
    .replace(/[\u200b-\u200f\u202a-\u202e\u2060\ufeff]/g, '').replace(/\s+/g, ' ').trim();
  const sha = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
    new TextEncoder().encode(value)))).map(x => x.toString(16).padStart(2, '0')).join('');
  const validRecord = row => row && /^[a-f0-9]{64}$/.test(row.key) && typeof row.text === 'string'
    && row.text.length > 0 && row.text.length <= 2000 && ['spam', 'normal'].includes(row.label)
    && Array.isArray(row.url_keys) && row.url_keys.length <= 20 && row.url_keys.every(x => /^[a-f0-9]{64}$/.test(x));

  async function prepare(text, urls = []) {
    if (typeof text !== 'string' || !text.trim() || text.length > 2000 || !Array.isArray(urls)
        || urls.length > 20 || urls.some(x => typeof x !== 'string' || x.length > 2048)) {
      throw new Error('라벨로 저장할 댓글 본문과 링크를 확인해 주세요.');
    }
    const normal = normalize(text);
    const links = urls.map(raw => { try { const u = new URL(raw); u.hash = ''; return u.href; } catch { return raw; } });
    const url_keys = [...new Set(await Promise.all(links.map(sha)))].sort();
    const key = await sha(normal + '\n' + url_keys.join('\n'));
    return { key, text: text.trim(), normal, url_keys };
  }

  async function record(text, urls, label, old) {
    if (!['spam', 'normal'].includes(label)) throw new Error('정상 또는 스팸 라벨을 선택해 주세요.');
    const sample = await prepare(text, urls);
    return { key: sample.key, text: sample.text, url_keys: sample.url_keys, label,
      label_source: 'user', user_scope: 'chrome_profile_local', review_status: 'user_confirmed',
      human_label: label, eligible_for_gold_evaluation: false,
      created_at: old?.created_at || new Date().toISOString(), updated_at: new Date().toISOString() };
  }
  function shingles(text) {
    const result = new Set();
    for (let i = 0; i + 3 <= text.length; i++) result.add(text.slice(i, i + 3));
    return result;
  }
  function jaccard(a, b) {
    let intersection = 0;
    for (const value of a) if (b.has(value)) intersection++;
    return a.size + b.size > intersection ? intersection / (a.size + b.size - intersection) : 0;
  }
  let seedPromise;
  async function buildIndex(rows = []) {
    const personal = rows.filter(validRecord).slice(0, MAX_LABELS);
    seedPromise ||= Promise.all(SEEDS.map(async item => ({ ...await prepare(item.text), ...item })));
    const exact = new Map((await seedPromise).map(item => [item.key, { ...item, kind: 'seed' }]));
    for (const item of personal) exact.set(item.key, { ...item, kind: 'exact' });
    const similar = personal.map(item => ({ ...item, normal: normalize(item.text) }))
      .filter(item => item.normal.length >= 30).map(item => ({ ...item, set: shingles(item.normal) }));
    return { exact, similar };
  }
  function match(sample, index) {
    const exact = index.exact.get(sample.key);
    if (exact) return { label: exact.label, kind: exact.kind, reference_id: exact.key, similarity: 1 };
    if (sample.normal.length < 30) return null;
    const set = shingles(sample.normal);
    let best = null;
    for (const row of index.similar) {
      // Normal examples cannot whitelist the same visible text pointing at different links.
      if (row.label === 'normal' && row.url_keys.join() !== sample.url_keys.join()) continue;
      const score = jaccard(set, row.set);
      if (score >= .92 && (!best || score > best.similarity)) {
        best = { label: 'review', kind: 'similar', reference_id: row.key,
          similarity: Number(score.toFixed(4)), reference_label: row.label };
      }
    }
    return best;
  }
  const stats = rows => ({ total: rows.length, spam: rows.filter(x => x.label === 'spam').length,
    normal: rows.filter(x => x.label === 'normal').length, limit: MAX_LABELS, seeds: SEEDS.length });
  const api = { MAX_LABELS, SEEDS, prepare, record, validRecord, buildIndex, match, stats };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PersonalLabels = api;
})(globalThis);
