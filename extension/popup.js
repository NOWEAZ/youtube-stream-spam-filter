const $ = id => document.getElementById(id);
let activeTab;
const DEFAULTS = { enabled: true, autoHide: true, allowSolar: false, port: 8765, token: "" };

async function health() {
  const response = await chrome.runtime.sendMessage({ type: "HEALTH" });
  $("connection").textContent = response?.ok ? "서버 연결됨" : "연결 필요";
  $("connection").className = `pill ${response?.ok ? "online" : "offline"}`;
  if (response?.ok) {
    const data = response.data;
    $("coverage").textContent = `${data.demo_data ? "시연 목록 · " : ""}악성 URL 목록 ${data.blocklist_count.toLocaleString()}건 · Solar ${data.solar_available ? "사용 가능" : "미설정"}` +
      (data.demo_data ? ". 시연 목록은 실제 위협을 탐지하지 않습니다." : !data.blocklist_count ? ". 악성 URL 목록을 가져와 주세요." : "");
  } else $("coverage").textContent = response?.error || "서버 실행 상태를 확인해 주세요.";
  return response;
}

async function refresh() {
  if (!activeTab?.id) return;
  try {
    const info = await chrome.tabs.sendMessage(activeTab.id, { type: "GET_STATUS" });
    if (!info?.supported) { $("status").textContent = "PC 유튜브의 일반 동영상에서 사용할 수 있습니다."; return; }
    const stats = info.stats || {};
    for (const key of ["total", "spam", "review", "safe", "window"]) $(key).textContent = (stats[key] || 0).toLocaleString();
    $("estimate").textContent = `${stats.recent_spam_estimate || 0}개`;
    $("urls").textContent = `${stats.unique_spam_urls_estimate || 0}개`;
    $("accounts").textContent = stats.unique_spam_accounts_estimate == null ? "관측 없음" : `${stats.unique_spam_accounts_estimate}개`;
    $("calls").textContent = `${stats.agent_calls || 0}회`;
    $("status").textContent = !info.enabled ? "필터를 일시 중지했습니다." : info.lastError ||
      (info.queued ? `${info.queued}개 댓글을 분석하고 있습니다.` : stats.total ? "새로 불러오는 댓글을 확인하고 있습니다." : "댓글 영역을 펼치거나 아래로 스크롤해 주세요.");
    $("history").replaceChildren();
    for (const bit of stats.history || Array(30).fill(0)) {
      const bar = document.createElement("b"); bar.className = bit ? "spam" : ""; $("history").append(bar);
    }
  } catch { $("status").textContent = "유튜브 동영상을 열거나 확장 프로그램 설치 후 페이지를 새로고침해 주세요."; }
}

async function init() {
  const config = { ...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS)) };
  for (const key of ["enabled", "autoHide", "allowSolar"]) {
    $(key).checked = config[key];
    $(key).addEventListener("change", async () => { await chrome.storage.local.set({ [key]: $(key).checked }); setTimeout(refresh, 500); });
  }
  $("port").value = config.port; $("token").value = config.token;
  if (!config.token) $("settings").open = true;
  [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("save").addEventListener("click", async () => {
    const port = Number($("port").value), token = $("token").value.trim();
    if (!Number.isInteger(port) || port < 1024 || port > 65535 || token.length < 24 || token.length > 200 || !/^[\x20-\x7e]+$/.test(token)) {
      $("settingsMessage").textContent = "포트와 24~200자의 연결 토큰을 확인해 주세요."; return;
    }
    await chrome.storage.local.set({ port, token });
    const response = await health();
    $("settingsMessage").textContent = response?.ok ? "설정을 저장했습니다. 댓글 분석에서 토큰을 확인합니다." : "저장했지만 서버에 연결하지 못했습니다.";
    refresh();
  });
  $("rescan").addEventListener("click", async () => {
    try { await chrome.tabs.sendMessage(activeTab.id, { type: "RESCAN" }); setTimeout(refresh, 500); }
    catch { $("status").textContent = "유튜브 일반 동영상을 열고 새로고침해 주세요."; }
  });
  $("demo").addEventListener("click", async () => {
    const { port = 8765 } = await chrome.storage.local.get("port");
    chrome.tabs.create({ url: `http://127.0.0.1:${Number(port)}/demo` });
  });
  health().catch(() => { $("connection").textContent = "연결 필요"; });
  refresh(); setInterval(refresh, 1500);
}
init();
