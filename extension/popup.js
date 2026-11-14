/**
 * YouTube Stream Spam Filter - Popup Controller (`extension/popup.js`)
 *
 * Polls local backend health and displays real-time stream sketch estimates
 * (DGIM, Flajolet-Martin) and E2E latency metrics.
 */

(() => {
  const API_BASE = "http://127.0.0.1:8000";

  const statusEl = document.getElementById("server-status");
  const diagEl = document.getElementById("diag-banner");
  const observedEl = document.getElementById("stat-observed");
  const suspectEl = document.getElementById("stat-suspect");
  const holdEl = document.getElementById("stat-hold");
  const dgimEl = document.getElementById("stat-dgim");
  const fmUrlsEl = document.getElementById("stat-fm-urls");
  const fmAccountsEl = document.getElementById("stat-fm-accounts");
  const p50El = document.getElementById("stat-p50");
  const p95El = document.getElementById("stat-p95");
  const toggleEl = document.getElementById("toggle-filter");
  const resetBtn = document.getElementById("btn-reset");

  let activeVideoId = "default_video";
  let lastObservedCount = 0;

  async function callBackend(type, videoId) {
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      return await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({ type, videoId }, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!response || !response.ok) {
            reject(new Error((response && response.error) || "Request failed"));
            return;
          }
          resolve(response.data);
        });
      });
    }

    if (type === "API_RESET") {
      const res = await fetch(`${API_BASE}/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: videoId }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    }

    const res = await fetch(
      `${API_BASE}/health?video_id=${encodeURIComponent(videoId)}`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  function renderStats(stats) {
    if (!stats) return;
    lastObservedCount = Number(stats.observed_count ?? 0);
    observedEl.textContent = String(lastObservedCount);
    suspectEl.textContent = String(stats.spam_suspect_count ?? 0);
    holdEl.textContent = String(stats.hold_count ?? 0);
    dgimEl.textContent = String(stats.dgim_suspect_estimate ?? 0.0);
    fmUrlsEl.textContent = String(stats.fm_unique_spam_urls_estimate ?? 0.0);
    fmAccountsEl.textContent = String(stats.fm_unique_spam_accounts_estimate ?? 0.0);
  }

  function updateBanner(online) {
    if (!diagEl) return;
    if (!online) {
      diagEl.textContent =
        "⚠️ 로컬 서버(127.0.0.1:8000)에 연결할 수 없습니다. 터미널에서 python -m src.server 를 실행하세요.";
      return;
    }
    if (lastObservedCount === 0) {
      diagEl.textContent =
        `ℹ️ 영상(${activeVideoId}) 관측 댓글 0건 — 유튜브 댓글 영역으로 스크롤하거나 탭을 새로고침(F5)해 주세요.`;
      return;
    }
    diagEl.innerHTML =
      "ℹ️ 아래 수치는 <strong>현재 영상의 관측 스트림 기준 통계</strong>(스크롤 DOM 로딩 순서)이며, 실제 작성 시각 순 빈도와 다를 수 있습니다.";
  }

  function setOnlineBadge(online) {
    if (online) {
      statusEl.textContent = "● 온라인 (127.0.0.1:8000)";
      statusEl.className = "status-pill online";
    } else {
      statusEl.textContent = "● 분석 불가(오프라인)";
      statusEl.className = "status-pill offline";
    }
    updateBanner(online);
  }

  async function refreshFromServer() {
    try {
      const data = await callBackend("API_HEALTH", activeVideoId);
      if (data.current_video_stats) {
        renderStats(data.current_video_stats);
      }
      setOnlineBadge(true);
    } catch (_err) {
      setOnlineBadge(false);
    }
  }

  function detectActiveTabVideoId(callback) {
    if (typeof chrome === "undefined" || !chrome.tabs || !chrome.tabs.query) {
      callback();
      return;
    }
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      if (tab && tab.url) {
        try {
          const u = new URL(tab.url);
          if (u.hostname.includes("youtube.com") && u.pathname.startsWith("/watch")) {
            const v = u.searchParams.get("v");
            if (v) activeVideoId = v;
          }
        } catch (_e) {
          // Ignore invalid URL
        }
      }
      callback();
    });
  }

  function loadStoredState() {
    detectActiveTabVideoId(() => {
      if (typeof chrome === "undefined" || !chrome.storage || !chrome.storage.local) {
        refreshFromServer();
        return;
      }
      chrome.storage.local.get(
        ["currentVideoId", "streamStats", "e2eLatency", "filteringEnabled", "serverOnline"],
        (res) => {
          if (activeVideoId === "default_video" && res.currentVideoId) {
            activeVideoId = res.currentVideoId;
          }
          if (typeof res.filteringEnabled === "boolean") {
            toggleEl.checked = res.filteringEnabled;
          }
          if (res.streamStats && res.streamStats.video_id === activeVideoId) {
            renderStats(res.streamStats);
          }
          if (res.e2eLatency) {
            p50El.textContent = String(res.e2eLatency.p50_ms ?? 0.0);
            p95El.textContent = String(res.e2eLatency.p95_ms ?? 0.0);
          }
          if (typeof res.serverOnline === "boolean") {
            setOnlineBadge(res.serverOnline);
          }
          refreshFromServer();
        }
      );
    });
  }

  toggleEl.addEventListener("change", () => {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      chrome.storage.local.set({ filteringEnabled: toggleEl.checked });
    }
  });

  resetBtn.addEventListener("click", async () => {
    try {
      const data = await callBackend("API_RESET", activeVideoId);
      renderStats(data.stream_stats);
      setOnlineBadge(true);
    } catch (_err) {
      setOnlineBadge(false);
    }
  });

  loadStoredState();
  setInterval(refreshFromServer, 2000);
})();
