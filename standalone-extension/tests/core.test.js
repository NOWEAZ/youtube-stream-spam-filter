const test = require('node:test');
const assert = require('node:assert/strict');
const CG = require('../extension/core.js');

const rnd = CG.mulberry32(7);

test('Bloom: 회원 미탐 없음, 오탐률이 이론값과 비슷', () => {
  const n = 5000, bloom = new CG.BloomFilter(n, 0.01);
  for (let i = 0; i < n; i++) bloom.add('https://m' + i + '.test/a');
  for (let i = 0; i < n; i++) assert.ok(bloom.has('https://m' + i + '.test/a'));
  let fp = 0; const q = 20000;
  for (let i = 0; i < q; i++) if (bloom.has('https://x' + i + '.test/b')) fp++;
  const measured = fp / q, expected = bloom.expectedFalsePositiveRate();
  assert.ok(measured < 0.02, 'measured ' + measured);
  assert.ok(Math.abs(measured - expected) < 0.006, `measured ${measured} expected ${expected}`);
});

test('Bloom: JSON 직렬화 후에도 같은 결과', () => {
  const b = new CG.BloomFilter(100); for (let i = 0; i < 100; i++) b.add('k' + i);
  const c = CG.BloomFilter.fromJSON(JSON.parse(JSON.stringify(b.toJSON())));
  for (let i = 0; i < 100; i++) assert.ok(c.has('k' + i));
  assert.equal(c.m, b.m); assert.equal(c.k, b.k);
});

test('DGIM: 여러 확률·창 크기에서 오차 50% 이내, 버킷 크기별 2개 이하', () => {
  for (const window of [32, 100, 500]) for (const p of [0.02, 0.2, 0.5, 0.95]) {
    const dgim = new CG.DGIM(window), bits = [], r = CG.mulberry32(window * 31 + Math.floor(p * 100));
    for (let i = 0; i < 4000; i++) {
      const bit = r() < p ? 1 : 0; bits.push(bit); dgim.add(bit);
      const actual = bits.slice(-window).reduce((a, b) => a + b, 0);
      assert.ok(Math.abs(dgim.estimate() - actual) <= Math.max(0.5 * actual, 1), `w=${window} p=${p} i=${i} est=${dgim.estimate()} act=${actual}`);
      const sizes = {}; for (const b of dgim.buckets) sizes[b.size] = (sizes[b.size] || 0) + 1;
      assert.ok(Object.values(sizes).every(c => c <= 2));
    }
  }
});

test('FM: 중복에 불변이고, 여러 크기에서 상대 오차 평균이 작음', () => {
  const fm = new CG.FlajoletMartin(); assert.equal(fm.estimate(), 0);
  for (let i = 0; i < 100; i++) fm.add('a' + i);
  const first = fm.estimate();
  for (let rep = 0; rep < 3; rep++) for (let i = 0; i < 100; i++) fm.add('a' + i);
  assert.equal(fm.estimate(), first);
  for (const n of [10, 100, 1000, 10000]) {
    let err = 0; const runs = 15;
    for (let s = 0; s < runs; s++) {
      const f = new CG.FlajoletMartin();
      for (let i = 0; i < n; i++) f.add(`s${s}-${i}`);
      err += Math.abs(f.estimate() - n) / n;
    }
    assert.ok(err / runs < 0.3, `n=${n} mean relative error ${err / runs}`);
  }
});

test('LSH: 근접 중복을 찾고, 서로 다른 문서는 후보로 거의 올리지 않으며, 오래된 항목을 제거', () => {
  const lsh = new CG.MinHashLSH({ limit: 3 });
  const text = '비트코인 수익 인증하고 텔레그램으로 오세요 지금 바로 참여하세요 오늘만';
  const set = CG.shingles(text), sig = lsh.signature(set);
  lsh.add('a', set, sig, text);
  const near = CG.shingles(text + '!!');
  const found = lsh.query(near, lsh.signature(near), 0.6);
  assert.equal(found[0].id, 'a'); assert.ok(found[0].similarity > 0.85);
  const other = CG.shingles('오늘 점심 뭐 먹지 고민되네요 다들 맛집 추천 부탁드려요');
  assert.deepEqual(lsh.query(other, lsh.signature(other), 0.6), []);
  for (let i = 0; i < 4; i++) { const s = CG.shingles('서로 다른 문장 번호 ' + i + ' ' + 'x'.repeat(i)); lsh.add('z' + i, s, lsh.signature(s), 'z'); }
  assert.equal(lsh.size, 3);
  assert.ok(!lsh.records.has('a'));
  for (const bucket of lsh.buckets.values()) assert.ok(!bucket.has('a'));
});

test('LSH: 후보 확률 곡선(이론)', () => {
  const p = s => CG.MinHashLSH.candidateProbability(s);
  assert.ok(p(0.8) > 0.999); assert.ok(p(0.2) < 0.03); assert.ok(p(0.5) > 0.5 && p(0.5) < 0.7);
});

test('URL: 정규화·유튜브 redirect 해제·내부 링크 분류', () => {
  assert.equal(CG.normalizeUrl('HTTPS://Example.COM:443/Pay?Code=A#x'), 'https://example.com/Pay?Code=A');
  assert.equal(CG.normalizeUrl('https://www.youtube.com/redirect?q=https%3A%2F%2Fevil.test%2Fa&event=x'), 'https://evil.test/a');
  assert.equal(CG.normalizeUrl('javascript:alert(1)'), null);
  assert.equal(CG.normalizeUrl('https://exa mple.com'), null);
  const urls = CG.extractUrls('봐요 https://www.youtube.com/watch?v=VID&t=12s 그리고 t.me/abc 와 https://a.test/x).');
  const info = CG.classifyUrls(urls, 'VID');
  assert.equal(info.youtube.length, 0, '같은 영상 타임스탬프는 무시');
  assert.deepEqual(info.messengers.map(u => new URL(u).hostname), ['t.me']);
  assert.ok(info.external.includes('https://a.test/x'));
});

test('규칙: 타임스탬프 링크+일반 한국어는 정상, 스팸 조합은 스팸', async () => {
  const e = new CG.Engine();
  const r1 = await e.analyze({ id: '1', text: '3:45 여기 참여 인원이 진짜 많네요 https://www.youtube.com/watch?v=V&t=225s', video: 'V' });
  assert.equal(r1.verdict, 'safe');
  const r2 = await e.analyze({ id: '2', text: '투자 수익인증 프로필 링크 텔레그램 문의하세요', video: 'V' });
  assert.equal(r2.verdict, 'spam');
  const r3 = await e.analyze({ id: '3', text: '텔 레 그 램 으로 문의 주세요 고수익 부업', video: 'V' });
  assert.equal(r3.verdict, 'spam', '글자 사이를 띄운 우회도 잡는다');
  const r4 = await e.analyze({ id: '4', text: 'Please subscribe to my channel, check out my new song!', video: 'V' });
  assert.equal(r4.verdict, 'spam');
  const r5 = await e.analyze({ id: '5', text: 'I joined the team last year and enjoyed it', video: 'V' });
  assert.equal(r5.verdict, 'safe', 'join이 joined/enjoyed에 걸리지 않음');
  const r6 = await e.analyze({ id: '6', text: '이 영상 덕분에 코인 공부 시작했어요 감사합니다', video: 'V' });
  assert.equal(r6.verdict, 'safe', '주제 언급만으로는 스팸이 아님');
});

test('악성 URL: Bloom 양성 → 정확 조회로 확인, 오탐은 걸러짐', async () => {
  const bloom = new CG.BloomFilter(10);
  bloom.add('domain:phishing.test'); bloom.add('https://bad.test/Pay?Code=A');
  const exact = new Set(['domain:phishing.test', 'https://bad.test/Pay?Code=A']);
  let lookups = 0;
  const e = new CG.Engine({ bloom, confirm: async keys => { lookups++; return keys.find(k => exact.has(k)) || null; } });
  const hit = await e.analyze({ id: '1', text: '여기 보세요 https://phishing.test/login' });
  assert.equal(hit.verdict, 'spam'); assert.equal(hit.source, 'blocklist');
  const clean = await e.analyze({ id: '2', text: '이 글에서 설명한 내용은 이 자료에 더 자세히 있어요 https://clean.example.org/a' });
  assert.equal(clean.verdict, 'safe');
  assert.equal(lookups, 1, '깨끗한 URL은 정확 조회를 하지 않는다');
  // 모든 비트가 1이어도(최악의 오탐) 정확 조회가 최종 결정
  bloom.bits.fill(0xff);
  const fp = await e.analyze({ id: '3', text: '이 글에서 설명한 내용은 이 자료에 더 자세히 있어요 https://clean.example.org/b' });
  assert.equal(fp.verdict, 'safe');
  assert.equal(e.stats().exactHits, 1);
});

test('유사 댓글: 오판 전파 방지, 복붙 반복 탐지, 정상 반복 유지', async () => {
  const e = new CG.Engine();
  // 규칙으로 스팸이 된 댓글과 거의 같은 변형은 유사도로 스팸
  await e.analyze({ id: 's1', text: '수익인증 투자 텔레그램 문의하세요 고수익 보장 지금 바로 참여', account: 'a1' });
  const variant = await e.analyze({ id: 's2', text: '수익인증 투자 텔레그램 문의하세요 고수익 보장 지금 바로 참여!', account: 'a2' });
  assert.equal(variant.verdict, 'spam');
  // 유사도만으로 스팸이 된 댓글은 참조에 들어가지 않는다 (예측이 예측을 키우지 않음)
  const refsBefore = e.spamRefs.size;
  const another = await e.analyze({ id: 's3', text: '수익인증 투자 텔레그램 문의하세요 고수익 보장 지금 바로 참여!!', account: 'a3' });
  assert.equal(another.verdict, 'spam');
  assert.equal(e.spamRefs.size, refsBefore + (another.signals.includes('similarSpam') && another.score - 3 >= 3 ? 1 : 0));
  // 서로 다른 계정이 같은 긴 문장을 반복 → 검토 이상
  const f = new CG.Engine();
  const flood = '이 영상을 보신 분들은 꼭 이 링크의 내용을 확인해 보시기 바랍니다 정말 중요해요';
  let last;
  for (let i = 0; i < 4; i++) last = await f.analyze({ id: 'f' + i, text: flood, account: 'acc' + i });
  assert.ok(last.signals.includes('dupFlood') && last.verdict !== 'safe');
  // 짧은 정상 반복("1등")은 영향 없음
  let short; for (let i = 0; i < 5; i++) short = await f.analyze({ id: 'g' + i, text: '1등', account: 'u' + i });
  assert.equal(short.verdict, 'safe');
});

test('사용자 피드백: 정상 표시한 댓글과 비슷한 댓글은 다시 표시하지 않음', async () => {
  const e = new CG.Engine();
  const text = '코인 투자 관련 정보는 프로필 링크에서 확인하세요 텔레그램 문의';
  const first = await e.analyze({ id: 'x1', text });
  assert.notEqual(first.verdict, 'safe');
  e.markHam('x1', text);
  const again = await e.analyze({ id: 'x2', text: text + '.' });
  assert.equal(again.verdict, 'safe'); assert.equal(again.source, 'user-ham');
  assert.deepEqual(e.exportLearned().ham.length, 1);
});

test('통계: DGIM·FM 추정치가 정확 계산과 같이 보고됨', async () => {
  const e = new CG.Engine({ window: 20 });
  for (let i = 0; i < 60; i++) {
    const spam = i % 3 === 0;
    await e.analyze({ id: 'c' + i, account: 'acc' + (i % 7), links: spam ? ['https://s' + (i % 5) + '.xyz/p'] : [],
      text: spam ? `카지노 토토 가입 ${i}` : `정상 댓글 ${i} 번째 감상입니다 ${'가나다라마바사'.slice(0, i % 7)}` });
  }
  const s = e.stats();
  assert.equal(s.total, 60); assert.equal(s.spam, 20);
  assert.ok(Math.abs(s.recentSpamEstimate - s.recentSpamExact) <= Math.max(1, 0.5 * s.recentSpamExact));
  assert.equal(s.spamUrlsExact, 5); assert.equal(s.spamAccountsExact, 7);
});

test('링크: 링크만 있는 댓글·위험 주소·단축 URL을 잡고, 설명이 있는 일반 링크·신뢰 사이트는 통과', async () => {
  const run = async (text, links) => (await new CG.Engine().analyze({ id: 'x', text: text + ' ' + links.join(' '), links })).verdict;
  assert.equal(await run('', ['https://some-site.com/page']), 'review', '링크만 있는 댓글');
  assert.equal(await run('', ['https://bit.ly/3abc']), 'spam', '단축 URL만 있는 댓글');
  assert.equal(await run('여기 가보세요', ['https://free-gift.xyz/a']), 'spam', '비정상 TLD');
  assert.equal(await run('확인', ['http://192.168.1.5/login']), 'spam', 'IP 주소 호스트');
  assert.equal(await run('', ['https://en.wikipedia.org/wiki/Bloom_filter']), 'safe', '신뢰 사이트');
  assert.equal(await run('이 영상에서 설명한 블룸 필터는 이 블로그에 더 자세히 정리되어 있어요 저도 이걸로 공부했습니다', ['https://blog.example.com/post/1']), 'safe');
  // 유튜브 링크 글자(앵커 텍스트)도 "링크만" 판단에서 제외
  const e = new CG.Engine();
  const r = await e.analyze({ id: 'y', text: 'example-site.com/abc…', links: ['https://example-site.com/abc'], linkTexts: ['example-site.com/abc…'] });
  assert.ok(r.signals.includes('linkOnly'));
});
