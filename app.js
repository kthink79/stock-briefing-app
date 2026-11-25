/* Stock Briefing App v3.0 — 실제 시세·뉴스 연동 엔진 */
/* ============================================================================
   Stock Briefing App — 실시간 데이터 엔진 (v3.0)
   ----------------------------------------------------------------------------
   · 시세 : GitHub Actions가 10분 간격으로 수집한 data/market_latest.json
            (원천: 네이버 금융 실시간 polling API)
   · 뉴스 : data/news_latest.json (원천: Google News RSS) + 키워드 실시간 조회
   · 원칙 : 실제 조회에 실패하면 "실패/스냅샷" 상태를 그대로 표시하고,
            임의의 가짜 시세·기사로 채우지 않는다.
   ========================================================================== */

const APP_VERSION = 'v3.4 (매수·매도 시점 분석)';
const MARKET_PATH = 'data/market_latest.json';
const NEWS_PATH = 'data/news_latest.json';
const REMOTE_BASE = 'https://kthink79.github.io/stock-briefing-app/';
const CACHE_KEY = 'stock_live_cache_v3';
const TRACE_STORAGE_VERSION = 'v7.3_20261123_save';
const STALE_MS = 15 * 60 * 1000;      // 15분 지난 데이터는 '스냅샷'으로 표시
const MARKET_WATCH_MS = 5 * 60 * 1000; // 5분마다 시세 파일 재확인
const TRACE_REFRESH_MS = 30 * 60 * 1000;
const NEWS_FRESH_MS = 25 * 60 * 1000;   // 서버 뉴스가 25분 이내면 실시간 재조회 생략
const UNIVERSE_PATH = 'data/stock_universe.json';
const UNIVERSE_CACHE_KEY = 'stock_universe_cache_v1';
const UNIVERSE_TTL_MS = 12 * 60 * 1000;  // 전종목 인덱스 재확인 주기(12분)
const COMPANY_NEWS_PATH = 'data/company_news.json';
const COMPANY_NEWS_CACHE_KEY = 'stock_company_news_cache_v1';
const COMPANY_NEWS_TTL_MS = 12 * 60 * 1000;
const NAME_CACHE_KEY = 'stock_company_names_v1';   // 종목코드 → 한글 종목명 (전종목 목록을 못 받아도 이름을 보여주기 위한 캐시)

const LIVE = {
  market: null,
  news: null,
  marketAt: null,
  newsAt: null,
  marketSource: '',
  newsSource: '',
  state: 'idle',        // idle | live | snapshot | fail
  busy: false,
  errors: [],
  lastSyncAt: 0,
  universe: { payload: null, index: [], map: {}, at: 0, loading: null, error: null },
  companyNews: { map: {}, at: 0, loading: null }
};

/* ------------------------------------------------------------------ utils */
function pad2(n) { return String(n).padStart(2, '0'); }

function parseDateSafe(v) {
  if (!v) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d;
}

function fmtClock(iso) {
  const d = parseDateSafe(iso);
  if (!d) return '—';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function fmtStamp(iso) {
  const d = parseDateSafe(iso);
  if (!d) return '—';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const datePart = sameDay ? '오늘' : `${d.getMonth() + 1}/${d.getDate()}`;
  return `${datePart} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

function fmtFullStamp(iso) {
  const d = parseDateSafe(iso);
  if (!d) return '—';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())} KST`;
}

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function fmtNum(v, digits) {
  if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) return '—';
  const d = digits === undefined ? 2 : digits;
  return Number(v).toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtPct(p) {
  if (p === null || p === undefined || (typeof p === 'number' && isNaN(p))) return '—';
  return `${p > 0 ? '+' : ''}${Number(p).toFixed(2)}%`;
}

function escapeHtml(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function truncate(s, n) {
  const t = String(s || '');
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

function marketStatusKo(status) {
  const map = {
    OPEN: '장중', CLOSE: '마감', PREOPEN: '장전', PRE_OPEN: '장전',
    AFTER: '시간외', AFTERMARKET: '시간외', CLOSED: '휴장', PRE: '장전'
  };
  return map[String(status || '').toUpperCase()] || (status || '');
}

function isStale(iso) {
  const d = parseDateSafe(iso);
  if (!d) return true;
  return (Date.now() - d.getTime()) > STALE_MS;
}

/* --------------------------------------------------------------- network */
function fetchJSON(url, ms) {
  const timeout = ms || 9000;
  return new Promise(function (resolve, reject) {
    let settled = false;
    const ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      if (ctrl) { try { ctrl.abort(); } catch (e) {} }
      reject(new Error('timeout'));
    }, timeout);
    fetch(url, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (json) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(json);
      })
      .catch(function (err) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      });
  });
}

function dataUrls(path) {
  const stamp = 't=' + Date.now();
  const local = path + (path.indexOf('?') === -1 ? '?' : '&') + stamp;
  const remote = REMOTE_BASE + path + '?' + stamp;
  if (typeof location !== 'undefined' && location.protocol === 'file:') {
    return [remote, local];
  }
  return [local, remote];
}

function readCache() {
  try { return JSON.parse(localStorage.getItem(CACHE_KEY) || '{}') || {}; } catch (e) { return {}; }
}

function writeCache(kind, payload) {
  try {
    const cache = readCache();
    cache[kind] = { savedAt: new Date().toISOString(), payload: payload };
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
  } catch (e) { /* 용량 초과 등은 무시 */ }
}

/* ==========================================================================
   시세 데이터
   ========================================================================== */
async function loadMarketData(opts) {
  opts = opts || {};
  const urls = dataUrls(MARKET_PATH);
  for (let i = 0; i < urls.length; i++) {
    try {
      const payload = await fetchJSON(urls[i], 9000);
      if (payload && payload.stocks && payload.market) {
        LIVE.market = payload;
        LIVE.marketAt = payload.generated_at;
        LIVE.marketSource = payload.source || (i === 0 ? '앱 저장소' : '원격 저장소');
        LIVE.errors = (payload.market && payload.market.errors) || [];
        LIVE.state = isStale(payload.generated_at) ? 'snapshot' : 'live';
        writeCache('market', payload);
        applyMarketData(payload, opts);
        return payload;
      }
    } catch (e) {
      LIVE.errors.push('시세 파일 조회 실패: ' + (e && e.message ? e.message : e));
    }
  }

  // 2차: 브라우저에서 직접 조회(제공처가 CORS를 허용할 때만 성공)
  const direct = await tryDirectQuotes();
  if (direct) {
    LIVE.market = direct;
    LIVE.marketAt = direct.generated_at;
    LIVE.marketSource = direct.source;
    LIVE.state = 'live';
    writeCache('market', direct);
    applyMarketData(direct, opts);
    return direct;
  }

  // 3차: 기기에 저장된 마지막 데이터 (기준 시각 그대로 표시)
  const cached = readCache().market;
  if (cached && cached.payload) {
    LIVE.market = cached.payload;
    LIVE.marketAt = cached.payload.generated_at;
    LIVE.marketSource = '기기에 저장된 마지막 데이터';
    LIVE.state = 'snapshot';
    applyMarketData(cached.payload, Object.assign({}, opts, { cached: true }));
    return cached.payload;
  }

  LIVE.state = 'fail';
  renderLiveStatus();
  return null;
}

/* Yahoo Finance 차트 API 직접 조회 (CORS 허용 시 실시간) */
const YAHOO_SYMBOLS = {
  '005930': '005930.KS', '000660': '000660.KS', '012450': '012450.KS',
  '005380': '005380.KS', '035420': '035420.KS', '042700': '042700.KS',
  '034020': '034020.KS', '035720': '035720.KS', '068270': '068270.KS',
  'NVDA': 'NVDA', 'TSLA': 'TSLA', 'AAPL': 'AAPL', 'AMD': 'AMD',
  'AVGO': 'AVGO', 'MU': 'MU'
};

async function tryDirectQuotes() {
  const codes = (userCompanyList || []).filter(function (c) { return YAHOO_SYMBOLS[c]; }).slice(0, 5);
  if (!codes.length) return null;
  const quotes = await Promise.all(codes.map(async function (code) {
    try {
      const url = 'https://query1.finance.yahoo.com/v8/finance/chart/' +
        encodeURIComponent(YAHOO_SYMBOLS[code]) + '?range=1d&interval=5m&t=' + Date.now();
      const json = await fetchJSON(url, 7000);
      const result = json && json.chart && json.chart.result && json.chart.result[0];
      if (!result || !result.meta) return null;
      const meta = result.meta;
      const price = meta.regularMarketPrice;
      const prev = meta.chartPreviousClose || meta.previousClose;
      if (!price || !prev) return null;
      return {
        code: code,
        name: code,
        price: price,
        prev_close: prev,
        change: price - prev,
        pct: ((price - prev) / prev) * 100,
        currency: meta.currency || (/^[0-9]{6}$/.test(code) ? 'KRW' : 'USD'),
        as_of: meta.regularMarketTime ? new Date(meta.regularMarketTime * 1000).toISOString() : new Date().toISOString(),
        market_status: meta.marketState || null
      };
    } catch (e) { return null; }
  }));
  const ok = quotes.filter(Boolean);
  if (!ok.length) return null;
  const payload = {
    generated_at: new Date().toISOString(),
    source: 'Yahoo Finance 직접 조회',
    stocks: {},
    market: { indices: {}, rates: {}, fx: {} }
  };
  ok.forEach(function (q) { payload.stocks[q.code] = q; });
  return payload;
}

function formatPriceValue(v, currency) {
  if (v === null || v === undefined) return '—';
  if (String(currency || '').toUpperCase() === 'USD') return '$' + fmtNum(v, 2);
  return fmtNum(v, 0) + ' 원';
}

function formatChangeText(q, currency) {
  if (!q || q.pct === null || q.pct === undefined) return '—';
  const sign = q.pct > 0 ? '+' : '';
  let amount = '';
  if (q.change !== null && q.change !== undefined) {
    if (String(currency || '').toUpperCase() === 'USD') {
      amount = ' (' + (q.change >= 0 ? '+' : '-') + '$' + fmtNum(Math.abs(q.change), 2) + ')';
    } else {
      amount = ' (' + (q.change >= 0 ? '+' : '-') + fmtNum(Math.abs(q.change), 0) + '원)';
    }
  }
  return sign + Number(q.pct).toFixed(2) + '%' + amount;
}

function formatCap(v, currency) {
  if (!v && v !== 0) return '—';
  if (String(currency || '').toUpperCase() === 'USD') {
    const t = v / 1e12;
    if (t >= 0.01) return '$' + fmtNum(t, 2) + 'T';
    return '$' + fmtNum(v / 1e9, 1) + 'B';
  }
  const jo = v / 1e12;
  if (jo >= 0.1) return fmtNum(jo, 1) + '조원';
  return fmtNum(v / 1e8, 0) + '억원';
}

function applyQuoteToCompany(code, q) {
  if (!q) return;
  let comp = companiesDB[code];
  if (!comp) {
    if (!predefinedCompaniesDB[code]) return;
    comp = JSON.parse(JSON.stringify(predefinedCompaniesDB[code]));
    companiesDB[code] = comp;
  }
  comp.currency = q.currency || comp.currency;
  comp.livePrice = q.price;
  comp.prevClose = q.prev_close;
  comp.price = formatPriceValue(q.price, comp.currency);
  comp.change = formatChangeText(q, comp.currency);
  comp.isUp = (q.pct || 0) >= 0;
  comp.marketStatus = q.market_status || null;
  comp.asOf = q.as_of || null;
  comp.extended = q.extended || null;
  comp.stale = !!q.stale;
  comp.basePrice = q.price;
  if (q.market_value) comp.cap = formatCap(q.market_value, comp.currency);
  if (comp.eps > 0 && q.price) comp.per = fmtNum(q.price / comp.eps, 2) + ' 배';
  if (comp.bps > 0 && q.price) comp.pbr = fmtNum(q.price / comp.bps, 2) + ' 배';

  comp.comment = '실시간 ' + comp.price + ' (' + comp.change + ') 기준 PER ' + comp.per +
    ' · PBR ' + comp.pbr + ' · 시총 ' + comp.cap;
  if (comp.quarter && comp.revOp) {
    comp.geminiEval = '[' + comp.name + ' 펀더멘털] ' + comp.quarter + ' — ' + comp.revOp + '. ' +
      '현재가 ' + comp.price + ' (' + comp.change + ') 기준 PER ' + comp.per +
      ', PBR ' + comp.pbr + ', 시총 ' + comp.cap + '. ' +
      '(실적 수치는 공시 기준, 주가·밸류에이션은 실시간 재계산)';
  }
}

function setRateMetric(id, quote) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!quote || quote.price === null || quote.price === undefined) {
    el.textContent = '—';
    el.className = 'metric-val';
    return;
  }
  const bp = (quote.bp === null || quote.bp === undefined) ? '' : ' (' + (quote.bp > 0 ? '+' : '') + Number(quote.bp).toFixed(2) + 'bp)';
  el.textContent = fmtNum(quote.price, 3) + '%' + bp;
  const pct = quote.pct || 0;
  el.className = 'metric-val ' + (pct > 0 ? 'up' : (pct < 0 ? 'down' : ''));
}

/* 기준금리(정책금리): 값 + 반영 기준일 + 출처. 실패 시 마지막 성공값 유지(⚠) */
function fmtPolicyDate(iso) {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return String(iso);
  return m[1] + '.' + m[2] + '.' + m[3];
}

function renderPolicyRate(valId, asofId, entry, kind) {
  const el = document.getElementById(valId);
  const sub = document.getElementById(asofId);
  if (!el) return;
  entry = entry || {};
  let valueText = null;
  if (kind === 'US') {
    if (entry.low !== null && entry.low !== undefined &&
        entry.high !== null && entry.high !== undefined) {
      valueText = fmtNum(entry.low, 2) + ' ~ ' + fmtNum(entry.high, 2) + '%';
    }
  } else if (entry.rate !== null && entry.rate !== undefined) {
    valueText = fmtNum(entry.rate, 2) + '%';
  }
  const stale = !!entry.stale || entry.status === 'stale';
  if (!valueText) {
    el.textContent = '—';
    el.className = 'metric-val';
    if (sub) {
      sub.textContent = entry.error ? '공식 발표값 조회 실패' : '기준금리 데이터 없음';
      sub.className = 'metric-sub warn';
    }
    return;
  }
  el.textContent = valueText;
  el.className = 'metric-val';
  if (!sub) return;
  const asof = fmtPolicyDate(entry.as_of);
  if (stale) {
    sub.textContent = '⚠ 직전 확인값' + (asof ? ' · 기준 ' + asof : '') + ' (최신 조회 실패)';
    sub.className = 'metric-sub warn';
  } else {
    sub.textContent = (asof ? '기준일 ' + asof : '기준일 미상') + (entry.effr !== null && entry.effr !== undefined ? ' · EFFR ' + fmtNum(entry.effr, 2) + '%' : '');
    sub.className = 'metric-sub';
  }
}

function renderPolicyRates(policyRates) {
  policyRates = policyRates || {};
  renderPolicyRate('policy-us-val', 'policy-us-asof', policyRates.US, 'US');
  renderPolicyRate('policy-kr-val', 'policy-kr-asof', policyRates.KR, 'KR');
}

function setMetric(id, quote, digits) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!quote || quote.price === null || quote.price === undefined) {
    el.textContent = '—';
    el.className = 'metric-val';
    return;
  }
  el.textContent = fmtNum(quote.price, digits) + ' (' + fmtPct(quote.pct) + ')';
  const pct = quote.pct || 0;
  el.className = 'metric-val ' + (pct > 0 ? 'up' : (pct < 0 ? 'down' : ''));
}

function applyMarketData(payload, opts) {
  opts = opts || {};
  const market = payload.market || {};
  const indices = market.indices || {};
  const rates = market.rates || {};
  const fx = market.fx || {};

  setMetric('sox-val', indices.SOX, 2);
  setMetric('nasdaq-val', indices.NASDAQ, 2);
  setMetric('sp500-val', indices.SP500, 2);
  setMetric('dow-val', indices.DOW, 2);
  setMetric('kospi-val', indices.KOSPI, 2);
  setMetric('kosdaq-val', indices.KOSDAQ, 2);
  setRateMetric('rate-10y-val', rates.US10Y);
  setRateMetric('rate-2y-val', rates.US2Y);
  renderPolicyRates(market.policy_rates);
  setMetric('fx-val', fx.USDKRW, 2);

  const stocks = payload.stocks || {};
  Object.keys(stocks).forEach(function (code) { applyQuoteToCompany(code, stocks[code]); });
  applyUniverseQuotes();

  if (payload.briefing) applyBriefing(payload.briefing);

  renderLiveStatus();
  renderCompanyTags();
  if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, !!opts.flash);
}

function applyBriefing(briefing) {
  // '실시간 시장 체크포인트' 한 장으로만 보여준다.
  // (예전의 '실시간 시황 요약' 카드는 같은 체크포인트를 한 번 더 나열해 중복이었으므로 삭제)
  const cpEl = document.getElementById('briefing-checkpoints');
  if (cpEl && Array.isArray(briefing.checkpoints)) {
    cpEl.innerHTML = briefing.checkpoints.map(function (c) {
      return '<li><strong>' + escapeHtml(c.title) + ':</strong> ' + escapeHtml(c.detail) + '</li>';
    }).join('') || '<li>체크포인트를 생성할 데이터가 부족합니다.</li>';
  }

  const sectorEl = document.getElementById('briefing-sector-news');
  if (sectorEl && Array.isArray(briefing.sector_news)) {
    sectorEl.innerHTML = briefing.sector_news.map(function (s) {
      return '<li>[' + escapeHtml(s.tag) + '] <a href="' + escapeHtml(s.url) + '" target="_blank" rel="noopener" ' +
        'style="color:#c9d1d9; text-decoration:none;">' + escapeHtml(s.text) + '</a> ' +
        '<span style="color:#8b949e; font-size:0.75rem;">(' + escapeHtml(s.source) + ' · ' + escapeHtml(s.date) + ')</span></li>';
    }).join('') || '<li>수집된 섹터 기사가 없습니다.</li>';
  }
}

function renderLiveStatus() {
  const chip = document.getElementById('live-status');
  const statusEl = document.getElementById('data-source-status');
  const stamp = LIVE.marketAt ? fmtFullStamp(LIVE.marketAt) : '—';
  let color = '#8b949e';
  let text = '🔄 실시간 데이터 연결 중…';
  if (LIVE.state === 'live') {
    color = '#3fb950';
    text = '🟢 실시간 데이터 · 기준 ' + stamp;
  } else if (LIVE.state === 'snapshot') {
    color = '#d29922';
    text = '🟡 저장된 스냅샷 · 기준 ' + stamp;
  } else if (LIVE.state === 'fail') {
    color = '#f85149';
    text = '🔴 실시간 조회 실패 · 네트워크를 확인해 주세요';
  }
  if (chip) { chip.textContent = text; chip.style.color = color; }

  const last = document.getElementById('last-sync-time');
  if (last) {
    if (LIVE.marketAt) {
      last.textContent = '최근 갱신: ' + fmtFullStamp(LIVE.marketAt) +
        (LIVE.state === 'snapshot' ? ' (오프라인 스냅샷)' : '');
    } else {
      last.textContent = '최근 갱신: —';
    }
  }
  const base = document.getElementById('briefing-base-time');
  if (base && LIVE.marketAt) {
    base.textContent = '데이터 기준 ' + fmtFullStamp(LIVE.marketAt) + ' · ' + (LIVE.marketSource || '');
  }

  if (statusEl) {
    const errs = (LIVE.errors || []).slice(0, 3);
    statusEl.innerHTML =
      '<div>· 시세 기준 시각: <strong style="color:#f0f6fc;">' + stamp + '</strong> (' +
      escapeHtml(LIVE.marketSource || '—') + ')</div>' +
      '<div>· 뉴스 기준 시각: <strong style="color:#f0f6fc;">' +
      (LIVE.newsAt ? fmtFullStamp(LIVE.newsAt) : '—') + '</strong> (' + escapeHtml(LIVE.newsSource || '—') + ')</div>' +
      (function () {
        const save = LIVE.news && LIVE.news.save;
        if (!save || save.status === 'disabled') return '';
        const age = saveAgeLabel(save);
        const label = save.status === 'unavailable' ? '연동 대기' : age.text;
        const color = save.status === 'unavailable' ? '#8b949e' : age.color;
        return '<div>· SAVE 연동: <strong style="color:' + color + ';">' + escapeHtml(label) + '</strong> · 기사 ' +
          (save.count || 0) + '건' + (save.fetched_at ? ' (기준 ' + escapeHtml(fmtFullStamp(save.fetched_at)) + ')' : '') + '</div>';
      })() +
      (errs.length ? '<div style="color:#d29922; margin-top:4px;">· 수집 경고: ' + escapeHtml(errs.join(' / ')) + '</div>' : '');
  }
}

/* ==========================================================================
   뉴스 데이터
   ========================================================================== */
async function loadNewsData(opts) {
  const urls = dataUrls(NEWS_PATH);
  for (let i = 0; i < urls.length; i++) {
    try {
      const payload = await fetchJSON(urls[i], 9000);
      if (payload && payload.keywords) {
        LIVE.news = payload;
        LIVE.newsAt = payload.generated_at;
        LIVE.newsSource = payload.source || (i === 0 ? '앱 저장소' : '원격 저장소');
        writeCache('news', payload);
        return payload;
      }
    } catch (e) { /* 다음 경로 */ }
  }
  const cached = readCache().news;
  if (cached && cached.payload) {
    LIVE.news = cached.payload;
    LIVE.newsAt = cached.payload.generated_at;
    LIVE.newsSource = '기기에 저장된 마지막 뉴스';
    return cached.payload;
  }
  return null;
}

function articleRelevant(article, keyword) {
  const tk = normalizeKeyword(keyword);
  const ak = normalizeKeyword(article && article.keyword ? article.keyword : '');
  if (!tk) return true;
  if (!ak) return true;
  return ak.indexOf(tk) !== -1 || tk.indexOf(ak) !== -1;
}

function normalizeKeyword(k) {
  return String(k || '').replace(/[^0-9A-Za-z가-힣]/g, '').toLowerCase();
}

/* 서버가 미리 수집한 기사 중 해당 키워드/기업 기사 찾기 */
function snapshotArticlesFor(keyword) {
  const news = LIVE.news;
  if (!news) return [];
  const target = normalizeKeyword(keyword);
  const buckets = [];
  const keywordMap = news.keywords || {};
  Object.keys(keywordMap).forEach(function (k) {
    if (normalizeKeyword(k) === target) buckets.push(keywordMap[k]);
  });
  const pool = news.pool || {};
  Object.keys(pool).forEach(function (k) {
    if (normalizeKeyword(k) === target) buckets.push(pool[k]);
  });
  let out = [];
  buckets.forEach(function (b) { if (b && Array.isArray(b.articles)) out = out.concat(b.articles); });
  // SAVE 연동 기사: 같은 키워드로 태깅된 항목도 함께 보여준다(서버 병합 상한 밖의 기사 포함)
  saveNewsList().forEach(function (a) {
    const hit = (a.keywords || []).some(function (k) { return normalizeKeyword(k) === target; });
    if (hit) out.push(Object.assign({}, a, { keyword: keyword }));
  });
  return normalizeArticles(out, keyword);
}

function companyArticlesFor(code, name) {
  const news = LIVE.news;
  if (!news) return [];
  const entry = (news.companies || {})[code];
  let out = [];
  if (entry && Array.isArray(entry.articles)) out = out.concat(entry.articles);
  if (name) {
    const nk = normalizeKeyword(name);
    const all = [];
    Object.keys(news.keywords || {}).forEach(function (k) { all.push(news.keywords[k]); });
    Object.keys(news.pool || {}).forEach(function (k) { all.push(news.pool[k]); });
    all.forEach(function (b) {
      (b.articles || []).forEach(function (a) {
        if (normalizeKeyword(a.title).indexOf(nk) !== -1) out.push(a);
      });
    });
  }
  // SAVE 연동 기사: 해당 종목으로 태깅된 기사(예: $NVDA, $005930.KR)
  saveNewsList().forEach(function (a) {
    if ((a.companies || []).indexOf(code) !== -1) {
      out.push(Object.assign({}, a, { keyword: name || code }));
    }
  });
  return normalizeArticles(out, name || code);
}

function normalizeArticle(raw, fallbackKeyword) {
  if (!raw) return null;
  const title = String(raw.title || '').trim();
  if (!title) return null;
  const published = raw.published_at || null;
  const d = parseDateSafe(published);
  const date = raw.date || (d ? `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` : '');
  return {
    keyword: raw.keyword || fallbackKeyword || '',
    title: title,
    source: raw.source || '뉴스',
    url: raw.url || raw.link || '',
    date: date,
    time: raw.time || (d ? pad2(d.getHours()) + ':' + pad2(d.getMinutes()) : ''),
    published_at: published || (d ? d.toISOString() : (date ? date + 'T00:00:00+09:00' : new Date().toISOString())),
    origin: raw.origin || '',
    badge: raw.badge || (raw.origin === 'SAVE' ? 'SAVE' : ''),
    summary: raw.summary || '',
    labels: Array.isArray(raw.labels) ? raw.labels : []
  };
}

function normalizeArticles(list, keyword) {
  const out = [];
  const seen = {};
  (list || []).forEach(function (raw) {
    const a = normalizeArticle(raw, keyword);
    if (!a) return;
    const key = normalizeKeyword(a.title).slice(0, 40);
    if (!key || seen[key]) return;
    seen[key] = true;
    out.push(a);
  });
  out.sort(function (a, b) { return String(b.published_at).localeCompare(String(a.published_at)); });
  return out;
}

function mergeArticles(lists) {
  const merged = [];
  lists.forEach(function (l) {
    if (Array.isArray(l)) l.forEach(function (a) { merged.push(a); });
  });
  return normalizeArticles(merged, '');
}

/* ==========================================================================
   SAVE(세이브티커) 연동 — saveticker.com
   data/news_latest.json 의 save 블록(수집기가 병합)을 읽어 배지·전용 목록에 쓴다.
   ========================================================================== */
function savePayload() {
  const news = LIVE.news;
  return (news && news.save) ? news.save : null;
}

function saveNewsList() {
  // SAVE 기사 목록: 서버가 키워드·기업 버킷에 병합해 둔 origin='SAVE' 기사를 모은다
  // (news.save.news 가 있으면 함께 포함). 같은 제목은 한 번만.
  const news = LIVE.news;
  if (!news) return [];
  const out = [];
  const seen = {};
  const push = function (a) {
    if (!a || a.origin !== 'SAVE') return;
    const key = normalizeKeyword(a.title).slice(0, 40);
    if (!key || seen[key]) return;
    seen[key] = true;
    out.push(a);
  };
  Object.keys(news.keywords || {}).forEach(function (k) {
    ((news.keywords[k] || {}).articles || []).forEach(push);
  });
  Object.keys(news.companies || {}).forEach(function (c) {
    ((news.companies[c] || {}).articles || []).forEach(push);
  });
  if (news.save && Array.isArray(news.save.news)) news.save.news.forEach(push);
  return out;
}

function saveAgeLabel(save) {
  // SAVE 데이터 신선도를 '몇 분/몇 시간 전'으로 보여준다(상태 문자열보다 실제 경과가 정확하다)
  const at = save && save.fetched_at ? Date.parse(save.fetched_at) : NaN;
  if (isNaN(at)) return { text: '시각 정보 없음', color: '#8b949e', stale: true };
  const mins = Math.max(0, Math.round((Date.now() - at) / 60000));
  if (mins <= 90) return { text: mins < 1 ? '방금 수집' : '최신 · ' + mins + '분 전', color: '#3fb950', stale: false };
  const hours = Math.round(mins / 60);
  return { text: '스냅샷 · ' + hours + '시간 전', color: hours <= 24 ? '#d29922' : '#8b949e', stale: true };
}

function saveBadgeHtml(badge, small) {
  const label = badge || 'SAVE';
  const size = small ? '0.62rem; padding:1px 5px;' : '0.66rem; padding:2px 6px;';
  const bg = label.indexOf('PICK') !== -1 ? '#1f6feb' : (label.indexOf('로이터') !== -1 ? '#8b5cf6' : '#238636');
  return '<span class="save-badge" style="font-size:' + size + ' color:#fff; background:' + bg +
    '; font-weight:700; border-radius:4px; letter-spacing:0.02em; white-space:nowrap;">' +
    escapeHtml(label) + '</span>';
}

function saveArticleLink(a) {
  return '<a href="' + escapeHtml(a.url || '#') + '" target="_blank" rel="noopener" style="color:#c9d1d9; text-decoration:none;">' +
    escapeHtml(a.title) + '</a>';
}

/* 키워드 실시간 조회: Google News RSS → rss2json (브라우저 CORS 우회 경로) */
async function fetchLiveKeywordNews(keyword) {
  const feed = 'https://news.google.com/rss/search?q=' + encodeURIComponent(keyword) + '&hl=ko&gl=KR&ceid=KR:ko';
  const url = 'https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(feed) + '&count=30';
  try {
    const json = await fetchJSON(url, 12000);
    if (!json || json.status !== 'ok' || !Array.isArray(json.items)) return [];
    const articles = json.items.map(function (item) {
      let title = item.title || '';
      const src = item.author || (item.source && item.source.name) || '';
      if (src && title.endsWith(' - ' + src)) title = title.slice(0, -(src.length + 3));
      const d = parseDateSafe(item.pubDate ? String(item.pubDate).replace(' ', 'T') + 'Z' : null);
      return {
        keyword: keyword,
        title: title,
        source: src || 'Google News',
        url: item.link || '',
        date: d ? `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` : '',
        time: d ? pad2(d.getHours()) + ':' + pad2(d.getMinutes()) : '',
        published_at: d ? d.toISOString() : null
      };
    }).filter(function (a) { return a.published_at; });
    const cutoff = Date.now() - 10 * 86400000;
    return articles.filter(function (a) { return Date.parse(a.published_at) >= cutoff; });
  } catch (e) {
    return [];
  }
}

/* ==========================================================================
   키워드 트레이스 엔진 (실제 기사 기반 7일·3일 요약 + 최신 타임라인)
   ========================================================================== */
const DEFAULT_TRACE_KEYWORDS = [
  { id: 'trace_nvda', keyword: '엔비디아', registered: '2026-11-06 08:00' },
  { id: 'trace_hbm', keyword: 'HBM', registered: '2026-11-06 08:00' },
  { id: 'trace_iran', keyword: '미국 이란 전쟁', registered: '2026-11-06 08:00' },
  { id: 'trace_trump', keyword: '트럼프', registered: '2026-11-06 08:00' }
];

let currentTraces = [];

function normTitle(t) {
  return String(t || '').replace(/\[[^\]]{1,12}\]|\([^)]{1,12}\)/g, ' ').replace(/\s+/g, ' ').trim();
}

function titleTokens(t) {
  const set = {};
  normTitle(t).split(/[^0-9A-Za-z가-힣]+/).forEach(function (w) {
    if (w.length >= 2) set[w.toLowerCase()] = true;
  });
  return set;
}

function bigramMap(text) {
  const map = {};
  for (let i = 0; i < text.length - 1; i++) {
    const g = text.substr(i, 2);
    map[g] = (map[g] || 0) + 1;
  }
  return map;
}

/* 같은 사건을 다룬 기사인지 판단: 부분 포함 + 문자 2-gram Dice 유사도 */
function similarTitles(a, b) {
  if (!a || !b) return false;
  const ka = normalizeKeyword(a);
  const kb = normalizeKeyword(b);
  if (!ka || !kb) return false;
  if (ka === kb) return true;
  const shortK = ka.length <= kb.length ? ka : kb;
  const longK = ka.length <= kb.length ? kb : ka;
  if (shortK.length >= 10 && longK.indexOf(shortK) !== -1) return true;

  const ga = bigramMap(ka);
  const gb = bigramMap(kb);
  let inter = 0;
  let total = 0;
  Object.keys(ga).forEach(function (g) { total += ga[g]; if (gb[g]) inter += Math.min(ga[g], gb[g]); });
  Object.keys(gb).forEach(function (g) { total += gb[g]; });
  if (!total) return false;
  const dice = (2 * inter) / total;
  const minLen = Math.min(ka.length, kb.length);
  return minLen < 12 ? dice >= 0.72 : dice >= 0.5;
}

function buildTraceClusters(articles, maxClusters) {
  const limit = maxClusters || 8;
  const sorted = (articles || []).slice().sort(function (a, b) {
    return String(b.published_at).localeCompare(String(a.published_at));
  });
  const clusters = [];
  sorted.forEach(function (art) {
    let hit = null;
    for (let i = 0; i < clusters.length; i++) {
      if (similarTitles(clusters[i].title, art.title)) { hit = clusters[i]; break; }
    }
    if (hit) {
      if (hit.items.length < 4) hit.items.push(art);
      if (hit.sources.indexOf(art.source) === -1) hit.sources.push(art.source);
      return;
    }
    clusters.push({ title: art.title, items: [art], sources: [art.source], url: art.url });
  });
  return clusters.slice(0, limit);
}

function clusterToEvent(cluster) {
  const first = cluster.items[0];
  const impact = (cluster.items.length >= 3 || cluster.sources.length >= 3) ? 'High' : 'Medium';
  const saveCount = cluster.items.filter(function (i) { return i.origin === 'SAVE'; }).length;
  return {
    date: first.date,
    time: first.time,
    title: first.title,
    source: cluster.sources.slice(0, 3).join(' · ') +
      (cluster.sources.length > 3 ? ' 외 ' + (cluster.sources.length - 3) + '곳' : ''),
    impact: impact,
    saveCount: saveCount,
    summary: cluster.items.slice(0, 3).map(function (i) {
      return i.title + ' (' + i.source + ', ' + i.date + ')';
    }),
    marketImpact: '동일 이슈 ' + cluster.items.length + '건 · 보도 매체 ' + cluster.sources.length + '곳',
    url: cluster.url,
    relatedSources: cluster.sources.slice(0, 5)
  };
}

function rangeLabel(days) {
  const now = new Date();
  const from = new Date(now.getTime() - (days - 1) * 86400000);
  return `${from.getMonth() + 1}.${pad2(from.getDate())} ~ ${now.getMonth() + 1}.${pad2(now.getDate())}`;
}

const TICKER_HINTS = [
  ['삼성전자', '삼성전자'], ['SK하이닉스', 'SK하이닉스'], ['하이닉스', 'SK하이닉스'],
  ['엔비디아', '엔비디아'], ['마이크론', '마이크론'], ['한화에어로', '한화에어로'],
  ['현대차', '현대차'], ['네이버', 'NAVER'], ['카카오', '카카오'], ['셀트리온', '셀트리온'],
  ['두산에너빌리티', '두산에너빌'], ['한미반도체', '한미반도체'], ['테슬라', '테슬라'],
  ['애플', '애플'], ['브로드컴', '브로드컴'], ['TSMC', 'TSMC'], ['AMD', 'AMD'],
  ['코스피', '코스피'], ['코스닥', '코스닥']
];

function detectTickers(text) {
  const src = String(text || '');
  const found = [];
  TICKER_HINTS.forEach(function (pair) {
    if (src.indexOf(pair[0]) !== -1 && found.indexOf(pair[1]) === -1) found.push(pair[1]);
  });
  return found.slice(0, 5).join(', ');
}

function articlesWithin(articles, days) {
  const cutoff = Date.now() - days * 86400000;
  return (articles || []).filter(function (a) {
    const t = Date.parse(a.published_at || a.date || '');
    return !isNaN(t) && t >= cutoff;
  });
}

function buildTraceSummary(articles, days, keyword) {
  const list = articlesWithin(articles, days);
  const clusters = buildTraceClusters(list, 5);
  const sources = {};
  list.forEach(function (a) { sources[a.source] = true; });
  const sourceCount = Object.keys(sources).length;
  if (!list.length) {
    return { text: '', count: 0, sources: 0, clusters: [], range: rangeLabel(days) };
  }
  const ranked = clusters.slice().sort(function (a, b) {
    return (b.items.length - a.items.length) ||
      String(b.items[0].published_at).localeCompare(String(a.items[0].published_at));
  });
  const top = ranked[0];
  const text = `최근 ${days}일간(${rangeLabel(days)}) '${keyword}' 관련 기사 ${list.length}건이 ${sourceCount}개 매체에서 보도되었습니다. ` +
    (top ? `가장 많이 다뤄진 이슈는 "${truncate(normTitle(top.title), 70)}"(${top.items.length}건, ${top.sources.length}곳)입니다.` : '');
  return { text: text, count: list.length, sources: sourceCount, clusters: clusters, ranked: ranked, range: rangeLabel(days) };
}

function rebuildTrace(trace) {
  const articles = Array.isArray(trace.articles) ? trace.articles : [];
  const s7 = buildTraceSummary(articles, 7, trace.keyword);
  const s3 = buildTraceSummary(articles, 3, trace.keyword);

  trace.range7d = s7.range;
  trace.range3d = s3.range;
  trace.summary7d = s7.text || `최근 7일간 '${trace.keyword}' 관련 기사가 수집되지 않았습니다. (실시간 뉴스 소스에서 검색 결과 없음)`;
  trace.summary3d = s3.text || `최근 3일간 '${trace.keyword}' 관련 신규 기사가 없습니다.`;

  if (s7.clusters.length) {
    const titles = s7.ranked.slice(0, 3).map(function (c) {
      return '"' + truncate(normTitle(c.title), 48) + '"(' + c.items.length + '건)';
    });
    trace.weeklyKeyMetrics = {
      turningPoint: '최다 보도 이슈: ' + truncate(normTitle(s7.ranked[0].title), 70),
      marketFlow: `보도 ${s7.count}건 · 매체 ${s7.sources}곳 / 주요 흐름: ` + titles.join(', ')
    };
  } else {
    trace.weeklyKeyMetrics = null;
  }

  if (s3.clusters.length) {
    const allTitles = articlesWithin(articles, 3).map(function (a) { return a.title; }).join(' ');
    trace.momentum3d = {
      catalyst: truncate(normTitle(s3.ranked[0].title), 70),
      impactTickers: detectTickers(allTitles) || '관련 종목 언급 없음'
    };
  } else {
    trace.momentum3d = null;
  }

  trace.events = buildTraceClusters(articles, 8).map(clusterToEvent);
  trace.eventCount = trace.events.length;
  trace.sourceCount = s7.sources;
  trace.articleCount = articles.length;
  trace.updatedAt = new Date().toISOString();
  return trace;
}

async function hydrateTrace(trace, force) {
  const fromSnapshot = snapshotArticlesFor(trace.keyword).filter(function (a) {
    return articleRelevant(a, trace.keyword);
  });
  const existing = (Array.isArray(trace.articles) ? trace.articles : []).filter(function (a) {
    return articleRelevant(a, trace.keyword);
  });
  const age = trace.articlesAt ? (Date.now() - Date.parse(trace.articlesAt)) : Infinity;
  const snapAge = LIVE.newsAt ? (Date.now() - Date.parse(LIVE.newsAt)) : Infinity;
  const hasFreshSnapshot = fromSnapshot.length > 0 && snapAge < NEWS_FRESH_MS;
  const needsFetch = force || (age > TRACE_REFRESH_MS && !hasFreshSnapshot);

  let fetched = [];
  if (needsFetch) {
    fetched = await fetchLiveKeywordNews(trace.keyword);
  }
  const merged = mergeArticles([fetched, fromSnapshot, existing]);
  if (merged.length) {
    trace.articles = merged.slice(0, 40);
    trace.articlesAt = new Date().toISOString();
    trace.liveFetched = fetched.length > 0;
  } else if (needsFetch) {
    trace.fetchFailed = true;
  }
  rebuildTrace(trace);
  return trace;
}

async function refreshTraces(opts) {
  opts = opts || {};
  for (let i = 0; i < currentTraces.length; i++) {
    await hydrateTrace(currentTraces[i], !!opts.force);
    if (i < currentTraces.length - 1) {
      renderTraces();
      await new Promise(function (r) { setTimeout(r, 350); });
    }
  }
  saveTracesToStorage();
  renderTraces();
}

function saveTracesToStorage() {
  try {
    localStorage.setItem('stock_traces_list', JSON.stringify(currentTraces));
    localStorage.setItem('stock_traces_version', TRACE_STORAGE_VERSION);
  } catch (e) { /* ignore */ }
}

function getDeletedDefaults() {
  try { return JSON.parse(localStorage.getItem('stock_traces_deleted') || '[]') || []; } catch (e) { return []; }
}

function markDefaultDeleted(id) {
  const list = getDeletedDefaults();
  if (list.indexOf(id) === -1) {
    list.push(id);
    try { localStorage.setItem('stock_traces_deleted', JSON.stringify(list)); } catch (e) {}
  }
}

function loadTracesFromStorage() {
  const deleted = getDeletedDefaults();
  // 저장 버전이 바뀌면(수집 규칙 개선 등) 누적 기사를 버리고 새로 수집한다.
  const versionChanged = localStorage.getItem('stock_traces_version') !== TRACE_STORAGE_VERSION;
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem('stock_traces_list') || '[]'); } catch (e) { saved = []; }
  if (!Array.isArray(saved)) saved = [];

  const byKeyword = {};
  saved.forEach(function (t) {
    if (t && t.keyword) byKeyword[normalizeKeyword(t.keyword)] = t;
  });

  const traces = [];
  DEFAULT_TRACE_KEYWORDS.forEach(function (def) {
    const old = byKeyword[normalizeKeyword(def.keyword)];
    if (old) delete byKeyword[normalizeKeyword(def.keyword)];
    if (deleted.indexOf(def.id) !== -1) return;
    traces.push({
      id: def.id,
      keyword: def.keyword,
      registered: (old && old.registered) || def.registered,
      articles: (!versionChanged && old && Array.isArray(old.articles)) ? old.articles : [],
      articlesAt: (!versionChanged && old && old.articlesAt) || null
    });
  });
  Object.keys(byKeyword).forEach(function (k) {
    const t = byKeyword[k];
    traces.push({
      id: t.id || ('trace_' + Date.now() + '_' + Math.floor(Math.random() * 1000)),
      keyword: t.keyword,
      registered: t.registered || (todayStr() + ' ' + fmtClock(new Date().toISOString())),
      articles: (!versionChanged && Array.isArray(t.articles)) ? t.articles : [],
      articlesAt: (!versionChanged && t.articlesAt) || null
    });
  });

  currentTraces = traces;
  localStorage.setItem('stock_traces_version', TRACE_STORAGE_VERSION);
}

function renderTraces() {
  const listEl = document.getElementById('trace-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  if (!currentTraces.length) {
    listEl.innerHTML = '<div class="card" style="text-align: center; color: var(--text-sub); padding: 30px;">' +
      '등록된 키워드 트레이스가 없습니다. 위 입력창에서 관심 키워드를 추가해 보세요.</div>';
    return;
  }

  currentTraces.forEach(function (t) {
    const item = document.createElement('div');
    item.className = 'card trace-item';

    const eventCount = (t.events || []).length;
    const articleCount = (t.articles || []).length;
    let html = '' +
      '<div class="trace-header" style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #30363d; padding-bottom: 8px; margin-bottom: 12px; flex-wrap: wrap; gap: 6px;">' +
        '<div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">' +
          '<span class="trace-keyword" style="font-size: 1.15rem; color: #58a6ff; font-weight: 800;"># ' + escapeHtml(t.keyword) + '</span>' +
          '<span class="trace-count" style="font-size: 0.72rem; background: #21262d; border: 1px solid #30363d; color: #8b949e; padding: 2px 8px; border-radius: 12px;">기사 ' + articleCount + '건 · 이슈 ' + eventCount + '개</span>' +
          '<span style="font-size: 0.7rem; color: #8b949e;">등록: ' + escapeHtml(t.registered || '') + '</span>' +
          (t.liveFetched ? '<span style="font-size: 0.68rem; color:#3fb950; background:rgba(63,185,80,0.12); padding:2px 6px; border-radius:4px;">실시간 조회</span>' : '') +
          (t.fetchFailed && !articleCount ? '<span style="font-size: 0.68rem; color:#d29922; background:rgba(210,153,34,0.12); padding:2px 6px; border-radius:4px;">실시간 조회 실패</span>' : '') +
        '</div>' +
        '<button class="btn btn-red" style="font-size: 0.72rem; padding: 4px 10px; border-radius: 4px; background: #da3633; border: none; color: #fff; cursor: pointer; font-weight: 700;" onclick="deleteTrace(\'' + t.id + '\')">✕ 삭제</button>' +
      '</div>';

    // 최근 7일 요약
    html += '' +
      '<div style="background: rgba(22, 27, 34, 0.85); border: 1px solid #1f6feb; border-radius: 8px; padding: 12px; margin-bottom: 10px;">' +
        '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid rgba(56, 139, 253, 0.3); padding-bottom: 6px; flex-wrap: wrap; gap: 4px;">' +
          '<span style="font-size: 0.85rem; font-weight: 700; color: #58a6ff;">📊 최근 7일간 요약 (' + (t.range7d || rangeLabel(7)) + ')</span>' +
          '<span style="font-size: 0.7rem; color: #58a6ff; background: rgba(56, 139, 253, 0.15); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(56, 139, 253, 0.3); font-weight: 600;">7-Day Flow</span>' +
        '</div>' +
        '<div style="font-size: 0.84rem; color: #e6edf3; line-height: 1.5; margin-bottom: 8px;">' + escapeHtml(t.summary7d || '') + '</div>' +
        (t.weeklyKeyMetrics ?
          '<div style="font-size: 0.78rem; color: #8b949e; background: rgba(13, 17, 23, 0.7); padding: 8px 10px; border-radius: 6px; border-left: 3px solid #58a6ff; display: flex; flex-direction: column; gap: 4px;">' +
            '<div><strong style="color: #79c0ff;">• 주간 최다 이슈:</strong> ' + escapeHtml(t.weeklyKeyMetrics.turningPoint) + '</div>' +
            '<div><strong style="color: #56d364;">• 7일 누적 흐름:</strong> ' + escapeHtml(t.weeklyKeyMetrics.marketFlow) + '</div>' +
          '</div>' : '') +
      '</div>';

    // 최근 3일 요약
    html += '' +
      '<div style="background: rgba(33, 38, 45, 0.85); border: 1px solid #d29922; border-radius: 8px; padding: 12px; margin-bottom: 12px;">' +
        '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid rgba(227, 179, 65, 0.3); padding-bottom: 6px; flex-wrap: wrap; gap: 4px;">' +
          '<span style="font-size: 0.85rem; font-weight: 700; color: #e3b341;">⚡ 최근 3일간 요약 (' + (t.range3d || rangeLabel(3)) + ')</span>' +
          '<span style="font-size: 0.7rem; color: #e3b341; background: rgba(227, 179, 65, 0.15); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(227, 179, 65, 0.3); font-weight: 600;">3-Day Momentum</span>' +
        '</div>' +
        '<div style="font-size: 0.84rem; color: #e6edf3; line-height: 1.5; margin-bottom: 8px;">' + escapeHtml(t.summary3d || '') + '</div>' +
        (t.momentum3d ?
          '<div style="font-size: 0.78rem; color: #8b949e; background: rgba(13, 17, 23, 0.7); padding: 8px 10px; border-radius: 6px; border-left: 3px solid #e3b341; display: flex; flex-direction: column; gap: 4px;">' +
            '<div><strong style="color: #f2cc60;">• 3일 핵심 촉매:</strong> ' + escapeHtml(t.momentum3d.catalyst) + '</div>' +
            '<div><strong style="color: #bc8cff;">• 영향 종목/섹터:</strong> ' + escapeHtml(t.momentum3d.impactTickers) + '</div>' +
          '</div>' : '') +
      '</div>';

    // 최신 타임라인
    html += '' +
      '<div style="font-size: 0.84rem; font-weight: 700; color: #f0f6fc; margin: 14px 0 10px 0; display: flex; align-items: center; justify-content: space-between;">' +
        '<span>⏱️ 최근 내용 (실제 보도 기사 · 중복 이슈 묶음)</span>' +
        '<span style="font-size: 0.7rem; color: #3fb950; background: rgba(63, 185, 80, 0.15); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(63, 185, 80, 0.3);">✓ 최신순</span>' +
      '</div>' +
      '<div class="timeline" style="border-left: 2px solid #30363d; margin: 6px 0 0 8px; padding-left: 12px;">';

    if (!(t.events || []).length) {
      html += '<div style="font-size: 0.82rem; color: #8b949e; padding: 6px 0;">수집된 기사가 없습니다. ‘⚡ 실시간 트레이스 갱신’을 눌러 다시 조회할 수 있습니다.</div>';
    }

    (t.events || []).forEach(function (e) {
      const dateStr = (e.date || '') + (e.time ? ' ' + e.time : '');
      html += '' +
        '<div class="timeline-event" style="margin-bottom: 16px; position: relative;">' +
          '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap: wrap; gap: 4px;">' +
            '<div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">' +
              '<span class="timeline-date" style="font-size: 0.78rem; font-weight: 700; color: #58a6ff; background: rgba(88, 166, 255, 0.12); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(88, 166, 255, 0.25);">📅 ' + escapeHtml(dateStr) + '</span>' +
              '<span style="font-size: 0.72rem; color: #8b949e;">• ' + escapeHtml(e.source || '') + '</span>' +
              (e.saveCount ? saveBadgeHtml('SAVE', true) : '') +
            '</div>' +
            '<span class="badge" style="font-size: 0.68rem; padding: 2px 6px; border-radius: 10px; background: ' + (e.impact === 'High' ? '#da3633' : '#1f6feb') + '; color: #fff; font-weight: 600;">' +
              (e.impact === 'High' ? '🔴 High (다수 보도)' : '🟡 Medium') + '</span>' +
          '</div>' +
          '<div class="timeline-title" style="font-size: 0.92rem; font-weight: 700; color: #f0f6fc; margin-bottom: 6px; line-height: 1.4;">' +
            (e.url ? '<a href="' + escapeHtml(e.url) + '" target="_blank" rel="noopener" style="color:#f0f6fc; text-decoration:none;">' + escapeHtml(e.title) + '</a>' : escapeHtml(e.title)) +
          '</div>' +
          '<div style="background: rgba(22, 27, 34, 0.95); border: 1px solid #30363d; border-radius: 6px; padding: 10px; margin-top: 6px;">' +
            '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; border-bottom: 1px solid #21262d; padding-bottom: 4px; flex-wrap: wrap; gap: 4px;">' +
              '<span style="font-size: 0.76rem; color: #58a6ff; font-weight: 700;">📋 관련 보도 묶음</span>' +
              '<span style="font-size: 0.72rem; color: #e3b341; font-weight: 700; background: rgba(227, 179, 65, 0.15); padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(227, 179, 65, 0.3);">📅 ' + escapeHtml(e.date || '') + '</span>' +
            '</div>' +
            '<ul style="list-style: none; font-size: 0.82rem; color: #c9d1d9; line-height: 1.5; padding-left: 0; margin-bottom: 6px;">' +
              (e.summary || []).map(function (s) {
                return '<li style="position: relative; padding-left: 14px; margin-bottom: 4px;">' +
                  '<span style="position: absolute; left: 0; color: #58a6ff;">•</span>' + escapeHtml(s) + '</li>';
              }).join('') +
            '</ul>' +
            (e.marketImpact ? '<div style="font-size: 0.78rem; color: #8b949e; border-top: 1px dashed #30363d; padding-top: 5px; margin-top: 5px;">' +
              '<strong style="color: #e3b341;">💡 보도 규모:</strong> ' + escapeHtml(e.marketImpact) + '</div>' : '') +
          '</div>' +
        '</div>';
    });

    html += '</div>';
    item.innerHTML = html;
    listEl.appendChild(item);
  });
}

/* SAVE 속보·연동 카드 — 저장소에서 병합된 SAVE 기사와 경제 일정을 요약해 보여준다 */
function renderSaveStrip() {
  const el = document.getElementById('save-strip');
  if (!el) return;
  const save = savePayload();
  if (!save || save.status === 'disabled') {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  el.style.display = 'block';
  const status = save.status || 'unavailable';
  const age = saveAgeLabel(save);
  const meta = status === 'unavailable'
    ? { label: '연동 대기', color: '#8b949e', bg: 'rgba(139,148,158,0.12)' }
    : { label: age.text, color: age.color, bg: 'rgba(139,148,158,0.12)' };

  const top = (Array.isArray(save.top_stories) ? save.top_stories : []).slice(0, 4);
  const fallback = (Array.isArray(save.news) ? save.news : []).slice(0, 4);
  const items = top.length ? top : fallback;
  const events = (Array.isArray(save.calendar) ? save.calendar : []).slice(0, 3);

  let html = '' +
    '<div class="card" style="border:1px solid #30363d; padding:12px;">' +
      '<div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">' +
        '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">' +
          '<span style="font-weight:800; color:#f0f6fc; font-size:0.92rem;">🔗 SAVE 속보</span>' +
          saveBadgeHtml('SAVE PICK') +
          '<span style="font-size:0.7rem; color:' + meta.color + '; background:' + meta.bg +
            '; padding:2px 8px; border-radius:10px; border:1px solid ' + meta.color + '33;">' + escapeHtml(meta.label) + '</span>' +
          '<span style="font-size:0.7rem; color:#8b949e;">기사 ' + (save.count || 0) + '건</span>' +
        '</div>' +
        '<a href="https://www.saveticker.com/news" target="_blank" rel="noopener" style="font-size:0.72rem; color:#58a6ff; text-decoration:none;">saveticker.com ↗</a>' +
      '</div>' +
      '<div style="font-size:0.72rem; color:#8b949e; margin-bottom:8px;">' +
        (save.fetched_at ? '기준 ' + escapeHtml(fmtFullStamp(save.fetched_at)) : '수집 시각 정보 없음') +
        (save.fetched_via === 'browser-session' ? ' · SAVE 계정 세션에서 수집' : '') +
        (age.stale ? ' · 직전 수집본' : '') +
      '</div>';

  if (items.length) {
    html += '<ul style="list-style:none; padding:0; margin:0 0 8px 0; display:flex; flex-direction:column; gap:6px;">' +
      items.map(function (a) {
        return '<li style="display:flex; gap:6px; align-items:flex-start; line-height:1.4;">' +
          saveBadgeHtml(a.badge, true) +
          '<span style="flex:1; font-size:0.82rem;">' + saveArticleLink(a) +
          ' <span style="color:#8b949e; font-size:0.72rem;">(' + escapeHtml(a.date || '') + ' ' + escapeHtml(a.time || '') +
          (a.view_count ? ' · ' + Number(a.view_count).toLocaleString() + '읽음' : '') + ')</span></span></li>';
      }).join('') + '</ul>';
  } else {
    html += '<div style="font-size:0.8rem; color:#8b949e; margin-bottom:8px;">SAVE 기사를 불러오지 못했습니다 — 헤드라인은 아래 트레이스/기업 뉴스에 SAVE 배지로 표시됩니다.</div>';
  }

  if (events.length) {
    html += '<div style="border-top:1px dashed #30363d; padding-top:8px; margin-top:2px;">' +
      '<div style="font-size:0.74rem; color:#e3b341; font-weight:700; margin-bottom:4px;">📅 SAVE 경제 캘린더 (다음 일정)</div>' +
      '<ul style="list-style:none; padding:0; margin:0; display:flex; flex-direction:column; gap:3px;">' +
      events.map(function (e) {
        return '<li style="font-size:0.76rem; color:#c9d1d9;">' +
          '<span style="color:#58a6ff; font-weight:700;">' + escapeHtml((e.date || '').slice(5) + ' ' + (e.time || '')) + '</span> · ' +
          escapeHtml(e.title || '') +
          (e.note ? ' <span style="color:#8b949e;">(' + escapeHtml(truncate(e.note, 40)) + ')</span>' : '') + '</li>';
      }).join('') + '</ul></div>';
  }

  const interestLine = asArrayLikeJoin();
  if (interestLine) {
    html += '<div style="font-size:0.7rem; color:#8b949e; margin-top:8px;">SAVE 관심 설정: ' + escapeHtml(interestLine) + '</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

function asArrayLikeJoin() {
  const save = savePayload() || {};
  const lists = [];
  if (save.interests && Array.isArray(save.interests.tickers) && save.interests.tickers.length) {
    lists.push(save.interests.tickers.join(' · '));
  }
  if (save.interests && Array.isArray(save.interests.keywords) && save.interests.keywords.length) {
    lists.push(save.interests.keywords.join(' · '));
  }
  return lists.join(' / ');
}

async function refreshTracesManual() {
  const btnText = '⚡ 실시간 트레이스 갱신';
  showNotif('⚡ 실제 최신 기사를 조회하고 있습니다…');
  await refreshTraces({ force: true });
  const total = currentTraces.reduce(function (acc, t) { return acc + ((t.articles || []).length); }, 0);
  const fetched = currentTraces.filter(function (t) { return t.liveFetched; }).length;
  showNotif(`⚡ 트레이스 갱신 완료: 기사 ${total}건 · 실시간 직접 조회 ${fetched}/${currentTraces.length}개 키워드`);
  return btnText;
}

function addNewTrace() {
  const input = document.getElementById('trace-input');
  const val = input ? input.value.trim() : '';
  if (!val) return;
  if (currentTraces.some(function (t) { return normalizeKeyword(t.keyword) === normalizeKeyword(val); })) {
    showNotif(`'${val}' 키워드는 이미 등록되어 있습니다.`);
    return;
  }
  const trace = {
    id: 'trace_' + Date.now(),
    keyword: val,
    registered: todayStr() + ' ' + fmtClock(new Date().toISOString()),
    articles: [],
    articlesAt: null
  };
  currentTraces.unshift(trace);
  if (input) input.value = '';
  renderTraces();
  showNotif(`'${val}' 트레이스 등록 완료 — 실제 기사를 조회합니다…`);
  hydrateTrace(trace, true).then(function () {
    saveTracesToStorage();
    renderTraces();
    const n = (trace.articles || []).length;
    showNotif(n ? `'${val}' 관련 기사 ${n}건을 수집했습니다.` : `'${val}' 관련 기사를 찾지 못했습니다. (뉴스 소스 응답 없음)`);
  });
}

function deleteTrace(traceId) {
  const target = currentTraces.find(function (t) { return t.id === traceId; });
  const name = target ? target.keyword : '키워드';
  if (!confirm(`'${name}' 트레이스를 삭제하시겠습니까?`)) return;
  if (DEFAULT_TRACE_KEYWORDS.some(function (d) { return d.id === traceId; })) {
    markDefaultDeleted(traceId);
  }
  currentTraces = currentTraces.filter(function (t) { return t.id !== traceId; });
  saveTracesToStorage();
  renderTraces();
  showNotif(`'${name}' 트레이스가 삭제되었습니다.`);
}

/* ==========================================================================
   전종목 검색 인덱스 (data/stock_universe.json)
   ----------------------------------------------------------------------------
   GitHub Actions가 네이버 금융 전종목 시세(10분 주기)와 Daum 금융 재무지표(하루 1회)를
   수집해 같은 저장소의 JSON으로 배포합니다. 브라우저는 이 파일만 읽으므로
   CORS 제약 없이 코스피·코스닥 전 종목과 미국 주요 종목을 검색할 수 있습니다.
   행 형식 - 국내: [이름, 시장, 현재가, 등락률, 시가총액(억원), PER, PBR, EPS, BPS]
            해외: [이름, 거래소, 현재가, 등락률, 시가총액(USD), PER, PBR, EPS, BPS]
   ========================================================================== */
function normalizeSearchText(v) {
  return String(v === null || v === undefined ? '' : v).toLowerCase().replace(/\s+/g, '');
}

function universeRowToEntry(code, row, currency, asOf, marketStatus) {
  return {
    code: code,
    name: row[0] || code,
    market: row[1] || (currency === 'USD' ? 'US' : 'KRX'),
    cur: currency,
    price: (row[2] === null || row[2] === undefined) ? null : Number(row[2]),
    pct: (row[3] === null || row[3] === undefined) ? null : Number(row[3]),
    mcap: (row[4] === null || row[4] === undefined) ? null : Number(row[4]),
    per: (row[5] === null || row[5] === undefined) ? null : Number(row[5]),
    pbr: (row[6] === null || row[6] === undefined) ? null : Number(row[6]),
    eps: (row[7] === null || row[7] === undefined) ? null : Number(row[7]),
    bps: (row[8] === null || row[8] === undefined) ? null : Number(row[8]),
    asOf: asOf || null,
    status: marketStatus || null,
    q: normalizeSearchText(row[0] || code),
    cq: String(code).toLowerCase()
  };
}

function buildUniverseIndex(payload) {
  const index = [];
  const map = {};
  const asOf = payload.as_of || {};
  const status = payload.status || {};
  const kr = payload.kr || {};
  const us = payload.us || {};
  Object.keys(kr).forEach(function (code) {
    const e = universeRowToEntry(code, kr[code] || [], 'KRW', asOf.kr, status.kr);
    index.push(e);
    map[code] = e;
  });
  Object.keys(us).forEach(function (sym) {
    const e = universeRowToEntry(sym, us[sym] || [], 'USD', asOf.us, status.us);
    index.push(e);
    map[sym] = e;
  });
  return { index: index, map: map };
}

function readUniverseCache() {
  try {
    const raw = localStorage.getItem(UNIVERSE_CACHE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.payload) return null;
    return obj;
  } catch (e) { return null; }
}

function saveUniverseCache(payload) {
  try {
    localStorage.setItem(UNIVERSE_CACHE_KEY, JSON.stringify({ savedAt: Date.now(), payload: payload }));
  } catch (e) { /* 용량 초과는 무시 */ }
}

function applyUniverse(payload, opts) {
  if (!payload) return;
  const built = buildUniverseIndex(payload);
  LIVE.universe.payload = payload;
  LIVE.universe.index = built.index;
  LIVE.universe.map = built.map;
  LIVE.universe.at = Date.now();
  LIVE.universe.error = null;
  applyUniverseQuotes();
  if (opts && opts.renderSuggest) renderCompanySuggest(getCompanySearchValue());
}

async function loadUniverse(opts) {
  opts = opts || {};
  if (LIVE.universe.loading) return LIVE.universe.loading;

  const cached = readUniverseCache();
  const cacheAge = cached ? (Date.now() - (cached.savedAt || 0)) : Infinity;
  if (cached && (!LIVE.universe.index.length || cacheAge < UNIVERSE_TTL_MS)) {
    if (!LIVE.universe.index.length) applyUniverse(cached.payload);
    if (!opts.force && cacheAge < UNIVERSE_TTL_MS) return LIVE.universe.payload;
  }

  const urls = dataUrls(UNIVERSE_PATH);
  LIVE.universe.loading = (async function () {
    for (let i = 0; i < urls.length; i++) {
      try {
        // 파일이 10분마다 갱신되므로 10분 단위의 주소로만 새로 받아 CDN·브라우저 캐시를 활용한다
        const bucket = 'v=' + Math.floor(Date.now() / 600000);
        const url = urls[i] + (urls[i].indexOf('?') === -1 ? '?' : '&') + bucket;
        const payload = await fetchJSON(url, 12000);
        if (payload && payload.kr) {
          applyUniverse(payload, { renderSuggest: true });
          saveUniverseCache(payload);
          return payload;
        }
      } catch (e) { /* 다음 경로 시도 */ }
    }
    LIVE.universe.error = '전종목 목록을 불러오지 못했습니다.';
    if (!LIVE.universe.index.length && cached) applyUniverse(cached.payload, { renderSuggest: true });
    return LIVE.universe.payload;
  })();

  try {
    return await LIVE.universe.loading;
  } finally {
    LIVE.universe.loading = null;
  }
}

function universeLookup(code) {
  if (!code) return null;
  return (LIVE.universe.map || {})[code] || null;
}

/* ------------------------------------------------------------------ 종목명 복원
   관심기업의 이름은 저장된 레코드·전종목 목록·이름 캐시 중 어디서든 찾아서 보여준다.
   전종목 목록(stock_universe.json)을 받지 못한 상태나, 예전에 이름 없이(코드만)
   저장된 레코드도 항상 한글 종목명으로 복구되게 하는 것이 목적이다.
   ========================================================================== */
const NAME_CACHE_LIMIT = 400;      // 캐시(로컬 저장)에 쌓아둘 최대 종목 수
let nameCacheMemo = null;

function readNameCache() {
  if (nameCacheMemo) return nameCacheMemo;
  try {
    const raw = localStorage.getItem(NAME_CACHE_KEY);
    const obj = raw ? JSON.parse(raw) : null;
    nameCacheMemo = (obj && typeof obj === 'object') ? obj : {};
  } catch (e) {
    nameCacheMemo = {};
  }
  return nameCacheMemo;
}

function rememberName(code, name) {
  if (!code || !name) return;
  const key = String(code);
  const value = String(name).trim();
  if (!value || /^\d{6}$/.test(value) || value.toUpperCase() === key.toUpperCase()) return;
  const cache = readNameCache();
  if (cache[key] === value) return;
  cache[key] = value;
  const keys = Object.keys(cache);
  if (keys.length > NAME_CACHE_LIMIT) {
    // 오래 쌓인 항목부터 정리 (삽입 순서 유지)
    for (let i = 0; i < keys.length - NAME_CACHE_LIMIT; i++) delete cache[keys[i]];
  }
  try { localStorage.setItem(NAME_CACHE_KEY, JSON.stringify(cache)); } catch (e) { /* 용량 초과는 무시 */ }
}

/* 이름이 비었거나 '코드만' 남은 상태인지 (예: '006400', '종목(006400)') */
function isBareCodeName(name, code) {
  const n = String(name === null || name === undefined ? '' : name).trim();
  if (!n) return true;
  const c = String(code === null || code === undefined ? '' : code).trim();
  if (c && n.toUpperCase() === c.toUpperCase()) return true;
  if (/^종목\(.*\)$/.test(n)) return true;   // 예전에 만들어진 '종목(006400)' 빈 카드 이름
  if (/^\d{6}$/.test(n)) return true;        // 코드만 남은 이름
  return false;
}

/* 레코드·사전 DB·전종목 목록·이름 캐시 순으로 한글 종목명을 찾는다. */
function resolveCompanyName(code, comp) {
  const key = String(code || '');
  const pre = predefinedCompaniesDB[key];
  const uni = universeLookup(key);
  const cached = readNameCache()[key];
  const candidates = [
    comp && comp.name,
    pre && pre.name,
    uni && uni.name,
    cached
  ];
  for (let i = 0; i < candidates.length; i++) {
    const n = String(candidates[i] === null || candidates[i] === undefined ? '' : candidates[i]).trim();
    if (n && !isBareCodeName(n, key)) {
      rememberName(key, n);
      return n;
    }
  }
  return key;
}

/* 관심기업 레코드를 얻는다(없으면 만들고, 이름이 코드로 남아 있으면 복구한다). */
function healCompanyRecord(code) {
  if (!code) return null;
  let comp = companiesDB[code] || null;
  const uni = universeLookup(code);
  if (!comp) {
    if (predefinedCompaniesDB[code]) comp = JSON.parse(JSON.stringify(predefinedCompaniesDB[code]));
    else if (uni) comp = makeCompanyRecord(code);
    else {
      const cached = readNameCache()[code];
      if (!cached) return null;
      comp = makeCompanyRecord(code);   // 이름·통화만 남은 최소 레코드
      comp.name = cached;
    }
    companiesDB[code] = comp;
  }
  const resolved = resolveCompanyName(code, comp);
  if (resolved && comp.name !== resolved) comp.name = resolved;
  if (uni) {
    const liveStocks = (LIVE.market && LIVE.market.stocks) || {};
    if (!(liveStocks[code] && !comp.fromUniverse)) {
      if (comp.ticker !== uni.code) comp.ticker = uni.code;
      applyUniverseQuoteToCompany(comp, uni);
    }
  }
  if (isBareCodeName(comp.name, code) && !LIVE.universe.index.length) {
    // 전종목 목록이 없어 이름을 모르는 상태 — 목록을 받으면 이름을 채워 다시 그린다
    ensureNameRepair();
  }
  return comp;
}

let nameRepairing = false;
let nameRepairTried = false;
function registeredCompanyNamesBroken() {
  const list = userCompanyList || [];
  for (let i = 0; i < list.length; i++) {
    const comp = companiesDB[list[i]] || predefinedCompaniesDB[list[i]];
    if (!comp || isBareCodeName(comp.name, list[i])) return true;
  }
  return false;
}

function ensureNameRepair() {
  if (nameRepairing || nameRepairTried) return;   // 페이지 로드당 한 번만 시도(반복 조회 방지)
  nameRepairing = true;
  nameRepairTried = true;
  loadUniverse().then(function () {
    let changed = false;
    (userCompanyList || []).forEach(function (code) {
      const before = companiesDB[code] ? companiesDB[code].name : null;
      const comp = healCompanyRecord(code);
      if (comp && comp.name !== before) changed = true;
    });
    nameRepairing = false;
    if (changed) {
      renderCompanyTags();
      if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, false);
      saveCompaniesToStorage();
    }
  }).catch(function () { nameRepairing = false; });
}

/* ------------------------------------------------------------------ 기업별 뉴스 파일 */
function readCompanyNewsCache() {
  try {
    const raw = localStorage.getItem(COMPANY_NEWS_CACHE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.payload) return null;
    return obj;
  } catch (e) { return null; }
}

function applyCompanyNews(payload) {
  if (!payload) return;
  LIVE.companyNews.map = payload.companies || {};
  LIVE.companyNews.at = Date.now();
}

async function loadCompanyNews(opts) {
  opts = opts || {};
  if (LIVE.companyNews.loading) return LIVE.companyNews.loading;
  const cached = readCompanyNewsCache();
  const age = cached ? (Date.now() - (cached.savedAt || 0)) : Infinity;
  if (cached && !LIVE.companyNews.at) applyCompanyNews(cached.payload);
  if (cached && !opts.force && age < COMPANY_NEWS_TTL_MS) return LIVE.companyNews.map;

  const urls = dataUrls(COMPANY_NEWS_PATH);
  LIVE.companyNews.loading = (async function () {
    for (let i = 0; i < urls.length; i++) {
      try {
        const bucket = 'v=' + Math.floor(Date.now() / 600000);
        const url = urls[i] + (urls[i].indexOf('?') === -1 ? '?' : '&') + bucket;
        const payload = await fetchJSON(url, 10000);
        if (payload && payload.companies) {
          applyCompanyNews(payload);
          try { localStorage.setItem(COMPANY_NEWS_CACHE_KEY, JSON.stringify({ savedAt: Date.now(), payload: payload })); } catch (e) {}
          if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, false);
          return LIVE.companyNews.map;
        }
      } catch (e) { /* 다음 경로 */ }
    }
    return LIVE.companyNews.map;
  })();
  try {
    return await LIVE.companyNews.loading;
  } finally {
    LIVE.companyNews.loading = null;
  }
}

/* 한 종목의 최신 기사: 서버 수집분(기업 뉴스 파일) 우선 */
function companyNewsArticles(code, name) {
  const entry = (LIVE.companyNews.map || {})[code];
  if (entry && Array.isArray(entry.articles) && entry.articles.length) {
    return normalizeArticles(entry.articles, name || code);
  }
  return [];
}

/* 검색 우선순위: 코드/이름 정확 → 코드 접두 → 이름 접두 → 이름 포함 → 시가총액 순 */
function searchCompanies(query, limit) {
  const q = normalizeSearchText(query);
  if (!q) return [];
  const rows = (LIVE.universe && LIVE.universe.index) || [];
  const exact = [];
  const codePrefix = [];
  const namePrefix = [];
  const contains = [];
  for (let i = 0; i < rows.length; i++) {
    const e = rows[i];
    if (e.cq === q || e.q === q) { exact.push(e); continue; }
    if (e.cq.indexOf(q) === 0) { codePrefix.push(e); continue; }
    if (e.q.indexOf(q) === 0) { namePrefix.push(e); continue; }
    if (e.q.indexOf(q) > 0) contains.push(e);
  }
  const bySize = function (a, b) { return (b.mcap || 0) - (a.mcap || 0); };
  const out = exact.sort(bySize)
    .concat(codePrefix.sort(bySize), namePrefix.sort(bySize), contains.sort(bySize));
  return out.slice(0, limit || 10);
}

/* 등록된 관심 종목의 시세를 전종목 인덱스 값으로 맞춘다(market_latest에 없는 종목만) */
function applyUniverseQuotes() {
  const liveStocks = (LIVE.market && LIVE.market.stocks) || {};
  let touched = false;
  (userCompanyList || []).forEach(function (code) {
    const e = universeLookup(code);
    if (!e) return;
    if (!companiesDB[code]) {
      companiesDB[code] = makeCompanyRecord(code);
      touched = true;
    }
    const comp = companiesDB[code];
    const resolved = resolveCompanyName(code, comp);
    if (resolved && comp.name !== resolved) { comp.name = resolved; touched = true; }
    if (comp.ticker !== e.code) { comp.ticker = e.code; touched = true; }
    if (liveStocks[code] && !comp.fromUniverse) return;   // 시장 파일 시세가 더 최신
    applyUniverseQuoteToCompany(comp, e);
    touched = true;
  });
  if (touched) {
    renderCompanyTags();
    if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, false);
  }
}

/* ==========================================================================
   기업 분석 (관심 종목 등록·삭제 + 실시간 시세/밸류에이션)
   ========================================================================== */
    const predefinedCompaniesDB = {
      "005930": {
        name: "삼성전자",
        ticker: "005930",
        currency: "KRW",
        prevClose: 189900,
        basePrice: 198900,
        price: "198,900 KRW",
        change: "+4.74% (+9,000원)",
        isUp: true,
        per: "7.80 배",
        pbr: "2.05 배",
        div: "2.58%",
        eps: 25506,
        bps: 96875,
        cap: "1,187조원",
        intradayHigh: 201500,
        intradayLow: 195000,
        quarter: "2026 3Q 실적 (2026-10-30 공시 확정)",
        revOp: "매출 195.25조원 | 영업이익 107.44조원 (YoY +783%, 글로벌 분기 1위)",
        comment: "10일 코스피 4%대 급등 속 기관 1,749억원 순매수로 198,900원(+4.74%) 마감. 장중 201,500원 돌파하며 시총 합산 2천조원 회복. 20만전자 안착 테스트",
        geminiEval: "[삼성전자 펀더멘털 평가] 10일 코스피 급반등과 함께 기관의 대규모 순매수(1,749억)가 유입되며 198,900원에 마감했습니다. 3Q 분기 영업이익 107.4조원이라는 독보적 실적 대비 PER 7배 후반으로 여전히 극심한 저평가 구간에 머물러 있습니다.",
        news: [
          "[특징주] 삼성전자 4.74% 급등 마감... 19만8900원, 장중 20만원선 돌파 (연합뉴스 2026-11-10)",
          "외국인·기관 8,500억 동반 순매수... 삼성전자 시총 1,187조원 회복 (2026-11-10)",
          "엔비디아 AI 데이 서울 성료... 반도체 공정 옴니버스 디지털 트윈 본격화 (2026-11-10)"
        ]
      },
      "000660": {
        name: "SK하이닉스",
        ticker: "000660",
        currency: "KRW",
        prevClose: 1136000,
        basePrice: 1212000,
        price: "1,212,000 KRW",
        change: "+6.69% (+76,000원)",
        isUp: true,
        per: "5.40 배",
        pbr: "3.29 배",
        div: "2.71%",
        eps: 224313,
        bps: 368800,
        cap: "885.4조원",
        intradayHigh: 1242000,
        intradayLow: 1180000,
        quarter: "2026 3Q 실적 (2026-10-28 공시 확정)",
        revOp: "매출 104.62조원 (YoY +328%) | 영업이익 83.33조원 (OPM 79.7% 어닝 서프라이즈)",
        comment: "10일 외국인 1,069억원 순매수 유입 및 HBM 독점력에 힘입어 1,212,000원(+6.69%) 급등 마감, 3거래일 만에 '120만닉스' 탈환 성공",
        geminiEval: "[SK하이닉스 펀더멘털 평가] 외국인이 8거래일 만에 순매수 전환하며 1,069억원을 쓸어담아 121만2천원에 안착했습니다. 영업이익률 80%에 육박하는 HBM 어닝 파워와 자사주 매입 방어선이 주가 상단을 강력히 견인하고 있습니다.",
        news: [
          "[특징주] SK하이닉스 6.69% 급등... '120만닉스' 3거래일 만에 탈환 (연합뉴스 2026-11-10)",
          "외국인 1,069억원 순매수 폭풍 유입... 시총 885조원 회복 (머니투데이 2026-11-10)",
          "HBM4 사전 계약 단가 강세 지속 및 4분기 사상 최대 실적 전망 (2026-11-10)"
        ]
      },
      "012450": {
        name: "한화에어로스페이스",
        ticker: "012450",
        currency: "KRW",
        prevClose: 660000,
        basePrice: 655000,
        price: "655,000 KRW",
        change: "+0.92% (+6,000원)",
        isUp: true,
        per: "16.33 배",
        pbr: "2.42 배",
        div: "0.85%",
        eps: 40120,
        bps: 270800,
        cap: "33.8조원",
        intradayHigh: 668000,
        intradayLow: 642000,
        quarter: "2026 2Q/3Q 실적 요약",
        revOp: "2Q 매출 9.20조원 | 영업이익 1.37조원 (어닝 서프라이즈, 비폴란드 OPM 30%대 후반)",
        comment: "50조원대 해외 방산 파이프라인과 중동 평화 기조 속 수출 다변화 모멘텀 지속",
        geminiEval: "[한화에어로스페이스 펀더멘털 평가] K9 자주포 및 천무의 견고한 양산 납품과 사우디·스페인 등 신규 수주 기대감으로 65만원선에서 안정적인 지지선을 구축하고 있습니다.",
        news: [
          "[특징주] 한화에어로스페이스 655,000원 지지력 확인... 해외 방산 수주 모멘텀 견조 (2026-11-10)",
          "K9 자주포·천무 2차 해외 양산 납품 정상 가동 확인 (방사청 2026-11-10)",
          "스페인·사우디·미국 등 해외 방산 파이프라인 50조원 달성 전망 (다올투자증권)"
        ]
      },
      "NVDA": {
        name: "엔비디아 (NVIDIA)",
        ticker: "NVDA",
        currency: "USD",
        prevClose: 178.67,
        basePrice: 168.33,
        price: "$168.33",
        change: "-5.79% (-$10.34)",
        isUp: false,
        per: "59.27 배",
        pbr: "26.42 배",
        div: "0.02%",
        eps: 2.84,
        bps: 6.37,
        cap: "$4,122B",
        quarter: "FY2027 Q2 (2026년 8월 공시)",
        revOp: "매출 $96.22B (약 133.2조원, YoY +106%) | 영업이익 $63.73B (약 88.3조원, YoY +124%)",
        comment: "젠슨 황 CEO 관련 이사회 조사 노이즈로 4거래일 연속 하락($168.33, -5.79%). 14일 RSI 24로 2019년 이후 극단적 과매도 구간 진입",
        geminiEval: "[엔비디아 펀더멘털 평가] CEO 개인 이슈 노이즈로 단기 급락세가 이어지며 RSI가 2019년 이후 최저치인 24까지 하락했습니다. 다만 브로드컴(+4%)·AMD(+1.5%)의 선전에서 보듯 AI 인프라 수요 자체는 견고하며, 극단적 과매도에 따른 기술적 반발 매수세 유입 가능성이 높습니다.",
        news: [
          "엔비디아 5.79% 급락 마감... 젠슨 황 조사 노이즈 지속 속 RSI 24 극단 과매도 (2026-11-10)",
          "야후파이낸스 '엔비디아 매도 압력 2019년 이후 최대치 도달' (2026-11-10)",
          "브로드컴 +4%·AMD +1.5% 상승... 빅테크 맞춤형 AI 칩 수요는 건재 (2026-11-10)"
        ]
      },
      "005380": {
        name: "현대차",
        ticker: "005380",
        currency: "KRW",
        prevClose: 248000,
        basePrice: 254000,
        price: "254,000 KRW",
        change: "+2.42% (+6,000원)",
        isUp: true,
        per: "5.20 배",
        pbr: "0.65 배",
        div: "5.10%",
        eps: 48846,
        bps: 390769,
        cap: "53.6조원",
        intradayHigh: 257000,
        intradayLow: 249000,
        quarter: "2026 3Q 실적 (2026-10-24 공시)",
        revOp: "매출 42.93조원 (YoY +4.7%) | 영업이익 3.58조원 (OPM 8.3%)",
        comment: "인도법인 상장 성공에 따른 유동성 확보 및 보스턴다이내믹스 로보틱스 협력 본격화. 저PER 5배 및 고배당 매력 부각",
        geminiEval: "[현대차 펀더멘털 평가] PBR 0.65배, PER 5.2배의 극심한 밸류에이션 저평가 상태입니다. 인도법인 IPO를 통한 4조원대 현금 유입과 엔비디아 아이작 로보틱스 플랫폼 도입으로 미래 모빌리티 밸류체인 재평가가 기대됩니다.",
        news: [
          "[특징주] 현대차 254,000원 안착... 인도법인 상장 효과 및 로보틱스 모멘텀 (2026-11-11)",
          "엔비디아와 피지컬 AI 로봇 제조 라인 협업 공식 발표 (2026-11-10)",
          "배당수익률 5%대 및 3개년 밸류업 프로그램 조기 달성 가시화 (신한투자증권)"
        ]
      },
      "TSLA": {
        name: "테슬라 (Tesla)",
        ticker: "TSLA",
        currency: "USD",
        prevClose: 255.80,
        basePrice: 265.40,
        price: "$265.40",
        change: "+3.75% (+$9.60)",
        isUp: true,
        per: "72.50 배",
        pbr: "11.20 배",
        div: "0.00%",
        eps: 3.66,
        bps: 23.70,
        cap: "$852B",
        intradayHigh: 269.00,
        intradayLow: 254.50,
        quarter: "2026 3Q 실적 요약",
        revOp: "매출 $25.18B (YoY +8%) | 영업이익 $2.72B (어닝 서프라이즈, 자동차 마진 회복)",
        comment: "미 대선 후 트럼프 행정부 자율주행 규제 완화 수혜주 부각. FSD v13 전면 배포 및 사이버캡 생산 준비 순항",
        geminiEval: "[테슬라 펀더멘털 평가] 3Q 어닝 서프라이즈로 차량 마진 저점을 확인한 뒤, FSD 규제 완화 및 옵티머스 3세대 조립 라인 투입 기대감으로 성장 프리미엄이 재평가되고 있습니다.",
        news: [
          "테슬라 $265선 회복... 트럼프 2기 연방 자율주행 표준화 기대감 고조 (2026-11-11)",
          "FSD v13 배포 개시... 운전자 개입 없는 주행 마일리지 6배 향상 (2026-11-10)",
          "사이버트럭 분기 흑자 달성 및 에너지 스토리지 메가팩 매출 52% 급증"
        ]
      },
      "035420": {
        name: "NAVER",
        ticker: "035420",
        currency: "KRW",
        prevClose: 181500,
        basePrice: 185200,
        price: "185,200 KRW",
        change: "+2.04% (+3,700원)",
        isUp: true,
        per: "18.50 배",
        pbr: "1.25 배",
        div: "0.95%",
        eps: 10010,
        bps: 148160,
        cap: "29.8조원",
        intradayHigh: 187500,
        intradayLow: 180000,
        quarter: "2026 3Q 실적 (2026-11-08 공시)",
        revOp: "매출 2.71조원 (YoY +11.1%) | 영업이익 5,253억원 (분기 사상 최대)",
        comment: "서치GPT 기반 통합검색 AI 탭 개편 및 B2B 하이퍼클로바X 엔터프라이즈 매출 급증으로 실적 서프라이즈 시현",
        geminiEval: "[NAVER 펀더멘털 평가] 3분기 분기 영업이익 5,200억원대를 돌파하며 사상 최대치를 경신했습니다. 광고·커머스 본업의 견고한 성장 위에 사우디 디지털 트윈 등 중동 프로젝트 매출 인식이 가시화되고 있습니다.",
        news: [
          "[특징주] NAVER 3분기 사상 최대 실적에 18만5천원선 안착 (2026-11-11)",
          "생성형 AI 하이퍼클로바X B2B 수주 2,000억원 돌파... 서치GPT 탭 개편 (2026-11-10)",
          "사우디 주택부 디지털 트윈 플랫폼 1차 구축 완료 및 추가 수출 기대"
        ]
      },
      "042700": {
        name: "한미반도체",
        ticker: "042700",
        currency: "KRW",
        prevClose: 114000,
        basePrice: 118500,
        price: "118,500 KRW",
        change: "+3.95% (+4,500원)",
        isUp: true,
        per: "28.50 배",
        pbr: "8.20 배",
        div: "0.72%",
        eps: 4158,
        bps: 14451,
        cap: "11.5조원",
        intradayHigh: 121000,
        intradayLow: 113500,
        quarter: "2026 3Q 실적 요약",
        revOp: "매출 2,085억원 | 영업이익 993억원 (OPM 47.6% 독보적 마진율)",
        comment: "TC 본더 글로벌 점유율 80% 독점 및 HBM4 하이브리드 본더 사전 장비 수주 협의 순항",
        geminiEval: "[한미반도체 펀더멘털 평가] HBM 제조의 핵심인 듀얼 TC 본더 시장을 독점하며 영업이익률 47%대를 달성했습니다. SK하이닉스뿐 아니라 마이크론향 수주 확대가 실적 상단을 견인하고 있습니다.",
        news: [
          "[특징주] 한미반도체 11만8500원 반등... HBM4 본더 독점력 재확인 (2026-11-11)",
          "마이크론 타이완 패키징 라인향 TC 본더 3차 공급 계약 체결 (2026-11-10)",
          "2026년 연간 영업이익 4,000억원 달성 전망 (현대차증권)"
        ]
      },
      "034020": {
        name: "두산에너빌리티",
        ticker: "034020",
        currency: "KRW",
        prevClose: 20800,
        basePrice: 21500,
        price: "21,500 KRW",
        change: "+3.37% (+700원)",
        isUp: true,
        per: "22.00 배",
        pbr: "1.85 배",
        div: "0.50%",
        eps: 977,
        bps: 11621,
        cap: "13.8조원",
        intradayHigh: 22100,
        intradayLow: 20600,
        quarter: "2026 3Q 실적 요약",
        revOp: "매출 4.39조원 | 영업이익 2,840억원 (원전 및 가스터빈 수주 견인)",
        comment: "체코 두코바니 24조 원전 수주 본계약 임박 및 미국 뉴스케일파워 SMR 주기기 전용 파운드리 가동",
        geminiEval: "[두산에너빌리티 펀더멘털 평가] 글로벌 원전 르네상스의 최대 수혜주입니다. 대형 원전 수주와 함께 AI 데이터센터 전력 공급용 SMR(소형모듈원자로) 파운드리 독점 지위를 구축하고 있습니다.",
        news: [
          "[특징주] 두산에너빌리티 21,500원 반등... 체코 원전 및 SMR 수주 파이프라인 (2026-11-11)",
          "미국 빅테크 데이터센터 전력망용 SMR 모듈 납품 계약 협의 (2026-11-10)",
          "가스터빈 100% 국산화 달성 후 중동 복합화력 발전소 수출 추진"
        ]
      },
      "AAPL": {
        name: "애플 (Apple)",
        ticker: "AAPL",
        currency: "USD",
        prevClose: 226.20,
        basePrice: 228.50,
        price: "$228.50",
        change: "+1.02% (+$2.30)",
        isUp: true,
        per: "32.10 배",
        pbr: "45.20 배",
        div: "0.44%",
        eps: 7.12,
        bps: 5.05,
        cap: "$3,480B",
        intradayHigh: 231.00,
        intradayLow: 225.50,
        quarter: "FY2026 Q4 실적 (2026-10-31 공시)",
        revOp: "매출 $94.93B (YoY +6.1%) | 순이익 $14.73B (서비스 부문 역대 최고)",
        comment: "애플 인텔리전스 글로벌 순차 롤아웃 및 아이폰16 프로 교체 수요 견조. 강력한 현금 흐름과 분기 자사주 매입 지속",
        geminiEval: "[애플 펀더멘털 평가] 온디바이스 AI '애플 인텔리전스' 배포와 함께 서비스 부문 매출총이익률이 74%에 도달하며 플랫폼 락인 효과가 한층 강화되었습니다.",
        news: [
          "애플 $228.50 마감... 애플 인텔리전스 한국어 지원 2027 상반기 확정 (2026-11-11)",
          "아이폰16 프로 수요 견조... 폭스콘 조립 라인 4분기 풀가동 (2026-11-10)",
          "워런 버핏의 버크셔 해서웨이, 애플 지분 매도세 일단락 관측"
        ]
      },
      "035720": {
        name: "카카오",
        ticker: "035720",
        currency: "KRW",
        prevClose: 37400,
        basePrice: 38200,
        price: "38,200 KRW",
        change: "+2.14% (+800원)",
        isUp: true,
        per: "24.10 배",
        pbr: "1.45 배",
        div: "0.35%",
        eps: 1585,
        bps: 26345,
        cap: "17.0조원",
        intradayHigh: 38900,
        intradayLow: 37100,
        quarter: "2026 3Q 실적 요약",
        revOp: "매출 1.92조원 | 영업이익 1,305억원 (비핵심 계열사 정리 및 톡비즈 마진 개선)",
        comment: "신규 AI 메이트 '카나나' 연내 CBT 개시 및 톡 개편 효과. 사법 리스크 고비 통과 후 밸류에이션 바닥 탈출 시도",
        geminiEval: "[카카오 펀더멘털 평가] 비핵심 자회사 구조 개편과 카카오톡 본업의 톡비즈 회복으로 영업이익이 턴어라운드 국면에 진입했습니다. AI 에이전트 카나나의 사용자 반응이 향후 주가 반등의 핵심 열쇠입니다.",
        news: [
          "[특징주] 카카오 38,200원 회복... AI 메이트 카나나 공개 및 실적 턴어라운드 (2026-11-11)",
          "카카오모빌리티, 엔비디아와 자율주행 MOU 체결로 모빌리티 AI 협력 가속 (2026-11-10)",
          "증권가 '카카오 구조조정 마무리 단계... 2027년 이익 레버리지 기대'"
        ]
      },
      "068270": {
        name: "셀트리온",
        ticker: "068270",
        currency: "KRW",
        prevClose: 178500,
        basePrice: 182000,
        price: "182,000 KRW",
        change: "+1.96% (+3,500원)",
        isUp: true,
        per: "35.20 배",
        pbr: "3.10 배",
        div: "0.40%",
        eps: 5170,
        bps: 58710,
        cap: "39.5조원",
        intradayHigh: 184500,
        intradayLow: 177000,
        quarter: "2026 3Q 실적 요약",
        revOp: "매출 8,819억원 (사상 최대) | 영업이익 2,077억원 (짐펜트라 매출 본격 반영)",
        comment: "짐펜트라(램시마SC) 미국 3대 PBM 처방집 전량 등재 완료로 2026년 4분기부터 미국 직판 매출 급증 가시화",
        geminiEval: "[셀트리온 펀더멘털 평가] 합병 상각비 부담이 3분기를 기점으로 해소되며 영업이익률이 20%대 후반으로 복귀했습니다. 미국 신약 짐펜트라의 PBM 등재 완료가 강력한 실적 드라이브로 작용 중입니다.",
        news: [
          "[특징주] 셀트리온 18만2000원 안착... 짐펜트라 미국 처방 급증 모멘텀 (2026-11-11)",
          "미국 3대 PBM 커버리지 100% 확보 및 2026 연간 매출 3.5조 목표 순항 (2026-11-10)",
          "키트루다 바이오시밀러 임상 3상 완료 임박으로 파이프라인 추가 확대"
        ]
      }
    };

let companiesDB = JSON.parse(JSON.stringify(predefinedCompaniesDB));
let userCompanyList = ['005930', '000660', '012450', 'NVDA'];
let currentSelectedCompany = '005930';

function loadCompaniesFromStorage() {
  for (const k in predefinedCompaniesDB) {
    if (!companiesDB[k]) companiesDB[k] = JSON.parse(JSON.stringify(predefinedCompaniesDB[k]));
  }

  const savedCustom = localStorage.getItem('stock_custom_companies_db');
  if (savedCustom) {
    try {
      const parsed = JSON.parse(savedCustom);
      for (const k in parsed) { companiesDB[k] = parsed[k]; }
    } catch (e) { /* ignore */ }
  }

  const savedList = localStorage.getItem('stock_app_registered_companies');
  if (savedList) {
    try {
      const parsedList = JSON.parse(savedList);
      if (Array.isArray(parsedList)) userCompanyList = parsedList;
    } catch (e) {
      userCompanyList = ['005930', '000660', '012450', 'NVDA'];
    }
  } else {
    userCompanyList = ['005930', '000660', '012450', 'NVDA'];
  }

  if (userCompanyList.length > 0) {
    if (!currentSelectedCompany || userCompanyList.indexOf(currentSelectedCompany) === -1) {
      currentSelectedCompany = userCompanyList[0];
    }
  } else {
    currentSelectedCompany = null;
  }

  // 이름 없이(코드만) 저장된 관심기업은 이름 캐시로 바로 복구해 둔다
  const nameCache = readNameCache();
  (userCompanyList || []).forEach(function (code) {
    const comp = companiesDB[code];
    const cached = nameCache[code];
    if (comp && cached && isBareCodeName(comp.name, code)) comp.name = cached;
  });
}

function saveCompaniesToStorage() {
  try {
    localStorage.setItem('stock_app_registered_companies', JSON.stringify(userCompanyList));
  } catch (e) { /* 저장 공간 부족 — 화면 상태는 유지한다 */ }
  const customDB = {};
  for (const k in companiesDB) {
    if (!predefinedCompaniesDB[k]) customDB[k] = companiesDB[k];
  }
  try {
    localStorage.setItem('stock_custom_companies_db', JSON.stringify(customDB));
  } catch (e) {
    // 용량 초과 시 기사 목록 등 큰 필드를 빼고 다시 저장한다(종목명·코드·시세는 반드시 남긴다)
    const slim = {};
    for (const k in customDB) {
      const c = customDB[k] || {};
      slim[k] = {
        name: c.name, ticker: c.ticker, currency: c.currency, price: c.price, change: c.change,
        isUp: c.isUp, per: c.per, pbr: c.pbr, div: c.div, eps: c.eps, bps: c.bps, cap: c.cap,
        quarter: c.quarter, revOp: c.revOp, comment: c.comment, geminiEval: c.geminiEval,
        fromUniverse: c.fromUniverse, asOf: c.asOf
      };
    }
    try { localStorage.setItem('stock_custom_companies_db', JSON.stringify(slim)); } catch (e2) { /* 무시 */ }
  }
}

function renderCompanyTags() {
  const container = document.getElementById('company-tags-container');
  const countEl = document.getElementById('registered-company-count');
  const dataView = document.getElementById('company-data-view');
  const emptyState = document.getElementById('company-empty-state');

  if (countEl) countEl.innerText = `등록 ${userCompanyList.length}종목`;

  if (!userCompanyList.length) {
    if (dataView) dataView.style.display = 'none';
    if (emptyState) emptyState.style.display = 'block';
    if (container) container.innerHTML = '<span style="font-size: 0.8rem; color: #8b949e; padding: 4px;">등록된 기업이 없습니다.</span>';
    return;
  }
  if (dataView) dataView.style.display = 'block';
  if (emptyState) emptyState.style.display = 'none';
  if (!container) return;

  container.innerHTML = '';
  userCompanyList.forEach(function (code) {
    // 이름이 코드로만 남아 있는 레코드도 여기서 한글 종목명으로 복구해 표시한다
    const comp = healCompanyRecord(code) || predefinedCompaniesDB[code] || { name: code, ticker: code };
    const isActive = (code === currentSelectedCompany);
    const tagItem = document.createElement('div');
    tagItem.className = 'company-tag-item ' + (isActive ? 'active' : '');
    tagItem.onclick = function () { selectCompany(code); };
    tagItem.innerHTML = '<span>' + escapeHtml(comp.name || code) + '</span>' +
      '<span style="font-size: 0.7rem; opacity: 0.75;">(' + escapeHtml(comp.ticker || code) + ')</span>' +
      '<span class="company-tag-del" title="' + escapeHtml(comp.name || code) + ' 삭제" onclick="event.stopPropagation(); deleteCompany(\'' + code + '\')">✕</span>';
    container.appendChild(tagItem);
  });
}

function selectCompany(code) {
  if (!companiesDB[code] && !predefinedCompaniesDB[code]) {
    healCompanyRecord(code);
  }
  if (!companiesDB[code]) return;
  if (!companiesDB[code] && predefinedCompaniesDB[code]) {
    companiesDB[code] = JSON.parse(JSON.stringify(predefinedCompaniesDB[code]));
    if (LIVE.market && LIVE.market.stocks && LIVE.market.stocks[code]) {
      applyQuoteToCompany(code, LIVE.market.stocks[code]);
    }
  }
  currentSelectedCompany = code;
  renderCompanyTags();
  renderCompanyDetails(code, true);
}

function deleteCompany(code) {
  const comp = companiesDB[code] || { name: code };
  if (!confirm(`'${comp.name}' (${code}) 기업을 관심 목록에서 삭제하시겠습니까?`)) return;
  userCompanyList = userCompanyList.filter(function (c) { return c !== code; });
  saveCompaniesToStorage();
  if (currentSelectedCompany === code) {
    currentSelectedCompany = userCompanyList.length ? userCompanyList[0] : null;
  }
  renderCompanyTags();
  if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, true);
  showNotif(`'${comp.name}' 기업이 관심 목록에서 삭제되었습니다.`);
}

function deleteCurrentCompany() {
  if (currentSelectedCompany) deleteCompany(currentSelectedCompany);
}

function findCompanyInDB(q) {
  const query = q.toLowerCase();
  let exact = null;
  let partial = null;
  for (const k in predefinedCompaniesDB) {
    const c = predefinedCompaniesDB[k];
    if (k.toLowerCase() === query || c.name.toLowerCase() === query) { exact = k; break; }
    if (!partial && (k.toLowerCase().indexOf(query) !== -1 || c.name.toLowerCase().indexOf(query) !== -1)) partial = k;
  }
  return exact || partial;
}

function registerCompanyFromInput(retried) {
  const input = document.getElementById('company-search-input');
  const val = input ? input.value.trim() : '';
  if (!val) {
    showNotif('등록할 종목명 또는 종목코드를 입력해 주세요.');
    return;
  }

  if (!LIVE.universe.index.length) {
    if (!retried) {
      // 전종목 목록이 아직 없으면 먼저 불러오고 한 번만 다시 시도
      showNotif('전체 종목 목록을 불러오는 중입니다…');
      loadUniverse().then(function () { registerCompanyFromInput(true); });
      return;
    }
    const preOnly = findCompanyInDB(val);
    if (preOnly) { registerCompanyByCode(preOnly); return; }
    showNotif('전종목 목록을 불러오지 못했습니다. 잠시 후 다시 시도하거나 사전 등록된 기업을 이용해 주세요.');
    return;
  }

  const q = normalizeSearchText(val);
  const list = searchCompanies(val, 10);

  // 1) 코드/이름이 정확히 일치하는 종목은 바로 등록
  for (let i = 0; i < list.length; i++) {
    if (list[i].cq === q || list[i].q === q) { registerCompanyByCode(list[i].code); return; }
  }
  // 2) 후보가 한 개뿐이면 바로 등록
  if (list.length === 1) { registerCompanyByCode(list[0].code); return; }
  // 3) 후보가 여러 개면 목록에서 고르게 한다
  if (list.length > 1) {
    renderCompanySuggest(val);
    showNotif('후보 ' + list.length + '개입니다. 목록에서 등록할 종목을 선택해 주세요.');
    return;
  }
  // 4) 전종목 목록에 없으면 사전 DB(실적 상세 12개)만 확인
  const pre = findCompanyInDB(val);
  if (pre) { registerCompanyByCode(pre); return; }
  closeCompanySuggest();
  showNotif(`'${val}' 종목을 찾지 못했습니다. 종목명이나 6자리 코드(예: 207940)로 다시 입력해 주세요.`);
}

/* 검색 후보에서 고른 종목을 관심 목록에 등록한다 */
function registerCompanyByCode(code) {
  if (!code) return;
  const input = document.getElementById('company-search-input');
  if (!companiesDB[code]) companiesDB[code] = makeCompanyRecord(code);
  const comp = companiesDB[code];
  const resolved = resolveCompanyName(code, comp);
  if (resolved && comp.name !== resolved) comp.name = resolved;
  if (isBareCodeName(comp.name, code)) {
    // 전종목 목록을 아직 못 받아 이름을 모르는 상태 — 목록을 받은 뒤 이름·시세를 채워 넣는다
    ensureNameRepair();
  }

  if (userCompanyList.indexOf(code) !== -1) {
    selectCompany(code);
    showNotif(`[${companiesDB[code].name}] 이미 등록된 관심 기업입니다.`);
  } else {
    userCompanyList.push(code);
    saveCompaniesToStorage();
    selectCompany(code);
    renderCompanyTags();
    showNotif(`[${companiesDB[code].name}] 관심 기업 등록 완료!`);
  }
  if (input) input.value = '';
  closeCompanySuggest();
  hydrateCompanyNews(code);
}

/* 사전 DB에 없는 종목의 카드 기본값 (가짜 수치는 넣지 않는다) */
function makeCompanyRecord(code) {
  if (predefinedCompaniesDB[code]) {
    const rec = JSON.parse(JSON.stringify(predefinedCompaniesDB[code]));
    const uni = universeLookup(code);
    if (uni) applyUniverseQuoteToCompany(rec, uni);
    return rec;
  }
  const e = universeLookup(code);
  const record = {
    name: e ? e.name : code,
    ticker: code,
    currency: e ? e.cur : (/^\d{6}$/.test(code) ? 'KRW' : 'USD'),
    price: '—',
    change: '—',
    isUp: true,
    per: '-',
    pbr: '-',
    div: '-',
    eps: null,
    bps: null,
    cap: '—',
    quarter: '실적 공시 데이터 미수집',
    revOp: '이 종목은 시세·밸류에이션만 자동 수집 대상입니다. 분기 매출·영업이익은 사전 등록된 12개 기업에 대해서만 제공되며, 그 밖의 종목은 공시 원문을 확인해 주세요.',
    comment: '',
    geminiEval: '',
    news: [],
    fromUniverse: true
  };
  if (e) applyUniverseQuoteToCompany(record, e);
  return record;
}

/* 전종목 인덱스의 시세·밸류에이션을 기업 카드에 반영 */
function applyUniverseQuoteToCompany(comp, e) {
  if (!comp || !e) return comp;
  comp.currency = e.cur || comp.currency;
  // 종목명은 코드만 남아 있으면 전종목 목록의 한글 이름으로 되살린다(예전에 이름 없이 저장된 관심기업 복구)
  if (e.name && isBareCodeName(comp.name, e.code)) comp.name = e.name;
  if (comp.ticker !== e.code) comp.ticker = e.code;
  if (e.price !== null && e.price !== undefined) {
    comp.livePrice = e.price;
    comp.price = formatPriceValue(e.price, comp.currency);
    let changeAmount = null;
    if (e.pct !== null && e.pct !== undefined && (1 + e.pct / 100) !== 0) {
      changeAmount = e.price - (e.price / (1 + e.pct / 100));
    }
    comp.change = formatChangeText({ pct: e.pct, change: changeAmount }, comp.currency);
    comp.isUp = (e.pct || 0) >= 0;
  }
  comp.asOf = e.asOf || comp.asOf || null;
  comp.marketStatus = e.status || comp.marketStatus || null;
  if (e.mcap) {
    comp.cap = formatCap(e.cur === 'USD' ? e.mcap : e.mcap * 1e8, comp.currency);
  }
  if (e.per !== null && e.per !== undefined) comp.per = fmtNum(e.per, 2) + ' 배';
  if (e.pbr !== null && e.pbr !== undefined) comp.pbr = fmtNum(e.pbr, 2) + ' 배';
  if (e.eps !== null && e.eps !== undefined) comp.eps = e.eps;
  if (e.bps !== null && e.bps !== undefined) comp.bps = e.bps;

  comp.comment = '실시간 ' + comp.price + ' (' + comp.change + ') · PER ' + comp.per +
    ' · PBR ' + comp.pbr + ' · 시총 ' + comp.cap;
  if (comp.fromUniverse) {
    comp.geminiEval = '[' + comp.name + ' 실시간 요약] 현재가 ' + comp.price + ' (' + comp.change + '), ' +
      '시가총액 ' + comp.cap + ', PER ' + comp.per + ', PBR ' + comp.pbr + '. ' +
      (comp.quarter === '실적 공시 데이터 미수집'
        ? '분기 실적(EPS ' + (comp.eps === null ? '-' : fmtNum(comp.eps, comp.currency === 'USD' ? 2 : 0) + ' ' + comp.currency) +
          ', BPS ' + (comp.bps === null ? '-' : fmtNum(comp.bps, comp.currency === 'USD' ? 2 : 0) + ' ' + comp.currency) + ')은 시세·밸류에이션 수치만 자동 수집되며, 매출·영업이익 공시 요약은 제공되지 않습니다.'
        : '');
  }
  return comp;
}

/* 임의 종목의 최신 뉴스 (Google News RSS · rss2json 경로) */
function hydrateCompanyNews(code, force) {
  const comp = code ? companiesDB[code] : null;
  if (!comp) return;
  if (comp.newsHydrating) return;
  if (!force && comp.liveArticles && (Date.now() - (comp.liveArticlesAt || 0)) < 30 * 60 * 1000) return;
  const keyword = /^\d{6}$/.test(code) ? comp.name : (comp.name || code).replace(/\s*\(.*$/, '') || code;
  comp.newsHydrating = true;
  fetchLiveKeywordNews(keyword).then(function (arts) {
    comp.newsHydrating = false;
    comp.liveArticles = (arts || []).slice(0, 6);
    comp.liveArticlesAt = Date.now();
    if (currentSelectedCompany === code) renderCompanyDetails(code, false);
  }).catch(function () {
    comp.newsHydrating = false;
  });
}

/* ------------------------------------------------------------------ 검색 후보 드롭다운 */
let suggestItems = [];
let suggestActive = -1;

function getCompanySearchValue() {
  const input = document.getElementById('company-search-input');
  return input ? input.value : '';
}

function onCompanySearchInput() {
  const val = getCompanySearchValue();
  if (!val.trim()) { closeCompanySuggest(); return; }
  if (!LIVE.universe.index.length) loadUniverse();
  renderCompanySuggest(val);
}

function renderCompanySuggest(val) {
  const box = document.getElementById('company-suggest');
  if (!box) return;
  const q = String(val || '').trim();
  if (!q) { closeCompanySuggest(); return; }

  if (!LIVE.universe.index.length) {
    box.innerHTML = '<div class="suggest-empty">전체 종목 목록을 불러오는 중…</div>';
    box.style.display = 'block';
    suggestItems = [];
    suggestActive = -1;
    return;
  }

  const list = searchCompanies(q, 10);
  suggestItems = list;
  suggestActive = list.length ? 0 : -1;
  if (!list.length) {
    box.innerHTML = '<div class="suggest-empty">일치하는 종목이 없습니다. 종목명 또는 6자리 코드로 다시 입력해 주세요.</div>';
    box.style.display = 'block';
    return;
  }
  box.innerHTML = list.map(function (e, i) {
    const priceText = (e.price === null || e.price === undefined) ? '시세 미수신' : formatPriceValue(e.price, e.cur);
    const pctCls = (e.pct || 0) >= 0 ? 'up' : 'down';
    const registered = (userCompanyList || []).indexOf(e.code) !== -1;
    return '<div class="suggest-item" data-idx="' + i + '" onmousedown="pickCompanySuggest(' + i + ', event)"' +
      ' onmouseover="setSuggestActive(' + i + ')">' +
      '<div class="suggest-main">' +
        '<span class="suggest-name">' + escapeHtml(e.name) + '</span>' +
        '<span class="suggest-code">' + escapeHtml(e.code) + '</span>' +
        '<span class="suggest-market">' + escapeHtml(e.market) + '</span>' +
        (registered ? '<span class="suggest-reg">등록됨</span>' : '') +
      '</div>' +
      '<div class="suggest-price">' + escapeHtml(priceText) +
        ' <span class="' + pctCls + '">' + fmtPct(e.pct) + '</span></div>' +
      '</div>';
  }).join('');
  box.style.display = 'block';
  highlightSuggest();
}

function setSuggestActive(i) {
  suggestActive = i;
  highlightSuggest();
}

function highlightSuggest() {
  const box = document.getElementById('company-suggest');
  if (!box) return;
  const items = box.querySelectorAll('.suggest-item');
  for (let i = 0; i < items.length; i++) {
    if (i === suggestActive) items[i].classList.add('active');
    else items[i].classList.remove('active');
  }
}

function pickCompanySuggest(i, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const e = suggestItems[i];
  if (!e) return;
  registerCompanyByCode(e.code);
}

function closeCompanySuggest() {
  const box = document.getElementById('company-suggest');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  suggestItems = [];
  suggestActive = -1;
}

function onCompanySearchKey(ev) {
  if (!ev) return;
  const key = ev.key;
  if (key === 'ArrowDown' || key === 'ArrowUp') {
    if (!suggestItems.length) return;
    ev.preventDefault();
    const step = key === 'ArrowDown' ? 1 : -1;
    suggestActive = (suggestActive + step + suggestItems.length) % suggestItems.length;
    highlightSuggest();
    return;
  }
  if (key === 'Escape') { closeCompanySuggest(); return; }
  if (key === 'Enter') {
    ev.preventDefault();
    if (suggestActive >= 0 && suggestItems[suggestActive]) {
      registerCompanyByCode(suggestItems[suggestActive].code);
    } else {
      registerCompanyFromInput();
    }
  }
}

function quickRegister(code) {
  if (!code) return;
  if (!companiesDB[code]) companiesDB[code] = makeCompanyRecord(code);
  if (userCompanyList.indexOf(code) === -1) {
    userCompanyList.push(code);
    saveCompaniesToStorage();
    showNotif(`[${companiesDB[code].name}] 관심 기업 등록 완료!`);
  } else {
    showNotif(`[${companiesDB[code].name}] 이미 등록된 기업입니다.`);
  }
  selectCompany(code);
  renderCompanyTags();
  hydrateCompanyNews(code);
}

function searchCustomCompany(retried) {
  const val = getCompanySearchValue().trim();
  if (!val) {
    showNotif('검색할 종목명 또는 코드를 입력해 주세요.');
    return;
  }
  if (!LIVE.universe.index.length) {
    if (!retried) {
      showNotif('전체 종목 목록을 불러오는 중입니다…');
      loadUniverse().then(function () { searchCustomCompany(true); });
      return;
    }
    showNotif('전종목 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
    return;
  }
  const list = searchCompanies(val, 10);
  if (!list.length) {
    closeCompanySuggest();
    showNotif(`'${val}' 종목을 찾지 못했습니다. 종목명이나 6자리 코드로 다시 입력해 주세요.`);
    return;
  }
  renderCompanySuggest(val);
  const first = list[0];
  const priceText = (first.price === null || first.price === undefined)
    ? '시세 미수신' : formatPriceValue(first.price, first.cur) + ' (' + fmtPct(first.pct) + ')';
  showNotif(`[${first.name}] ${priceText} · 후보 ${list.length}개 (선택하면 관심 기업에 등록됩니다)`);
}

function refreshCompanyPricesOnly(showFlash, showToast) {
  if (!LIVE.universe.index.length || (Date.now() - (LIVE.universe.at || 0)) > UNIVERSE_TTL_MS) loadUniverse();
  if (!LIVE.companyNews.at || (Date.now() - (LIVE.companyNews.at || 0)) > COMPANY_NEWS_TTL_MS) loadCompanyNews();
  if (LIVE.market) applyMarketData(LIVE.market, { flash: !!showFlash });
  if (!LIVE.market || isStale(LIVE.marketAt)) {
    loadMarketData({ flash: !!showFlash });
  }
  if (showToast && currentSelectedCompany) {
    const comp = companiesDB[currentSelectedCompany];
    if (comp && comp.livePrice !== undefined) {
      showNotif(`[${comp.name}] ${comp.price} (${comp.change}) · 기준 ${fmtFullStamp(comp.asOf)}`);
    }
  }
}

function refreshCurrentCompanyPriceManual() {
  showNotif('시세를 다시 조회하고 있습니다…');
  // 전종목 인덱스·기업 뉴스도 함까 강제로 새로받는다 (10분 주기 데이터)
  loadUniverse({ force: true });
  loadCompanyNews({ force: true });
  loadMarketData({ flash: true }).then(function (payload) {
    if (!payload) { showNotif('시세 조회 실패 — 네트워크를 확인해 주세요.'); return; }
    const comp = currentSelectedCompany ? companiesDB[currentSelectedCompany] : null;
    if (comp) showNotif(`[${comp.name}] ${comp.price} (${comp.change}) · 기준 ${fmtFullStamp(comp.asOf)}`);
  });
}

function renderCompanyDetails(code, triggerFlash) {
  const dataView = document.getElementById('company-data-view');
  const emptyState = document.getElementById('company-empty-state');

  if (code && !companiesDB[code]) healCompanyRecord(code);
  if (!code || !companiesDB[code]) {
    if (dataView) dataView.style.display = 'none';
    if (emptyState) emptyState.style.display = 'block';
    return;
  }
  if (dataView) dataView.style.display = 'block';
  if (emptyState) emptyState.style.display = 'none';

  const d = companiesDB[code];
  const nameEl = document.getElementById('comp-name');
  const tickerEl = document.getElementById('comp-ticker');
  const priceEl = document.getElementById('comp-price');
  const chgEl = document.getElementById('comp-change');

  if (nameEl) nameEl.innerText = d.name;
  if (tickerEl) tickerEl.innerText = d.ticker;
  if (priceEl) priceEl.innerText = d.price || '—';
  if (chgEl) {
    chgEl.innerText = d.change || '—';
    chgEl.className = d.isUp ? 'up' : 'down';
  }

  if (triggerFlash) {
    const priceBox = document.getElementById('comp-price-box');
    if (priceBox) {
      priceBox.classList.remove('flash-update');
      void priceBox.offsetWidth;
      priceBox.classList.add('flash-update');
      setTimeout(function () { priceBox.classList.remove('flash-update'); }, 1500);
    }
  }

  const extEl = document.getElementById('comp-extended');
  if (extEl) {
    if (d.extended && d.extended.price !== null && d.extended.price !== undefined) {
      const sessMap = { PRE_MARKET: '장전 시간외', AFTER_MARKET: '시간외', AFTER_HOURS: '시간외', REGULAR_MARKET: '장중' };
      const sess = sessMap[String(d.extended.session || '').toUpperCase()] || '시간외';
      extEl.innerHTML = '<span style="color:#58a6ff;">' + sess + '</span> ' +
        formatPriceValue(d.extended.price, d.currency) + ' (' + fmtPct(d.extended.pct) + ')';
      extEl.style.display = 'block';
    } else {
      extEl.style.display = 'none';
    }
  }

  const timeEl = document.getElementById('comp-updated-time');
  if (timeEl) {
    if (d.asOf) {
      timeEl.innerText = '기준 ' + fmtFullStamp(d.asOf) + ' · ' + marketStatusKo(d.marketStatus) + (d.stale ? ' (직전값)' : '');
      timeEl.style.color = d.stale ? '#d29922' : '#8b949e';
    } else {
      timeEl.innerText = '실시간 시세 미수신';
      timeEl.style.color = '#d29922';
    }
  }

  const perEl = document.getElementById('comp-per');
  const pbrEl = document.getElementById('comp-pbr');
  const divEl = document.getElementById('comp-div');
  const epsEl = document.getElementById('comp-eps');
  const bpsEl = document.getElementById('comp-bps');
  const capEl = document.getElementById('comp-cap');

  if (perEl) perEl.innerText = d.per || '-';
  if (pbrEl) pbrEl.innerText = d.pbr || '-';
  if (divEl) divEl.innerText = d.div || '-';
  if (epsEl) {
    const epsDigits = String(d.currency || '').toUpperCase() === 'USD' ? 2 : 0;
    epsEl.innerText = typeof d.eps === 'number' ? fmtNum(d.eps, epsDigits) + ' ' + d.currency : (d.eps || '-');
  }
  if (bpsEl) {
    const bpsDigits = String(d.currency || '').toUpperCase() === 'USD' ? 2 : 0;
    bpsEl.innerText = typeof d.bps === 'number' ? fmtNum(d.bps, bpsDigits) + ' ' + d.currency : (d.bps || '-');
  }
  if (capEl) capEl.innerText = d.cap || '—';

  const qEl = document.getElementById('comp-quarter');
  const revEl = document.getElementById('comp-rev-op');
  const commEl = document.getElementById('comp-earn-comment');
  const evalEl = document.getElementById('comp-gemini-eval');
  if (qEl) qEl.innerText = d.quarter || '실적 데이터 없음';
  if (revEl) revEl.innerText = d.revOp || '—';
  if (commEl) commEl.innerText = d.comment || '';
  if (evalEl) evalEl.innerText = d.geminiEval || '';

  const newsList = document.getElementById('comp-news-list');
  if (newsList) {
    const serverArts = companyArticlesFor(code, d.name);
    const collected = companyNewsArticles(code, d.name);
    const liveArts = (d.liveArticles || []).slice(0, 5);
    const picked = (serverArts.length ? serverArts : (collected.length ? collected : liveArts)).slice(0, 5);
    if (picked.length) {
      newsList.innerHTML = picked.map(function (a) {
        return '<li>' + (a.origin === 'SAVE' ? saveBadgeHtml(a.badge, true) + ' ' : '') +
          '<a href="' + escapeHtml(a.url) + '" target="_blank" rel="noopener" style="color:#c9d1d9; text-decoration:none;">' +
          escapeHtml(a.title) + '</a> <span style="color:#8b949e; font-size:0.75rem;">(' + escapeHtml(a.source) + ' · ' + escapeHtml(a.date) + ')</span></li>';
      }).join('');
    } else if ((d.news || []).length) {
      newsList.innerHTML = (d.news || []).slice(0, 4).map(function (n) {
        return '<li>' + escapeHtml(n) + '</li>';
      }).join('');
    } else if (d.newsHydrating) {
      newsList.innerHTML = '<li style="color:#8b949e;">실시간 기사를 불러오는 중…</li>';
    } else {
      newsList.innerHTML = '<li style="color:#8b949e;">최근 10일 안에 이 종목을 다룬 기사를 찾지 못했습니다.</li>';
    }
    // SAVE 연동 기사: 최신 구글 기사에 밀려 잘릴 수 있으므로 종목 태깅 기사를 2건까지 덧붙인다
    const shown = {};
    picked.forEach(function (a) { shown[normalizeKeyword(a.title)] = true; });
    const saveOwn = saveNewsList().filter(function (a) {
      return (a.companies || []).indexOf(code) !== -1 && !shown[normalizeKeyword(a.title)];
    }).slice(0, 2);
    if (saveOwn.length) {
      newsList.innerHTML += saveOwn.map(function (a) {
        return '<li>' + saveBadgeHtml(a.badge, true) + ' ' +
          '<a href="' + escapeHtml(a.url) + '" target="_blank" rel="noopener" style="color:#c9d1d9; text-decoration:none;">' +
          escapeHtml(a.title) + '</a> <span style="color:#8b949e; font-size:0.75rem;">(' +
          escapeHtml(a.source) + ' · ' + escapeHtml(a.date) + ')</span></li>';
      }).join('');
    }
    if (!picked.length) {
      if (!LIVE.companyNews.at) loadCompanyNews();
      else hydrateCompanyNews(code);
    }
  }

  // 매수·매도 시점 분석 엔진(signal-engine.js) — 종합 점수·신호별 근거·호재/악재 뉴스
  if (typeof StockSignal !== 'undefined') {
    try {
      const signalArticles = (function () {
        try {
          const server = companyArticlesFor(code, d.name) || [];
          const tagged = saveNewsList().filter(function (a) {
            return (a.companies || []).indexOf(code) !== -1;
          });
          return mergeArticles([server, tagged]);
        } catch (e) { return []; }
      })();
      StockSignal.mount({
        code: code,
        name: d.name,
        market: /^\d{6}$/.test(code) ? 'KR' : 'US',
        price: (typeof d.livePrice === 'number') ? d.livePrice : null,
        currency: d.currency || (/^\d{6}$/.test(code) ? 'KRW' : 'USD'),
        articles: signalArticles
      });
    } catch (e) { /* 신호 엔진 오류가 기업 탭 전체를 막지 않게 한다 */ }
  }
}

/* ==========================================================================
   알림 · 설정 · 자동 갱신 · 초기화
   ========================================================================== */
function showNotif(text) {
  const banner = document.getElementById('notif-banner');
  const textEl = document.getElementById('notif-text');
  if (!banner || !textEl) return;
  textEl.innerText = text;
  banner.style.display = 'block';
  clearTimeout(showNotif._timer);
  showNotif._timer = setTimeout(function () { banner.style.display = 'none'; }, 4000);
}

function updateIntervalPreview() {
  const val = parseInt(document.getElementById('interval-value').value, 10) || 0;
  const unit = document.getElementById('interval-unit').value;
  const previewEl = document.getElementById('interval-preview-text');
  if (!previewEl) return;
  if (unit === 'hours') {
    previewEl.innerText = `(= ${val * 60}분 간격)`;
  } else {
    const hrs = (val / 60).toFixed(1);
    previewEl.innerText = val >= 60 ? `(= 약 ${hrs}시간 간격)` : `(= ${val}분 간격)`;
  }
}

function setIntervalPreset(val, unit) {
  document.getElementById('interval-toggle').checked = true;
  document.getElementById('interval-value').value = val;
  document.getElementById('interval-unit').value = unit;
  toggleIntervalInputState();
  updateIntervalPreview();
  showNotif(`업데이트 주기가 ${val}${unit === 'hours' ? '시간' : '분'}으로 설정되었습니다.`);
}

function toggleIntervalInputState() {
  const isEnabled = document.getElementById('interval-toggle').checked;
  const grp = document.getElementById('interval-input-group');
  if (!grp) return;
  grp.style.opacity = isEnabled ? '1' : '0.4';
  grp.style.pointerEvents = isEnabled ? 'auto' : 'none';
}

function saveSettings() {
  const time = document.getElementById('report-time').value;
  const isIntervalEnabled = document.getElementById('interval-toggle').checked;
  const val = parseInt(document.getElementById('interval-value').value, 10) || 1;
  const unit = document.getElementById('interval-unit').value;
  const totalMinutes = unit === 'hours' ? val * 60 : val;
  const marketOnly = document.getElementById('market-only').checked;

  localStorage.setItem('stock_report_time', time);
  localStorage.setItem('stock_interval_enabled', isIntervalEnabled);
  localStorage.setItem('stock_interval_val', val);
  localStorage.setItem('stock_interval_unit', unit);
  localStorage.setItem('stock_interval_minutes', totalMinutes);
  localStorage.setItem('stock_market_only', marketOnly);

  setupIntervalTimer();
  const intervalText = isIntervalEnabled ? `${val}${unit === 'hours' ? '시간' : '분'}마다 (${totalMinutes}분 간격)` : '미사용';
  showNotif(`설정이 저장되었습니다: 리포트 ${time}, 갱신 주기: ${intervalText}`);
}

let intervalTimer = null;

function setupIntervalTimer() {
  if (intervalTimer) clearInterval(intervalTimer);
  const isEnabled = localStorage.getItem('stock_interval_enabled') === 'true';
  const mins = parseInt(localStorage.getItem('stock_interval_minutes'), 10) || 60;
  if (isEnabled && mins > 0) {
    intervalTimer = setInterval(function () { runIntervalSyncAll(true); }, mins * 60 * 1000);
  }
}

let marketWatchTimer = null;

function setupMarketWatch() {
  if (marketWatchTimer) clearInterval(marketWatchTimer);
  marketWatchTimer = setInterval(function () {
    if (typeof document !== 'undefined' && document.hidden) return;
    loadMarketData({});
  }, MARKET_WATCH_MS);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (!LIVE.marketAt || (Date.now() - Date.parse(LIVE.marketAt)) > MARKET_WATCH_MS) {
      loadMarketData({});
    }
  });
}

function loadSavedSettings() {
  const savedTime = localStorage.getItem('stock_report_time');
  if (savedTime) document.getElementById('report-time').value = savedTime;

  const savedEnabled = localStorage.getItem('stock_interval_enabled');
  if (savedEnabled !== null) document.getElementById('interval-toggle').checked = (savedEnabled === 'true');

  const savedVal = localStorage.getItem('stock_interval_val');
  if (savedVal) document.getElementById('interval-value').value = savedVal;

  const savedUnit = localStorage.getItem('stock_interval_unit');
  if (savedUnit) document.getElementById('interval-unit').value = savedUnit;

  const savedMarketOnly = localStorage.getItem('stock_market_only');
  if (savedMarketOnly !== null) document.getElementById('market-only').checked = (savedMarketOnly === 'true');

  const isAppLock = localStorage.getItem('stock_app_lock_all') === 'true';
  const lockToggle = document.getElementById('full-app-lock-toggle');
  if (lockToggle) lockToggle.checked = isAppLock;

  toggleIntervalInputState();
  updateIntervalPreview();
  setupIntervalTimer();
}

function testNotification() {
  showNotif('🔔 [알림 테스트] 실제 데이터 기준 ' + (LIVE.marketAt ? fmtFullStamp(LIVE.marketAt) : '데이터 없음'));
}

/* ------------------------------------------------------------ 전체 동기화 */
async function refreshAll(opts) {
  opts = opts || {};
  if (LIVE.busy) return;
  LIVE.busy = true;

  const btn = document.getElementById('sync-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 동기화 중…'; }
  const chip = document.getElementById('live-status');
  if (chip && opts.initial) { chip.textContent = '🔄 실시간 데이터 연결 중…'; chip.style.color = '#8b949e'; }

  try {
    const market = await loadMarketData({ flash: !opts.initial });
    await loadNewsData({});
    await refreshTraces({ force: !!opts.force });
    renderSaveStrip();
    if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, false);

    const parts = [];
    if (market && market.stocks) parts.push('시세 ' + Object.keys(market.stocks).length + '종목');
    const articleCount = currentTraces.reduce(function (acc, t) { return acc + (t.articles || []).length; }, 0);
    if (articleCount) parts.push('기사 ' + articleCount + '건');
    const save = savePayload();
    if (save && save.count) parts.push('SAVE ' + save.count + '건');
    const stamp = LIVE.marketAt ? fmtFullStamp(LIVE.marketAt) : '—';

    if (opts.initial) {
      showNotif('실시간 데이터 연결: ' + (parts.join(' · ') || '데이터 없음') + ' (기준 ' + stamp + ')');
    } else {
      showNotif('🔄 동기화 완료: ' + (parts.join(' · ') || '데이터 없음') + ' (기준 ' + stamp + ')');
    }
    renderLiveStatus();
    LIVE.lastSyncAt = Date.now();
  } catch (e) {
    showNotif('동기화 중 오류: ' + (e && e.message ? e.message : e));
  } finally {
    LIVE.busy = false;
    if (btn) { btn.disabled = false; btn.textContent = '🔄 지금 전체 동기화'; }
  }
}

function triggerManualSync() {
  refreshAll({ force: true });
}

/* 하위 호환: 기존 인터벌 타이머가 호출하는 이름 */
function runIntervalSyncAll(isAuto) {
  refreshAll({ force: !!isAuto });
}

/* ------------------------------------------------------------------ 시작 */
async function initApp() {
  const badge = document.getElementById('app-version-badge');
  if (badge) badge.innerText = APP_VERSION;

  loadCompaniesFromStorage();
  loadTracesFromStorage();
  renderCompanyTags();
  // 예전에 이름 없이(코드만) 저장된 관심기업이 있으면 전종목 목록을 받아 이름을 복구한다
  if (registeredCompanyNamesBroken()) ensureNameRepair();
  renderTraces();
  loadSavedSettings();
  if (typeof checkFullAppLockOnLoad === 'function') checkFullAppLockOnLoad();

  if (currentSelectedCompany) renderCompanyDetails(currentSelectedCompany, false);

  await refreshAll({ initial: true });
  setupMarketWatch();
}

document.addEventListener('DOMContentLoaded', function () {
  initApp();
});
