/**
 * YouTube Stream Spam Filter - MV3 Background Service Worker (`extension/background.js`)
 *
 * Proxies HTTP requests to the local Python stream engine (`http://127.0.0.1:8000`)
 * so that content scripts running on https://www.youtube.com are never blocked by
 * Chrome Mixed-Content, CSP, or Private Network Access (PNA) restrictions.
 */

const API_BASE = "http://127.0.0.1:8000";
const FETCH_TIMEOUT_MS = 4000;

async function fetchJsonWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.type) {
    return false;
  }

  if (message.type === "API_ANALYZE") {
    fetchJsonWithTimeout(`${API_BASE}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload || {}),
    })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message.type === "API_RESET") {
    fetchJsonWithTimeout(`${API_BASE}/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: message.videoId || "default_video" }),
    })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message.type === "API_HEALTH") {
    const vid = encodeURIComponent(message.videoId || "default_video");
    fetchJsonWithTimeout(`${API_BASE}/health?video_id=${vid}`, {
      method: "GET",
    })
      .then((data) => sendResponse({ ok: true, data }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  return false;
});
