#!/usr/bin/env node
/*
 * 재현 가능한 평가. 모든 난수는 --seed로 고정된다.
 *   node --expose-gc tools/evaluate.js                       # 알고리즘 벤치마크만
 *   node --expose-gc tools/evaluate.js --csv data/uci.csv    # + 분류 성능 (text,label[,group])
 * 결과: reports/evaluation.json, reports/evaluation.md
 *
 * 분류 평가 규칙
 *  - 근접 중복(유사도 ≥ 0.8) 댓글은 한 덩어리로 묶어 같은 쪽(train 또는 test)에만 들어가게 한다.
 *  - LSH 참조 집합은 train의 "정답 라벨"로만 만든다. 예측이 예측을 키우지 않는다.
 *  - 민감도(임계값)는 train에서만 고르고, test에서는 한 번만 보고한다.
 *  - 'review'는 별도로 보고한다: (a) 스팸 의심만 양성, (b) 스팸 의심+검토 필요를 양성.
 */
const fs = require('node:fs');
const path = require('node:path');
const CG = require('../extension/core.js');
const { parseCsv } = require('./csv.js');

const args = process.argv.slice(2);
const opt = { csv: null, seed: 42, testRatio: 0.3, out: path.join(__dirname, '..', 'reports') };
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--csv') opt.csv = args[++i];
  else if (args[i] === '--seed') opt.seed = Number(args[++i]);
  else if (args[i] === '--test-ratio') opt.testRatio = Number(args[++i]);
  else if (args[i] === '--out') opt.out = args[++i];
}
const now = () => Number(process.hrtime.bigint()) / 1e6;
const mean = a => a.reduce((x, y) => x + y, 0) / (a.length || 1);
const std = a => { const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))); };
const pct = x => (x * 100).toFixed(1) + '%';
const report = { seed: opt.seed, node: process.version };

/* ═════════ A. 분류 성능 ═════════ */

function metrics(tp, fp, fn, tn) {
  const precision = tp + fp ? tp / (tp + fp) : 0, recall = tp + fn ? tp / (tp + fn) : 0;
  return { tp, fp, fn, tn, precision, recall, f1: precision + recall ? (2 * precision * recall) / (precision + recall) : 0 };
}

function splitData(rows, seed, testRatio) {
  // 1) 근접 중복 덩어리 만들기 (LSH 후보 + Jaccard ≥ 0.8, union-find)
  const lsh = new CG.MinHashLSH({ limit: rows.length + 1 });
  const parent = rows.map((_, i) => i);
  const find = x => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  rows.forEach((r, i) => {
    const set = CG.shingles(r.text), sig = lsh.signature(set);
    for (const m of lsh.query(set, sig, 0.8, 50)) parent[find(i)] = find(Number(m.id));
    lsh.add(String(i), set, sig, r.text);
  });
  const clusters = new Map();
  rows.forEach((_, i) => { const root = find(i); if (!clusters.has(root)) clusters.set(root, []); clusters.get(root).push(i); });
  // 2) 라벨별로 덩어리를 섞어 약 testRatio만큼 test로 보낸다 (층화)
  const rnd = CG.mulberry32(seed), train = [], test = [];
  for (const label of ['spam', 'ham']) {
    const groups = [...clusters.values()].filter(c => rows[c[0]].label === label)
      .map(c => ({ c, r: rnd() })).sort((a, b) => a.r - b.r).map(x => x.c);
    const total = groups.reduce((s, g) => s + g.length, 0);
    let taken = 0;
    for (const g of groups) { if (taken < total * testRatio) { test.push(...g); taken += g.length; } else train.push(...g); }
  }
  return { train: train.map(i => rows[i]), test: test.map(i => rows[i]), clusterCount: clusters.size };
}

async function classify(rows, sensitivity, refs) {
  const engine = new CG.Engine({ sensitivity, learned: refs });
  const out = [];
  for (let i = 0; i < rows.length; i++) {
    if (i % 200 === 0) engine.newSession();   // 영상 단위 세션 흉내
    out.push(await engine.analyze({ id: 'r' + i, text: rows[i].text, account: 'acc' + (i % 97) }));
  }
  return out;
}

function confusion(rows, preds, positive) {
  let tp = 0, fp = 0, fn = 0, tn = 0;
  rows.forEach((r, i) => {
    const actual = r.label === 'spam', pred = positive(preds[i].verdict);
    if (actual && pred) tp++; else if (!actual && pred) fp++; else if (actual) fn++; else tn++;
  });
  return metrics(tp, fp, fn, tn);
}

async function classification(file) {
  const table = parseCsv(fs.readFileSync(file, 'utf8'));
  const header = table[0].map(h => h.trim().toLowerCase());
  const t = header.indexOf('text'), l = header.indexOf('label');
  if (t < 0 || l < 0) throw new Error('CSV 헤더에 text, label이 필요합니다.');
  const rows = table.slice(1).map(r => ({ text: r[t], label: ['1', 'spam'].includes(r[l].trim().toLowerCase()) ? 'spam' : 'ham' }))
    .filter(r => r.text && r.text.trim());
  const { train, test, clusterCount } = splitData(rows, opt.seed, opt.testRatio);
  const refs = { spam: train.filter(r => r.label === 'spam').map(r => r.text), ham: train.filter(r => r.label === 'ham').map(r => r.text) };
  const res = { file: path.basename(file), total: rows.length, spam: rows.filter(r => r.label === 'spam').length, nearDuplicateClusters: clusterCount,
    train: train.length, test: test.length, runs: {} };
  for (const sensitivity of ['strict', 'normal', 'lenient']) {
    const run = {};
    for (const [name, useRefs] of [['rules', null], ['rules+lsh', refs]]) {
      // rules+lsh를 train에서 재면 자기 자신을 참조하므로 train 지표는 규칙만 보고한다.
      const trainPred = await classify(train, sensitivity, null);
      const testPred = await classify(test, sensitivity, useRefs);
      run[name] = {
        train_spam_only: name === 'rules' ? confusion(train, trainPred, v => v === 'spam') : undefined,
        test_spam_only: confusion(test, testPred, v => v === 'spam'),
        test_spam_or_review: confusion(test, testPred, v => v !== 'safe'),
        test_review_count: testPred.filter(p => p.verdict === 'review').length,
      };
      if (name === 'rules' && sensitivity === 'normal') {
        run.errors = {
          false_negative: test.filter((r, i) => r.label === 'spam' && testPred[i].verdict !== 'spam').slice(0, 8).map(r => r.text),
          false_positive: test.filter((r, i) => r.label === 'ham' && testPred[i].verdict === 'spam').slice(0, 8).map(r => r.text),
        };
      }
    }
    res.runs[sensitivity] = run;
  }
  // train F1이 가장 높은 민감도를 train에서만 골라 test에서 한 번 보고한다.
  const best = Object.entries(res.runs).map(([s, r]) => [s, r.rules.train_spam_only.f1]).sort((a, b) => b[1] - a[1])[0][0];
  res.selected_sensitivity_by_train = best;
  res.final_test = { sensitivity: best, rules: res.runs[best].rules.test_spam_only, rules_plus_lsh: res.runs[best]['rules+lsh'].test_spam_only };
  return res;
}

/* ═════════ B. Bloom Filter ═════════ */

function bloomBench() {
  const rows = [];
  for (const n of [10_000, 100_000, 500_000]) {
    const members = Array.from({ length: n }, (_, i) => `https://member-${i}.example/p/${i}`);
    const queries = Array.from({ length: 100_000 }, (_, i) => `https://unseen-${i}.example/q/${i}`);
    if (global.gc) global.gc();
    const heap0 = process.memoryUsage().heapUsed;
    const set = new Set(members);
    if (global.gc) global.gc();
    const setBytes = process.memoryUsage().heapUsed - heap0;
    const bloom = new CG.BloomFilter(n, 0.01);
    let t = now(); for (const m of members) bloom.add(m); const buildMs = now() - t;
    t = now(); let fp = 0; for (const q of queries) if (bloom.has(q)) fp++; const bloomMs = now() - t;
    t = now(); let sh = 0; for (const q of queries) if (set.has(q)) sh++; const setMs = now() - t;
    let fn = 0; for (const m of members) if (!bloom.has(m)) fn++;
    const hitRate = 0.001;     // 실제 댓글의 URL 중 악성 비율을 0.1%로 가정
    rows.push({ n, m_bits: bloom.m, k: bloom.k, bloom_bytes: bloom.bits.length, set_bytes_approx: global.gc ? setBytes : null,
      expected_fpr: bloom.expectedFalsePositiveRate(), measured_fpr: fp / queries.length, false_negatives: fn, set_false_positives: sh,
      bloom_query_ns: (bloomMs * 1e6) / queries.length, set_query_ns: (setMs * 1e6) / queries.length, build_ms: buildMs,
      // Bloom이 정확 조회(IndexedDB 등 느린 저장소)를 얼마나 건너뛰게 하는지: 조회 비율 = 진짜 적중 + 오탐
      exact_lookup_share_with_bloom: hitRate + (1 - hitRate) * (fp / queries.length), exact_lookup_share_without_bloom: 1 });
  }
  return rows;
}

/* ═════════ C. MinHash + LSH ═════════ */

const SYL = '가나다라마바사아자차카타파하강낙담랑만박상안장창칸탄판한건넌던런먼번선언전천켄텐펜헨규뉴듀류뮤뷰슈유주추쿠투푸후'.split('');
function randomWord(rnd) { const len = 2 + Math.floor(rnd() * 3); let w = ''; for (let i = 0; i < len; i++) w += SYL[Math.floor(rnd() * SYL.length)]; return w; }
function makeVocabulary(rnd, size) { return Array.from({ length: size }, () => randomWord(rnd)); }
function makeDoc(rnd, vocab) { const n = 8 + Math.floor(rnd() * 14); return Array.from({ length: n }, () => vocab[Math.floor(rnd() * vocab.length)]); }
function perturb(words, rate, rnd, vocab) {
  const out = [];
  for (const w of words) {
    const r = rnd();
    if (r < rate / 3) continue;                                   // 삭제
    else if (r < (2 * rate) / 3) out.push(vocab[Math.floor(rnd() * vocab.length)]); // 치환
    else { out.push(w); if (r > 1 - rate / 3) out.push(vocab[Math.floor(rnd() * vocab.length)]); } // 삽입
  }
  return out.length ? out : words;
}

function lshBench(seed) {
  const rnd = CG.mulberry32(seed), vocab = makeVocabulary(rnd, 3000);
  const N = 2000;
  const docs = Array.from({ length: N }, () => makeDoc(rnd, vocab));
  const lsh = new CG.MinHashLSH({ limit: N + 10 });
  const sets = docs.map(d => CG.shingles(d.join(' ')));
  sets.forEach((s, i) => lsh.add(String(i), s, lsh.signature(s), ''));
  // 변형 쌍을 만들어 실제 Jaccard 구간별로 "후보가 되었는지" 센다.
  const bins = [[0.3, 0.4], [0.4, 0.5], [0.5, 0.6], [0.6, 0.7], [0.7, 0.8], [0.8, 0.9], [0.9, 1.01]];
  const stat = bins.map(([lo, hi]) => ({ lo, hi: Math.min(hi, 1), pairs: 0, candidate: 0, sumS: 0 }));
  for (let rep = 0; rep < 6000; rep++) {
    const i = Math.floor(rnd() * N), rate = 0.05 + rnd() * 0.75;
    const variant = perturb(docs[i], rate, rnd, vocab), vs = CG.shingles(variant.join(' '));
    const s = CG.jaccard(sets[i], vs), bin = stat.find(b => s >= b.lo && s < (b.hi === 1 ? 1.01 : b.hi));
    if (!bin) continue;
    bin.pairs++; bin.sumS += s;
    if (lsh.candidates(lsh.signature(vs)).has(String(i))) bin.candidate++;
  }
  const curve = stat.map(b => ({ range: `${b.lo.toFixed(1)}–${b.hi.toFixed(1)}`, pairs: b.pairs,
    measured_candidate_rate: b.pairs ? b.candidate / b.pairs : null,
    theory_at_mean_similarity: b.pairs ? CG.MinHashLSH.candidateProbability(b.sumS / b.pairs) : null }));
  // 규모: 변형 500개를 질의할 때 후보 비교 수 vs 전수 비교 수
  lsh.comparisons = 0;
  let found = 0, trueNear = 0;
  for (let q = 0; q < 500; q++) {
    const i = Math.floor(rnd() * N), vs = CG.shingles(perturb(docs[i], 0.1, rnd, vocab).join(' ')), sig = lsh.signature(vs);
    const hit = lsh.query(vs, sig, 0.6, 50);
    if (CG.jaccard(sets[i], vs) >= 0.6) { trueNear++; if (hit.some(h => h.id === String(i))) found++; }
  }
  const t0 = now(); const probe = CG.shingles(docs[0].join(' ')); for (let k = 0; k < 200; k++) lsh.signature(probe); const sigMs = (now() - t0) / 200;
  return { documents: N, queries: 500, brute_force_comparisons: N * 500, lsh_comparisons: lsh.comparisons,
    reduction_factor: (N * 500) / Math.max(1, lsh.comparisons), recall_at_0_6: trueNear ? found / trueNear : null,
    signature_ms_per_comment: sigMs, curve, params: { bands: lsh.bands, rows: lsh.rows, permutations: lsh.numPerm } };
}

/* ═════════ D. Flajolet-Martin ═════════ */

// 비교용 기준선: 해시 40개의 최대 trailing zero, 8개씩 5그룹 2^R 평균 → 중앙값 (팀 데모 버전과 같은 방식을 이 코드의 해시로 다시 구현한 것)
function baselineMaxTail(values, salt) {
  const tails = new Array(40).fill(-1);
  for (const v of values) for (let s = 0; s < 40; s++) {
    const h = CG.hash32(v, 1000 + s + salt * 7919); const tz = h === 0 ? 32 : 31 - Math.clz32(h & -h);
    if (tz > tails[s]) tails[s] = tz;
  }
  const means = [];
  for (let g = 0; g < 5; g++) means.push(mean(tails.slice(g * 8, g * 8 + 8).map(r => 2 ** r)));
  means.sort((a, b) => a - b); return means[2];
}

function fmBench() {
  const sizes = [10, 100, 1000, 10000, 100000], runs = 30, out = [];
  for (const n of sizes) {
    const row = { n };
    for (const m of [16, 64, 256]) {
      const errs = [];
      for (let s = 0; s < runs; s++) {
        const fm = new CG.FlajoletMartin(m); for (let i = 0; i < n; i++) fm.add(`s${s}:${i}`);
        errs.push((fm.estimate() - n) / n);
      }
      row['pcsa_m' + m] = { mean_abs_rel_error: mean(errs.map(Math.abs)), bias: mean(errs), std: std(errs), theory_std: 0.78 / Math.sqrt(m) };
    }
    if (n <= 10000) {
      const errs = [];
      for (let s = 0; s < 10; s++) errs.push((baselineMaxTail(Array.from({ length: n }, (_, i) => `s${s}:${i}`), s) - n) / n);
      row.baseline_max_tail_40hash = { mean_abs_rel_error: mean(errs.map(Math.abs)), bias: mean(errs), std: std(errs) };
    }
    out.push(row);
  }
  return out;
}

/* ═════════ E. DGIM ═════════ */

function dgimBench(seed) {
  const out = [];
  for (const window of [50, 100, 1000]) for (const p of [0.05, 0.2, 0.5, 0.9]) {
    const rnd = CG.mulberry32(seed + window), dgim = new CG.DGIM(window), bits = [];
    let maxErr = 0, sumErr = 0, samples = 0, maxBuckets = 0;
    for (let i = 0; i < 20000; i++) {
      const bit = rnd() < p ? 1 : 0; bits.push(bit); dgim.add(bit);
      if (i >= window) {
        let actual = 0; for (let j = i - window + 1; j <= i; j++) actual += bits[j];
        if (actual) { const e = Math.abs(dgim.estimate() - actual) / actual; maxErr = Math.max(maxErr, e); sumErr += e; samples++; }
      }
      maxBuckets = Math.max(maxBuckets, dgim.buckets.length);
    }
    out.push({ window, p, max_rel_error: maxErr, mean_rel_error: sumErr / Math.max(1, samples), max_buckets: maxBuckets, bound: 0.5 });
  }
  return out;
}

/* ═════════ 실행·보고서 ═════════ */

function markdown(r) {
  const L = [];
  L.push('# 평가 결과', '', `seed ${r.seed} · Node ${r.node} · 생성 ${new Date().toISOString().slice(0, 10)}`, '');
  if (r.classification) {
    const c = r.classification, f = c.final_test;
    L.push('## 1. 분류 성능', '', `데이터 ${c.file}: ${c.total}건(spam ${c.spam}), 근접 중복 덩어리 ${c.nearDuplicateClusters}개 → train ${c.train} / test ${c.test}.`,
      ...(path.basename(c.file).startsWith('sample') ? ['> ⚠️ 이 파일은 개발자가 규칙을 만들면서 직접 쓴 소규모 샘플입니다. 스모크 테스트용이며 성능을 주장하는 근거가 아닙니다. 실제 데이터(예: UCI)로 다시 측정하세요.', ''] : []),
      `민감도는 train에서만 골라 \`${f.sensitivity}\`로 정했고, 아래는 test에서 한 번만 잰 값입니다. 스팸 의심만 양성으로 셉니다.`, '',
      '| 구성 | 정밀도 | 재현율 | F1 | TP | FP | FN | TN |', '|---|---|---|---|---|---|---|---|');
    for (const [name, m] of [['규칙만', f.rules], ['규칙 + LSH(train 정답 참조)', f.rules_plus_lsh]])
      L.push(`| ${name} | ${pct(m.precision)} | ${pct(m.recall)} | ${pct(m.f1)} | ${m.tp} | ${m.fp} | ${m.fn} | ${m.tn} |`);
    L.push('', '민감도별(test, 스팸 의심만 양성 / 스팸 의심+검토 필요 양성):', '', '| 민감도 | 구성 | 정밀도 | 재현율 | F1 | (검토 포함) 재현율 | 검토 필요 수 |', '|---|---|---|---|---|---|---|');
    for (const [s, run] of Object.entries(c.runs)) for (const name of ['rules', 'rules+lsh']) {
      const a = run[name].test_spam_only, b = run[name].test_spam_or_review;
      L.push(`| ${s} | ${name} | ${pct(a.precision)} | ${pct(a.recall)} | ${pct(a.f1)} | ${pct(b.recall)} | ${run[name].test_review_count} |`);
    }
    L.push('', '놓친 스팸 예시(test, normal):', ...c.runs.normal.errors.false_negative.map(t => '- ' + t), '', '잘못 잡은 정상 예시:', ...(c.runs.normal.errors.false_positive.length ? c.runs.normal.errors.false_positive.map(t => '- ' + t) : ['- 없음']), '');
  } else L.push('## 1. 분류 성능', '', '`--csv`를 주지 않아 실행하지 않았습니다.', '');
  L.push('## 2. Bloom Filter', '', '| n | m(비트) | k | Bloom 메모리 | Set 메모리(근사) | 이론 오탐 | 실측 오탐 | 미탐 | Bloom 조회 | Set 조회 | 정확 조회 비율(Bloom 사용) |', '|---|---|---|---|---|---|---|---|---|---|---|');
  for (const b of r.bloom) L.push(`| ${b.n.toLocaleString()} | ${b.m_bits.toLocaleString()} | ${b.k} | ${(b.bloom_bytes / 1024).toFixed(0)}KB | ${b.set_bytes_approx ? (b.set_bytes_approx / 1048576).toFixed(1) + 'MB' : '-'} | ${pct(b.expected_fpr)} | ${pct(b.measured_fpr)} | ${b.false_negatives} | ${b.bloom_query_ns.toFixed(0)}ns | ${b.set_query_ns.toFixed(0)}ns | ${pct(b.exact_lookup_share_with_bloom)} |`);
  L.push('', 'Bloom 자체의 조회는 JS Set보다 느릴 수 있습니다(해시 k번 계산). 이득은 (1) 메모리, (2) 악성 URL 비율이 낮을 때 느린 정확 조회(IndexedDB)를 거의 모두 건너뛰는 것입니다. 마지막 열은 악성 URL 비율을 0.1%로 가정했을 때 정확 조회로 넘어가는 비율이며, Bloom이 없으면 100%입니다.', '');
  const l = r.lsh;
  L.push('## 3. MinHash + LSH', '', `서명 ${l.params.permutations}개 = ${l.params.bands}밴드 × ${l.params.rows}행. 문서 ${l.documents}개에 변형 ${l.queries}개를 질의: 후보 비교 ${l.lsh_comparisons.toLocaleString()}회 / 전수 ${l.brute_force_comparisons.toLocaleString()}회 (${l.reduction_factor.toFixed(0)}배 감소), 유사도 0.6 이상 쌍의 재현율 ${pct(l.recall_at_0_6)}. 서명 계산 약 ${l.signature_ms_per_comment.toFixed(2)}ms/댓글.`, '',
    '실제 Jaccard 유사도 구간별 후보 포함 비율(문장 틀을 공유하지 않는 문서, 단어 삭제·치환·삽입 변형):', '', '| 유사도 구간 | 쌍 수 | 실측 | 이론 1-(1-s⁴)¹⁶ |', '|---|---|---|---|');
  for (const c of l.curve) L.push(`| ${c.range} | ${c.pairs} | ${c.measured_candidate_rate == null ? '-' : pct(c.measured_candidate_rate)} | ${c.theory_at_mean_similarity == null ? '-' : pct(c.theory_at_mean_similarity)} |`);
  L.push('', '## 4. Flajolet-Martin (30회 반복, 평균 절대 상대오차 / 편향)', '', '| n | PCSA m=16 | PCSA m=64 | PCSA m=256 | 기준선(최대 tail, 40해시) |', '|---|---|---|---|---|');
  for (const row of r.fm) {
    const f = k => row[k] ? `${pct(row[k].mean_abs_rel_error)} / ${row[k].bias >= 0 ? '+' : ''}${pct(row[k].bias)}` : '-';
    L.push(`| ${row.n.toLocaleString()} | ${f('pcsa_m16')} | ${f('pcsa_m64')} | ${f('pcsa_m256')} | ${f('baseline_max_tail_40hash')} |`);
  }
  L.push('', `이론상 PCSA의 표준오차는 약 0.78/√m (m=64면 약 ${pct(0.78 / 8)}). 원소가 적을 때는 빈 구간 수로 보정합니다.`, '', '## 5. DGIM (20,000개 스트림)', '', '| 창 | 1의 비율 | 최대 상대오차 | 평균 상대오차 | 최대 버킷 수 |', '|---|---|---|---|---|');
  for (const d of r.dgim) L.push(`| ${d.window} | ${d.p} | ${pct(d.max_rel_error)} | ${pct(d.mean_rel_error)} | ${d.max_buckets} |`);
  L.push('', '이론 한계는 50%입니다.', '');
  return L.join('\n');
}

(async () => {
  if (opt.csv) report.classification = await classification(opt.csv);
  report.bloom = bloomBench();
  report.lsh = lshBench(opt.seed);
  report.fm = fmBench();
  report.dgim = dgimBench(opt.seed);
  fs.mkdirSync(opt.out, { recursive: true });
  fs.writeFileSync(path.join(opt.out, 'evaluation.json'), JSON.stringify(report, null, 2));
  fs.writeFileSync(path.join(opt.out, 'evaluation.md'), markdown(report));
  console.log(markdown(report));
})();
