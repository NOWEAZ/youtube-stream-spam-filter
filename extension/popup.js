const $ = id => document.getElementById(id);
let activeTab;
const DEFAULTS = { enabled: true, autoHide: true, allowOpenCode: false, allowProfileImages: false, port: 8765, token: "" };

async function health() {
  const response = await chrome.runtime.sendMessage({ type: "HEALTH" });
  $("connection").textContent = response?.ok ? "서버 연결됨" : "연결 필요";
  $("connection").className = `status-pill ${response?.ok ? "online" : "offline"}`;
  if (response?.ok) {
    const data = response.data;
    $("coverage").textContent = `${data.provider || "zen"} / ${data.model || ""} · ${data.demo_data ? "시연 목록 · " : ""}악성 URL 목록 ${data.blocklist_count.toLocaleString()}건 · OpenCode ${data.opencode_available ? "사용 가능" : "미설정"}` +
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
    for (const key of ["ko", "en", "mixed", "other"]) $(`lang-${key}`).textContent = stats.languages?.[key] || 0;
    for (const key of ["p50", "p95"]) $(key).textContent = info.latency?.[key] == null ? "—" : `${info.latency[key].toFixed(1)} ms`;
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

let personalData = [];
function renderPersonal() {
  const query = $("labelSearch").value.trim().toLowerCase();
  $("personalList").replaceChildren();
  for (const row of [...personalData].reverse().filter(row => !query || row.text.toLowerCase().includes(query)).slice(0, 20)) {
    const item = document.createElement("div"); item.className = "personal-item";
    const text = document.createElement("p"); text.textContent = `${row.label === "spam" ? "스팸" : "정상"} · ${row.text.slice(0, 100)}`;
    const remove = document.createElement("button"); remove.textContent = "등록 취소"; remove.className = "btn";
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      const response = await chrome.runtime.sendMessage({ type: "DELETE_PERSONAL_LABEL", key: row.key });
      if (response?.ok) await refreshPersonal();
      else { $("personalMessage").textContent = response?.error || "취소하지 못했습니다."; remove.disabled = false; }
    });
    item.append(text, remove); $("personalList").append(item);
  }
}
async function refreshPersonal() {
  const response = await chrome.runtime.sendMessage({ type: "GET_PERSONAL_LABELS" });
  if (!response?.ok) { $("personalMessage").textContent = response?.error || "개인 라벨을 읽지 못했습니다."; return; }
  personalData = response.data.labels;
  const { total, spam, normal, limit } = response.data.stats;
  $("personalCount").textContent = total;
  $("personalStats").textContent = `내 스팸 ${spam}개 · 내 정상 ${normal}개 / 최대 ${limit}개. 최근/검색 결과 20개 표시.`;
  renderPersonal();
}

async function init() {
  const config = { ...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS)) };
  for (const key of ["enabled", "autoHide", "allowOpenCode"]) {
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
  $("labelSearch").addEventListener("input", renderPersonal);
  $("exportPersonal").addEventListener("click", async () => {
    await refreshPersonal();
    const content = personalData.map(row => JSON.stringify(row)).join("\n") + (personalData.length ? "\n" : "");
    const url = URL.createObjectURL(new Blob([content], { type: "application/x-ndjson;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = "my-youtube-labels.jsonl"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("personalMessage").textContent = `${personalData.length}개 개인 라벨을 내보냈습니다. 파일에는 등록한 댓글 본문이 포함됩니다.`;
  });
  chrome.storage.onChanged.addListener(changes => { if (changes.personalLabels) refreshPersonal().catch(() => {}); });
  refreshPersonal().catch(() => { $("personalMessage").textContent = "확장 프로그램을 다시 로드해 주세요."; });
  health().catch(() => { $("connection").textContent = "연결 필요"; });
  refresh(); setInterval(refresh, 1500);
}
init();
