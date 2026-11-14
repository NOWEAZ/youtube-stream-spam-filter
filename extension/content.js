/**
 * YouTube Stream Spam Filter - Content Script (`extension/content.js`)
 *
 * Observes YouTube comment DOM stream via MutationObserver, batches new comments
 * via the MV3 background worker to the local Python stream engine (`127.0.0.1:8000`),
 * and renders folding/blur masking, badges, and [원문 보기] toggle buttons.
 */

(() => {
  const API_BASE = "http://127.0.0.1:8000";
  const BATCH_DELAY_MS = 150;
  const FETCH_TIMEOUT_MS = 4000;

  let currentVideoId = null;
  let resetPromise = Promise.resolve();
  let filteringEnabled = true;
  let fallbackCounter = 0;
  const processedCommentIds = new Set();
  const pendingQueue = new Map(); // commentId -> { commentId, text, authorId, contentEl, detectedAt }
  const e2eLatenciesMs = [];
  let batchTimer = null;

  function getVideoIdFromUrl() {
    if (window.location.pathname.startsWith("/watch")) {
      const params = new URLSearchParams(window.location.search);
      return params.get("v");
    }
    return null;
  }

  function computePercentile(sortedArr, p) {
    if (!sortedArr.length) return 0;
    const idx = Math.min(sortedArr.length - 1, Math.floor((p / 100) * sortedArr.length));
    return Number(sortedArr[idx].toFixed(2));
  }

  function updateStoredState(serverOnline, streamStats, lastError = null) {
    if (typeof chrome === "undefined" || !chrome.storage || !chrome.storage.local) {
      return;
    }
    const sorted = [...e2eLatenciesMs].sort((a, b) => a - b);
    chrome.storage.local.set({
      serverOnline,
      currentVideoId,
      streamStats: streamStats || null,
      lastError,
      e2eLatency: {
        count: sorted.length,
        p50_ms: computePercentile(sorted, 50),
        p95_ms: computePercentile(sorted, 95),
      },
      updatedAt: Date.now(),
    });
  }

  async function callBackend(type, payloadOrVideoId) {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      return await new Promise((resolve, reject) => {
        const msg =
          type === "API_ANALYZE"
            ? { type, payload: payloadOrVideoId }
            : { type, videoId: payloadOrVideoId };
        chrome.runtime.sendMessage(msg, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!response || !response.ok) {
            reject(new Error((response && response.error) || "Background fetch failed"));
            return;
          }
          resolve(response.data);
        });
      });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const endpoint = type === "API_ANALYZE" ? "/analyze" : "/reset";
      const bodyObj =
        type === "API_ANALYZE"
          ? payloadOrVideoId
          : { video_id: payloadOrVideoId };
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyObj),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  async function resetVideoOnServer(videoId) {
    if (!videoId) return;
    try {
      const data = await callBackend("API_RESET", videoId);
      updateStoredState(true, data.stream_stats, null);
    } catch (err) {
      updateStoredState(false, null, String(err));
    }
  }

  function hashTextId(videoId, text, seq) {
    let h = 2166136261;
    const str = `${videoId}::${seq}::${text}`;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return `ytc_${(h >>> 0).toString(16)}`;
  }

  function extractCommentInfo(commentNode) {
    const contentEl = commentNode.querySelector(
      "#content-text, yt-attributed-string#content-text, #comment-content #content-text"
    );
    if (!contentEl) return null;
    const text = (contentEl.innerText || contentEl.textContent || "").trim();
    if (!text) return null;

    // Extract stable comment ID from permalink if available
    let commentId = commentNode.getAttribute("data-yt-spam-cid");
    if (!commentId) {
      const timeLink = commentNode.querySelector(
        "#published-time-text a, a.yt-simple-endpoint[href*='lc='], a[href*='lc=']"
      );
      if (timeLink && timeLink.href) {
        try {
          const url = new URL(timeLink.href, window.location.origin);
          const lc = url.searchParams.get("lc");
          if (lc) commentId = lc;
        } catch (_e) {
          // Fallback to deterministic hash
        }
      }
    }
    if (!commentId) {
      fallbackCounter += 1;
      commentId = hashTextId(currentVideoId || "default", text, fallbackCounter);
    }
    commentNode.setAttribute("data-yt-spam-cid", commentId);

    // Privacy Rule 4: Only use channel URL path/ID if available, never raw display name
    let authorId = null;
    const authorLink = commentNode.querySelector(
      "a#author-text[href], #header-author a[href^='/channel/'], #header-author a[href^='/@'], a[href^='/channel/'], a[href^='/@']"
    );
    if (authorLink) {
      const href = authorLink.getAttribute("href");
      if (href) {
        authorId = href.trim();
      }
    }

    return { commentId, text, authorId, contentEl };
  }

  function renderVerdictOnDOM(item, verdict) {
    const { contentEl, detectedAt } = item;
    if (!contentEl || !contentEl.parentElement) return;

    const existingBar = contentEl.parentElement.querySelector(".yt-spam-filter-bar");
    if (existingBar) {
      existingBar.remove();
    }
    contentEl.classList.remove("yt-spam-filter-folded", "yt-spam-filter-unfolded");

    if (!filteringEnabled) {
      return;
    }

    if (verdict.status === "NORMAL") {
      const elapsed = performance.now() - detectedAt;
      e2eLatenciesMs.push(elapsed);
      return;
    }

    const bar = document.createElement("div");
    bar.className = "yt-spam-filter-bar";

    const badge = document.createElement("span");
    badge.className = "yt-spam-filter-badge";

    if (verdict.status === "OFFLINE") {
      badge.classList.add("yt-spam-filter-badge--offline");
      badge.textContent = "🔌 분석 불가(오프라인)";
    } else if (verdict.status === "HOLD") {
      badge.classList.add("yt-spam-filter-badge--hold");
      badge.textContent = "⏸️ 판단 보류";
    } else if (verdict.badge_type === "MALICIOUS_URL") {
      badge.classList.add("yt-spam-filter-badge--malicious");
      badge.textContent = "🚨 악성 URL 의심";
    } else {
      badge.classList.add("yt-spam-filter-badge--spam");
      badge.textContent = "⚠️ 도배/홍보 의심";
    }
    bar.appendChild(badge);

    if (verdict.reason) {
      const reasonSpan = document.createElement("span");
      reasonSpan.className = "yt-spam-filter-reason";
      reasonSpan.textContent = verdict.reason;
      bar.appendChild(reasonSpan);
    }

    if (verdict.status === "SPAM_SUSPECT") {
      contentEl.classList.add("yt-spam-filter-folded");
      const toggleBtn = document.createElement("button");
      toggleBtn.type = "button";
      toggleBtn.className = "yt-spam-filter-toggle-btn";
      toggleBtn.textContent = "원문 보기";
      let unfolded = false;
      toggleBtn.addEventListener("click", () => {
        unfolded = !unfolded;
        if (unfolded) {
          contentEl.classList.add("yt-spam-filter-unfolded");
          toggleBtn.textContent = "다시 접기";
        } else {
          contentEl.classList.remove("yt-spam-filter-unfolded");
          toggleBtn.textContent = "원문 보기";
        }
      });
      bar.appendChild(toggleBtn);
    }

    contentEl.parentElement.insertBefore(bar, contentEl);

    const elapsed = performance.now() - detectedAt;
    e2eLatenciesMs.push(elapsed);
  }

  async function flushPendingQueue() {
    batchTimer = null;
    await resetPromise;
    if (pendingQueue.size === 0 || !currentVideoId) return;

    const batchItems = Array.from(pendingQueue.values());
    pendingQueue.clear();

    const payload = {
      video_id: currentVideoId,
      comments: batchItems.map((it) => ({
        comment_id: it.commentId,
        text: it.text,
        video_id: currentVideoId,
        author_id: it.authorId,
      })),
    };

    try {
      const data = await callBackend("API_ANALYZE", payload);
      const resultMap = new Map();
      for (const r of data.results || []) {
        resultMap.set(r.comment_id, r);
      }

      for (const item of batchItems) {
        const verdict = resultMap.get(item.commentId);
        if (verdict) {
          processedCommentIds.add(item.commentId);
          renderVerdictOnDOM(item, verdict);
        }
      }
      updateStoredState(true, data.stream_stats, null);
    } catch (err) {
      // Fail-safe: Do NOT mark unanalyzed comments as NORMAL when server is offline
      for (const item of batchItems) {
        renderVerdictOnDOM(item, {
          status: "OFFLINE",
          reason: "로컬 분석 서버(127.0.0.1:8000) 연결 실패 — 정상으로 확정하지 않음",
        });
      }
      updateStoredState(false, null, String(err));
    }
  }

  function scanCommentsInDOM() {
    const vid = getVideoIdFromUrl();
    if (!vid) return;

    if (vid !== currentVideoId) {
      currentVideoId = vid;
      fallbackCounter = 0;
      processedCommentIds.clear();
      pendingQueue.clear();
      e2eLatenciesMs.length = 0;
      resetPromise = resetVideoOnServer(currentVideoId);
    }

    const commentNodes = document.querySelectorAll(
      "ytd-comment-view-model, ytd-comment-renderer"
    );
    commentNodes.forEach((node) => {
      const info = extractCommentInfo(node);
      if (!info) return;
      if (processedCommentIds.has(info.commentId) || pendingQueue.has(info.commentId)) {
        return;
      }
      pendingQueue.set(info.commentId, {
        ...info,
        detectedAt: performance.now(),
      });
    });

    if (pendingQueue.size > 0 && !batchTimer) {
      batchTimer = setTimeout(flushPendingQueue, BATCH_DELAY_MS);
    }
  }

  function initObserver() {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(["filteringEnabled"], (res) => {
        if (typeof res.filteringEnabled === "boolean") {
          filteringEnabled = res.filteringEnabled;
        }
      });
      chrome.storage.onChanged.addListener((changes) => {
        if (changes.filteringEnabled) {
          filteringEnabled = Boolean(changes.filteringEnabled.newValue);
        }
      });
    }

    const observer = new MutationObserver(() => {
      scanCommentsInDOM();
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    window.addEventListener("yt-navigate-finish", () => {
      scanCommentsInDOM();
    });
    window.addEventListener("popstate", () => {
      scanCommentsInDOM();
    });

    // Periodic fallback scan for lazy-hydrated or already-loaded comments
    setInterval(scanCommentsInDOM, 1500);
    scanCommentsInDOM();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initObserver);
  } else {
    initObserver();
  }
})();
