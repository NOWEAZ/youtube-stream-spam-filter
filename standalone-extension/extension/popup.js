const $ = id => document.getElementById(id);
const DEFAULTS = { enabled: true, autoHide: true, sensitivity: 'normal', ai: false, aiKey: '' };
const UPSTAGE = ['https://api.upstage.ai/*'];
let tabId = null;

const fmt = n => Number(n || 0).toLocaleString('ko-KR');
const withExact = (estimate, exact) => `${fmt(estimate)} (${fmt(exact)})`;

async function refresh() {
  if (tabId == null) { $('status').textContent = '현재 탭을 확인하지 못했습니다.'; return; }
  let info = null;
  try { info = await chrome.tabs.sendMessage(tabId, { type: 'STATUS' }); } catch { /* 콘텐츠 스크립트 없음 */ }
  if (!info) { $('status').textContent = '유튜브 영상 페이지를 열고 새로고침해 주세요.'; return; }
  if (!info.supported) { $('status').textContent = '유튜브 일반 동영상 페이지에서 동작합니다.'; return; }
  const s = info.stats || {};
  for (const key of ['total', 'spam', 'review', 'safe']) $(key).textContent = fmt(s[key]);
  $('windowSize').textContent = fmt(s.window);
  $('dgim').textContent = withExact(s.recentSpamEstimate, s.recentSpamExact);
  $('fmUrls').textContent = withExact(s.spamUrlsEstimate, s.spamUrlsExact);
  $('fmAccounts').textContent = withExact(s.spamAccountsEstimate, s.spamAccountsExact);
  $('lsh').textContent = `${fmt(s.lshComparisons)}회 · 기록 ${fmt(s.lshRecords)}개`;
  $('blocklist').textContent = s.blocklistSize ? `${fmt(s.blocklistSize)}건` : '비어 있음';
  $('learned').textContent = info.learned ? info.learned.spam + info.learned.ham : 0;
  $('status').textContent = !info.enabled ? '필터가 꺼져 있습니다.' : info.lastError ||
    (info.queued ? `${info.queued}개 댓글을 분석하는 중…` : s.total ? '새로 불러오는 댓글을 계속 확인합니다.' : '댓글 영역까지 스크롤하면 분석을 시작합니다.');
  const bars = $('history'); bars.replaceChildren();
  for (const v of s.history || []) { const bar = document.createElement('i'); if (v === 2) bar.className = 's'; else if (v === 1) bar.className = 'r'; bars.append(bar); }
}

async function init() {
  const config = { ...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS)) };
  for (const key of ['enabled', 'autoHide', 'ai']) $(key).checked = Boolean(config[key]);
  $('sensitivity').value = config.sensitivity; $('aiKey').value = config.aiKey;
  if (config.ai) $('aiBox').open = true;
  [{ id: tabId }] = await chrome.tabs.query({ active: true, currentWindow: true }).then(t => t.length ? t : [{}]);

  for (const key of ['enabled', 'autoHide']) $(key).addEventListener('change', () => chrome.storage.local.set({ [key]: $(key).checked }));
  $('sensitivity').addEventListener('change', () => chrome.storage.local.set({ sensitivity: $('sensitivity').value }));
  $('aiKey').addEventListener('change', () => chrome.storage.local.set({ aiKey: $('aiKey').value.trim() }));
  $('ai').addEventListener('change', async () => {
    if (!$('ai').checked) { await chrome.storage.local.set({ ai: false }); $('aiMessage').textContent = ''; return; }
    // 사용자가 직접 켠 순간에만 Upstage 접속 권한을 요청한다.
    const key = $('aiKey').value.trim();
    if (!key) { $('ai').checked = false; $('aiMessage').textContent = '먼저 API 키를 입력해 주세요.'; return; }
    const granted = await chrome.permissions.request({ origins: UPSTAGE });
    if (!granted) { $('ai').checked = false; $('aiMessage').textContent = '접속 권한이 허용되지 않아 켜지 않았습니다.'; return; }
    await chrome.storage.local.set({ ai: true, aiKey: key });
    $('aiMessage').textContent = 'AI 추가 검토를 켰습니다. ‘검토 필요’ 댓글만 보냅니다.';
  });
  $('rescan').addEventListener('click', async () => { try { await chrome.tabs.sendMessage(tabId, { type: 'RESCAN' }); } catch {} setTimeout(refresh, 400); });
  $('clearLearned').addEventListener('click', async () => { try { await chrome.tabs.sendMessage(tabId, { type: 'CLEAR_LEARNED' }); } catch {} setTimeout(refresh, 400); });
  refresh(); setInterval(refresh, 1500);
}
init();
