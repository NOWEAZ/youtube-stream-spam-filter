/* Local fixture adapter. The production extension never loads or uses this shim. */
(() => {
  const $ = id => document.getElementById(id);
  const listeners = [], storageListeners = [];
  const config = { enabled: true, autoHide: true, allowSolar: false, token: "" };
  globalThis.chrome = {
    runtime: {
      onMessage: { addListener: callback => listeners.push(callback) },
      async sendMessage(message) {
        if (message.type !== "ANALYZE") return;
        if (!config.token) return { ok: false, error: "연결 토큰을 입력해 주세요." };
        try {
          const response = await fetch("/api/analyze", { method: "POST",
            headers: { "Content-Type": "application/json", "X-YCSF-Token": config.token },
            body: JSON.stringify({ session: message.session, comments: message.comments, allow_solar: false }) });
          const data = await response.json();
          return response.ok ? { ok: true, data } : { ok: false, error: data.error };
        } catch { return { ok: false, error: "로컬 서버 연결이 끊겼습니다." }; }
      }
    },
    storage: { local: { get: async () => ({ ...config }) },
      onChanged: { addListener: callback => storageListeners.push(callback) } }
  };
  const notify = (name, value) => {
    const oldValue = config[name]; config[name] = value;
    storageListeners.forEach(callback => callback({ [name]: { oldValue, newValue: value } }, "local"));
  };
  let sequence = 0;
  function addComment(text, author = "시청자") {
    const comment = document.createElement("ytd-comment-view-model");
    const account = document.createElement("a"); account.id = "author-text";
    account.href = `/channel/demo-${sequence++}`; account.textContent = author;
    const content = document.createElement("div"); content.id = "content-text"; content.textContent = text;
    comment.append(account, content); $("comments").append(comment);
  }
  [
    ["강의 예시가 이해하기 쉬웠습니다. 다음 영상도 기대됩니다.", "알고리즘 공부중"],
    ["계정 인증이 필요합니다. 아래 링크를 클릭하세요 https://phishing.test/login", "샘플 계정 A"],
    ["투자 수익 인증! 프로필 링크 확인 후 텔레그램으로 문의하세요.", "샘플 계정 B"],
    ["관련 자료는 프로필 링크에서 확인할 수 있어요.", "샘플 계정 C"],
    ["노래 너무 좋아요. 매일 듣고 있습니다.", "음악을 좋아하는 사람"],
  ].forEach(args => addComment(...args));
  $("connect").addEventListener("click", () => {
    const token = $("demoToken").value.trim();
    if (token.length < 24) { $("connectionMessage").textContent = "서버 터미널의 토큰을 그대로 붙여 넣어 주세요."; return; }
    notify("token", token); $("connectionMessage").textContent = "연결을 요청했습니다. 아래 분석 결과를 확인해 주세요.";
  });
  $("autoHide").addEventListener("change", () => notify("autoHide", $("autoHide").checked));
  $("reset").addEventListener("click", () => listeners.forEach(callback => callback({ type: "RESCAN" }, {}, () => {})));
  $("add").addEventListener("click", () => {
    addComment("투자 수익 인증! 프로필 링크 확인 후 텔레그램으로 문의하세요!!", "샘플 계정 D");
    addComment("저도 이 부분이 궁금했는데 설명 감사합니다.", "새 시청자");
  });
  $("custom").addEventListener("submit", event => {
    event.preventDefault(); const text = $("customText").value.trim();
    if (text) { addComment(text, "직접 입력"); $("customText").value = ""; }
  });
  fetch("/api/health").then(r => r.json()).then(data => {
    $("serverStatus").textContent = data.demo_data ? "시연 목록 사용 중" : `목록 ${data.blocklist_count}건`;
  }).catch(() => { $("serverStatus").textContent = "서버 연결 필요"; });
  setInterval(() => {
    listeners.forEach(callback => callback({ type: "GET_STATUS" }, {}, info => {
      const stats = info.stats || {};
      for (const key of ["total", "spam", "review", "safe"]) $(key).textContent = stats[key] || 0;
      if (info.lastError) $("connectionMessage").textContent = info.lastError;
      else if (stats.total) $("connectionMessage").textContent = "연결되었습니다. 샘플 댓글은 로컬 서버에서 분석합니다. Solar는 이 시연에서 사용하지 않습니다.";
      $("streamStats").textContent = `DGIM 최근 ${stats.window || 0}개 중 의심 ${stats.recent_spam_estimate || 0}개 추정 · FM 고유 스팸 URL ${stats.unique_spam_urls_estimate || 0}개 추정`;
    }));
  }, 600);
})();
