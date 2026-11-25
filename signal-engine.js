/* ============================================================================
   매수·매도 시점 분석 엔진 (signal-engine.js)
   ----------------------------------------------------------------------------
   · 입력 : 종목별 원천 데이터(data/signal/h/<코드>.json: 120일 일봉·수급·
            밸류에이션) + 앱이 이미 받아 둔 뉴스(company_news·SAVE)
   · 구조 : 규칙(RULES) 하나가 독립적으로 0~100 점과 근거를 낸다.
            규칙을 그룹(차트·뉴스·수급·밸류에이션)으로 묶어 가중 평균하고,
            데이터가 없는 그룹은 빼고 남은 가중치로 다시 정규화한다(동적 보정).
            새 조건을 넣을 때는 RULES 배열에 항목 하나만 추가하면 된다(플러그인 구조).
   · 점수 : 0~100 (50 = 중립). 70↑ 매수우위 / 60~70 매수관심 / 40~60 관망 /
            30~40 매도주의 / 30↓ 매도우위
   · 원칙 : 없는 데이터로 점수를 만들지 않는다. 데이터가 비면 그 규칙은
            '근거 없음'으로 빠지고 신뢰도(%)가 그만큼 낮아진다.
   ========================================================================== */
var StockSignal = (function () {
  'use strict';

  var VERSION = '1.0.0';
  var HISTORY_BASE = 'data/signal/h/';
  var MANIFEST_PATH = 'data/signal/manifest.json';
  var SETTINGS_KEY = 'stock_signal_settings_v1';

  var GROUP_META = {
    chart: { name: '차트', icon: '📈' },
    news: { name: '뉴스', icon: '📰' },
    supply: { name: '수급', icon: '🏦' },
    valuation: { name: '밸류에이션', icon: '⚖️' }
  };
  var GROUP_ORDER = ['chart', 'news', 'supply', 'valuation'];

  var DEFAULT_SETTINGS = {
    weights: { chart: 40, news: 25, supply: 20, valuation: 15 },
    rules: {
      ma_trend: true, rsi: true, macd: true, bollinger: true, volume: true,
      news: true, supply: true, valuation: true
    }
  };

  var RULE_LABELS = {
    ma_trend: '이동평균(5·20·60·120)',
    rsi: 'RSI(14)',
    macd: 'MACD(12,26,9)',
    bollinger: '볼린저밴드(20, ±2σ)',
    volume: '거래량 급증',
    news: '뉴스 호재·악재',
    supply: '외국인·기관 수급',
    valuation: 'PER·PBR·52주 위치'
  };

  /* ---------------------------------------------------------------- utils */
  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function round(v, digits) {
    var p = Math.pow(10, digits === undefined ? 1 : digits);
    return Math.round(v * p) / p;
  }
  function num(v) {
    if (v === null || v === undefined) return null;
    var n = typeof v === 'number' ? v : parseFloat(String(v).replace(/,/g, ''));
    return isFinite(n) ? n : null;
  }
  function avg(list) {
    if (!list || !list.length) return null;
    var s = 0;
    for (var i = 0; i < list.length; i++) s += list[i];
    return s / list.length;
  }
  function sma(values, period) {
    if (!values || values.length < period || period <= 0) return null;
    var out = [];
    var sum = 0;
    for (var i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= period) sum -= values[i - period];
      if (i >= period - 1) out.push(sum / period);
    }
    return out;
  }
  function ema(values, period) {
    if (!values || values.length < period || period <= 0) return null;
    var k = 2 / (period + 1);
    var prev = values.slice(0, period).reduce(function (a, b) { return a + b; }, 0) / period;
    var out = [prev];
    for (var i = period; i < values.length; i++) {
      prev = values[i] * k + prev * (1 - k);
      out.push(prev);
    }
    return out;
  }
  function stddev(values, period) {
    var m = sma(values, period);
    if (!m) return null;
    var out = [];
    for (var i = period - 1; i < values.length; i++) {
      var mean = m[i - period + 1];
      var acc = 0;
      for (var j = i - period + 1; j <= i; j++) {
        var d = values[j] - mean;
        acc += d * d;
      }
      out.push(Math.sqrt(acc / period));
    }
    return out;
  }
  function last(list, back) {
    if (!list || !list.length) return null;
    var idx = list.length - 1 - (back || 0);
    return idx >= 0 ? list[idx] : null;
  }
  function fmtPrice(v, currency) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    var digits = String(currency || '').toUpperCase() === 'USD' ? 2 : 0;
    var fixed = Number(v).toFixed(digits);
    var parts = fixed.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (parts[0].charAt(0) === '-' && parts[0].length === 1) return fixed;
    return parts.join('.');
  }

  /* ----------------------------------------------------------- indicators */
  function computeIndicators(bars) {
    var closes = [], volumes = [], dates = [];
    (bars || []).forEach(function (b) {
      var c = num(b[4]);
      if (c === null || c <= 0) return;
      closes.push(c);
      volumes.push(num(b[5]) || 0);
      dates.push(String(b[0]));
    });
    var n = closes.length;
    var ind = {
      n: n, closes: closes, volumes: volumes, dates: dates,
      close: n ? closes[n - 1] : null,
      prevClose: n > 1 ? closes[n - 2] : null,
      ma5: null, ma20: null, ma60: null, ma120: null,
      rsi: null, macd: null, macdSignal: null, macdHist: null,
      macdHistPrev: null, macdPrev: null, macdSignalPrev: null,
      bbUpper: null, bbLower: null, bbMid: null, bbPctB: null, bbWidth: null,
      volRatio: null, crossMa5: 0, crossMa60: 0, maSlope20: null
    };
    if (n < 2) return ind;

    var ma5 = sma(closes, 5), ma20 = sma(closes, 20), ma60 = sma(closes, 60), ma120 = sma(closes, 120);
    ind.ma5 = last(ma5); ind.ma20 = last(ma20); ind.ma60 = last(ma60); ind.ma120 = last(ma120);
    ind.ma20Prev = last(ma20, 3);
    ind.maSlope20 = (ind.ma20 !== null && ind.ma20Prev) ? (ind.ma20 / ind.ma20Prev - 1) : null;

    // 크로스 판정(최근 5거래일 안에 발생했는지)
    function crossWithin(fastSeries, slowSeries, lookback) {
      if (!fastSeries || !slowSeries) return 0;
      var len = Math.min(fastSeries.length, slowSeries.length);
      var from = Math.max(1, len - lookback);
      for (var i = from; i < len; i++) {
        var prevDiff = fastSeries[i - 1] - slowSeries[i - 1];
        var diff = fastSeries[i] - slowSeries[i];
        if (prevDiff <= 0 && diff > 0) return 1;
        if (prevDiff >= 0 && diff < 0) return -1;
      }
      return 0;
    }
    ind.crossMa5 = crossWithin(ma5, ma20, 5);
    ind.crossMa60 = crossWithin(ma60, ma120, 8);

    // RSI(14) — Wilder
    if (n >= 15) {
      var gain = 0, loss = 0, i;
      for (i = 1; i <= 14; i++) {
        var diff = closes[i] - closes[i - 1];
        if (diff >= 0) gain += diff; else loss -= diff;
      }
      var avgGain = gain / 14, avgLoss = loss / 14;
      for (i = 15; i < n; i++) {
        var ch = closes[i] - closes[i - 1];
        avgGain = (avgGain * 13 + (ch > 0 ? ch : 0)) / 14;
        avgLoss = (avgLoss * 13 + (ch < 0 ? -ch : 0)) / 14;
      }
      ind.rsi = avgLoss === 0 ? 100 : 100 - (100 / (1 + avgGain / avgLoss));
    }

    // MACD(12,26,9)
    var e12 = ema(closes, 12), e26 = ema(closes, 26);
    if (e12 && e26) {
      var off = e12.length - e26.length;
      var macdSeries = [];
      for (var k = 0; k < e26.length; k++) macdSeries.push(e12[k + off] - e26[k]);
      var sigSeries = ema(macdSeries, 9);
      if (sigSeries) {
        var align = macdSeries.length - sigSeries.length;
        ind.macd = last(macdSeries);
        ind.macdSignal = last(sigSeries);
        ind.macdHist = ind.macd - ind.macdSignal;
        ind.macdPrev = macdSeries[macdSeries.length - 2];
        ind.macdSignalPrev = sigSeries[sigSeries.length - 2];
        ind.macdHistPrev = (ind.macdPrev !== undefined && ind.macdSignalPrev !== undefined)
          ? ind.macdPrev - ind.macdSignalPrev : null;
        ind.macdCross = crossWithin(macdSeries.slice(align), sigSeries, 3);
      }
    }

    // 볼린저밴드(20, ±2σ)
    var sd = stddev(closes, 20);
    if (sd && sd.length) {
      ind.bbMid = ind.ma20;
      ind.bbUpper = ind.bbMid + 2 * last(sd);
      ind.bbLower = ind.bbMid - 2 * last(sd);
      var band = ind.bbUpper - ind.bbLower;
      ind.bbPctB = band > 0 ? (ind.close - ind.bbLower) / band : null;
      ind.bbWidth = ind.bbMid ? band / ind.bbMid : null;
    }

    // 거래량(20일 평균 대비)
    var volAvg20 = last(sma(volumes, 20));
    if (volAvg20 && volAvg20 > 0) ind.volRatio = volumes[n - 1] / volAvg20;
    return ind;
  }

  /* --------------------------------------------------------------- rules
     각 규칙: function(inputs) -> {avail, score(0~100), reason, bullets[]}
     inputs = {ind, bars, price, supply, valuation, news, currency}
     ------------------------------------------------------------------- */
  function ruleMaTrend(inp) {
    var ind = inp.ind;
    if (ind.n < 60 || ind.ma20 === null || ind.ma60 === null) {
      return { avail: false, reason: '일봉 60일 이상 필요' };
    }
    var s = 0.5, bullets = [];
    if (ind.ma5 !== null && ind.ma20 !== null && ind.ma60 !== null) {
      if (ind.ma5 > ind.ma20 && ind.ma20 > ind.ma60) {
        s += 0.18; bullets.push('5·20·60 정배열 (상승 추세)');
      } else if (ind.ma5 < ind.ma20 && ind.ma20 < ind.ma60) {
        s -= 0.18; bullets.push('5·20·60 역배열 (하락 추세)');
      } else {
        bullets.push('이동평균 혼조 (추세 미확정)');
      }
    }
    if (ind.ma120 !== null) {
      if (ind.close > ind.ma120) { s += 0.06; bullets.push('120일선 위 (' + fmtPrice(ind.ma120, inp.currency) + ')'); }
      else { s -= 0.06; bullets.push('120일선 아래 (' + fmtPrice(ind.ma120, inp.currency) + ')'); }
    }
    if (ind.crossMa5 > 0) { s += 0.12; bullets.push('5일선이 20일선을 상향 돌파(골든크로스)'); }
    else if (ind.crossMa5 < 0) { s -= 0.12; bullets.push('5일선이 20일선을 하향 돌파(데드크로스)'); }
    if (ind.crossMa60 > 0) { s += 0.08; bullets.push('60일선이 120일선을 상향 돌파'); }
    else if (ind.crossMa60 < 0) { s -= 0.08; bullets.push('60일선이 120일선을 하향 돌파'); }
    var dis = ind.ma20 ? (ind.close / ind.ma20 - 1) : null;
    if (dis !== null) {
      if (dis > 0.12) { s -= 0.12; bullets.push('20일선 이격 +' + round(dis * 100, 1) + '% (과열)'); }
      else if (dis < -0.12) { s += 0.12; bullets.push('20일선 이격 ' + round(dis * 100, 1) + '% (낙폭 과대)'); }
      else { s -= dis * 0.6; bullets.push('20일선 이격 ' + round(dis * 100, 1) + '%'); }
    }
    return { avail: true, score: clamp(s, 0, 1) * 100, reason: bullets[0] || '', bullets: bullets };
  }

  function ruleRsi(inp) {
    var r = inp.ind.rsi;
    if (r === null || !isFinite(r)) return { avail: false, reason: 'RSI 계산에 일봉 15개 이상 필요' };
    var score, tag;
    if (r >= 80) { score = 8; tag = '극단적 과매수'; }
    else if (r >= 70) { score = 22; tag = '과매수'; }
    else if (r >= 55) { score = 40; tag = '강세 구간'; }
    else if (r >= 45) { score = 52; tag = '중립'; }
    else if (r >= 30) { score = 68; tag = '약세 구간'; }
    else if (r >= 20) { score = 86; tag = '과매도'; }
    else { score = 94; tag = '극단적 과매도'; }
    return {
      avail: true, score: score,
      reason: 'RSI ' + round(r, 1) + ' · ' + tag,
      bullets: ['RSI(14) = ' + round(r, 1) + ' → ' + tag + (r < 30 ? ' (반등 후보)' : (r > 70 ? ' (조정 주의)' : ''))]
    };
  }

  function ruleMacd(inp) {
    var ind = inp.ind;
    if (ind.macd === null || ind.macdSignal === null) {
      return { avail: false, reason: 'MACD 계산에 일봉 35개 이상 필요' };
    }
    var s = 0.5, bullets = [];
    if (ind.macd > ind.macdSignal) { s += 0.12; bullets.push('MACD가 시그널선 위 (상승 모멘텀)'); }
    else { s -= 0.12; bullets.push('MACD가 시그널선 아래 (하락 모멘텀)'); }
    if (ind.macd > 0) { s += 0.08; bullets.push('MACD 0선 위 (중기 상승)'); }
    else { s -= 0.08; bullets.push('MACD 0선 아래 (중기 하락)'); }
    if (ind.macdCross > 0) { s += 0.15; bullets.push('MACD 시그널 상향 돌파(최근 3봉)'); }
    else if (ind.macdCross < 0) { s -= 0.15; bullets.push('MACD 시그널 하향 돌파(최근 3봉)'); }
    if (ind.macdHist !== null && ind.macdHistPrev !== null) {
      if (ind.macdHist > ind.macdHistPrev) { s += 0.06; }
      else { s -= 0.06; }
    }
    return { avail: true, score: clamp(s, 0, 1) * 100, reason: bullets[0], bullets: bullets };
  }

  function ruleBollinger(inp) {
    var ind = inp.ind;
    if (ind.bbPctB === null || !isFinite(ind.bbPctB)) {
      return { avail: false, reason: '볼린저밴드 계산에 일봉 20개 이상 필요' };
    }
    var b = ind.bbPctB, score, tag;
    if (b < 0) { score = 86; tag = '하단 이탈 (과매도)'; }
    else if (b < 0.2) { score = 70; tag = '하단 근접'; }
    else if (b > 1) { score = 16; tag = '상단 돌파 (과열)'; }
    else if (b > 0.8) { score = 32; tag = '상단 근접'; }
    else { score = 50 + (0.5 - b) * 30; tag = '밴드 중간'; }
    return {
      avail: true, score: clamp(score, 0, 100),
      reason: '밴드 내 위치 ' + round(b * 100, 0) + '% · ' + tag,
      bullets: ['볼린저 %B ' + round(b, 2) + ' (' + tag + ')',
        '밴드 폭 ' + (ind.bbWidth !== null ? round(ind.bbWidth * 100, 1) + '%' : '—')]
    };
  }

  function ruleVolume(inp) {
    var ind = inp.ind;
    if (ind.volRatio === null || !isFinite(ind.volRatio)) {
      return { avail: false, reason: '거래량 20일 평균을 계산할 일봉이 부족' };
    }
    var r = ind.volRatio;
    var chg = (ind.prevClose && ind.close) ? (ind.close / ind.prevClose - 1) : 0;
    var score, tag;
    if (r >= 2 && chg > 0.01) { score = 84; tag = '거래량 2배↑ + 상승 (수급 유입)'; }
    else if (r >= 2 && chg < -0.01) { score = 20; tag = '거래량 2배↑ + 하락 (투매)'; }
    else if (r >= 1.5 && chg > 0) { score = 68; tag = '거래량 증가 + 상승'; }
    else if (r >= 1.5 && chg < 0) { score = 32; tag = '거래량 증가 + 하락'; }
    else if (r < 0.6) { score = 45; tag = '거래량 위축 (관망)'; }
    else { score = 50; tag = '평균 수준'; }
    return {
      avail: true, score: score,
      reason: '20일 평균 대비 ' + round(r * 100, 0) + '%',
      bullets: ['거래량 20일 평균 대비 ' + round(r * 100, 0) + '% · ' + tag,
        '전일 대비 ' + (chg >= 0 ? '+' : '') + round(chg * 100, 2) + '%']
    };
  }

  function ruleNews(inp) {
    var nw = inp.news;
    if (!nw || !nw.count) return { avail: false, reason: '최근 10일 관련 기사 없음' };
    var sc = clamp(nw.score, -1, 1);
    var score = 50 + sc * 40;
    var top = (nw.pos && nw.pos[0]) ? nw.pos[0].title : ((nw.neg && nw.neg[0]) ? nw.neg[0].title : '');
    var posN = nw.posCount === undefined ? (nw.pos || []).length : nw.posCount;
    var negN = nw.negCount === undefined ? (nw.neg || []).length : nw.negCount;
    return {
      avail: true, score: score,
      reason: '호재 ' + posN + '건 · 악재 ' + negN + '건' +
        (sc > 0.35 ? ' (호재 우위)' : (sc < -0.35 ? ' (악재 우위)' : ' (혼조)')),
      bullets: ['기사 ' + nw.count + '건 분석 · 감성 점수 ' + round(sc, 2),
        top ? '대표: ' + top : '']
    };
  }

  function ruleSupply(inp) {
    var sp = inp.supply || {};
    var isUS = (inp.market || '') === 'US';
    var days = (sp.days || []).filter(function (d) { return d && d.f !== null; });
    if (!days.length) {
      return { avail: false, reason: isUS ? '해외 종목은 외국인·기관 수급 데이터가 없습니다' : '수급 데이터 미수집' };
    }
    var vol = last(sma(inp.ind.volumes, 20)) || 0;
    var fStreak = 0, oStreak = 0, fSellStreak = 0, fSum = 0, i;
    for (i = 0; i < days.length; i++) {
      var f = days[i].f, o = days[i].o;
      if (fStreak === i && f > 0) fStreak++;
      if (fSellStreak === i && f < 0) fSellStreak++;
      if (oStreak === i && o > 0) oStreak++;
      fSum += f || 0;
    }
    var s = 0.5, bullets = [];
    s += clamp(fStreak, 0, 5) * 0.06;
    s -= clamp(fSellStreak, 0, 5) * 0.05;
    if (fStreak >= 2) bullets.push('외국인 ' + fStreak + '일 연속 순매수');
    else if (fStreak === 1) bullets.push('외국인 순매수 (최근 1일)');
    else if (fSellStreak >= 2) bullets.push('외국인 ' + fSellStreak + '일 연속 순매도');
    else bullets.push('외국인 순매도 (최근 1일)');
    if (oStreak >= 2) bullets.push('기관 ' + oStreak + '일 연속 순매수');
    else if (oStreak === 1) { bullets.push('기관 순매수 (최근 1일)'); s += 0.02; }
    else bullets.push('기관 순매도 우위');
    if (oStreak >= 2) s += 0.06;
    if (vol > 0) {
      var fRatio = fSum / (vol * Math.max(1, days.length));
      if (fRatio > 0.03) { s += 0.12; bullets.push('최근 외국인 순매수량이 거래량의 ' + round(fRatio * 100, 1) + '%'); }
      else if (fRatio < -0.03) { s -= 0.12; bullets.push('최근 외국인 순매도량이 거래량의 ' + round(-fRatio * 100, 1) + '%'); }
    }
    var fr = sp.foreign_rate;
    if (fr !== null && fr !== undefined) bullets.push('외국인 보유율 ' + round(fr, 2) + '%');
    return {
      avail: true, score: clamp(s, 0, 1) * 100,
      reason: bullets[0], bullets: bullets
    };
  }

  function ruleValuation(inp) {
    var v = inp.valuation || {};
    var price = inp.price || inp.ind.close;
    var high = num(v.high52), low = num(v.low52);
    var per = num(v.per), pbr = num(v.pbr);
    // 기업 카드 위쪽의 PER/PBR 은 앱이 자체 EPS/BPS 로 다시 계산한 값이라 기준이 다를 수 있다 —
    // 이 규칙의 값은 어느 제공처에서 왔는지 함께 적는다.
    var src = (inp.market === 'US') ? '야후 파이낸스' : '네이버 금융';
    var pos52 = (high !== null && low !== null && high > low && price) ? (price - low) / (high - low) : null;
    if (pos52 === null && per === null && pbr === null) {
      return { avail: false, reason: '밸류에이션·52주 데이터 미수집' };
    }
    var s = 0.5, bullets = [];
    if (pos52 !== null) {
      var p = clamp(pos52, 0, 1);
      if (p >= 0.9) { s -= 0.16; bullets.push('52주 최고가 대비 ' + round((1 - p) * 100, 0) + '% (고점권)'); }
      else if (p >= 0.75) { s -= 0.08; bullets.push('52주 위치 ' + round(p * 100, 0) + '% (상단)'); }
      else if (p <= 0.15) { s += 0.14; bullets.push('52주 최저가 대비 ' + round(p * 100, 0) + '% (저점권)'); }
      else if (p <= 0.35) { s += 0.07; bullets.push('52주 위치 ' + round(p * 100, 0) + '% (하단)'); }
      else { bullets.push('52주 위치 ' + round(p * 100, 0) + '%'); }
    }
    if (per !== null) {
      if (per <= 0) { s -= 0.05; bullets.push('PER ' + round(per, 2) + ' (적자 · ' + src + ')'); }
      else if (per <= 8) { s += 0.10; bullets.push('PER ' + round(per, 2) + ' (낮음 · ' + src + ')'); }
      else if (per <= 15) { s += 0.05; bullets.push('PER ' + round(per, 2) + ' (' + src + ')'); }
      else if (per <= 30) { bullets.push('PER ' + round(per, 2) + ' (' + src + ')'); }
      else if (per <= 50) { s -= 0.06; bullets.push('PER ' + round(per, 2) + ' (부담 · ' + src + ')'); }
      else { s -= 0.12; bullets.push('PER ' + round(per, 2) + ' (높음 · ' + src + ')'); }
    }
    if (pbr !== null) {
      if (pbr < 1) { s += 0.08; bullets.push('PBR ' + round(pbr, 2) + ' (자산가치 이하)'); }
      else if (pbr <= 2) { s += 0.04; bullets.push('PBR ' + round(pbr, 2)); }
      else if (pbr <= 5) { bullets.push('PBR ' + round(pbr, 2)); }
      else { s -= 0.08; bullets.push('PBR ' + round(pbr, 2) + ' (높음)'); }
    }
    return { avail: true, score: clamp(s, 0, 1) * 100, reason: bullets[0], bullets: bullets };
  }

  var RULES = [
    { id: 'ma_trend', group: 'chart', weight: 25, fn: ruleMaTrend },
    { id: 'rsi', group: 'chart', weight: 20, fn: ruleRsi },
    { id: 'macd', group: 'chart', weight: 25, fn: ruleMacd },
    { id: 'bollinger', group: 'chart', weight: 15, fn: ruleBollinger },
    { id: 'volume', group: 'chart', weight: 15, fn: ruleVolume },
    { id: 'news', group: 'news', weight: 100, fn: ruleNews },
    { id: 'supply', group: 'supply', weight: 100, fn: ruleSupply },
    { id: 'valuation', group: 'valuation', weight: 100, fn: ruleValuation }
  ];

  /* --------------------------------------------------------------- 뉴스 감성 */
  var POS_TERMS = {
    '급등': 2, '상한가': 3, '신고가': 3, '최고가': 2, '사상 최대': 2.5, '역대급': 2,
    '호실적': 2.5, '어닝서프라이즈': 3, '흑자': 1.5, '흑자전환': 2.5, '적자 축소': 1.8,
    '수주': 2, '계약': 1, '공급 확대': 2, '증설': 1.5, '투자 확대': 1.5, '수혜': 1.5,
    '목표가 상향': 2.5, '목표주가 상향': 2.5, '상향': 1, '매수의견': 1.5, '매수': 1,
    '실적 개선': 2, '개선': 1, '성장': 1, '반등': 1.5, '회복': 1, '돌파': 1,
    '승인': 1.5, '출시': 1, '협력': 0.8, '제휴': 0.8, '기대': 0.8, '강세': 1,
    '자사주': 1, '배당 확대': 1.5, '무상증자': 1.2, '수출 증가': 1.5, '점유율 확대': 1.5,
    'MOU': 1, '수혜주': 1.5, '훈풍': 1, '랠리': 1.2, '상승 마감': 1.2, '최대 실적': 2.5
  };
  var NEG_TERMS = {
    '급락': 2, '하한가': 3, '폭락': 3, '신저가': 2.5, '최저가': 2, '약세': 1,
    '부진': 1.8, '실적 부진': 2.5, '쇼크': 1.5, '적자': 1.5, '적자전환': 2.5, '손실': 1.2,
    '우려': 1.5, '리스크': 1, '경고': 1.5, '위기': 1.8, '악재': 2, '하락': 1,
    '목표가 하향': 2.5, '목표주가 하향': 2.5, '하향': 1, '매도의견': 1.5, '축소': 1,
    '감소': 0.8, '둔화': 1.2, '부도': 2.5, '상장폐지': 3, '유상증자': 1.5, '감자': 1.5,
    '소송': 1.5, '제재': 1.5, '조사': 1, '압수수색': 2, '과징금': 1.8, '벌금': 1.5,
    '리콜': 2, '결함': 1.5, '파업': 1.5, '해킹': 1.5, '논란': 1, '의혹': 1.2,
    '경영권 분쟁': 1.5, '철수': 1.5, '구조조정': 1.5, '하락 마감': 1.2, '매도': 1
  };
  // 문맥으로 뒤집히는 표현(먼저 처리하고 본문에서 지운다)
  var PHRASES = [
    { re: /적자\s*탈출|흑자\s*전환/g, w: 2.5 },
    { re: /우려\s*해소|리스크\s*해소/g, w: 2 },
    { re: /목표가\s*하향|목표주가\s*하향/g, w: -2.5 },
    { re: /목표가\s*상향|목표주가\s*상향/g, w: 2.5 },
    { re: /실적\s*부진/g, w: -2.5 },
    { re: /사상\s*최대/g, w: 2.5 },
    { re: /하락\s*마감/g, w: -1.2 },
    { re: /상승\s*마감/g, w: 1.2 }
  ];

  function termHits(text, terms) {
    var total = 0, found = [];
    if (!text) return { total: 0, found: found };
    Object.keys(terms).forEach(function (term) {
      if (text.indexOf(term) !== -1) {
        total += terms[term];
        found.push(term);
      }
    });
    return { total: total, found: found };
  }

  function scoreArticle(title, summary) {
    var text = String(title || '');
    var body = String(summary || '');
    var delta = 0, hits = [];
    PHRASES.forEach(function (p) {
      var m = text.match(p.re);
      if (m) {
        delta += p.w * m.length;
        hits.push(m[0]);
        text = text.replace(p.re, ' ');
      }
    });
    var tPos = termHits(text, POS_TERMS), tNeg = termHits(text, NEG_TERMS);
    delta += tPos.total - tNeg.total;
    hits = hits.concat(tPos.found, tNeg.found);
    var bPos = termHits(body, POS_TERMS), bNeg = termHits(body, NEG_TERMS);
    delta += (bPos.total - bNeg.total) * 0.45;
    var polarity = clamp(delta / 2.5, -1, 1);
    return { polarity: polarity, hits: hits };
  }

  function articleDate(a) {
    var raw = a.published_at || a.date;
    if (!raw) return null;
    var t = Date.parse(raw);
    return isFinite(t) ? t : null;
  }

  function analyzeNews(articles, nowMs) {
    var now = nowMs || Date.now();
    var items = [], pos = [], neg = [];
    (articles || []).forEach(function (a) {
      if (!a || !a.title) return;
      var t = articleDate(a);
      var ageDays = t ? Math.max(0, (now - t) / 86400000) : 3;
      var weight = Math.max(0.15, Math.pow(0.5, ageDays / 3));   // 반감기 3일
      var sc = scoreArticle(a.title, a.summary || a.desc || '');
      var entry = {
        title: a.title, source: a.source || '', url: a.url || '',
        date: a.date || (a.published_at || '').slice(0, 10),
        polarity: sc.polarity, weight: weight, hits: sc.hits, origin: a.origin || ''
      };
      items.push(entry);
      if (sc.polarity > 0.15) pos.push(entry);
      else if (sc.polarity < -0.15) neg.push(entry);
    });
    if (!items.length) return { score: 0, count: 0, pos: [], neg: [], neu: 0, posCount: 0, negCount: 0 };
    var wsum = 0, acc = 0;
    items.forEach(function (it) { wsum += it.weight; acc += it.weight * it.polarity; });
    // 최근 기사 비중만 반영하면 '오래된 기사 한 건'도 100% 로 남는다. 몇 건이 모였는지에
    // 따라 점수 크기를 정하도록 분모에 최소 근거량(1.8)을 둔다 — 오래된 뉴스는 0에 가깝게 수렴.
    var score = wsum > 0 ? clamp((acc / Math.max(wsum, 1.8)) * 1.8, -1, 1) : 0;
    pos.sort(function (a, b) { return (b.weight * b.polarity) - (a.weight * a.polarity); });
    neg.sort(function (a, b) { return (a.weight * a.polarity) - (b.weight * b.polarity); });
    return {
      score: score, count: items.length,
      pos: pos.slice(0, 3), neg: neg.slice(0, 3),
      posCount: pos.length, negCount: neg.length,
      neu: items.length - pos.length - neg.length,
      asof: items.length ? items[0].date : null
    };
  }

  /* ------------------------------------------------------------- settings */
  function normalizeSettings(raw) {
    var out = { weights: {}, rules: {} };
    var w = (raw && raw.weights) || {};
    GROUP_ORDER.forEach(function (g) {
      var v = num(w[g]);
      out.weights[g] = (v === null) ? DEFAULT_SETTINGS.weights[g] : clamp(v, 0, 100);
    });
    var r = (raw && raw.rules) || {};
    RULES.forEach(function (rule) {
      out.rules[rule.id] = (r[rule.id] === undefined) ? DEFAULT_SETTINGS.rules[rule.id] : !!r[rule.id];
    });
    return out;
  }

  function loadSettings() {
    try {
      var raw = localStorage.getItem(SETTINGS_KEY);
      return normalizeSettings(raw ? JSON.parse(raw) : null);
    } catch (e) { return normalizeSettings(null); }
  }

  function saveSettings(settings) {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(normalizeSettings(settings))); } catch (e) {}
  }

  /* --------------------------------------------------------------- engine */
  function grade(score, confidence) {
    var label, color, key;
    if (score >= 70) { key = 'strong_buy'; label = '매수우위'; color = '#2ea043'; }
    else if (score >= 60) { key = 'buy_interest'; label = '매수관심'; color = '#3fb950'; }
    else if (score >= 40) { key = 'hold'; label = '관망'; color = '#8b949e'; }
    else if (score >= 30) { key = 'sell_caution'; label = '매도주의'; color = '#d29922'; }
    else { key = 'strong_sell'; label = '매도우위'; color = '#f85149'; }
    var tag = confidence < 40 ? ' (신뢰도 낮음)' : '';
    return { key: key, label: label + tag, color: color };
  }

  function compute(inputs, settings) {
    settings = normalizeSettings(settings);
    inputs = inputs || {};
    var bars = inputs.bars || [];
    var ind = computeIndicators(bars);
    var price = num(inputs.price);
    var ctx = {
      bars: bars, ind: ind, currency: inputs.currency || 'KRW',
      price: price || ind.close, market: inputs.market || '',
      supply: inputs.supply || {}, valuation: inputs.valuation || {},
      news: inputs.news || { score: 0, count: 0, pos: [], neg: [] }
    };

    var rules = [];
    var groupWeights = {};     // 이번 계산에서 실제로 쓴 규칙 가중치 합
    var groupAccum = {};
    GROUP_ORDER.forEach(function (g) { groupWeights[g] = 0; groupAccum[g] = { sum: 0, w: 0, bullets: [], rules: [] }; });

    var availableRuleWeight = 0;
    RULES.forEach(function (rule) {
      var on = settings.rules[rule.id] !== false;
      var groupWeight = settings.weights[rule.group] || 0;
      var entry = { id: rule.id, group: rule.group, label: RULE_LABELS[rule.id] || rule.id, on: on };
      if (!on) {
        entry.available = false; entry.off = true; entry.reason = '사용 안 함';
        rules.push(entry);
        return;
      }
      var res;
      try { res = rule.fn(ctx) || {}; }
      catch (e) { res = { avail: false, reason: '계산 오류' }; }
      entry.available = !!res.avail;
      entry.score = res.avail ? clamp(res.score, 0, 100) : null;
      entry.reason = res.reason || '';
      entry.bullets = res.bullets || (res.reason ? [res.reason] : []);
      entry.weight = groupWeight * (rule.weight / 100);
      if (entry.available) {
        availableRuleWeight += entry.weight * (groupWeight > 0 ? 1 : 0);
        groupAccum[rule.group].sum += (entry.score * rule.weight);
        groupAccum[rule.group].w += rule.weight;
        groupWeights[rule.group] += rule.weight;
      }
      rules.push(entry);
    });

    var groups = [];
    var totalSum = 0, totalWeight = 0;
    GROUP_ORDER.forEach(function (g) {
      var acc = groupAccum[g];
      var weight = settings.weights[g] || 0;
      var score = acc.w > 0 ? acc.sum / acc.w : null;
      var enabledInGroup = rules.some(function (r) { return r.group === g && r.on; });
      var entry = {
        key: g, name: GROUP_META[g].name, icon: GROUP_META[g].icon,
        weight: weight, score: score, available: score !== null,
        enabled: enabledInGroup && weight > 0,
        bullets: rules.filter(function (r) { return r.group === g && r.on && r.available; })
          .reduce(function (acc2, r) { return acc2.concat(r.bullets.slice(0, 2)); }, []).slice(0, 4)
      };
      if (entry.available && weight > 0) {
        totalSum += score * weight;
        totalWeight += weight;
      }
      groups.push(entry);
    });

    var total = totalWeight > 0 ? totalSum / totalWeight : null;
    var enabledWeightTotal = GROUP_ORDER.reduce(function (sum, g) {
      var on = rules.some(function (r) { return r.group === g && r.on; });
      return sum + (on ? (settings.weights[g] || 0) : 0);
    }, 0);
    var confidence = enabledWeightTotal > 0 ? (availableRuleWeight / enabledWeightTotal) : 0;
    confidence = clamp(confidence, 0, 1) * 100;

    var availableRules = rules.filter(function (r) { return r.available; }).length;
    var enabledRules = rules.filter(function (r) { return r.on; }).length;

    var g = total === null ? { key: 'none', label: '데이터 없음', color: '#8b949e' } : grade(total, confidence);

    return {
      score: total === null ? null : round(total, 1),
      grade: g,
      confidence: round(confidence, 0),
      counts: { available: availableRules, enabled: enabledRules, total: RULES.length },
      groups: groups,
      rules: rules,
      indicators: {
        rsi: ind.rsi === null ? null : round(ind.rsi, 1),
        macd: ind.macd === null ? null : round(ind.macd, 2),
        ma5: ind.ma5, ma20: ind.ma20, ma60: ind.ma60, ma120: ind.ma120,
        volRatio: ind.volRatio === null ? null : round(ind.volRatio, 2),
        pctB: ind.bbPctB === null ? null : round(ind.bbPctB, 2),
        lastDate: ind.dates.length ? ind.dates[ind.dates.length - 1] : null,
        bars: ind.n
      },
      news: ctx.news,
      settings: settings
    };
  }

  /* ------------------------------------------------------------------ data */
  var recordCache = {};

  function dataUrl(path) {
    var stamp = '?t=' + Date.now();
    if (typeof location !== 'undefined' && location.protocol === 'file:') {
      return 'https://kthink79.github.io/stock-briefing-app/' + path + stamp;
    }
    return path + stamp;
  }

  function fetchJSON(url, ms) {
    var timeout = ms || 12000;
    return new Promise(function (resolve, reject) {
      var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      var timer = setTimeout(function () { if (ctrl) { try { ctrl.abort(); } catch (e) {} } reject(new Error('timeout')); }, timeout);
      fetch(url, { cache: 'no-store', signal: ctrl ? ctrl.signal : undefined })
        .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
        .then(function (json) { clearTimeout(timer); resolve(json); })
        .catch(function (err) { clearTimeout(timer); reject(err); });
    });
  }

  function loadRecord(code, force) {
    if (!force && recordCache[code]) return Promise.resolve(recordCache[code]);
    var safe = String(code).replace(/[^0-9A-Za-z._-]/g, '_');
    return fetchJSON(dataUrl(HISTORY_BASE + safe + '.json')).then(function (rec) {
      recordCache[code] = rec;
      return rec;
    });
  }

  /* -------------------------------------------------------------------- UI */
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function scoreColor(score) {
    if (score === null || score === undefined) return '#8b949e';
    if (score >= 70) return '#2ea043';
    if (score >= 60) return '#3fb950';
    if (score >= 40) return '#8b949e';
    if (score >= 30) return '#d29922';
    return '#f85149';
  }

  function gaugeHtml(result) {
    var score = result.score === null ? 0 : result.score;
    var color = result.grade.color;
    var r = 78, cx = 100, cy = 100;
    var len = Math.PI * r;
    var offset = len * (1 - clamp(score, 0, 100) / 100);
    return '' +
      '<div style="position:relative; width:200px; max-width:100%; margin:0 auto;">' +
      '<svg viewBox="0 0 200 118" width="100%" height="118" aria-hidden="true">' +
      '<path d="M22,100 A78,78 0 0,1 178,100" fill="none" stroke="#21262d" stroke-width="16" stroke-linecap="round"></path>' +
      '<path d="M22,100 A78,78 0 0,1 178,100" fill="none" stroke="' + color + '" stroke-width="16" stroke-linecap="round" ' +
      'stroke-dasharray="' + len.toFixed(1) + '" stroke-dashoffset="' + offset.toFixed(1) + '"></path>' +
      '<text x="100" y="88" text-anchor="middle" fill="#f0f6fc" font-size="40" font-weight="800">' +
      (result.score === null ? '—' : Math.round(result.score)) + '</text>' +
      '<text x="100" y="110" text-anchor="middle" fill="#8b949e" font-size="12">100점 만점</text>' +
      '</svg></div>';
  }

  function groupRowHtml(g) {
    if (!g.enabled) {
      return '<div class="sig-row sig-off"><div class="sig-row-head"><span class="sig-name">' + g.icon + ' ' + esc(g.name) + '</span>' +
        '<span class="sig-score">제외</span></div>' +
        '<div class="sig-reason">가중치 0% 또는 규칙 꺼짐</div></div>';
    }
    if (!g.available) {
      return '<div class="sig-row sig-off"><div class="sig-row-head"><span class="sig-name">' + g.icon + ' ' + esc(g.name) + '</span>' +
        '<span class="sig-score">—</span></div>' +
        '<div class="sig-reason">데이터 없음 → 가중치 자동 제외</div></div>';
    }
    var pct = clamp(g.score, 0, 100);
    var color = scoreColor(g.score);
    return '<div class="sig-row">' +
      '<div class="sig-row-head"><span class="sig-name">' + g.icon + ' ' + esc(g.name) + '</span>' +
      '<span class="sig-score" style="color:' + color + ';">' + Math.round(g.score) + '<span class="sig-weight"> · 가중 ' + g.weight + '</span></span></div>' +
      '<div class="sig-bar"><span style="width:' + pct.toFixed(0) + '%; background:' + color + ';"></span></div>' +
      (g.bullets.filter(Boolean).slice(0, 3).map(function (b) {
        return '<div class="sig-reason">· ' + esc(b) + '</div>';
      }).join('')) +
      '</div>';
  }

  function newsBlockHtml(news) {
    if (!news || !news.count) {
      return '<div class="sig-news-empty">최근 관련 기사를 찾지 못했습니다. (뉴스 규칙은 가중치에서 제외됩니다)</div>';
    }
    function list(items, tone) {
      if (!items.length) {
        return '<div class="sig-news-none">' + (tone === 'pos' ? '호재 기사 없음' : '악재 기사 없음') + '</div>';
      }
      return items.map(function (a) {
        var inner = esc(a.title);
        var head = a.url
          ? '<a href="' + esc(a.url) + '" target="_blank" rel="noopener" style="color:#e6edf3; text-decoration:none;">' + inner + '</a>'
          : inner;
        return '<div class="sig-news-item"><span class="sig-dot" style="background:' +
          (tone === 'pos' ? '#3fb950' : '#f85149') + ';"></span><div>' + head +
          '<div class="sig-news-meta">' + esc(a.source || '') + (a.date ? ' · ' + esc(a.date) : '') +
          (a.hits && a.hits.length ? ' · ' + esc(a.hits.slice(0, 3).join(', ')) : '') + '</div></div></div>';
      }).join('');
    }
    return '<div class="sig-news-cols">' +
      '<div class="sig-news-col"><div class="sig-news-title pos">호재 ' + news.pos.length + '건</div>' + list(news.pos, 'pos') + '</div>' +
      '<div class="sig-news-col"><div class="sig-news-title neg">악재 ' + news.neg.length + '건</div>' + list(news.neg, 'neg') + '</div>' +
      '</div>' +
      '<div class="sig-news-meta" style="margin-top:6px;">분석 기사 ' + news.count + '건 (중립 ' + (news.neu || 0) + '건) · 최근 10일 가중</div>';
  }

  function summaryLine(result) {
    var avail = result.groups.filter(function (g) { return g.available && g.enabled; });
    if (!avail.length) return '분석에 쓸 데이터가 아직 없습니다.';
    var best = avail.slice().sort(function (a, b) { return b.score - a.score; })[0];
    var worst = avail.slice().sort(function (a, b) { return a.score - b.score; })[0];
    if (best.key === worst.key) return best.name + ' 신호가 중립 수준입니다.';
    return best.name + ' ' + Math.round(best.score) + '점(긍정) · ' + worst.name + ' ' + Math.round(worst.score) + '점(부정)';
  }

  function cardHtml(result, ctx) {
    var updated = ctx.record && ctx.record.updated_at ? ctx.record.updated_at.slice(0, 16).replace('T', ' ') : '—';
    var lastBar = result.indicators.lastDate ? result.indicators.lastDate.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3') : '—';
    var basis = (ctx.livePrice ? '장중 ' : '종가 ') + esc(lastBar);
    return '' +
      '<div class="sig-head">' +
        '<div><div class="sig-title">🎯 매수·매도 시점 분석</div>' +
        '<div class="sig-sub">' + basis + ' · 일봉 ' + result.indicators.bars + '일 · 갱신 ' + esc(updated) + '</div></div>' +
        '<button class="btn" style="background:#21262d;border:1px solid #30363d;color:#c9d1d9;font-size:0.78rem;padding:6px 10px;" onclick="StockSignal.reload(\'' + esc(ctx.code) + '\')">↻ 다시 분석</button>' +
      '</div>' +
      gaugeHtml(result) +
      '<div class="sig-grade" style="color:' + result.grade.color + ';">' + esc(result.grade.label) + '</div>' +
      '<div class="sig-meta">신뢰도 ' + result.confidence + '% · 근거 ' + result.counts.available + '/' + result.counts.enabled + '개 규칙' +
        (ctx.livePrice ? ' · <span style="color:#58a6ff;">장중 현재가 반영</span>' : '') + '</div>' +
      '<div class="sig-summary">' + esc(summaryLine(result)) + '</div>' +
      '<div class="sig-groups">' + result.groups.map(groupRowHtml).join('') + '</div>' +
      '<div class="sig-section-title">📰 호재 · 악재 뉴스</div>' + newsBlockHtml(result.news) +
      '<div class="sig-note">규칙별 점수(0~100)를 가중 평균한 참고 지표입니다. 투자 판단과 책임은 본인에게 있습니다.</div>';
  }

  function errorHtml(message, code) {
    return '<div class="sig-head"><div class="sig-title">🎯 매수·매도 시점 분석</div></div>' +
      '<div class="sig-empty">' + esc(message) + '</div>' +
      '<div class="sig-note">이 종목의 일봉 데이터가 아직 수집되지 않았습니다. 시가총액 상위 종목은 자동 수집되며, 다음 갱신 때 다시 시도합니다. ' +
      '<button class="btn" style="background:#21262d;border:1px solid #30363d;color:#c9d1d9;font-size:0.78rem;padding:5px 10px;margin-left:6px;" onclick="StockSignal.reload(\'' + esc(code) + '\')">↻ 다시 시도</button></div>';
  }

  var current = null;   // {ctx, result, el}
  var lastHtml = { code: null, html: '' };

  function paint(el, html, code) {
    if (lastHtml.code === code && lastHtml.html === html) return;
    lastHtml = { code: code, html: html };
    el.innerHTML = html;
  }

  function mount(ctx) {
    var el = document.getElementById('comp-signal-body');
    if (!el) return;
    current = { ctx: ctx, el: el };
    if (!recordCache[ctx.code]) {
      el.innerHTML = '<div class="sig-loading">차트·수급·뉴스 데이터를 분석하는 중…</div>';
      lastHtml = { code: ctx.code, html: el.innerHTML };
    }
    analyze(ctx).then(function (res) {
      if (!current || current.ctx.code !== ctx.code) return;
      paint(el, res.error ? errorHtml(res.error, ctx.code) : cardHtml(res.result, res.ctx), ctx.code);
    }).catch(function (err) {
      if (!current) return;
      paint(el, errorHtml('분석 데이터를 불러오지 못했습니다 (' + (err && err.message ? err.message : 'network') + ')', ctx.code), ctx.code);
    });
  }

  function reload(code) {
    if (code) delete recordCache[code];
    if (current && current.ctx) {
      if (code) current.ctx.code = code;
      mount(current.ctx);
    }
  }

  /* 뉴스 기사: 앱이 이미 받아 둔 기사를 분석 시점에 다시 읽는다.
     (뉴스가 늦게 도착하면 처음 마운트 때 목록이 비어 있으므로, 전역 함수가 있으면 그것을 쓴다) */
  function collectArticles(ctx) {
    try {
      if (typeof companyArticlesFor === 'function' && typeof saveNewsList === 'function' &&
          typeof mergeArticles === 'function') {
        var server = companyArticlesFor(ctx.code, ctx.name) || [];
        var tagged = saveNewsList().filter(function (a) {
          return (a.companies || []).indexOf(ctx.code) !== -1;
        });
        var merged = mergeArticles([server, tagged]);
        if (merged && merged.length) return merged;
      }
    } catch (e) { /* 앱 전역 함수가 없으면 넘겨받은 목록을 쓴다 */ }
    return ctx.articles || [];
  }

  function analyze(ctx) {
    return loadRecord(ctx.code).then(function (rec) {
      if (!rec || !rec.bars || rec.bars.length < 5) {
        return { error: '일봉 데이터가 부족합니다 (' + ((rec && rec.bars && rec.bars.length) || 0) + '일).' };
      }
      var bars = rec.bars.slice();
      var livePrice = null;
      var price = num(ctx.price) || num(rec.price);
      if (price && bars.length) {
        var lastBar = bars[bars.length - 1];
        var lastClose = num(lastBar[4]);
        var today = new Date(Date.now() + 9 * 3600 * 1000);
        var todayKey = today.getUTCFullYear() + ('0' + (today.getUTCMonth() + 1)).slice(-2) + ('0' + today.getUTCDate()).slice(-2);
        if (String(lastBar[0]) !== todayKey && lastClose && Math.abs(price - lastClose) > 0.001) {
          // 마지막 봉 이후의 실제 현재가를 임시 봉으로 붙여 장중 흐름을 반영한다(거래량은 평균치로 대체하지 않고 0).
          bars.push([Number(todayKey), price, price, price, price, 0, null]);
          livePrice = price;
        }
      }
      var news = analyzeNews(collectArticles(ctx));
      var inputs = {
        bars: bars, price: price, currency: rec.currency || ctx.currency || 'KRW',
        market: rec.market || ctx.market, supply: rec.supply,
        valuation: rec.valuation, news: news
      };
      var result = compute(inputs, loadSettings());
      return { result: result, ctx: { code: ctx.code, record: rec, livePrice: livePrice } };
    });
  }

  function refresh() {
    if (!current || !current.ctx) return;
    analyze(current.ctx).then(function (res) {
      if (!res || !res.result || !current) return;
      paint(current.el, cardHtml(res.result, res.ctx), current.ctx.code);
    });
  }

  /* --------------------------------------------------------- settings UI */
  var PRESETS = {
    balanced: { label: '균형', weights: { chart: 40, news: 25, supply: 20, valuation: 15 } },
    chart: { label: '차트 중심', weights: { chart: 55, news: 20, supply: 15, valuation: 10 } },
    news: { label: '뉴스 중심', weights: { chart: 25, news: 50, supply: 15, valuation: 10 } },
    safe: { label: '수급·가치 중심', weights: { chart: 25, news: 15, supply: 30, valuation: 30 } }
  };

  function settingsHtml(settings) {
    var total = GROUP_ORDER.reduce(function (s, g) { return s + (settings.weights[g] || 0); }, 0) || 1;
    var rows = GROUP_ORDER.map(function (g) {
      var w = settings.weights[g];
      return '<div class="sig-set-row">' +
        '<div class="sig-set-head"><span class="sig-name">' + GROUP_META[g].icon + ' ' + GROUP_META[g].name + '</span>' +
        '<span class="sig-set-val" id="sig-weight-val-' + g + '">' + w + ' <span class="sig-weight">(' + Math.round(w / total * 100) + '%)</span></span></div>' +
        '<input type="range" min="0" max="100" step="5" value="' + w + '" id="sig-weight-' + g + '" ' +
        'oninput="StockSignal.setWeight(\'' + g + '\', this.value)" style="width:100%; accent-color:#58a6ff; height:26px;">' +
        '</div>';
    }).join('');

    var toggles = RULES.map(function (rule) {
      var on = settings.rules[rule.id] !== false;
      return '<label class="sig-toggle">' +
        '<input type="checkbox" ' + (on ? 'checked' : '') + ' onchange="StockSignal.setRule(\'' + rule.id + '\', this.checked)" style="width:20px;height:20px;accent-color:#238636;">' +
        '<span><strong>' + esc(RULE_LABELS[rule.id]) + '</strong><br><span class="sig-weight">' + esc(GROUP_META[rule.group].name) + '</span></span>' +
        '</label>';
    }).join('');

    var presetBtns = Object.keys(PRESETS).map(function (k) {
      return '<button type="button" class="btn-tag" style="font-size:0.75rem;padding:6px 10px;" onclick="StockSignal.applyPreset(\'' + k + '\')">' + PRESETS[k].label + '</button>';
    }).join('');

    return '' +
      '<div class="sig-sub" style="margin-bottom:10px;">차트·뉴스·수급·밸류에이션 비중을 직접 조절합니다. 데이터가 없는 영역은 자동으로 빠지고 남은 비중으로 다시 계산합니다.</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">' + presetBtns + '</div>' +
      rows +
      '<div class="sig-section-title" style="margin-top:14px;">규칙 ON/OFF</div>' +
      '<div class="sig-toggles">' + toggles + '</div>' +
      '<div style="display:flex;gap:8px;margin-top:12px;">' +
      '<button class="btn btn-blue" style="flex:1;" onclick="StockSignal.persistSettings()">가중치 저장</button>' +
      '<button class="btn" style="background:#30363d;color:#c9d1d9;" onclick="StockSignal.resetSettings()">기본값</button>' +
      '</div>' +
      '<div class="sig-note">저장한 설정은 이 기기에 보관되며, 기업 분석 카드의 점수가 즉시 다시 계산됩니다.</div>';
  }

  var draftSettings = null;

  function renderSettings(containerId) {
    var el = document.getElementById(containerId || 'sig-settings-body');
    if (!el) return;
    draftSettings = loadSettings();
    el.innerHTML = settingsHtml(draftSettings);
  }

  function setWeight(group, value) {
    if (!draftSettings) draftSettings = loadSettings();
    draftSettings.weights[group] = clamp(num(value) || 0, 0, 100);
    var total = GROUP_ORDER.reduce(function (s, g) { return s + (draftSettings.weights[g] || 0); }, 0) || 1;
    GROUP_ORDER.forEach(function (g) {
      var labelEl = document.getElementById('sig-weight-val-' + g);
      if (labelEl) {
        labelEl.innerHTML = draftSettings.weights[g] + ' <span class="sig-weight">(' +
          Math.round(draftSettings.weights[g] / total * 100) + '%)</span>';
      }
    });
    saveSettings(draftSettings);
    debounceRefresh();
  }

  function setRule(id, on) {
    if (!draftSettings) draftSettings = loadSettings();
    draftSettings.rules[id] = !!on;
    saveSettings(draftSettings);
    debounceRefresh();
  }

  function applyPreset(key) {
    var preset = PRESETS[key];
    if (!preset) return;
    if (!draftSettings) draftSettings = loadSettings();
    GROUP_ORDER.forEach(function (g) { draftSettings.weights[g] = preset.weights[g]; });
    saveSettings(draftSettings);
    renderSettings();
    debounceRefresh();
  }

  function persistSettings() {
    if (draftSettings) saveSettings(draftSettings);
    refresh();
    var el = document.getElementById('sig-settings-saved');
    if (el) {
      el.innerText = '저장됨 · 기업 분석 카드에 반영했습니다';
      el.style.color = '#3fb950';
      setTimeout(function () { if (el) el.innerText = ''; }, 3000);
    }
  }

  function resetSettings() {
    draftSettings = normalizeSettings(null);
    saveSettings(draftSettings);
    renderSettings();
    debounceRefresh();
  }

  var refreshTimer = null;
  function debounceRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () { refreshTimer = null; refresh(); }, 220);
  }

  function loadManifest() {
    return fetchJSON(dataUrl(MANIFEST_PATH)).then(function (m) { return m; });
  }

  /* ------------------------------------------------------------- public */
  var api = {
    version: VERSION,
    compute: compute,
    grade: grade,
    computeIndicators: computeIndicators,
    analyzeNews: analyzeNews,
    scoreArticle: scoreArticle,
    normalizeSettings: normalizeSettings,
    mount: mount,
    reload: reload,
    refresh: refresh,
    analyze: analyze,
    renderSettings: renderSettings,
    setWeight: setWeight,
    setRule: setRule,
    applyPreset: applyPreset,
    persistSettings: persistSettings,
    resetSettings: resetSettings,
    loadManifest: loadManifest,
    loadRecord: loadRecord,
    __cardHtml: cardHtml,
    __settingsHtml: function () { return settingsHtml(loadSettings()); },
    RULES: RULES,
    GROUP_ORDER: GROUP_ORDER
  };
  return api;
})();

if (typeof window !== 'undefined') {
  window.StockSignal = StockSignal;
}
