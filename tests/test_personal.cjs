const test = require('node:test');
const assert = require('node:assert/strict');
const personal = require('../extension/personal.js');

test('exact user labels override synthetic starter examples and remain separate from gold', async () => {
  const seed = personal.SEEDS[0];
  const label = await personal.record(seed.text, [], 'normal');
  const sample = await personal.prepare(seed.text);
  assert.equal(personal.match(sample, await personal.buildIndex([])).kind, 'seed');
  const result = personal.match(sample, await personal.buildIndex([label]));
  assert.equal(result.kind, 'exact');
  assert.equal(result.label, 'normal');
  assert.equal(label.label_source, 'user');
  assert.equal(label.user_scope, 'chrome_profile_local');
  assert.equal(label.eligible_for_gold_evaluation, false);
});

test('normal exact labels are bound to the actual DOM link destinations', async () => {
  const text = 'Useful instructions are available through the link here.';
  const row = await personal.record(text, ['https://safe.test/info'], 'normal');
  const index = await personal.buildIndex([row]);
  assert.equal(personal.match(await personal.prepare(text, ['https://safe.test/info']), index).label, 'normal');
  assert.equal(personal.match(await personal.prepare(text, ['https://other.test/trap']), index), null);
  assert.equal(JSON.stringify(row).includes('safe.test'), false);
});

test('near examples are review hints rather than automatic spam or normal', async () => {
  const text = '매일 원금 보장으로 고수익을 얻을 수 있어요. 텔레그램으로 문의해 주세요';
  const row = await personal.record(text, [], 'spam');
  const result = personal.match(await personal.prepare(text + '!!'), await personal.buildIndex([row]));
  assert.equal(result.kind, 'similar');
  assert.equal(result.label, 'review');
  assert.equal(result.reference_label, 'spam');
});

test('short reactions do not become broad near-match rules', async () => {
  const row = await personal.record('Good', [], 'spam');
  const index = await personal.buildIndex([row]);
  assert.equal(personal.match(await personal.prepare('Good'), index).label, 'spam');
  assert.equal(personal.match(await personal.prepare('Good!'), index), null);
});

test('URL normalisation and Unicode normalisation are stable across restarts', async () => {
  const first = await personal.prepare('Ｔｈａｎｋｓ   for this!', ['HTTPS://Example.COM:443/Info#one']);
  const second = await personal.prepare('Thanks for this!', ['https://example.com/Info#two']);
  assert.equal(first.key, second.key);
  const row = await personal.record(first.text, ['https://example.com/Info'], 'normal');
  const restored = JSON.parse(JSON.stringify(row));
  assert.equal(personal.match(second, await personal.buildIndex([restored])).label, 'normal');
});

test('oversized or malformed feedback is rejected', async () => {
  await assert.rejects(() => personal.record('x'.repeat(2001), [], 'spam'));
  await assert.rejects(() => personal.record('comment', [], 'unknown'));
  await assert.rejects(() => personal.prepare('comment', [7]));
});
