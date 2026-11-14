/*
 * 유튜브 페이지에 붙는 content script. core.js(같은 격리 환경)의 Engine으로 댓글을 직접 분석한다.
 * 서버가 없으므로 댓글이 브라우저 밖으로 나가지 않는다. (AI 검토를 사용자가 켠 경우만 예외)
 *
 * 설계 원칙
 *  - 댓글마다 독립적으로 처리한다. 한 댓글이 실패해도 대기열 전체가 멈추지 않는다.
 *  - 유튜브 내부 링크(타임스탬프·해시태그·멘션)는 스팸 근거로 세지 않는다 (core.js classifyUrls).
 *  - 스팸 의심만 접고, 검토 필요는 배지만 단다. 삭제·신고는 하지 않는다.
 */
(() => {
  'use strict';
  if (window.__commentShieldLoaded) return;
  window.__commentShieldLoaded = true;
  const CG = globalThis.CG;

  const DEFAULTS = { enabled: true, autoHide: true, sensitivity: 'normal', ai: false };
  let config = { ...DEFAULTS };
  let engine = null, bloomJson = null;
  let video = '', generation = 0;
  let nodeState = new WeakMap();
  let results = new Map(), expanded = new Set(), queue = [];
  let pumping = false, scheduled = false, overflow = false, lastError = '', persistTimer = null;
  const MAX_QUEUE = 300;

  const send = message => new Promise(resolve => {
    try { chrome.runtime.sendMessage(message, response => resolve(chrome.runtime.lastError ? null : response)); }
    catch { resolve(null); }
  });
  const videoId = () => location.pathname === '/watch' ? new URL(location.href).searchParams.get('v') || '' : '';

  /* ───── 엔진 준비 ───── */

  async function initEngine() {
    const saved = await chrome.storage.local.get(['learned']);
    let bloom = null;
    if (bloomJson) { try { bloom = CG.BloomFilter.fromJSON(bloomJson); } catch { bloom = null; } }
    engine = new CG.Engine({
      bloom, sensitivity: config.sensitivity, learned: saved.learned || { spam: [], ham: [] },
      confirm: async keys => { const r = await send({ type: 'CHECK_KEYS', keys }); return r && r.ok ? r.data : null; },
    });
  }

  function persistLearned() {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(() => { try { chrome.storage.local.set({ learned: engine.exportLearned() }); } catch { /* 확장 프로그램이 다시 로드된 경우 */ } }, 500);
  }

  /* ───── 화면 ───── */

  function clearDecorations() {
    document.querySelectorAll('.cg-bar').forEach(el => el.remove());
    document.querySelectorAll('.cg-text-hidden').forEach(el => el.classList.remove('cg-text-hidden'));
  }

  function resetSession() {
    generation++;
    queue = []; overflow = false; results = new Map(); expanded = new Set(); nodeState = new WeakMap(); lastError = '';
    if (engine) engine.newSession();
    clearDecorations();
  }

  function button(label, onClick, title) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = label; if (title) b.title = title;
    b.addEventListener('click', event => { event.stopPropagation(); event.preventDefault(); onClick(b); });
    return b;
  }

  function render(item, result) {
    const { node, textNode } = item;
    if (!node.isConnected) return;
    item.rendered = result;
    node.querySelectorAll(':scope > .cg-bar').forEach(el => el.remove());
    textNode.classList.remove('cg-text-hidden');
    if (!config.enabled || result.verdict === 'safe') return;

    const spam = result.verdict === 'spam';
    const bar = document.createElement('div');
    bar.className = 'cg-bar'; bar.dataset.verdict = result.verdict;
    bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', '댓글 방패 분석 결과');

    const badge = document.createElement('strong');
    badge.className = 'cg-badge'; badge.textContent = spam ? '스팸 의심' : '검토 필요';
    const reason = document.createElement('span');
    reason.className = 'cg-reason';
    reason.textContent = result.reasons.slice(0, 3).join(' · ') || '규칙 점수가 기준을 넘었습니다.';
    reason.title = `점수 ${result.score} · 출처 ${result.source}`;
    bar.append(badge, reason);

    const actions = document.createElement('span');
    actions.className = 'cg-actions';
    if (spam) {
      const hidden = config.autoHide && !expanded.has(item.id);
      textNode.classList.toggle('cg-text-hidden', hidden);
      actions.append(button(hidden ? '원문 보기' : '댓글 접기', b => {
        const nowHidden = !textNode.classList.contains('cg-text-hidden');
        textNode.classList.toggle('cg-text-hidden', nowHidden);
        if (nowHidden) expanded.delete(item.id); else expanded.add(item.id);
        b.textContent = nowHidden ? '원문 보기' : '댓글 접기';
      }));
    } else {
      actions.append(button('스팸입니다', () => {
        engine.markSpam(item.id, item.payload.text); persistLearned();
        const upgraded = { ...result, verdict: 'spam', reasons: ['사용자가 스팸으로 표시'], source: 'user-spam' };
        results.set(item.id, upgraded); render(item, upgraded);
      }, '이 댓글과 비슷한 댓글을 다음부터 스팸 의심으로 표시합니다.'));
    }
    actions.append(button('정상입니다', () => {
      engine.markHam(item.id, item.payload.text); persistLearned();
      const cleared = { ...result, verdict: 'safe', reasons: [], source: 'user-ham' };
      results.set(item.id, cleared); render(item, cleared);
    }, '이 댓글과 비슷한 댓글은 다음부터 표시하지 않습니다. 신고나 서버 전송은 없습니다.'));
    bar.append(actions);
    node.prepend(bar);
  }

  /* ───── 수집 ───── */

  function schedule(delay = 300) {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; scan(); }, delay);
  }

  function scan() {
    if (!engine) return;
    const current = videoId();
    if (current !== video) { video = current; resetSession(); }
    if (!config.enabled || !video) return;
    const nodes = document.querySelectorAll('#comments ytd-comment-view-model, #comments ytd-comment-renderer');
    for (const node of nodes) {
      if (node.matches('ytd-comment-renderer') && node.querySelector('ytd-comment-view-model')) continue;
      const textNode = node.querySelector('#content-text');
      const text = textNode?.textContent?.trim().slice(0, 1500);
      if (!text) continue;
      const anchors = [...textNode.querySelectorAll('a[href]')].filter(a => /^https?:/.test(a.href)).slice(0, 20);
      const links = anchors.map(a => a.href);
      const linkTexts = anchors.map(a => a.textContent.trim());
      const account = node.querySelector('#author-text')?.getAttribute('href') || '';
      const permalink = node.querySelector('#published-time-text a[href]')?.href || '';
      let commentId = '';
      try { commentId = permalink ? new URL(permalink, location.href).searchParams.get('lc') || '' : ''; } catch { /* 무시 */ }
      const fingerprint = `${commentId}|${account}|${text}|${links.join(',')}`;
      const state = nodeState.get(node);
      if (state && state.fingerprint === fingerprint) {
        const known = results.get(state.id);
        if (known) { if (state.rendered !== known) render(state, known); }
        else if (!state.queued && !state.failed) {          // 대기열이 가득 차서 못 넣었던 댓글을 다시 넣는다
          if (queue.length < MAX_QUEUE) { state.queued = true; queue.push(state); } else overflow = true;
        }
        continue;
      }
      const id = (commentId || CG.hash32(account + '|' + text).toString(36)) + ':' + CG.hash32(text).toString(36);
      const item = { node, textNode, fingerprint, id, rendered: null, payload: { id, text, links, linkTexts, account, video } };
      nodeState.set(node, item);
      if (results.has(id)) { render(item, results.get(id)); continue; }
      if (queue.length < MAX_QUEUE) { item.queued = true; queue.push(item); } else overflow = true;
    }
    pump();
  }

  async function pump() {
    if (pumping) return;
    pumping = true;
    const epoch = generation;
    try {
      let processed = 0;
      while (queue.length && epoch === generation && config.enabled) {
        const item = queue.shift();
        item.queued = false;
        if (!item.node.isConnected || nodeState.get(item.node) !== item) continue;
        try {
          let result = results.get(item.id);
          if (!result) {
            const refine = config.ai ? async ctx => { const r = await send({ type: 'AI_REVIEW', payload: ctx }); return r && r.ok ? r.data : null; } : undefined;
            result = await engine.analyze({ ...item.payload, refine });
            if (epoch !== generation) break;
            results.set(item.id, result);
            if (results.size > 3000) results.delete(results.keys().next().value);
          }
          render(item, result);
          lastError = '';
        } catch (error) {
          // 이 댓글만 건너뛴다 (재시도로 대기열을 막지 않음). 내용이 바뀌면 다시 분석된다.
          engine.counts.errors++; item.failed = true;
          lastError = '일부 댓글을 분석하지 못했습니다.';
        }
        if (++processed % 8 === 0) await new Promise(r => setTimeout(r, 0)); // 화면이 멈추지 않도록 양보
      }
    } finally {
      pumping = false;
      if (queue.length && config.enabled) setTimeout(pump, 50);
      else if (overflow && config.enabled) { overflow = false; schedule(100); }   // 넘쳤던 댓글을 이어서 처리
    }
  }

  /* ───── 팝업·설정 연동 ───── */

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || !message) return false;
    if (message.type === 'STATUS') {
      respond({ supported: Boolean(videoId()), enabled: config.enabled, stats: engine ? engine.stats() : null,
        queued: queue.length, lastError, learned: engine ? { spam: engine.learnedSpam.length, ham: engine.learnedHam.length } : null });
    } else if (message.type === 'RESCAN') {
      resetSession(); schedule(0); respond({ ok: true });
    } else if (message.type === 'CLEAR_LEARNED') {
      chrome.storage.local.remove('learned').then(async () => { await initEngine(); resetSession(); schedule(0); respond({ ok: true }); });
      return true;
    }
    return false;
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    let rescan = false;
    for (const key of Object.keys(DEFAULTS)) if (changes[key]) { config[key] = changes[key].newValue ?? DEFAULTS[key]; rescan = true; }
    if (changes.sensitivity && engine) engine.sensitivity = config.sensitivity;
    if (rescan) { resetSession(); schedule(0); }
  });

  const observer = new MutationObserver(mutations => {
    for (const m of mutations) {
      const el = m.target.nodeType === Node.ELEMENT_NODE ? m.target : m.target.parentElement;
      if (!el || el.closest?.('.cg-bar')) continue;
      if (el.closest?.('#comments') || [...m.addedNodes].some(n => n.nodeType === 1 && (n.matches?.('#comments') || n.querySelector?.('#comments')))) { schedule(); return; }
    }
  });

  (async () => {
    config = { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
    const response = await send({ type: 'GET_BLOOM' });
    bloomJson = response && response.ok ? response.data : null;
    await initEngine();
    video = videoId();
    observer.observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener('yt-navigate-finish', () => schedule(100));
    window.addEventListener('popstate', () => schedule(100));
    schedule(0);
  })();
})();
