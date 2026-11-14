/*
 * 실제 크롬(Chromium)에 확장 프로그램을 로드해 검증한다. 유튜브에 접속하지 않고,
 * https://www.youtube.com/watch 요청을 가로채 같은 구조의 가짜 댓글 DOM을 돌려준다.
 *   node tests/e2e.js          (Playwright와 Chromium이 필요. 화면 없는 환경은 xvfb-run node tests/e2e.js)
 */
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cs-e2e-'));
const ext = path.join(tmp, 'extension');
fs.cpSync(path.join(root, 'extension'), ext, { recursive: true });
// 테스트용 차단 목록(.test는 예약된 도메인)을 복사본에만 만든다.
fs.writeFileSync(path.join(tmp, 'blocklist.txt'), 'domain:phishing.test\nhttps://malware.test/download\n');
execFileSync('node', [path.join(root, 'tools', 'build_blocklist.js'), '--input', path.join(tmp, 'blocklist.txt'), '--out', path.join(ext, 'data')], { stdio: 'pipe' });

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
function commentHtml(i, text, links = []) {
  const body = esc(text) + links.map(l => ` <a href="${l.href}">${esc(l.label || l.href)}</a>`).join('');
  return `<ytd-comment-thread-renderer><ytd-comment-view-model id="c${i}"><div id="body">
    <a id="author-text" href="/@user${i}"><span>user${i}</span></a>
    <span id="published-time-text"><a href="/watch?v=VID1&lc=Ug${i}">1일 전</a></span>
    <ytd-expander><div id="content"><yt-formatted-string id="content-text">${body}</yt-formatted-string></div></ytd-expander>
  </div></ytd-comment-view-model></ytd-comment-thread-renderer>`;
}
const redirect = url => 'https://www.youtube.com/redirect?event=comments&redir_token=abc&q=' + encodeURIComponent(url);
const longHref = 'https://example.org/' + 'a'.repeat(3000);   // 구버전에서 대기열을 막던 종류의 입력
const comments = [
  [1, '노래 너무 좋아요 매일 듣고 있어요'],
  [2, '3:45 여기 참여 인원이 정말 많네요', [{ href: 'https://www.youtube.com/watch?v=VID1&t=225s', label: '3:45' }]],
  [3, '투자 수익인증 프로필 링크 텔레그램 문의하세요'],
  [4, '비밀번호 확인 필요 링크 클릭', [{ href: redirect('https://phishing.test/login') }]],
  [5, '자료는 프로필 링크에서 확인해 보세요'],
  [6, '정상적인 긴 링크 공유', [{ href: longHref, label: 'link' }]],
  [7, 'Please subscribe to my channel, check out my new song!'],
];
const page = n => `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>fake yt</title></head><body>
  <ytd-app><div id="comments"><ytd-comments id="cm">${comments.map(c => commentHtml(...c)).join('')}</ytd-comments></div></ytd-app></body></html>`;

(async () => {
  const context = await chromium.launchPersistentContext(path.join(tmp, 'profile'), {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`],
  });
  const errors = [];
  try {
    await context.route('https://www.youtube.com/**', route => route.fulfill({ contentType: 'text/html', body: page() }));
    const tab = await context.newPage();
    tab.on('pageerror', e => errors.push('pageerror: ' + e.message));
    tab.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
    await tab.goto('https://www.youtube.com/watch?v=VID1');

    // 모든 댓글이 처리됐는지 (스팸/검토 막대 수가 안정될 때까지)
    await tab.waitForFunction(() => document.querySelectorAll('.cg-bar').length >= 4, null, { timeout: 15000 });
    const verdicts = await tab.evaluate(() => Object.fromEntries([...document.querySelectorAll('ytd-comment-view-model')].map(n =>
      [n.id, n.querySelector('.cg-bar')?.dataset.verdict || 'safe'])));
    console.log('판정:', JSON.stringify(verdicts));
    assert.equal(verdicts.c1, 'safe');
    assert.equal(verdicts.c2, 'safe', '같은 영상 타임스탬프 링크는 정상');
    assert.equal(verdicts.c3, 'spam');
    assert.equal(verdicts.c4, 'spam', '차단 목록(Bloom→IndexedDB 정확 조회)으로 확정');
    assert.equal(verdicts.c5, 'review');
    assert.equal(verdicts.c6, 'review', '긴 URL 하나만 있는 댓글은 검토 필요이고, 다른 댓글 처리를 막지 않음');
    assert.equal(verdicts.c7, 'spam');

    // 접기: 스팸의 본문이 숨겨지고 '원문 보기'로 복원
    assert.equal(await tab.locator('#c3 #content-text').isVisible(), false);
    assert.equal(await tab.locator('#c5 #content-text').isVisible(), true, '검토 필요는 접지 않음');
    await tab.locator('#c3 .cg-bar button', { hasText: '원문 보기' }).click();
    assert.equal(await tab.locator('#c3 #content-text').isVisible(), true);

    // 차단 목록 근거 문구
    assert.match(await tab.locator('#c4 .cg-reason').innerText(), /악성 URL/);

    // 피드백: '정상입니다' → 막대 제거, 비슷한 새 댓글은 표시되지 않음
    await tab.locator('#c5 .cg-bar button', { hasText: '정상입니다' }).click();
    assert.equal(await tab.locator('#c5 .cg-bar').count(), 0);
    await tab.evaluate(html => document.getElementById('cm').insertAdjacentHTML('beforeend', html),
      commentHtml(8, '자료는 프로필 링크에서 확인해 보세요!'));
    await tab.waitForTimeout(1200);
    assert.equal(await tab.locator('#c8 .cg-bar').count(), 0, '정상 표시한 문장과 비슷하면 다시 표시하지 않음');

    // 동적 로드: 새 스팸 댓글이 추가되면 분석
    await tab.evaluate(html => document.getElementById('cm').insertAdjacentHTML('beforeend', html),
      commentHtml(9, '카지노 토토 바카라 먹튀 없는 사이트 https://bet.top/x'));
    await tab.waitForSelector('#c9 .cg-bar[data-verdict="spam"]', { timeout: 8000 });

    // 서비스 워커에서 팝업이 쓰는 상태 메시지를 호출
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ active: true }))[0].id);
    const status = await worker.evaluate(id => chrome.tabs.sendMessage(id, { type: 'STATUS' }), tabId);
    console.log('통계:', JSON.stringify({ total: status.stats.total, spam: status.stats.spam, review: status.stats.review,
      dgim: [status.stats.recentSpamEstimate, status.stats.recentSpamExact], urls: [status.stats.spamUrlsEstimate, status.stats.spamUrlsExact],
      blocklist: status.stats.blocklistSize, errors: status.stats.errors }));
    assert.equal(status.supported, true);
    assert.equal(status.stats.total, 9);
    assert.equal(status.stats.spam, 4);
    assert.equal(status.stats.blocklistSize, 2);
    assert.equal(status.stats.errors, 0);

    // 영상 이동 → 이전 영상 결과가 남지 않음
    await tab.evaluate(() => { history.pushState({}, '', '/watch?v=VID2'); document.querySelectorAll('.cg-bar').forEach(() => {}); document.dispatchEvent(new Event('yt-navigate-finish')); });
    await tab.waitForTimeout(1200);
    const after = await worker.evaluate(id => chrome.tabs.sendMessage(id, { type: 'STATUS' }), tabId);
    assert.ok(after.stats.total <= 9);

    // 팝업 화면이 오류 없이 열리는지 + 스크린샷
    const extensionId = new URL(worker.url()).host;
    const popup = await context.newPage();
    popup.on('pageerror', e => errors.push('popup pageerror: ' + e.message));
    await popup.setViewportSize({ width: 340, height: 560 });
    await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    await popup.waitForTimeout(600);
    assert.match(await popup.locator('#status').innerText(), /유튜브|확인/);
    await popup.screenshot({ path: path.join(root, 'reports', 'popup.png') });
    await tab.bringToFront();
    await tab.screenshot({ path: path.join(root, 'reports', 'page.png') });

    assert.deepEqual(errors, [], '오류 로그: ' + errors.join('\n'));
    console.log('E2E 통과');
  } finally {
    await context.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
})().catch(error => { console.error('E2E 실패:', error.message); process.exit(1); });
