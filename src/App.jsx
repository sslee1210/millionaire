import { useEffect, useMemo, useState } from 'react';

const UI_REVISION = 'moneyboard-html-kiwoom-20260629-1';
const DEFAULT_SNAPSHOT = {
  ok: false,
  sectors: [],
  sectorFlowBoard: [],
  flowAlerts: [],
  overview: { items: [] },
  stats: {},
  provider: 'Kiwoom OpenAPI+ only',
};
const MISC_SECTOR_NAMES = new Set(['기타', '미분류', '기타업종', '기타 제조', '기타제조']);
const SORT_OPTIONS = [
  { value: 'tradeAmount', label: '거래대금 순' },
  { value: 'volume', label: '거래량 순' },
  { value: 'rate', label: '등락률 순' },
  { value: 'score', label: '섹터 점수 순' },
];

export default function App() {
  const [snapshot, setSnapshot] = useState(DEFAULT_SNAPSHOT);
  const [sort, setSort] = useState('tradeAmount');
  const [sectorFilter, setSectorFilter] = useState('all');
  const [top20Only, setTop20Only] = useState(false);
  const [status, setStatus] = useState('connecting');
  const [streamRevision, setStreamRevision] = useState(0);

  useEffect(() => {
    setStatus('connecting');
    const params = new URLSearchParams({
      sort: sort === 'volume' ? 'volume' : 'tradeAmount',
      sectorLimit: '12',
      stocksPerSector: '5',
      maxRealtimeCodes: '80',
    });
    const source = new EventSource(`/api/stream?${params.toString()}`);

    source.addEventListener('snapshot', (event) => {
      try {
        const next = JSON.parse(event.data);
        setSnapshot(next);
        setStatus(next.ok ? 'online' : 'bridge-error');
      } catch (error) {
        setStatus('parse-error');
      }
    });

    source.onerror = () => setStatus('stream-error');
    return () => source.close();
  }, [sort, streamRevision]);

  const sectors = useMemo(() => {
    const items = snapshot.sectorFlowBoard?.length ? snapshot.sectorFlowBoard : snapshot.sectors || [];
    return items.filter((sector) => !MISC_SECTOR_NAMES.has(String(sector.name || '').trim()));
  }, [snapshot]);

  useEffect(() => {
    if (sectorFilter !== 'all' && !sectors.some((sector) => sector.name === sectorFilter)) {
      setSectorFilter('all');
    }
  }, [sectorFilter, sectors]);

  const stocks = useMemo(() => {
    const rows = [];
    sectors.forEach((sector, sectorIndex) => {
      (sector.stocks || []).forEach((stock) => {
        rows.push({
          ...stock,
          sector: sector.name,
          sectorRank: sectorIndex + 1,
          sectorScore: sector.score ?? calcSectorScore(sector),
        });
      });
    });
    return rows;
  }, [sectors]);

  const tableRows = useMemo(() => {
    let rows = [...stocks];
    if (sectorFilter !== 'all') rows = rows.filter((stock) => stock.sector === sectorFilter);

    if (sort === 'volume') {
      rows.sort((a, b) => (Number(b.volume) || 0) - (Number(a.volume) || 0));
    } else if (sort === 'rate') {
      rows.sort((a, b) => (Number(b.changeRate) || 0) - (Number(a.changeRate) || 0));
    } else if (sort === 'score') {
      rows.sort((a, b) => (Number(b.sectorScore) || 0) - (Number(a.sectorScore) || 0));
    } else {
      rows.sort((a, b) => (Number(b.tradeAmountMillion) || 0) - (Number(a.tradeAmountMillion) || 0));
    }
    return rows.slice(0, top20Only ? 20 : 100);
  }, [stocks, sectorFilter, sort, top20Only]);

  const stats = useMemo(() => {
    const totalTradeAmount = sectors.reduce((sum, sector) => sum + (Number(sector.tradeAmountMillion) || 0), 0);
    const topTradeAmount = stocks
      .slice()
      .sort((a, b) => (Number(b.tradeAmountMillion) || 0) - (Number(a.tradeAmountMillion) || 0))
      .slice(0, 100)
      .reduce((sum, stock) => sum + (Number(stock.tradeAmountMillion) || 0), 0);
    const upCount = stocks.filter((stock) => Number(stock.changeRate) > 0).length;
    const downCount = stocks.filter((stock) => Number(stock.changeRate) < 0).length;
    return { totalTradeAmount, topTradeAmount, upCount, downCount };
  }, [sectors, stocks]);

  const manualRefresh = async () => {
    setStatus('connecting');
    try {
      await fetch('/api/refresh', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ maxRealtimeCodes: 80 }),
      });
    } catch (error) {
      setStatus('stream-error');
    } finally {
      setStreamRevision((value) => value + 1);
    }
  };

  return (
    <main className="moneyboard-shell">
      <header className="top-header">
        <div className="brand">
          <div className="brand-row">
            <span className="logo">MoneyBoard</span>
            <span className={`live-dot ${status}`}><span className="dot" />{statusLabel(status)}</span>
          </div>
          <p className="subtitle">
            섹터별 거래대금 현황 · 키움 OpenAPI+ 로컬 브릿지 · {UI_REVISION} · <b>{snapshot.updatedAt ? new Date(snapshot.updatedAt).toLocaleTimeString('ko-KR', { hour12: false }) : '수신 대기'}</b>
          </p>
        </div>

        <div className="stat-strip">
          <Metric label="전체 거래대금" value={fmtTradeAmount(stats.totalTradeAmount)} />
          <Metric label="상위 표시 거래대금" value={`${fmtTradeAmount(stats.topTradeAmount)} (${ratio(stats.topTradeAmount, stats.totalTradeAmount)})`} />
          <Metric label="상승 종목 수" value={`${fmt(stats.upCount)}개`} tone="up" />
          <Metric label="하락 종목 수" value={`${fmt(stats.downCount)}개`} tone="down" />
          <button className="refresh-btn" onClick={manualRefresh} type="button">새로고침</button>
        </div>
      </header>

      <MarketOverview snapshot={snapshot} />

      <div className="layout">
        <div className="main-col">
          {(!snapshot.ok || snapshot.message) && (
            <section className="notice">
              <strong>{snapshot.ok ? '데이터 수신 상태' : '키움 브릿지 연결 필요'}</strong>
              <p>{snapshot.message || snapshot.error || '`start-bridge.bat` 실행 후 키움 로그인을 완료하세요.'}</p>
            </section>
          )}

          <section className="sector-grid" aria-label="섹터별 거래대금 보드">
            {sectors.length ? sectors.map((sector, index) => (
              <SectorCard
                key={sector.name}
                sector={sector}
                rank={index + 1}
                selected={sectorFilter === sector.name}
                onSelect={() => setSectorFilter(sector.name)}
              />
            )) : <EmptyCard text="키움 실시간/현재가 TR 수신 대기 중입니다." />}
          </section>

          <section className="legend-row">
            <div>
              <span>※ 섹터 점수 = 거래대금 비중 + 상승/하락 강도 + 순매수/체결강도 보정</span>
            </div>
            <div>
              <span><b>★★</b> 1위</span>
              <span><b>★☆</b> 2~3위</span>
              <span>☆☆ 그 외</span>
            </div>
          </section>

          <section className="table-toolbar">
            <div>
              <p className="eyebrow">Realtime Stock Ranking</p>
              <h2>상위 종목 리스트</h2>
            </div>
            <div className="table-controls">
              <label className="chip"><input type="checkbox" checked={top20Only} onChange={(event) => setTop20Only(event.target.checked)} /> 상위 20개</label>
              <select value={sectorFilter} onChange={(event) => setSectorFilter(event.target.value)}>
                <option value="all">전체 섹터</option>
                {sectors.map((sector) => <option key={sector.name} value={sector.name}>{sector.name}</option>)}
              </select>
              <select value={sort} onChange={(event) => setSort(event.target.value)}>
                {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
          </section>

          <section className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>순위</th>
                  <th>종목코드</th>
                  <th>종목명</th>
                  <th>섹터</th>
                  <th>현재가</th>
                  <th>등락률</th>
                  <th>거래량</th>
                  <th>거래대금</th>
                  <th>1분</th>
                  <th>3분</th>
                  <th>순매수</th>
                  <th>점수</th>
                  <th>수신</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.length ? tableRows.map((stock, index) => (
                  <StockRow key={`${stock.code}-${stock.sector}`} stock={stock} index={index} />
                )) : (
                  <tr><td colSpan="13" className="empty-cell">표시 가능한 종목이 없습니다.</td></tr>
                )}
              </tbody>
            </table>
          </section>

          <p className="footnote">숫자 데이터는 키움 OpenAPI+ 실시간 FID를 우선 사용하고, 실시간 공백 구간은 키움 현재가 TR 값으로만 보완합니다.</p>
        </div>

        <aside className="side-col">
          <BurstPanel alerts={snapshot.flowAlerts || []} threshold={snapshot.stats?.flowAlertThresholdMillion} />
          <RuntimePanel snapshot={snapshot} stocks={stocks} sectors={sectors} />
        </aside>
      </div>
    </main>
  );
}

function MarketOverview({ snapshot }) {
  const items = snapshot.overview?.items || [];
  const fallbackItems = [
    { key: 'registered', label: '실시간 등록', value: snapshot.stats?.registeredCount, suffix: '종목', ok: snapshot.ok },
    { key: 'fid', label: 'FID 수신', value: snapshot.stats?.realtimeQuoteCount, suffix: '종목', ok: snapshot.ok },
    { key: 'visible', label: '표시 종목', value: snapshot.stats?.visibleStockCount, suffix: '종목', ok: snapshot.ok },
    { key: 'alerts', label: '1/3분 포착', value: snapshot.stats?.flowEventCount, suffix: '건', ok: snapshot.ok },
  ];
  const displayItems = items.length ? items : fallbackItems;

  return (
    <section className="overview-strip">
      {displayItems.map((item) => (
        <div key={item.key} className={`overview-card ${item.ok === false ? 'waiting' : ''}`}>
          <div className="overview-head">
            <strong>{item.label}</strong>
            <span>{fmtOverviewValue(item)}</span>
          </div>
          <Sparkline points={item.points || []} />
          <small>{item.source || snapshot.provider || 'Kiwoom OpenAPI+'}</small>
        </div>
      ))}
    </section>
  );
}

function SectorCard({ sector, rank, selected, onSelect }) {
  const stocks = (sector.stocks || []).slice(0, 5);
  const starCount = rank === 1 ? 2 : rank <= 3 ? 1 : 0;
  return (
    <button className={`sector-card ${selected ? 'selected' : ''} ${sector.hotFlowCount ? 'alerting' : ''}`} onClick={onSelect} type="button">
      <div className="sc-top">
        <div className="sc-name"><span className="sc-rank">{rank}</span>{sector.name}</div>
        <div className="sc-stars">{starText(starCount)}</div>
      </div>
      <div className="sc-amount-row">
        <span className="sc-amount">{fmtTradeAmount(sector.tradeAmountMillion)}</span>
        <span className={`sc-change ${tone(sector.changeRate)}`}>{fmtRate(sector.changeRate)}</span>
      </div>
      <div className="sc-meta">
        <span>종목 수 <b>{fmt(stocks.length)}개</b></span>
        <span>순매수 <b className={tone(sector.netBuyRatio)}>{fmtSignedRatio(sector.netBuyRatio)}</b></span>
      </div>
      <div className="sc-meta muted">
        <span>1분 {fmtTradeAmount(sector.flow60sTradeAmountMillion)}</span>
        <span>3분 {fmtTradeAmount(sector.flow180sTradeAmountMillion)}</span>
      </div>
      <div className="sc-top5-label">TOP5</div>
      <div className="stock-mini-list">
        {stocks.length ? stocks.map((stock, index) => (
          <div key={stock.code} className={`sc-stock-row ${index === 0 ? 'top1' : ''} ${stock.flowHot ? 'hot-flow' : ''}`}>
            <span className="idx">{index + 1}</span>
            <span className="name">{stock.name}</span>
            <span className="amt">{fmtTradeAmount(stock.tradeAmountMillion)}</span>
            <span className={`chg ${tone(stock.changeRate)}`}>{fmtRate(stock.changeRate)}</span>
          </div>
        )) : <div className="mini-empty">수신 대기</div>}
      </div>
    </button>
  );
}

function StockRow({ stock, index }) {
  const medal = index === 0 ? '🥇' : index === 1 ? '🥈' : index === 2 ? '🥉' : index + 1;
  return (
    <tr className={stock.flowHot ? 'hot-row' : ''}>
      <td className={index < 3 ? 'rank-cell medal' : 'rank-cell'}>{medal}</td>
      <td className="code-cell">{stock.code}</td>
      <td className="name-cell">{stock.name}</td>
      <td><span className="sector-tag">{stock.sector}</span></td>
      <td className="num strong">{fmtPrice(stock.price)}</td>
      <td className={`num strong ${tone(stock.changeRate)}`}>{fmtRate(stock.changeRate)}</td>
      <td className="num">{fmt(stock.volume)}</td>
      <td className="num">{fmtTradeAmount(stock.tradeAmountMillion)}</td>
      <td className="num flow-cell">{fmtTradeAmount(stock.flow60sTradeAmountMillion)}</td>
      <td className="num flow-cell">{fmtTradeAmount(stock.flow180sTradeAmountMillion)}</td>
      <td className={`num strong ${tone(stock.netBuyRatio)}`}>{fmtSignedRatio(stock.netBuyRatio)}</td>
      <td className="num"><span className="score-badge">{fmt(stock.sectorScore)}점</span></td>
      <td><span className={`source-badge ${stock.isRealtime ? 'realtime' : 'provisional'}`}>{stock.sourceLabel || '-'}</span></td>
    </tr>
  );
}

function BurstPanel({ alerts, threshold }) {
  const rows = [...alerts].sort((a, b) => new Date(b.detectedAt || 0) - new Date(a.detectedAt || 0)).slice(0, 60);
  return (
    <section className="burst-panel">
      <div className="burst-head">
        <div className="burst-title">거래대금 포착</div>
        <span className="burst-count-badge">{fmt(rows.length)}건</span>
      </div>
      <p className="burst-sub">1분 또는 3분 거래대금 <b>{fmtTradeAmount(threshold || 1000)}</b> 이상 발생 시 포착</p>
      <div className="burst-list">
        {rows.length ? rows.map((alert) => (
          <div className="burst-row fresh" key={alert.key || `${alert.code}-${alert.windowLabel}-${alert.detectedAt}`}>
            <div className="burst-name-wrap">
              <span className="burst-name">{alert.name}</span>
              <span className="burst-meta-line"><span className={`tf-mini ${alert.windowSec === 60 ? 'tf1' : 'tf3'}`}>{alert.windowLabel}</span>{alert.sector} · {alert.code}</span>
            </div>
            <span className="burst-amt">{fmtTradeAmount(alert.tradeAmountMillion)}</span>
            <span className="burst-count">{fmt(alert.count || 1)}회</span>
          </div>
        )) : <div className="burst-empty">아직 포착된 종목이 없습니다.</div>}
      </div>
      <div className="burst-foot"><span>{rows[0]?.detectedAt ? `마지막 ${new Date(rows[0].detectedAt).toLocaleTimeString('ko-KR', { hour12: false })}` : '대기 중'}</span></div>
    </section>
  );
}

function RuntimePanel({ snapshot, stocks, sectors }) {
  return (
    <section className="runtime-panel">
      <h3>실시간 수신 상태</h3>
      <dl>
        <div><dt>브릿지</dt><dd>{snapshot.ok ? '연결됨' : '대기'}</dd></div>
        <div><dt>감시 후보</dt><dd>{fmt(snapshot.stats?.candidateCount)}종목</dd></div>
        <div><dt>실시간 등록</dt><dd>{fmt(snapshot.stats?.registeredCount)}종목</dd></div>
        <div><dt>FID 수신</dt><dd>{fmt(snapshot.stats?.realtimeQuoteCount)}종목</dd></div>
        <div><dt>표시 섹터</dt><dd>{fmt(sectors.length)}개</dd></div>
        <div><dt>표시 종목</dt><dd>{fmt(stocks.length)}종목</dd></div>
      </dl>
    </section>
  );
}

function Metric({ label, value, tone: metricTone }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${metricTone || ''}`}>{value}</div>
    </div>
  );
}

function EmptyCard({ text }) {
  return <div className="sector-card empty-card">{text}</div>;
}

function Sparkline({ points }) {
  const values = (points || []).map((point) => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return <div className="sparkline empty" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const d = values.map((value, index) => {
    const x = (index / Math.max(1, values.length - 1)) * 100;
    const y = 34 - ((value - min) / range) * 30;
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');
  return <svg className="sparkline" viewBox="0 0 100 38" preserveAspectRatio="none"><path d={d} /></svg>;
}

function calcSectorScore(sector) {
  const amount = Number(sector.tradeAmountMillion) || 0;
  const rate = Math.max(0, Number(sector.changeRate) || 0);
  const net = Math.max(0, Number(sector.netBuyRatio) || 0);
  return Math.round(Math.min(100, amount / 1000 + rate * 8 + net * 0.6));
}

function statusLabel(status) {
  switch (status) {
    case 'online': return '실시간';
    case 'bridge-error': return '브릿지 오류';
    case 'stream-error': return '재연결 대기';
    case 'parse-error': return '데이터 오류';
    default: return '연결 중';
  }
}

function fmt(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '-';
  return number.toLocaleString('ko-KR');
}

function fmtPrice(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return '-';
  return `${number.toLocaleString('ko-KR')}원`;
}

function fmtRate(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return '0.00%';
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function fmtSignedRatio(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return '0.0%';
  return `${number > 0 ? '+' : ''}${number.toFixed(1)}%`;
}

function fmtTradeAmount(value) {
  const million = Number(value || 0);
  if (!Number.isFinite(million) || million <= 0) return '-';
  const eok = million / 100;
  if (eok >= 10000) return `${(eok / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조`;
  if (eok >= 1) return `${eok.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
  return `${million.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}백만`;
}

function fmtOverviewValue(item) {
  const value = Number(item?.value || 0);
  if (!Number.isFinite(value)) return '-';
  const suffix = item?.suffix || '';
  if (item?.key === 'USD_KRW') return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원`;
  return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}${suffix}`;
}

function ratio(part, total) {
  const p = Number(part || 0);
  const t = Number(total || 0);
  if (!p || !t) return '0.0%';
  return `${((p / t) * 100).toFixed(1)}%`;
}

function tone(value) {
  return Number(value || 0) >= 0 ? 'up' : 'down';
}

function starText(count) {
  return `${'★'.repeat(count)}${'☆'.repeat(Math.max(0, 2 - count))}`;
}
