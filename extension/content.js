(() => {
  "use strict";
  if (globalThis.__commentGuardLoaded) return;
  globalThis.__commentGuardLoaded = true;
  const defaults = { enabled: true, autoHide: true, allowOpenCode: false, allowProfileImages: false };
  let config = { ...defaults }, video = "", session = "", generation = 0;
  let nodeState = new WeakMap(), results = new Map(), pending = new Map();
  let originals = new Set();
  let scheduled = false, scanning = false, busy = false, retryTimer = null;
  let latencies = [];
  let stats = {}, lastError = "", lastLatency = 0;

  const supportedVideo = () => location.hostname === "127.0.0.1" ? "demo" :
    location.pathname === "/watch" ? new URL(location.href).searchParams.get("v") || "" : "";
  const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(value)))).map(v => v.toString(16).padStart(2, "0")).join("");
  const send = message => chrome.runtime.sendMessage(message);

  function clearDecorations() {
    document.querySelectorAll(".ycg-bar").forEach(el => el.remove());
    document.querySelectorAll(".ycg-text-hidden").forEach(el => el.classList.remove("ycg-text-hidden"));
  }

  function reset() {
    generation++;
    session = crypto.randomUUID();
    pending.clear(); results.clear(); originals.clear();
    nodeState = new WeakMap(); stats = {}; latencies = []; lastError = "";
    clearTimeout(retryTimer); retryTimer = null;
    clearDecorations();
  }

  function render(node, textNode, id, result) {
    if (!node.isConnected) return;
    node.querySelectorAll(":scope > .ycg-bar").forEach(el => el.remove());
    textNode.classList.remove("ycg-text-hidden");
    if (!config.enabled) return;
    const bar = document.createElement("div");
    bar.className = "ycg-bar";
    bar.dataset.verdict = result.verdict;
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "댓글 가드 분석 결과");
    const title = document.createElement("strong");
    title.textContent = result.source === "personal_exact" ? (result.verdict === "spam" ? "내 스팸 라벨" : "내 정상 라벨") :
      result.verdict === "spam" ? "스팸 의심" : result.verdict === "review" ? "검토 필요" : "개인 라벨";
    const reason = document.createElement("span");
    reason.className = "ycg-reason";
    reason.textContent = `${result.language === "en" ? "EN · " : result.language === "mixed" ? "KO+EN · " : ""}${result.reason}`;
    if (result.verdict !== "safe" || result.source === "personal_exact") bar.append(title, reason);
    else bar.classList.add("ycg-personal-controls");
    if (result.verdict === "spam") {
      const toggle = document.createElement("button");
      toggle.type = "button";
      const update = () => {
        const hidden = config.autoHide && !originals.has(id);
        textNode.classList.toggle("ycg-text-hidden", hidden);
        toggle.textContent = hidden ? "원문 보기" : "댓글 접기";
        toggle.setAttribute("aria-expanded", String(!hidden));
      };
      toggle.addEventListener("click", event => {
        event.stopPropagation();
        const hidden = textNode.classList.contains("ycg-text-hidden");
        textNode.classList.toggle("ycg-text-hidden", !hidden);
        if (hidden) originals.add(id); else originals.delete(id);
        toggle.textContent = hidden ? "댓글 접기" : "원문 보기";
        toggle.setAttribute("aria-expanded", String(hidden));
      });
      update(); bar.append(toggle);
    }
    const payload = nodeState.get(node)?.payload || { text: textNode.textContent.trim().slice(0, 2000),
      urls: [...textNode.querySelectorAll("a[href]")].map(a => a.href).filter(url => /^https?:/.test(url) && url.length <= 2048).slice(0, 20) };
    for (const label of ["spam", "normal"]) {
      const feedback = document.createElement("button");
      feedback.type = "button"; feedback.textContent = label === "spam" ? "스팸입니다" : "정상입니다";
      feedback.title = "이 Chrome 프로필의 개인 라벨로 저장합니다. 같은 댓글에 다음부터 적용하며 다른 사용자에게 공유하지 않습니다.";
      feedback.addEventListener("click", async event => {
        if (!event.isTrusted) return;
        event.stopPropagation(); feedback.disabled = true;
        try {
          const response = await send({ type: "SET_PERSONAL_LABEL", text: payload.text, urls: payload.urls || [], label });
          if (!response?.ok) throw new Error(response?.error || "개인 라벨을 저장하지 못했습니다.");
          reset(); schedule();
        } catch (error) { reason.textContent = error.message; if (!reason.parentNode) bar.append(reason); }
        finally { feedback.disabled = false; }
      });
      bar.append(feedback);
    }
    if (result.personal_match?.kind === "exact") {
      const undo = document.createElement("button"); undo.type = "button"; undo.textContent = "등록 취소";
      undo.addEventListener("click", async event => {
        if (!event.isTrusted) return;
        event.stopPropagation(); undo.disabled = true;
        try {
          const response = await send({ type: "DELETE_PERSONAL_LABEL", key: result.personal_match.reference_id });
          if (!response?.ok) throw new Error(response?.error || "취소하지 못했습니다.");
          reset(); schedule();
        } catch (error) { reason.textContent = error.message; undo.disabled = false; }
      });
      bar.append(undo);
    }
    node.prepend(bar);
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; scan(); }, 300);
  }

  async function scan() {
    if (scanning) return schedule();
    const current = supportedVideo();
    if (current !== video) { video = current; reset(); }
    if (!config.enabled || !video) return;
    scanning = true;
    const epoch = generation;
    try {
      const nodes = document.querySelectorAll("#comments ytd-comment-view-model, #comments ytd-comment-renderer");
      for (const node of nodes) {
        if (epoch !== generation || !config.enabled) break;
        if (node.matches("ytd-comment-renderer") && node.querySelector("ytd-comment-view-model")) continue;
        const textNode = node.querySelector("#content-text");
        const text = textNode?.textContent?.trim().slice(0, 2000);
        if (!text) continue;
        const urls = [...textNode.querySelectorAll("a[href]")].map(a => a.href).filter(url => /^https?:/.test(url) && url.length <= 2048).slice(0, 20);
        const authorNode = node.querySelector("#author-text");
        const author_name = (authorNode?.textContent || "").trim().slice(0, 200);
        const channel = authorNode?.getAttribute("href") || "";
        const fingerprint = `${text}\n${channel}\n${urls.join("\n")}\n${author_name}`;
        const previous = nodeState.get(node);
        if (previous?.fingerprint === fingerprint && previous.textNode === textNode) {
          const result = results.get(previous.id);
          if (result && previous.result !== result) {
            render(node, textNode, previous.id, result); previous.result = result;
          }
          continue;
        }
        const permalink = node.querySelector("#published-time-text a[href]")?.href || "";
        const commentId = permalink ? new URL(permalink, location.href).searchParams.get("lc") : "";
        const id = await digest(`${video}:${commentId || fingerprint}:${text}:${urls.join("\n")}:${author_name}`);
        const account = channel ? await digest(`${session}:${channel}`) : "";
        if (epoch !== generation || !config.enabled || !node.isConnected) continue;
        const item = { node, textNode, id, fingerprint };
        if (results.has(id)) {
          render(node, textNode, id, results.get(id)); item.result = results.get(id);
        }
        else if (pending.size < 300) pending.set(id, { ...item, payload: { id, text, urls, account, video, author_name } });
        else continue;
        nodeState.set(node, item);
      }
    } catch { lastError = "댓글을 읽는 중 오류가 발생했습니다. 페이지를 새로고침해 주세요."; }
    finally { scanning = false; drain(); }
  }

  async function drain() {
    if (busy || !pending.size || !config.enabled || retryTimer) return;
    busy = true;
    const epoch = generation;
    const batch = [...pending.values()].filter(item => item.node.isConnected).slice(0, 5);
    // Drop detached nodes so endless navigation cannot grow the queue.
    for (const [id, item] of pending) if (!item.node.isConnected) pending.delete(id);
    if (!batch.length) { busy = false; return; }
    try {
      const started = performance.now();
      const response = await send({ type: "ANALYZE", session, comments: batch.map(item => item.payload) });
      if (epoch !== generation) return;
      if (!response?.ok) throw new Error(response?.error || "확장 프로그램을 다시 로드해 주세요.");
      const data = response.data;
      stats = data.stats; lastError = "";
      latencies.push(performance.now() - started);
      if (latencies.length > 200) latencies.shift();
      for (const result of data.results) {
        results.set(result.id, result); pending.delete(result.id);
        lastLatency = result.latency_ms;
      }
      while (results.size > 2000) results.delete(results.keys().next().value);
      for (const item of batch) {
        const current = nodeState.get(item.node);
        if (config.enabled && current?.id === item.id && current.textNode === item.textNode && results.has(item.id)) {
          render(item.node, item.textNode, item.id, results.get(item.id));
          current.result = results.get(item.id);
          // A duplicated render node can share an ID; rescan to decorate it too.
        }
      }
    } catch (error) {
      if (epoch === generation) {
        lastError = error.message;
        retryTimer = setTimeout(() => { retryTimer = null; drain(); }, 5000);
      }
    } finally {
      busy = false;
      if (pending.size && !retryTimer && config.enabled) setTimeout(drain, 100);
      schedule();
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === "GET_STATUS") {
      const sorted = [...latencies].sort((a, b) => a - b);
      const percentile = p => sorted.length ? sorted[Math.ceil(sorted.length * p) - 1] : null;
      respond({ latency: { p50: percentile(.5), p95: percentile(.95) }, video, supported: Boolean(supportedVideo()), stats, lastError, lastLatency,
                queued: pending.size, enabled: config.enabled });
    }
    if (message?.type === "RESCAN") {
      reset(); schedule(); respond({ ok: true });
    }
    return false;
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    let redraw = false, restart = false;
    for (const key of Object.keys(defaults)) {
      if (changes[key]) { config[key] = changes[key].newValue ?? defaults[key]; redraw = true; }
    }
    restart = Boolean(changes.allowOpenCode || changes.allowProfileImages || changes.personalLabels || changes.personalLabelsRevision || changes.token || changes.port);
    if (restart) reset();
    else if (redraw) {
      clearDecorations(); nodeState = new WeakMap();
    }
    schedule();
  });
  const observer = new MutationObserver(mutations => {
    if (mutations.some(mutation => {
      const el = mutation.target.nodeType === Node.ELEMENT_NODE ? mutation.target : mutation.target.parentElement;
      if (el?.closest(".ycg-bar")) return false;
      return el?.closest("#comments") || [...mutation.addedNodes].some(n =>
        n.nodeType === Node.ELEMENT_NODE && (n.matches?.("#comments") || n.querySelector?.("#comments")));
    })) schedule();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["src", "srcset", "href"] });
  document.addEventListener("yt-navigate-finish", schedule);
  window.addEventListener("popstate", schedule);
  chrome.storage.local.get(Object.keys(defaults)).then(saved => {
    config = { ...defaults, ...saved }; video = supportedVideo(); reset(); schedule();
  });
})();
