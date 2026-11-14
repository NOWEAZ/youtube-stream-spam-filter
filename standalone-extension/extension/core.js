/*
 * 댓글 방패 코어: 알고리즘(Bloom, MinHash+LSH, DGIM, Flajolet-Martin)과 규칙 엔진.
 * 브라우저(content script)와 Node(테스트·평가)에서 같은 파일을 그대로 씁니다. 외부 의존성 없음.
 *
 * 표기: 강의와 같게 Bloom Filter의 m = 비트 배열 크기, n = 원소 수, k = 해시 개수.
 */
(function (root) {
  'use strict';

  /* ───────────── 해시 ───────────── */

  // murmur3 계열 32비트 문자열 해시. seed가 다르면 서로 독립인 해시로 쓴다.
  function hash32(str, seed = 0) {
    let h = seed >>> 0;
    for (let i = 0; i < str.length; i++) {
      let k = Math.imul(str.charCodeAt(i), 0xcc9e2d51);
      k = (k << 15) | (k >>> 17);
      k = Math.imul(k, 0x1b873593);
      h ^= k;
      h = (h << 13) | (h >>> 19);
      h = (Math.imul(h, 5) + 0xe6546b64) | 0;
    }
    h ^= str.length;
    h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b);
    h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35);
    h ^= h >>> 16;
    return h >>> 0;
  }

  function mulberry32(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function trailingZeros32(x) {
    if (x === 0) return 32;
    let n = 0;
    while ((x & 1) === 0) { x >>>= 1; n++; }
    return n;
  }

  function toBase64(bytes) {
    if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  function fromBase64(text) {
    if (typeof Buffer !== 'undefined') return new Uint8Array(Buffer.from(text, 'base64'));
    const s = atob(text), out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  /* ───────────── Bloom Filter ───────────── */

  class BloomFilter {
    // n: 담을 원소 수, errorRate: 목표 오탐률. m·k는 표준 공식으로 정한다.
    constructor(n, errorRate = 0.01, bits = null, m = null, k = null) {
      n = Math.max(1, n | 0);
      this.m = m || Math.max(64, Math.ceil((-n * Math.log(errorRate)) / Math.LN2 ** 2));
      this.k = k || Math.min(16, Math.max(1, Math.round((this.m / n) * Math.LN2)));
      this.bits = bits || new Uint8Array((this.m + 7) >> 3);
      this.count = 0;
    }
    _indexes(value) {
      const h1 = hash32(value, 0x1234abcd), h2 = (hash32(value, 0x9e3779b9) | 1) >>> 0; // |1은 부호 있는 정수를 만들므로 >>>0 필요
      const out = new Array(this.k);
      for (let i = 0; i < this.k; i++) out[i] = (h1 + i * h2) % this.m; // 최대 약 2^36이라 Number로 안전
      return out;
    }
    add(value) {
      for (const i of this._indexes(value)) this.bits[i >> 3] |= 1 << (i & 7);
      this.count++;
    }
    has(value) {
      for (const i of this._indexes(value)) if (!(this.bits[i >> 3] & (1 << (i & 7)))) return false;
      return true;
    }
    // 이론 오탐률 (1 - e^(-kn/m))^k
    expectedFalsePositiveRate() {
      return (1 - Math.exp((-this.k * this.count) / this.m)) ** this.k;
    }
    toJSON() { return { m: this.m, k: this.k, count: this.count, bits: toBase64(this.bits) }; }
    static fromJSON(o) {
      const f = new BloomFilter(Math.max(1, o.count), 0.01, fromBase64(o.bits), o.m, o.k);
      f.count = o.count;
      return f;
    }
  }

  /* ───────────── Shingling + MinHash + LSH ───────────── */

  const SHINGLE = 3;

  // 문자 3-shingle을 32비트 해시 정수 집합으로 만든다 (문자열 집합보다 메모리가 작다).
  function shingles(text, limit = 1000) {
    const s = text.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim().slice(0, limit);
    const set = new Set();
    if (s.length === 0) return set;
    if (s.length <= SHINGLE) { set.add(hash32(s, 7)); return set; }
    for (let i = 0; i + SHINGLE <= s.length; i++) set.add(hash32(s.substr(i, SHINGLE), 7));
    return set;
  }

  function jaccard(a, b) {
    if (!a.size && !b.size) return 0;
    let small = a, large = b;
    if (a.size > b.size) { small = b; large = a; }
    let inter = 0;
    for (const x of small) if (large.has(x)) inter++;
    return inter / (a.size + b.size - inter);
  }

  class MinHashLSH {
    // 기본값: 서명 64개 = 16밴드 × 4행. 후보가 될 확률 P(s) = 1 - (1 - s^4)^16
    constructor({ limit = 800, bands = 16, rows = 4 } = {}) {
      this.limit = limit; this.bands = bands; this.rows = rows;
      this.numPerm = bands * rows;
      const rnd = mulberry32(20261104);
      this.a = new Uint32Array(this.numPerm).map(() => (Math.floor(rnd() * 0xffffffff) | 1) >>> 0);
      this.b = new Uint32Array(this.numPerm).map(() => Math.floor(rnd() * 0xffffffff) >>> 0);
      this.records = new Map();   // id -> {set, sig, text, account}
      this.buckets = new Map();   // "band:rows" -> Set(id)
      this.comparisons = 0;       // 실제 Jaccard를 계산한 횟수 (후보 수)
    }
    signature(set) {
      const sig = new Uint32Array(this.numPerm).fill(0xffffffff);
      for (const x of set) {
        for (let i = 0; i < this.numPerm; i++) {
          let v = (Math.imul(x, this.a[i]) + this.b[i]) | 0;
          v ^= v >>> 15; v = Math.imul(v, 0x2c1b3c6d); v ^= v >>> 12;
          v >>>= 0;
          if (v < sig[i]) sig[i] = v;
        }
      }
      return sig;
    }
    _keys(sig) {
      const keys = [];
      for (let band = 0; band < this.bands; band++) {
        let key = band + ':';
        for (let r = 0; r < this.rows; r++) key += sig[band * this.rows + r] + '.';
        keys.push(key);
      }
      return keys;
    }
    // 후보를 모은 뒤 실제 Jaccard로 다시 확인한다. 유사도 내림차순.
    query(set, sig, threshold = 0.6, max = 5) {
      if (!set.size) return [];
      const ids = new Set();
      for (const key of this._keys(sig)) {
        const bucket = this.buckets.get(key);
        if (bucket) for (const id of bucket) ids.add(id);
      }
      const out = [];
      for (const id of ids) {
        const rec = this.records.get(id);
        if (!rec) continue;
        this.comparisons++;
        const similarity = jaccard(set, rec.set);
        if (similarity >= threshold) out.push({ id, similarity, text: rec.text, account: rec.account });
      }
      out.sort((x, y) => y.similarity - x.similarity);
      return out.slice(0, max);
    }
    // 후보 id만 필요할 때 (벤치마크에서 후보 재현율을 잴 때 사용)
    candidates(sig) {
      const ids = new Set();
      for (const key of this._keys(sig)) {
        const bucket = this.buckets.get(key);
        if (bucket) for (const id of bucket) ids.add(id);
      }
      return ids;
    }
    add(id, set, sig, text = '', account = '') {
      if (!set.size || this.records.has(id)) return;
      this.records.set(id, { set, sig, text: text.slice(0, 200), account });
      for (const key of this._keys(sig)) {
        let bucket = this.buckets.get(key);
        if (!bucket) this.buckets.set(key, (bucket = new Set()));
        bucket.add(id);
      }
      while (this.records.size > this.limit) this.remove(this.records.keys().next().value);
    }
    remove(id) {
      const rec = this.records.get(id);
      if (!rec) return;
      this.records.delete(id);
      for (const key of this._keys(rec.sig)) {
        const bucket = this.buckets.get(key);
        if (bucket) { bucket.delete(id); if (!bucket.size) this.buckets.delete(key); }
      }
    }
    clear() { this.records.clear(); this.buckets.clear(); }
    get size() { return this.records.size; }
    // 이론 후보 확률 (문서화·평가용)
    static candidateProbability(s, bands = 16, rows = 4) { return 1 - (1 - s ** rows) ** bands; }
  }

  /* ───────────── DGIM ───────────── */

  class DGIM {
    // 최근 window개 비트 중 1의 개수를 O(log² window) 공간으로 추정한다.
    constructor(window = 100) { this.window = window; this.t = 0; this.buckets = []; } // 최신 → 오래된 순 {size, ts}
    add(bit) {
      this.t++;
      while (this.buckets.length && this.buckets[this.buckets.length - 1].ts <= this.t - this.window) this.buckets.pop();
      if (!bit) return;
      this.buckets.unshift({ size: 1, ts: this.t });
      for (let size = 1; ; size *= 2) {
        const first = this.buckets.findIndex(b => b.size === size);
        if (first < 0) break;
        let last = first;
        while (last + 1 < this.buckets.length && this.buckets[last + 1].size === size) last++;
        if (last - first + 1 <= 2) break;
        // 같은 크기 버킷이 3개 → 가장 오래된 두 개를 합친다 (타임스탬프는 더 최근 것)
        const merged = { size: size * 2, ts: this.buckets[last - 1].ts };
        this.buckets.splice(last - 1, 2, merged);
      }
    }
    estimate() {
      if (!this.buckets.length) return 0;
      const total = this.buckets.reduce((s, b) => s + b.size, 0);
      if (this.t <= this.window) return total; // 아직 창이 안 찼으면 모든 1이 창 안에 있어 정확하다
      return total - this.buckets[this.buckets.length - 1].size / 2;
    }
  }

  /* ───────────── Flajolet-Martin (PCSA) ───────────── */

  // 해시 하나의 최대 trailing zero만 쓰면 분산이 매우 크다. 입력을 m개 구간으로 나눠
  // 구간별 비트맵을 두고 "처음 0인 비트 위치" R의 평균으로 추정한다 (원 논문의 stochastic averaging).
  // n̂ = (m/φ)·2^(평균 R),  φ = 0.77351. 원소가 적을 때는 빈 구간 수로 보정(linear counting).
  class FlajoletMartin {
    constructor(buckets = 64) { this.m = buckets; this.maps = new Uint32Array(buckets); }
    add(value) {
      const bucket = hash32(value, 0x51ed270b) % this.m;
      const rank = Math.min(trailingZeros32(hash32(value, 0xa5a5a5a5)), 31);
      this.maps[bucket] |= 1 << rank;
    }
    estimate() {
      let sumR = 0, empty = 0;
      for (const map of this.maps) {
        if (map === 0) empty++;
        sumR += trailingZeros32(~map >>> 0);
      }
      if (empty === this.m) return 0;
      const raw = (this.m / 0.77351) * 2 ** (sumR / this.m);
      if (empty > 0 && raw <= 2.5 * this.m) return Math.round(this.m * Math.log(this.m / empty));
      return Math.round(raw);
    }
  }

  /* ───────────── URL 처리 ───────────── */

  const INTERNAL_HOSTS = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be']);
  const SHORTENERS = new Set(['bit.ly', 'tinyurl.com', 'cutt.ly', 'rb.gy', 'is.gd', 'goo.gl', 'ow.ly', 'han.gl',
    'url.kr', 'vo.la', 'me2.do', 'buly.kr', 'tiny.one', 'shorturl.at', 'linktr.ee', 'beacons.ai', 't.ly', 'lrl.kr']);
  const MESSENGER_HOSTS = new Set(['t.me', 'telegram.me', 'wa.me', 'open.kakao.com', 'discord.gg', 'discord.com', 'line.me', 'chat.whatsapp.com']);
  const SUSPICIOUS_TLD = 'xyz|top|vip|site|club|click|link|win|bet|cc|tk|icu|monster|fun|live|shop|store|online|cfd|sbs';

  const URL_PATTERN = new RegExp(
    '(?:https?:\\/\\/|www\\.)[^\\s<>"\\u200b]+' +
    '|\\b(?:t\\.me|wa\\.me|open\\.kakao\\.com|discord\\.gg|bit\\.ly|tinyurl\\.com|cutt\\.ly|han\\.gl|url\\.kr|me2\\.do|linktr\\.ee|rb\\.gy|is\\.gd)\\/[^\\s<>"\\u200b]+' +
    '|\\b[a-z0-9-]{2,}\\.(?:' + SUSPICIOUS_TLD + ')\\b(?:\\/[^\\s<>"\\u200b]*)?', 'gi');

  // 정규화: 호스트 소문자, 기본 포트·fragment 제거, 유튜브 /redirect?q= 풀기. 경로·쿼리의 대소문자는 유지한다.
  function normalizeUrl(raw, depth = 0) {
    if (typeof raw !== 'string' || depth > 2) return null;
    raw = raw.trim().replace(/[)\]}>.,!?;:'"。，、」』]+$/u, '');
    if (!raw) return null;
    if (raw.startsWith('//')) raw = 'https:' + raw;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(raw)) raw = 'https://' + raw;
    let u;
    try { u = new URL(raw); } catch { return null; }
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    if (INTERNAL_HOSTS.has(u.hostname) && u.pathname === '/redirect') {
      const target = u.searchParams.get('q');
      if (target && /^https?:\/\//i.test(target)) return normalizeUrl(target, depth + 1);
    }
    u.hash = '';
    return u.href;
  }

  function hostOf(url) { try { return new URL(url).hostname; } catch { return ''; } }

  function extractUrls(text, links = []) {
    const raws = [...links, ...(text.match(URL_PATTERN) || [])];
    const out = [];
    for (const raw of raws) {
      const url = normalizeUrl(raw);
      if (url && !out.includes(url)) out.push(url);
      if (out.length >= 20) break;
    }
    return out;
  }

  // 같은 영상 타임스탬프, 해시태그, 멘션처럼 스팸 근거가 아닌 유튜브 내부 링크는 걸러낸다.
  function classifyUrls(urls, ownVideoId = '') {
    const info = { external: [], youtube: [], shorteners: [], messengers: [] };
    for (const url of urls) {
      let u; try { u = new URL(url); } catch { continue; }
      const host = u.hostname.replace(/^www\./, '');
      if (INTERNAL_HOSTS.has(u.hostname) || INTERNAL_HOSTS.has(host)) {
        const v = u.hostname === 'youtu.be' ? u.pathname.slice(1) : u.searchParams.get('v');
        const neutral = u.pathname.startsWith('/hashtag/') || u.pathname.startsWith('/@') ||
          (v && ownVideoId && v === ownVideoId) || (!v && u.pathname === '/');
        if (!neutral) info.youtube.push(url);
        continue;
      }
      info.external.push(url);
      if (SHORTENERS.has(host)) info.shorteners.push(url);
      if (MESSENGER_HOSTS.has(host)) info.messengers.push(url);
    }
    return info;
  }

  /* ───────────── 텍스트 정규화 ───────────── */

  function normalizeText(text) {
    return text.normalize('NFKC')
      .replace(/[​-‏‪-‮⁠﻿]/g, '')
      .toLowerCase()
      .replace(/(.)\1{3,}/gu, '$1$1$1')       // ㅋㅋㅋㅋㅋ… 같은 반복 축약
      .replace(/\s+/g, ' ').trim();
  }
  // 글자 사이를 띄우거나 기호로 끊어 쓴 한국어 우회("텔 레 그 램")를 잡기 위한 압축형
  const compactKo = norm => norm.replace(/[\s.\-_*·~,|/\\()[\]]+/g, '');

  /* ───────────── 규칙(신호) 정의 ─────────────
   * 각 신호는 가중치를 가지며, 합이 임계값을 넘으면 검토 필요/스팸 의심이 된다.
   * 키워드 하나로 판정하지 않고, 서로 다른 종류의 근거가 겹칠 때 점수가 크게 오르도록 설계했다.
   */
  const KO = (pattern, flags = '') => new RegExp(pattern, flags);
  const SIGNALS = {
    contact:      { weight: 1.5,  label: '외부 연락처(텔레그램·카톡 등) 유도',
      ko: KO('텔레그램|텔그|카카오톡|카톡|오픈채팅|오픈톡|위챗|디스코드|라인아이디|디엠|쪽지'),
      en: /\b(telegram|whatsapp|wechat|discord|dm me|dm us|pm me|message me|text me|contact me|add me)\b/ },
    money:        { weight: 1.0,  label: '수익·투자 홍보 표현',
      ko: KO('고수익|수익보장|원금보장|부업|재택알바|재택근무|수익인증|수익공개|리딩방|코인|비트코인|투자|돈벌|수익률|환급|무담보|월\\d+만원|일당'),
      en: /\b(make money|earn \$?\d+|passive income|crypto|bitcoin|forex|trading signals?|investment|invest(ing)? (now|with)|guaranteed (profit|returns?)|get rich|financial freedom|work from home|\$\d{2,}\s?(a|per) (day|week|month|hour))\b|[💰💵💸🤑]/u },
    cta:          { weight: 0.75, label: '클릭·문의 등 행동 유도',
      ko: KO('클릭|문의|입장|참여|연락|상담|신청|가입|방문|들어오세요|추천인|초대'),
      en: /\b(click|join|contact|visit|sign ?up|register|apply now|check (it )?out|link in (bio|profile|description)|follow me)\b/ },
    profileLink:  { weight: 1.5,  label: '"프로필 링크 확인" 유도',
      ko: KO('프로필.{0,6}(링크|확인|클릭|들어|참고)|소개란.{0,6}(링크|확인)|고정댓글.{0,6}(링크|확인)'),
      en: /\b(link (in|on) (my )?(bio|profile)|(my|the) (bio|profile) (link|for)|check my (bio|profile))\b/ },
    selfPromo:    { weight: 3.0,  label: '자기 채널·영상 홍보',
      ko: KO('맞구독|선구독|맞팔|내채널|제채널|채널.{0,4}놀러|구독.{0,4}(부탁|해주|하면|해요|할게)'),
      en: /\b(check out (my|our)|subscribe (to )?(my|me|our)|sub ?4 ?sub|(plz|please|pls) (sub|subscribe|follow)|my (new )?(channel|video|song|mixtape|playlist)|visit (my|our)|follow me|free (followers|robux|v-?bucks|gift ?cards?|iphone))\b/ },
    weakPromo:    { weight: 0.75, label: '"체크아웃" 류 영상 소개 표현',
      ko: null,
      en: /\bcheck (this|it) out\b|\bcheck out (this|the|these)\b|\bwatch (this|my)\b/ },
    phishing:     { weight: 2.0,  label: '계정·인증정보·당첨 사칭',
      ko: KO('비밀번호|계정(인증|확인|정지|복구)|지갑연결|본인인증|개인정보(입력|확인)|당첨|경품|이벤트당첨'),
      en: /\b(verify your (account|identity)|account (suspended|locked|verification)|wallet connect|seed phrase|you('ve| have)? (won|been selected)|claim your (prize|reward|gift)|congratulations.{0,30}(won|winner))\b/ },
    gambling:     { weight: 3.0,  label: '도박·성인 광고',
      ko: KO('토토|카지노|바카라|먹튀|배팅|베팅|사설사이트|성인방송|성인사이트|조건만남|야동|출장안마|출장마사지'),
      en: /\b(casino|betting|sportsbook|jackpot|porn|xxx|onlyfans|escort|nsfw)\b/ },
  };

  const LINK_WEIGHTS = { link: 0.5, shortener: 1.5, messenger: 2.0, riskyHost: 1.5, linkOnly: 1.5, multiLink: 0.75 };
  const TRUSTED_HOSTS = ['wikipedia.org', 'wikimedia.org', 'namu.wiki', 'github.com', 'news.naver.com', 'nytimes.com', 'bbc.com', 'imdb.com'];
  const trusted = host => TRUSTED_HOSTS.some(t => host === t || host.endsWith('.' + t));
  // 비정상 TLD, IP 주소 호스트, 퓨니코드(xn--), 사용자정보(@)가 들어간 주소는 위험 신호로 본다.
  const RISKY_TLD = new RegExp('\\.(?:' + SUSPICIOUS_TLD + ')$');
  function riskyHost(url) {
    let u; try { u = new URL(url); } catch { return false; }
    const h = u.hostname;
    return RISKY_TLD.test(h) || /^\d{1,3}(\.\d{1,3}){3}$/.test(h) || h.includes('xn--') || Boolean(u.username || u.password);
  }
  const LABELS = {
    link: '외부 링크 포함', shortener: '단축 URL·링크 모음 사용', messenger: '메신저 초대 링크 포함',
    riskyHost: '의심스러운 주소(비정상 도메인·IP·퓨니코드)', linkOnly: '링크만 있는 댓글', multiLink: '서로 다른 사이트 링크 여러 개',
    style: '이모지·대문자 과다', dupFlood: '같은 영상에 거의 같은 댓글이 여러 계정에서 반복',
    sameAccountDup: '같은 계정이 거의 같은 댓글을 반복', similarSpam: '스팸으로 확인된 댓글과 매우 유사',
    similarSpamWeak: '스팸으로 확인된 댓글과 비슷', blocklist: '등록된 악성 URL·도메인과 일치',
    combo: '서로 다른 스팸 근거가 함께 나타남',
  };
  const THRESHOLDS = {
    strict:  { review: 1.25, spam: 2.5 },
    normal:  { review: 1.5,  spam: 3.0 },
    lenient: { review: 2.0,  spam: 3.5 },
  };

  function textSignals(norm) {
    const compact = compactKo(norm);
    const hit = {};
    for (const [id, def] of Object.entries(SIGNALS)) {
      hit[id] = Boolean((def.ko && (def.ko.test(norm) || def.ko.test(compact))) || (def.en && def.en.test(norm)));
    }
    return hit;
  }

  function styleSignal(text) {
    const emoji = (text.match(/\p{Extended_Pictographic}/gu) || []).length;
    const letters = text.replace(/[^A-Za-z]/g, '');
    const upper = letters.replace(/[^A-Z]/g, '').length;
    return emoji >= 8 || (text.length >= 10 && emoji / text.length > 0.4) || (letters.length >= 20 && upper / letters.length > 0.7);
  }

  /*
   * 규칙 점수 계산 (상태 없음). 평가 스크립트가 규칙만 따로 측정할 수 있도록 분리했다.
   * 반환: { score, ids: [신호 id], reasons: [한국어 근거] }
   */
  function scoreRules(text, urls = [], ownVideoId = '', linkTexts = []) {
    const norm = normalizeText(text);
    const hit = textSignals(norm);
    const info = classifyUrls(urls, ownVideoId);
    const hasLink = info.external.length + info.youtube.length > 0;
    const parts = [];  // [id, weight]
    for (const id of Object.keys(SIGNALS)) if (hit[id]) parts.push([id, SIGNALS[id].weight]);
    if (info.messengers.length) parts.push(['messenger', LINK_WEIGHTS.messenger]);
    if (info.shorteners.length) parts.push(['shortener', LINK_WEIGHTS.shortener]);
    if (hasLink) parts.push(['link', LINK_WEIGHTS.link]);
    if (info.external.some(riskyHost)) parts.push(['riskyHost', LINK_WEIGHTS.riskyHost]);
    const untrusted = info.external.filter(u => !trusted(hostOf(u)));
    if (new Set(untrusted.map(hostOf)).size >= 2) parts.push(['multiLink', LINK_WEIGHTS.multiLink]);
    if (untrusted.length || info.youtube.length) {
      // 링크 주소와 링크 글자를 뺀 나머지 글이 거의 없으면 "링크만 있는 댓글"
      let rest = text;
      for (const t of [...linkTexts, ...(text.match(URL_PATTERN) || [])]) if (t && t.length > 2) rest = rest.split(t).join(' ');
      rest = rest.replace(/[\s\p{P}\p{S}…]+/gu, '');
      if (rest.length < 15 && untrusted.length) parts.push(['linkOnly', LINK_WEIGHTS.linkOnly]);
    }
    if (styleSignal(text)) parts.push(['style', 0.75]);

    const has = id => parts.some(p => p[0] === id);
    let combo = 0;
    const promo = has('money') || has('contact') || has('phishing') || has('selfPromo') || has('weakPromo');
    if (has('contact') && has('money')) combo += 1.5;
    if (has('contact') && has('cta')) combo += 0.5;
    if (has('money') && has('cta')) combo += 0.5;
    if (promo && (has('shortener') || has('messenger'))) combo += 0.5;
    if (has('phishing') && hasLink) combo += 1.0;
    if (combo) parts.push(['combo', combo]);

    const score = parts.reduce((s, p) => s + p[1], 0);
    const ids = parts.map(p => p[0]);
    const reasons = parts.sort((x, y) => y[1] - x[1]).map(([id]) => (SIGNALS[id] || { label: LABELS[id] }).label || LABELS[id]);
    return { score, ids, reasons, info };
  }

  /* ───────────── 엔진 ───────────── */

  class Engine {
    /*
     * options.bloom       : 악성 URL용 BloomFilter (없으면 차단 목록 미사용)
     * options.confirm     : async (keys) => 일치한 key | null. Bloom 양성일 때만 호출(정확 조회)
     * options.learned     : {spam:[text], ham:[text]} 사용자가 직접 확인한 참조 댓글
     * options.sensitivity : 'strict' | 'normal' | 'lenient'
     */
    constructor(options = {}) {
      this.bloom = options.bloom || null;
      this.confirm = options.confirm || null;
      this.sensitivity = options.sensitivity || 'normal';
      this.window = options.window || 100;
      this.spamRefs = new MinHashLSH({ limit: 400 });   // 정답 라벨이 확인된 스팸만 (예측 전파 금지)
      this.hamRefs = new MinHashLSH({ limit: 400 });
      this.learnedSpam = []; this.learnedHam = [];
      for (const t of (options.learned && options.learned.spam) || []) this.addReference('spam', t);
      for (const t of (options.learned && options.learned.ham) || []) this.addReference('ham', t);
      this.newSession();
    }

    newSession() {
      this.seen = new MinHashLSH({ limit: 800 });
      this.dgim = new DGIM(this.window);
      this.fmUrls = new FlajoletMartin(); this.fmAccounts = new FlajoletMartin();
      this.exactUrls = new Set(); this.exactAccounts = new Set();
      this.recentBits = [];
      this.counts = { total: 0, spam: 0, review: 0, safe: 0, errors: 0, bloomPositive: 0, exactLookups: 0, exactHits: 0 };
      this.history = [];
      this.sessionSpam = new Map(); // 이번 영상에서 독립 근거로 확정한 스팸 id → 참조 인덱스 id
    }

    addReference(kind, text) {
      const index = kind === 'spam' ? this.spamRefs : this.hamRefs;
      const set = shingles(text);
      if (!set.size) return;
      index.add(kind + ':' + (this._refCounter = (this._refCounter || 0) + 1), set, index.signature(set), text);
      const list = kind === 'spam' ? this.learnedSpam : this.learnedHam;
      list.push(text.slice(0, 300));
      while (list.length > 200) list.shift();
    }
    exportLearned() { return { spam: this.learnedSpam.slice(-200), ham: this.learnedHam.slice(-200) }; }

    async analyze(comment) {
      const started = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const text = String(comment.text || '').slice(0, 1500);
      const account = comment.account || '';
      const urls = extractUrls(text, comment.links || []);
      const rules = scoreRules(text, urls, comment.video || '', comment.linkTexts || []);
      const th = THRESHOLDS[this.sensitivity] || THRESHOLDS.normal;
      let { score } = rules;
      const ids = rules.ids.slice(), reasons = rules.reasons.slice();
      let source = 'rules', hard = false;

      // 1) 악성 URL: Bloom Filter로 후보를 고르고, 양성일 때만 정확 조회로 확인
      if (this.bloom && urls.length) {
        const keys = [];
        for (const url of urls) { keys.push(url); keys.push('domain:' + hostOf(url)); }
        const positives = keys.filter(k => this.bloom.has(k));
        if (positives.length) {
          this.counts.bloomPositive++;
          if (this.confirm) {
            this.counts.exactLookups++;
            let matched = null;
            try { matched = await this.confirm(positives); } catch { /* 조회 실패는 근거 없음으로 처리 */ }
            if (matched) { hard = true; source = 'blocklist'; ids.push('blocklist'); reasons.unshift(LABELS.blocklist); this.counts.exactHits++; }
          }
        }
      }

      // 2) 유사 댓글 (MinHash+LSH)
      const set = shingles(text);
      const sig = set.size ? this.seen.signature(set) : null;
      let simContribution = 0, hamMatch = false;
      if (sig && !hard) {
        const spamMatch = this.spamRefs.query(set, sig, 0.6)[0];   // 같은 시그니처 함수라 인덱스 간 재사용 가능
        const hamHit = this.hamRefs.query(set, sig, 0.85)[0];
        if (hamHit) hamMatch = true;
        if (spamMatch && !hamMatch) {
          if (spamMatch.similarity >= 0.8) { simContribution = 3.0; ids.push('similarSpam'); reasons.push(LABELS.similarSpam); }
          else { simContribution = 1.0; ids.push('similarSpamWeak'); reasons.push(LABELS.similarSpamWeak); }
        }
        // 이 영상에서 반복되는 복붙 댓글
        if (norm20(text)) {
          const near = this.seen.query(set, sig, 0.8);
          const sameAccount = account && near.some(m => m.account === account);
          const otherAccounts = new Set(near.filter(m => m.account !== account).map(m => m.account || m.id));
          if (sameAccount) { simContribution += 2.5; ids.push('sameAccountDup'); reasons.push(LABELS.sameAccountDup); }
          else if (otherAccounts.size >= 2) { simContribution += 2.0; ids.push('dupFlood'); reasons.push(LABELS.dupFlood); }
        }
      }
      score += simContribution;

      let verdict = hard ? 'spam' : score >= th.spam ? 'spam' : score >= th.review ? 'review' : 'safe';
      if (hamMatch && !hard) { verdict = 'safe'; source = 'user-ham'; }
      else if (simContribution && verdict !== 'safe' && source === 'rules' && rules.score < th.review) source = 'similarity';

      // 2.5) 선택 기능: 애매한 댓글(review)만 외부 AI 검토. 실패·미사용이면 규칙 판정을 그대로 둔다.
      if (verdict === 'review' && typeof comment.refine === 'function') {
        let refined = null;
        try { refined = await comment.refine({ text, urls, reasons: dedupe(reasons), score }); } catch { /* 규칙 판정 유지 */ }
        if (refined && (refined.verdict === 'spam' || refined.verdict === 'safe')) {
          verdict = refined.verdict; source = 'ai';
          reasons.unshift('AI 검토: ' + String(refined.reason || '').slice(0, 120));
        }
      }

      // 3) 상태 갱신. 스팸 참조는 "유사도 말고 독립적인 근거"로 스팸이 된 댓글만 추가해 오판 전파를 막는다.
      const independent = (hard || rules.score >= th.spam) && source !== 'ai';
      if (sig) {
        this.seen.add(comment.id, set, sig, text, account);
        if (independent && verdict === 'spam') {
          const refId = 'session:' + comment.id;
          this.spamRefs.add(refId, set, sig, text);
          this.sessionSpam.set(comment.id, refId);
        }
      }
      this._record(verdict, urls, account, rules.info);

      const finished = typeof performance !== 'undefined' ? performance.now() : Date.now();
      return { id: comment.id, verdict, score: Math.round(score * 100) / 100, reasons: dedupe(reasons), signals: ids,
        source, urls, latencyMs: Math.round((finished - started) * 100) / 100 };
    }

    // 사용자 피드백: 스팸이 아니라고 표시 → 이후 비슷한 댓글은 표시하지 않음
    markHam(commentId, text) {
      this.addReference('ham', text);
      const ref = this.sessionSpam.get(commentId);
      if (ref) { this.spamRefs.remove(ref); this.sessionSpam.delete(commentId); }
    }
    markSpam(commentId, text) { this.addReference('spam', text); }

    _record(verdict, urls, account, info) {
      const c = this.counts;
      c.total++; c[verdict]++;
      this.dgim.add(verdict === 'spam' ? 1 : 0);
      this.recentBits.push(verdict === 'spam' ? 1 : 0);
      if (this.recentBits.length > this.window) this.recentBits.shift();
      if (verdict === 'spam') {
        for (const url of [...info.external, ...info.youtube]) {
          this.fmUrls.add(url);
          if (this.exactUrls.size < 5000) this.exactUrls.add(url);
        }
        if (account) {
          this.fmAccounts.add(account);
          if (this.exactAccounts.size < 5000) this.exactAccounts.add(account);
        }
      }
      this.history.push(verdict === 'spam' ? 2 : verdict === 'review' ? 1 : 0);
      if (this.history.length > 60) this.history.shift();
    }

    stats() {
      const c = this.counts;
      return {
        ...c, window: Math.min(c.total, this.window),
        recentSpamEstimate: this.dgim.estimate(), recentSpamExact: this.recentBits.reduce((a, b) => a + b, 0),
        spamUrlsEstimate: this.fmUrls.estimate(), spamUrlsExact: this.exactUrls.size,
        spamAccountsEstimate: this.fmAccounts.estimate(), spamAccountsExact: this.exactAccounts.size,
        lshRecords: this.seen.size, lshComparisons: this.seen.comparisons, history: this.history.slice(),
        blocklistSize: this.bloom ? this.bloom.count : 0,
      };
    }
  }

  const norm20 = text => normalizeText(text).length >= 20;
  const dedupe = list => [...new Set(list)];

  const CG = {
    hash32, mulberry32, BloomFilter, shingles, jaccard, MinHashLSH, DGIM, FlajoletMartin,
    normalizeUrl, extractUrls, classifyUrls, hostOf, normalizeText, scoreRules, Engine,
    SIGNALS, THRESHOLDS, LABELS, toBase64, fromBase64,
  };
  root.CG = CG;
  if (typeof module === 'object' && module.exports) module.exports = CG;
})(typeof globalThis !== 'undefined' ? globalThis : this);
