import cors from 'cors';
import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import morgan from 'morgan';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

loadDotEnv();

const PORT = Number(process.env.PORT || 4173);
const BRIDGE_URL = process.env.KIWOOM_BRIDGE_URL || 'http://127.0.0.1:8765';
const POLL_MS = clampNumber(process.env.POLL_MS, 500, 10000, 1000);
const DEFAULT_SECTOR_LIMIT = clampNumber(process.env.SECTOR_LIMIT, 1, 50, 10);
const DEFAULT_STOCKS_PER_SECTOR = clampNumber(process.env.STOCKS_PER_SECTOR, 1, 50, 8);
const DEFAULT_MAX_REALTIME_CODES = clampNumber(process.env.MAX_REALTIME_CODES, 1, 300, 80);
const DEFAULT_CANDIDATE_REFRESH_MS = clampNumber(process.env.CANDIDATE_REFRESH_MS, 15000, 600000, 60000);

const app = express();
app.use(cors());
app.use(express.json());
app.use(morgan('tiny'));

const distPath = path.join(__dirname, 'dist');
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath));
}

app.get('/api/provider', (req, res) => {
  res.json({
    provider: 'Kiwoom OpenAPI+ only',
    mode: 'local-bridge',
    bridgeUrl: BRIDGE_URL,
    rankingBasis: 'daily accumulated volume and daily accumulated trading value',
    numericSource: 'Kiwoom real-time FID only',
    excludes: ['ETF', 'ETN', 'ELW', 'SPAC', 'REIT'],
    pollMs: POLL_MS,
    maxRealtimeCodes: DEFAULT_MAX_REALTIME_CODES,
    candidateRefreshMs: DEFAULT_CANDIDATE_REFRESH_MS,
  });
});

app.get('/api/health', async (req, res) => {
  const health = await bridgeJson('/health');
  res.status(health.ok ? 200 : 503).json({
    server: true,
    bridge: health,
  });
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

app.get('/api/stream', async (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  let closed = false;
  req.on('close', () => {
    closed = true;
  });

  const send = async () => {
    if (closed) return;
    const snapshot = await fetchSnapshot(req.query);
    res.write(`event: snapshot\n`);
    res.write(`data: ${JSON.stringify(snapshot)}\n\n`);
  };

  await send();
  const timer = setInterval(send, POLL_MS);
  req.on('close', () => clearInterval(timer));
});

app.get('*', (req, res) => {
  const indexPath = path.join(distPath, 'index.html');
  if (fs.existsSync(indexPath)) {
    res.sendFile(indexPath);
    return;
  }
  res.status(200).send('Millionaire server is running. Run npm run dev for Vite UI or npm run build before npm run server.');
});

app.listen(PORT, () => {
  console.log(`[millionaire] server listening on http://localhost:${PORT}`);
  console.log(`[millionaire] bridge ${BRIDGE_URL}`);
});

async function fetchSnapshot(query = {}) {
  const params = new URLSearchParams();
  params.set('sectorLimit', String(toInt(query.sectorLimit, DEFAULT_SECTOR_LIMIT)));
  params.set('stocksPerSector', String(toInt(query.stocksPerSector, DEFAULT_STOCKS_PER_SECTOR)));
  params.set('maxRealtimeCodes', String(toInt(query.maxRealtimeCodes, DEFAULT_MAX_REALTIME_CODES)));
  params.set('candidateRefreshMs', String(toInt(query.candidateRefreshMs, DEFAULT_CANDIDATE_REFRESH_MS)));
  params.set('sort', String(query.sort || 'tradeAmount'));
  return bridgeJson(`/snapshot?${params.toString()}`);
}

async function bridgeJson(pathname, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${BRIDGE_URL}${pathname}`, {
      ...options,
      signal: controller.signal,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};
    return {
      ok: response.ok && payload.ok !== false,
      httpStatus: response.status,
      ...payload,
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error?.message || error),
      bridgeUrl: BRIDGE_URL,
    };
  } finally {
    clearTimeout(timeout);
  }
}

function toInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clampNumber(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
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
    const value = trimmed.slice(index + 1).trim().replace(/^['"]|['"]$/g, '');
    if (!process.env[key]) process.env[key] = value;
  }
}
