const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function worker(overrides = {}, response = { ok: true }) {
  let listener;
  const requests = [];
  const config = { enabled: true, token: 'test-pairing-token-1234567890', port: 8765, allowOpenCode: false, ...overrides };
  const chrome = {
    storage: { local: { get: async () => config } },
    runtime: { id: 'a'.repeat(32), getURL: x => `chrome-extension://${'a'.repeat(32)}/${x}`,
      onMessage: { addListener: fn => { listener = fn; } } }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../extension/background.js'), 'utf8'), {
    chrome, URL, AbortSignal, fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => response };
    }
  });
  return { requests, invoke(message, sender) {
    return new Promise(resolve => { if (!listener(message, sender, resolve)) resolve(undefined); });
  } };
}
const contentSender = { id: 'a'.repeat(32), tab: { id: 1 }, url: 'https://www.youtube.com/watch?v=test' };
const popupSender = { id: 'a'.repeat(32), url: `chrome-extension://${'a'.repeat(32)}/popup.html` };
const message = { type: 'ANALYZE', session: 'test', comments: [{ id: '1', text: 'hello' }], url: 'https://evil.test' };

test('worker uses loopback only and overrides untrusted OpenCode flags', async () => {
  const instance = worker();
  const result = await instance.invoke({ ...message, allow_opencode: true }, contentSender);
  assert.equal(result.ok, true);
  assert.equal(instance.requests[0].url, 'http://127.0.0.1:8765/api/analyze');
  const body = JSON.parse(instance.requests[0].options.body);
  assert.equal(body.allow_opencode, false);
  assert.equal(body.url, undefined);
  assert.equal(instance.requests[0].options.headers['X-YCSF-Token'], 'test-pairing-token-1234567890');
});
test('OpenCode opt-in is sent only when the new local preference is enabled', async () => {
  const instance = worker({ allowOpenCode: true });
  await instance.invoke(message, contentSender);
  assert.equal(JSON.parse(instance.requests[0].options.body).allow_opencode, true);
});
test('previous provider preference does not enable OpenCode consent', async () => {
  const instance = worker({ allowGLM: true, allowSolar: true });
  await instance.invoke(message, contentSender);
  assert.equal(JSON.parse(instance.requests[0].options.body).allow_opencode, false);
});
test('worker refuses other sites, non-watch pages and other extensions', async () => {
  const instance = worker();
  for (const sender of [{ ...contentSender, url: 'https://evil.test' },
    { ...contentSender, url: 'https://www.youtube.com/shorts/test' }, { ...contentSender, id: 'b'.repeat(32) }]) {
    assert.equal(await instance.invoke(message, sender), undefined);
  }
  assert.equal(instance.requests.length, 0);
});
test('worker pauses and refuses missing token before network access', async () => {
  for (const config of [{ enabled: false }, { token: '' }, { port: 'bad' }]) {
    const instance = worker(config);
    const result = await instance.invoke(message, contentSender);
    assert.equal(result.ok, false);
    assert.equal(instance.requests.length, 0);
  }
});
test('only extension popup can request server health', async () => {
  const instance = worker();
  assert.equal(await instance.invoke({ type: 'HEALTH' }, contentSender), undefined);
  assert.equal((await instance.invoke({ type: 'HEALTH' }, popupSender)).ok, true);
  assert.equal(instance.requests.length, 1);
  assert.equal(instance.requests[0].options.method, 'GET');
});
test('worker rejects excessive batches and unknown messages', async () => {
  const instance = worker();
  assert.equal(await instance.invoke({ ...message, comments: Array(6).fill(message.comments[0]) }, contentSender), undefined);
  assert.equal(await instance.invoke({ type: 'FETCH_URL', url: 'https://evil.test' }, contentSender), undefined);
  assert.equal(instance.requests.length, 0);
});


test('profile images require separate consent and are stripped when either switch is off', async () => {
  for (const config of [{ allowOpenCode: true }, { allowProfileImages: true }, {}]) {
    const instance = worker(config);
    await instance.invoke({ ...message, allow_profile_images: true, comments: [
      { ...message.comments[0], author_avatar: 'https://yt3.ggpht.com/profile-test' }] }, contentSender);
    const body = JSON.parse(instance.requests[0].options.body);
    assert.equal(body.allow_profile_images, false);
    assert.equal(body.comments[0].author_avatar, undefined);
  }
});

test('profile image consent is sent only when both local switches are on', async () => {
  const instance = worker({ allowOpenCode: true, allowProfileImages: true });
  await instance.invoke({ ...message, comments: [{ ...message.comments[0], author_avatar: 'https://yt3.ggpht.com/profile-test' }] }, contentSender);
  const body = JSON.parse(instance.requests[0].options.body);
  assert.equal(body.allow_profile_images, true);
  assert.equal(body.comments[0].author_avatar, 'https://yt3.ggpht.com/profile-test');
});
