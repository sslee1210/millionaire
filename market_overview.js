const OVERVIEW_CACHE_MS = Number(process.env.OVERVIEW_CACHE_MS || 5000);
const OVERVIEW_HISTORY_LIMIT = Number(process.env.OVERVIEW_HISTORY_LIMIT || 80);

let cachedAt = 0;
let cachedPayload = null;
const history = new Map();

const SOURCES = [
  { key: 'KOSPI', label: '코스피', type: 'naver-index', code: 'KOSPI' },
  { key: 'KOSDAQ', label: '코스닥', type: 'naver-index', code: 'KOSDAQ' },
  { key: 'USD_KRW', label: '달러/원', type: 'naver-exchange', code: 'FX_USDKRW' },
  { key: 'NASDAQ', label: '나스닥', type: 'naver-world', code: 'NAS@IXIC', yahoo: '^IXIC' },
];

export async function getMarketOverview() {
  const now = Date.now();
  if (cachedPayload && now - cachedAt < OVERVIEW_CACHE_MS) {
    return cachedPayload;
  }

  const items = await Promise.all(SOURCES.map(fetchOverviewItem));
  cachedAt = now;
  cachedPayload = {
    ok: items.some((item) => item.ok),
    updatedAt: new Date(now).toISOString(),
    cacheMs: OVERVIEW_CACHE_MS,
    sourcePolicy: 'Naver overview first; Yahoo fallback for Nasdaq only when Naver page parsing fails',
    items,
  };
  return cachedPayload;
}

async function fetchOverviewItem(config) {
  try {
    let parsed = null;
    if (config.type === 'naver-index') parsed = await fetchNaverIndex(config.code);
    if (config.type === 'naver-exchange') parsed = await fetchNaverExchange(config.code);
    if (config.type === 'naver-world') parsed = await fetchNaverWorld(config.code);

    if ((!parsed || !Number.isFinite(parsed.value)) && config.yahoo) {
      parsed = await fetchYahooChart(config.yahoo);
    }

    if (!parsed || !Number.isFinite(parsed.value)) {
      return emptyItem(config, 'no-value');
    }

    const point = {
      t: Date.now(),
      value: parsed.value,
      change: parsed.change || 0,
      changeRate: parsed.changeRate || 0,
    };
    const points = history.get(config.key) || [];
    points.push(point);
    while (points.length > OVERVIEW_HISTORY_LIMIT) points.shift();
    history.set(config.key, points);

    return {
      ok: true,
      key: config.key,
      label: config.label,
      value: parsed.value,
      change: parsed.change || 0,
      changeRate: parsed.changeRate || 0,
      source: parsed.source,
      updatedAt: new Date(point.t).toISOString(),
      points: points.map((item) => ({ t: item.t, value: item.value })),
    };
  } catch (error) {
    return emptyItem(config, String(error?.message || error));
  }
}

function emptyItem(config, error) {
  return {
    ok: false,
    key: config.key,
    label: config.label,
    value: 0,
    change: 0,
    changeRate: 0,
    source: 'unavailable',
    error,
    points: history.get(config.key) || [],
  };
}

async function fetchNaverIndex(code) {
  const html = await fetchText(`https://finance.naver.com/sise/sise_index.naver?code=${encodeURIComponent(code)}`);
  const value = pickNumber(html, /class=["']now_value["'][^>]*>\s*([0-9,.]+)/i);
  const change = pickNumber(html, /class=["']change_value_and_rate["'][\s\S]*?([+-]?[0-9,.]+)\s*<\/span>/i);
  const changeRate = pickNumber(html, /class=["']change_value_and_rate["'][\s\S]*?([+-]?[0-9,.]+)%/i);
  return { value, change, changeRate, source: `naver-index-${code}` };
}

async function fetchNaverExchange(code) {
  const html = await fetchText('https://finance.naver.com/marketindex/');
  const block = pickBlock(html, code) || pickBlock(html, 'USD') || html;
  const value = pickNumber(block, /class=["']value["'][^>]*>\s*([0-9,.]+)/i);
  const change = pickNumber(block, /class=["']change["'][^>]*>\s*([+-]?[0-9,.]+)/i);
  const changeRate = pickNumber(block, /class=["']blind["'][^>]*>\s*([+-]?[0-9,.]+)%/i);
  return { value, change, changeRate, source: `naver-exchange-${code}` };
}

async function fetchNaverWorld(symbol) {
  const html = await fetchText(`https://finance.naver.com/world/sise.naver?symbol=${encodeURIComponent(symbol)}`);
  const value = pickNumber(html, /class=["']no_today["'][\s\S]*?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)/i)
    || pickNumber(html, /class=["']now["'][^>]*>\s*([0-9,.]+)/i);
  const change = pickNumber(html, /class=["']no_exday["'][\s\S]*?([+-]?[0-9,.]+)/i);
  const changeRate = pickNumber(html, /class=["']no_exday["'][\s\S]*?([+-]?[0-9,.]+)%/i);
  return { value, change, changeRate, source: `naver-world-${symbol}` };
}

async function fetchYahooChart(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1d&interval=1m`;
  const response = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' } });
  if (!response.ok) throw new Error(`Yahoo ${response.status}`);
  const json = await response.json();
  const result = json?.chart?.result?.[0];
  const quote = result?.indicators?.quote?.[0];
  const close = quote?.close || [];
  const values = close.filter((value) => Number.isFinite(value));
  const latest = values.at(-1);
  const previous = values.length > 1 ? values.at(-2) : latest;
  const change = latest - previous;
  const changeRate = previous ? (change / previous) * 100 : 0;
  return { value: latest, change, changeRate, source: `yahoo-chart-${symbol}` };
}

async function fetchText(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'Mozilla/5.0',
      accept: 'text/html,application/json',
    },
  });
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.text();
}

function pickNumber(text, pattern) {
  const match = String(text || '').match(pattern);
  if (!match) return 0;
  const value = Number(String(match[1]).replace(/,/g, '').replace(/\s/g, ''));
  return Number.isFinite(value) ? value : 0;
}

function pickBlock(text, token) {
  const index = String(text || '').indexOf(token);
  if (index < 0) return '';
  return String(text).slice(Math.max(0, index - 500), index + 1800);
}
