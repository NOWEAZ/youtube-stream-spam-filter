/* Network requests belong in the MV3 worker, not YouTube's content origin. */
importScripts('personal.js');
const DEFAULTS = { enabled: true, autoHide: true, allowOpenCode: false, allowProfileImages: false, port: 8765, token: "" };

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

let labelMutation = Promise.resolve();
async function personalRows() {
  const saved = await chrome.storage.local.get('personalLabels');
  return (Array.isArray(saved.personalLabels) ? saved.personalLabels : []).filter(PersonalLabels.validRecord).slice(0, PersonalLabels.MAX_LABELS);
}
function mutateLabels(action) {
  labelMutation = labelMutation.catch(() => {}).then(async () => {
    const rows = await personalRows();
    const updated = await action(rows);
    await chrome.storage.local.set({ personalLabels: updated, personalLabelsRevision: Date.now() });
    return { stats: PersonalLabels.stats(updated) };
  });
  return labelMutation;
}
async function setLabel(message) {
  const sample = await PersonalLabels.prepare(message.text, message.urls || []);
  return mutateLabels(async rows => {
    const old = rows.find(row => row.key === sample.key);
    if (!old && rows.length >= PersonalLabels.MAX_LABELS) throw new Error('개인 라벨 500개 한도입니다. 팝업에서 기존 라벨을 내보내거나 정리해 주세요.');
    const item = await PersonalLabels.record(message.text, message.urls || [], message.label, old);
    return [...rows.filter(row => row.key !== item.key), item];
  });
}

async function backend(path, body) {
  const config = await settings();
  const port = Number(config.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("서버 포트를 확인해 주세요.");
  if (body && !config.enabled) throw new Error("필터가 일시 중지되었습니다.");
  if (body && !config.token) throw new Error("팝업 설정에 서버 연결 토큰을 입력해 주세요.");
  let comments;
  if (body) {
    const index = await PersonalLabels.buildIndex(await personalRows());
    comments = await Promise.all(body.comments.map(async ({ author_avatar, personal_match, ...comment }) => {
      const sample = await PersonalLabels.prepare(comment.text, comment.urls || []);
      const match = PersonalLabels.match(sample, index);
      return { ...comment, ...(match ? { personal_match: match } : {}) };
    }));
  }
  const response = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json", "X-YCSF-Token": config.token } : {},
    body: body ? JSON.stringify({ ...body, allow_opencode: Boolean(config.allowOpenCode),
      allow_profile_images: false, comments }) : undefined,
    signal: AbortSignal.timeout(45000),
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
  if (message?.type === 'SET_PERSONAL_LABEL' && sender.tab) task = setLabel(message);
  if (message?.type === 'GET_PERSONAL_LABELS' && !sender.tab) task = personalRows().then(labels => ({ labels, stats: PersonalLabels.stats(labels) }));
  if (message?.type === 'DELETE_PERSONAL_LABEL' && typeof message.key === 'string' && /^[a-f0-9]{64}$/.test(message.key)) {
    task = mutateLabels(rows => rows.filter(row => row.key !== message.key));
  }
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
