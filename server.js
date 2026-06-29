import cors from 'cors';
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import morgan from 'morgan';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

loadDotEnv();

const PORT = Number(process.env.PORT || 5188);
const BRIDGE_URL = process.env.KIWOOM_BRIDGE_URL || 'http://127.0.0.1:8765';
const POLL_MS = clampNumber(process.env.POLL_MS, 500, 10000, 1000);
const DEFAULT_SECTOR_LIMIT = clampNumber(process.env.SECTOR_LIMIT, 1, 50, 12);
const DEFAULT_STOCKS_PER_SECTOR = clampNumber(process.env.STOCKS_PER_SECTOR, 1, 50, 5);
const DEFAULT_MAX_REALTIME_CODES = clampNumber(process.env.MAX_REALTIME_CODES, 1, 300, 80);
const DEFAULT_CANDIDATE_REFRESH_MS = clampNumber(process.env.CANDIDATE_REFRESH_MS, 15000, 600000, 60000);
const FLOW_ALERT_THRESHOLD_MILLION = clampNumber(process.env.FLOW_ALERT_THRESHOLD_MILLION, 100, 1000000, 1000);

const samplesByCode = new Map();
const alertMap = new Map();

const app = express();
app.use(cors());
app.use(express.json());
app.use(morgan('tiny'));

app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  next();
});

const distPath = path.join(__dirname, 'dist');
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath, {
    etag: false,
    lastModified: false,
    setHeaders: (res) => {
      res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
      res.setHeader('Pragma', 'no-cache');
      res.setHeader('Expires', '0');
    },
  }));
}

app.get('/api/provider', (req, res) => {
  res.json({
    provider: 'Kiwoom OpenAPI+ only',
    mode: 'local-bridge',
    bridgeUrl: BRIDGE_URL,
    rankingBasis: 'daily accumulated trading value with server-side 1m/3m delta flow',
    numericSource: 'Kiwoom real-time FID first, Kiwoom current-price TR fallback',
    dataBoundary: 'No external market-data parser',
    excludes: ['ETF', 'ETN', 'ELW', 'SPAC', 'REIT'],
    pollMs: POLL_MS,
    maxRealtimeCodes: DEFAULT_MAX_REALTIME_CODES,
    candidateRefreshMs: DEFAULT_CANDIDATE_REFRESH_MS,
    flowAlertThresholdMillion: FLOW_ALERT_THRESHOLD_MILLION,
  });
});

app.get('/api/health', async (req, res) => {
  const health = await bridgeJson('/health');
  res.status(health.ok ? 200 : 503).json({ server: true, dataBoundary: 'Kiwoom OpenAPI+ only', bridge: health });
});

app.get('/api/snapshot', async (req, res) => {
  const snapshot = await fetchSnapshot(req.query);
  res.status(snapshot.ok ? 200 : 503).json(snapshot);
});

app.post('/api/refresh', async (req, res) => {
  const result = await bridgeJson('/refresh', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(req.body || {}),
  });
  res.status(result.ok ? 200 : 503).json(result);
});

app.get('/api/screener', async (req, res) => {
  const params = new URLSearchParams();
  params.set('lookbackDays', String(toInt(req.query.lookbackDays, 63)));
  params.set('thresholdRate', String(toNumber(req.query.thresholdRate, 10)));
  params.set('thresholdAmountEok', String(toNumber(req.query.thresholdAmountEok, 100)));
  params.set('maxCodes', String(toInt(req.query.maxCodes, 20)));
  params.set('sort', String(req.query.sort || 'recent'));
  if (req.query.sector && req.query.sector !== 'all') params.set('sector', String(req.query.sector));

  const bridge = await bridgeJson(`/screener?${params.toString()}`, {}, 120000);
  if (bridge.ok) {
    return res.json(normalizeScreenerPayload(bridge));
  }

  const snapshot = await fetchSnapshot({
    sectorLimit: 50,
    stocksPerSector: 20,
    maxRealtimeCodes: DEFAULT_MAX_REALTIME_CODES,
    sort: 'tradeAmount',
  });
  const fallback = buildSnapshotScreener(snapshot, bridge, {
    sector: String(req.query.sector || 'all'),
    sort: String(req.query.sort || 'recent'),
    thresholdRate: toNumber(req.query.thresholdRate, 10),
    thresholdAmountEok: toNumber(req.query.thresholdAmountEok, 100),
  });
  res.status(snapshot.ok ? 200 : 503).json(fallback);
});

app.get('/api/stock/:code', async (req, res) => {
  const code = cleanCode(req.params.code);
  const bridge = await bridgeJson(`/stock/${code}`, {}, 90000);
  if (bridge.ok) {
    return res.json(normalizeStockPayload(bridge, code));
  }

  const snapshot = await fetchSnapshot({
    sectorLimit: 50,
    stocksPerSector: 20,
    maxRealtimeCodes: DEFAULT_MAX_REALTIME_CODES,
    sort: 'tradeAmount',
  });
  const fallback = buildSnapshotStockDetail(snapshot, code, bridge);
  res.status(fallback.ok ? 200 : 503).json(fallback);
});

app.get('/api/ranking-debug/:code', async (req, res) => {
  const code = cleanCode(req.params.code);
  const bridge = await bridgeJson(`/ranking-debug/${code}`, {}, 120000);
  res.status(bridge.ok ? 200 : 503).json(bridge);
});

app.get('/api/stream', async (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-store, no-cache, must-revalidate, proxy-revalidate',
    Pragma: 'no-cache',
    Expires: '0',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  let closed = false;
  req.on('close', () => { closed = true; });

  const send = async () => {
    if (closed) return;
    const snapshot = await fetchSnapshot(req.query);
    res.write('event: snapshot\n');
    res.write(`data: ${JSON.stringify(snapshot)}\n\n`);
  };

  await send();
  const timer = setInterval(send, POLL_MS);
  req.on('close', () => clearInterval(timer));
});

app.use((req, res) => {
  const indexPath = path.join(distPath, 'index.html');
  if (fs.existsSync(indexPath)) return res.sendFile(indexPath);
  res.status(200).send('Millionaire server is running. Run npm run dev for Vite UI or npm run build before npm run server.');
});

app.listen(PORT, () => {
  console.log(`[millionaire] server listening on http://127.0.0.1:${PORT}/`);
  console.log(`[millionaire] local alias http://localhost:${PORT}/`);
  console.log(`[millionaire] bridge ${BRIDGE_URL}`);
});

async function fetchSnapshot(query = {}) {
  const params = new URLSearchParams();
  params.set('sectorLimit', String(toInt(query.sectorLimit, DEFAULT_SECTOR_LIMIT)));
  params.set('stocksPerSector', String(toInt(query.stocksPerSector, DEFAULT_STOCKS_PER_SECTOR)));
  params.set('maxRealtimeCodes', String(toInt(query.maxRealtimeCodes, DEFAULT_MAX_REALTIME_CODES)));
  params.set('candidateRefreshMs', String(toInt(query.candidateRefreshMs, DEFAULT_CANDIDATE_REFRESH_MS)));
  params.set('sort', String(query.sort || 'tradeAmount'));
  const raw = await bridgeJson(`/snapshot?${params.toString()}`);
  return enrichFlow(raw, String(query.sort || 'tradeAmount'));
}

function enrichFlow(snapshot, sortKey) {
  if (!snapshot || !Array.isArray(snapshot.sectors)) return snapshot;
  const now = Date.now();
  const alerts = [];
  const sectors = snapshot.sectors.map((sector) => {
    const stocks = (sector.stocks || []).map((stock) => enrichStockFlow(stock, sector.name, now, alerts));
    const sorted = sortStocks(stocks, sortKey);
    const flow60 = sorted.reduce((sum, stock) => sum + Number(stock.flow60sTradeAmountMillion || 0), 0);
    const flow180 = sorted.reduce((sum, stock) => sum + Number(stock.flow180sTradeAmountMillion || 0), 0);
    const amount = sorted.reduce((sum, stock) => sum + Number(stock.tradeAmountMillion || 0), 0);
    const volume = sorted.reduce((sum, stock) => sum + Number(stock.volume || 0), 0);
    const avgRate = sorted.length ? sorted.reduce((sum, stock) => sum + Number(stock.changeRate || 0), 0) / sorted.length : 0;
    const netBuyRatio = sorted.length ? sorted.reduce((sum, stock) => sum + Number(stock.netBuyRatio || 0), 0) / sorted.length : 0;
    return { ...sector, stocks: sorted, tradeAmountMillion: amount || sector.tradeAmountMillion, volume: volume || sector.volume, changeRate: avgRate, netBuyRatio, flow60sTradeAmountMillion: flow60, flow180sTradeAmountMillion: flow180, hotFlowCount: sorted.filter((stock) => stock.flowHot).length, score: sectorScore(amount, avgRate, netBuyRatio) };
  });
  const sectorFlowBoard = sectors.slice().sort((a, b) => Number(b.tradeAmountMillion || 0) - Number(a.tradeAmountMillion || 0));
  const flowAlerts = [...alerts, ...alertMap.values()].sort((a, b) => new Date(b.detectedAt) - new Date(a.detectedAt)).slice(0, 80);
  return { ...snapshot, sectors, sectorFlowBoard, flowAlerts, stats: { ...(snapshot.stats || {}), flowEventCount: flowAlerts.length, flowAlertThresholdMillion: FLOW_ALERT_THRESHOLD_MILLION } };
}

function enrichStockFlow(stock, sector, now, alerts) {
  const code = String(stock.code || '');
  const amount = Number(stock.realtimeTradeAmountMillion ?? stock.tradeAmountMillion ?? 0);
  const volume = Number(stock.realtimeVolume ?? stock.volume ?? 0);
  const price = Number(stock.price || 0);
  const samples = samplesByCode.get(code) || [];
  samples.push({ ts: now, amount, volume });
  while (samples.length && now - samples[0].ts > 210000) samples.shift();
  samplesByCode.set(code, samples);
  const flow60 = calcDelta(samples, now, 60000, 'amount');
  const flow180 = calcDelta(samples, now, 180000, 'amount');
  const vol60 = calcDelta(samples, now, 60000, 'volume');
  const vol180 = calcDelta(samples, now, 180000, 'volume');
  const netBuyRatio = calcNetBuyRatio(stock);
  const flowHot = flow60 >= FLOW_ALERT_THRESHOLD_MILLION || flow180 >= FLOW_ALERT_THRESHOLD_MILLION;
  const next = { ...stock, sector, flow60sTradeAmountMillion: flow60, flow180sTradeAmountMillion: flow180, flow60sVolume: vol60, flow180sVolume: vol180, netBuyRatio, flowHot };
  if (flowHot) {
    recordAlert(next, flow60 >= FLOW_ALERT_THRESHOLD_MILLION ? 60 : 180, flow60 >= FLOW_ALERT_THRESHOLD_MILLION ? flow60 : flow180, price, alerts, now);
  }
  return next;
}

function calcDelta(samples, now, windowMs, key) {
  if (samples.length < 2) return 0;
  const current = samples[samples.length - 1];
  let base = samples[0];
  for (let index = samples.length - 1; index >= 0; index -= 1) {
    if (now - samples[index].ts >= windowMs) { base = samples[index]; break; }
  }
  return Math.max(0, Number(current[key] || 0) - Number(base[key] || 0));
}

function calcNetBuyRatio(stock) {
  const strength = Number(stock.strength || 0);
  if (Number.isFinite(strength) && strength > 0) return Math.max(-100, Math.min(100, strength - 100));
  const rate = Number(stock.changeRate || 0);
  return Math.max(-100, Math.min(100, rate * 4));
}

function recordAlert(stock, windowSec, tradeAmountMillion, price, alerts, now) {
  const key = `${stock.code}-${windowSec}`;
  const previous = alertMap.get(key);
  const next = { key, code: stock.code, name: stock.name, sector: stock.sector, windowSec, windowLabel: `${windowSec / 60}분`, tradeAmountMillion, volume: windowSec === 60 ? stock.flow60sVolume : stock.flow180sVolume, price, count: (previous?.count || 0) + 1, detectedAt: new Date(now).toISOString() };
  alertMap.set(key, next);
  alerts.push(next);
}

function sortStocks(stocks, sortKey) {
  const rows = [...stocks];
  if (sortKey === 'volume') rows.sort((a, b) => Number(b.volume || 0) - Number(a.volume || 0));
  else rows.sort((a, b) => Number(b.tradeAmountMillion || 0) - Number(a.tradeAmountMillion || 0));
  return rows;
}

function sectorScore(amount, rate, net) {
  return Math.round(Math.min(100, Math.max(0, amount / 1000 + Math.max(0, rate) * 8 + Math.max(0, net) * 0.6)));
}

async function bridgeJson(pathname, options = {}, timeoutMs = 5000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${BRIDGE_URL}${pathname}`, { ...options, signal: controller.signal });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    return { ok: response.ok && payload.ok !== false, httpStatus: response.status, ...payload };
  } catch (error) {
    return { ok: false, error: String(error?.message || error), bridgeUrl: BRIDGE_URL };
  } finally {
    clearTimeout(timeout);
  }
}

function toInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toNumber(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clampNumber(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function cleanCode(value) {
  return String(value || '').replace(/\D/g, '').padStart(6, '0').slice(-6);
}

function flattenSnapshotStocks(snapshot) {
  const rows = [];
  const sectors = snapshot?.sectorFlowBoard?.length ? snapshot.sectorFlowBoard : snapshot?.sectors || [];
  sectors.forEach((sector, sectorIndex) => {
    (sector.stocks || []).forEach((stock) => {
      rows.push({
        ...stock,
        sector: stock.sector || sector.name || '미분류',
        sectorRank: sectorIndex + 1,
        sectorScore: sector.score || 0,
      });
    });
  });
  return rows;
}

function normalizeScreenerPayload(payload) {
  const rows = Array.isArray(payload.items) ? payload.items : [];
  return {
    ok: true,
    provider: payload.provider || 'Kiwoom OpenAPI+',
    updatedAt: payload.updatedAt || new Date().toISOString(),
    criteria: payload.criteria || {},
    sectors: payload.sectors || uniqueValues(rows.map((row) => row.sector)),
    items: rows,
    stats: payload.stats || screenerStats(rows),
    message: payload.message || null,
  };
}

function normalizeStockPayload(payload, code) {
  return {
    ok: true,
    provider: payload.provider || 'Kiwoom OpenAPI+',
    updatedAt: payload.updatedAt || new Date().toISOString(),
    stock: payload.stock || payload.quote || { code },
    candles: Array.isArray(payload.candles) ? payload.candles : [],
    company: payload.company || {},
    financials: payload.financials || { quarter: [], year: [] },
    peers: payload.peers || [],
    news: payload.news || [],
    unavailable: payload.unavailable || [],
  };
}

function buildSnapshotScreener(snapshot, bridgeError, options) {
  let rows = flattenSnapshotStocks(snapshot)
    .filter((stock) => options.sector === 'all' || stock.sector === options.sector)
    .filter((stock) => Number(stock.changeRate || 0) >= 5 || Number(stock.flow60sTradeAmountMillion || 0) > 0)
    .map((stock) => {
      const amountEok = Number(stock.tradeAmountMillion || 0) / 100;
      const rate = Number(stock.changeRate || 0);
      const event = {
        date: String(stock.updatedAt || snapshot.updatedAt || new Date().toISOString()).slice(0, 10),
        rate,
        amountEok,
        open: 0,
        close: Number(stock.price || 0),
        source: stock.sourceLabel || '스냅샷',
      };
      return {
        code: stock.code,
        name: stock.name,
        sector: stock.sector,
        market: stock.market || '-',
        price: stock.price,
        changeRate: rate,
        amountEok,
        volume: stock.volume,
        events: [event],
        topEvent: event,
        sourceLabel: '현재 스냅샷 대체',
      };
    });

  rows = sortScreenerRows(rows, options.sort);
  return {
    ok: Boolean(snapshot.ok),
    provider: 'Kiwoom OpenAPI+ snapshot fallback',
    updatedAt: snapshot.updatedAt || new Date().toISOString(),
    criteria: {
      lookbackDays: 63,
      thresholdRate: options.thresholdRate,
      thresholdAmountEok: options.thresholdAmountEok,
      fallback: true,
    },
    sectors: uniqueValues(rows.map((row) => row.sector)),
    items: rows,
    stats: screenerStats(rows),
    message: snapshot.ok
      ? '키움 브리지의 일봉 스크리너 엔드포인트가 없어 현재 스냅샷으로 대체 표시 중입니다.'
      : bridgeError?.error || snapshot.error || snapshot.message || '키움 브릿지 연결이 필요합니다.',
  };
}

function buildSnapshotStockDetail(snapshot, code, bridgeError) {
  const rows = flattenSnapshotStocks(snapshot);
  const stock = rows.find((row) => cleanCode(row.code) === code);
  if (!stock) {
    return {
      ok: false,
      provider: 'Kiwoom OpenAPI+ snapshot fallback',
      stock: { code },
      candles: [],
      company: {},
      financials: { quarter: [], year: [] },
      peers: [],
      news: [],
      unavailable: ['일봉', '뉴스', '기업개요', '재무', '동종업계 비교'],
      message: bridgeError?.error || snapshot.message || '현재 스냅샷에서 종목을 찾지 못했습니다.',
    };
  }

  const peers = rows
    .filter((row) => row.sector === stock.sector)
    .sort((a, b) => Number(b.tradeAmountMillion || 0) - Number(a.tradeAmountMillion || 0))
    .slice(0, 6)
    .map((row) => ({ code: row.code, name: row.name, amountEok: Number(row.tradeAmountMillion || 0) / 100, me: cleanCode(row.code) === code }));

  return {
    ok: true,
    provider: 'Kiwoom OpenAPI+ snapshot fallback',
    updatedAt: snapshot.updatedAt || new Date().toISOString(),
    stock,
    candles: [],
    company: {
      sector: stock.sector,
      market: stock.market || '-',
      summary: '키움 현재가/실시간 스냅샷으로 표시 중입니다. 기업개요 데이터 공급자가 연결되면 이 영역이 채워집니다.',
    },
    financials: { quarter: [], year: [] },
    peers,
    news: [],
    unavailable: ['일봉', '뉴스', '기업개요 일부', '재무'],
    message: '키움 브리지 상세 엔드포인트가 없어 현재 스냅샷으로 대체 표시 중입니다.',
  };
}

function sortScreenerRows(rows, sort) {
  const sorted = [...rows];
  if (sort === 'rate') sorted.sort((a, b) => Number(b.topEvent?.rate || 0) - Number(a.topEvent?.rate || 0));
  else if (sort === 'amount') sorted.sort((a, b) => Number(b.topEvent?.amountEok || 0) - Number(a.topEvent?.amountEok || 0));
  else if (sort === 'count') sorted.sort((a, b) => Number(b.events?.length || 0) - Number(a.events?.length || 0));
  else sorted.sort((a, b) => new Date(b.topEvent?.date || 0) - new Date(a.topEvent?.date || 0));
  return sorted;
}

function screenerStats(rows) {
  const eventCount = rows.reduce((sum, row) => sum + Number(row.events?.length || 0), 0);
  const rates = rows.flatMap((row) => (row.events || []).map((event) => Number(event.rate || 0))).filter(Number.isFinite);
  const recentCount = rows.filter((row) => (row.events || []).some((event) => daysAgo(event.date) <= 7)).length;
  return {
    stockCount: rows.length,
    eventCount,
    avgRate: rates.length ? rates.reduce((sum, rate) => sum + rate, 0) / rates.length : 0,
    recentCount,
  };
}

function daysAgo(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 9999;
  return Math.floor((Date.now() - date.getTime()) / 86400000);
}

function uniqueValues(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'ko-KR'));
}

function loadDotEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const index = trimmed.indexOf('=');
    if (index <= 0) continue;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1).trim().replace(/^["']|["']$/g, '');
    if (!process.env[key]) process.env[key] = value;
  }
}
