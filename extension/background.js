/* Network requests belong in the MV3 worker, not YouTube's content origin. */
const DEFAULTS = { enabled: true, autoHide: true, allowSolar: false, port: 8765, token: "" };

async function settings() {
  return { ...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS)) };
}

function allowedSender(sender) {
  if (sender.id !== chrome.runtime.id) return false;
  if (!sender.tab) return sender.url?.startsWith(chrome.runtime.getURL(""));
  try {
    const url = new URL(sender.url);
    return url.origin === "https://www.youtube.com" && url.pathname === "/watch";
  } catch { return false; }
}

async function backend(path, body) {
  const config = await settings();
  const port = Number(config.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("서버 포트를 확인해 주세요.");
  if (body && !config.enabled) throw new Error("필터가 일시 중지되었습니다.");
  if (body && !config.token) throw new Error("팝업 설정에 서버 연결 토큰을 입력해 주세요.");
  const response = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json", "X-YCSF-Token": config.token } : {},
    body: body ? JSON.stringify({ ...body, allow_solar: Boolean(config.allowSolar) }) : undefined,
    signal: AbortSignal.timeout(24000),
    cache: "no-store",
    credentials: "omit",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("연결 토큰이 맞지 않습니다. 서버 터미널의 토큰을 확인해 주세요.");
    throw new Error(`서버가 요청을 처리하지 못했습니다 (${response.status}).`);
  }
  return response.json();
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!allowedSender(sender)) return false;
  let task;
  if (message?.type === "HEALTH" && !sender.tab) task = backend("/api/health");
  if (message?.type === "ANALYZE" && sender.tab &&
      typeof message.session === "string" && Array.isArray(message.comments) &&
      message.comments.length > 0 && message.comments.length <= 5) {
    task = backend("/api/analyze", { session: message.session, comments: message.comments });
  }
  if (!task) return false;
  task.then(data => respond({ ok: true, data })).catch(error => {
    const message = error.name === "TimeoutError" ? "서버 응답이 늦어지고 있습니다. 다시 시도해 주세요." :
      error instanceof TypeError ? "로컬 서버에 연결할 수 없습니다. 서버 실행 상태와 포트를 확인해 주세요." : error.message;
    respond({ ok: false, error: message });
  });
  return true;
});
