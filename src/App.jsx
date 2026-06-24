import { useEffect, useMemo, useState } from 'react';

const UI_REVISION = 'kiwoom-dashboard-overview-sectorflow-20260624-4';

const DEFAULT_SNAPSHOT = {
  ok: false,
  sectors: [],
  sectorFlowBoard: [],
  flowAlerts: [],
  overview: { items: [] },
  stats: {},
  provider: 'Kiwoom OpenAPI+ primary',
};

const SORT_OPTIONS = [
  { value: 'tradeAmount', label: '거래대금순' },
  { value: 'volume', label: '거래량순' },
];

export default function App() {
  const [snapshot, setSnapshot] = useState(DEFAULT_SNAPSHOT);
  const [sort, setSort] = useState('tradeAmount');
  const [selectedSector, setSelectedSector] = useState(null);
  const [status, setStatus] = useState('connecting');

  useEffect(() => {
    setStatus('connecting');
    const source = new EventSource(`/api/stream?sort=${sort}`);

    source.addEventListener('snapshot', (event) => {
      const next = JSON.parse(event.data);
      setSnapshot(next);
      setStatus(next.ok ? 'online' : 'bridge-error');
    });

    source.onerror = () => setStatus('stream-error');
    return () => source.close();
  }, [sort]);

  useEffect(() => {
    if (!snapshot.sectors?.length) return;
    if (!selectedSector || !snapshot.sectors.some((sector) => sector.name === selectedSector)) {
      setSelectedSector(snapshot.sectors[0].name);
    }
  }, [snapshot, selectedSector]);

  const selected = useMemo(() => {
    return snapshot.sectors?.find((sector) => sector.name === selectedSector) || snapshot.sectors?.[0] || null;
  }, [snapshot, selectedSector]);

  const flowAlerts = snapshot.flowAlerts || [];
  const sectorBoard = snapshot.sectorFlowBoard?.length ? snapshot.sectorFlowBoard : snapshot.sectors || [];

  return (
    <main className="app-shell dashboard-shell">
      <header className="hero compact-hero">
        <div>
          <p className="eyebrow">Millionaire · Kiwoom Dashboard · {UI_REVISION}</p>
          <h1>실시간 거래대금·섹터 플로우 보드</h1>
          <p className="hero-copy">
            숫자 데이터는 키움 실시간 FID를 우선 사용합니다. 미분류 섹터는 보조 분류를 허용하고, 1분/3분 10억 이상 거래대금 유입은 알림으로 강조합니다.
          </p>
        </div>
        <div className={`status-card ${status}`}>
          <span className="status-dot" />
          <strong>{statusLabel(status)}</strong>
          <small>{snapshot.message || snapshot.error || snapshot.provider}</small>
        </div>
      </header>

      <MarketOverview overview={snapshot.overview} />

      <section className="toolbar">
        <div className="metric-grid">
          <Metric label="실시간 등록" value={`${fmt(snapshot.stats?.registeredCount)}종목`} />
          <Metric label="FID 수신" value={`${fmt(snapshot.stats?.realtimeReadyCount)}종목`} />
          <Metric label="표시 종목" value={`${fmt(snapshot.stats?.visibleStockCount)}종목`} />
          <Metric label="1/3분 알림" value={`${fmt(snapshot.stats?.flowEventCount)}건`} />
        </div>
        <div className="sort-tabs">
          {SORT_OPTIONS.map((option) => (
            <button key={option.value} className={sort === option.value ? 'active' : ''} onClick={() => setSort(option.value)}>
              {option.label}
            </button>
          ))}
        </div>
      </section>

      <section className="runtime-strip">
        <span>감시 {fmt(snapshot.stats?.maxRealtimeCodes)}종목</span>
        <span>현재가TR 배치 {fmt(snapshot.stats?.currentQuoteBatchLimit)}종목</span>
        <span>섹터 {fmt(snapshot.stats?.sectorCount)}개</span>
        <span>최종 갱신 {snapshot.updatedAt ? new Date(snapshot.updatedAt).toLocaleTimeString() : '-'}</span>
      </section>

      <FlowAlertPanel alerts={flowAlerts} />

      {(!snapshot.ok || snapshot.message) && (
        <section className="notice">
          <strong>{snapshot.ok ? '데이터 수신 상태' : '키움 브릿지 연결 필요'}</strong>
          <p>{snapshot.message || snapshot.error || '`start-bridge.bat`을 먼저 실행하고 키움 로그인을 완료하세요.'}</p>
        </section>
      )}

      <section className="sector-board-panel">
        <div className="detail-title compact">
          <div>
            <p className="eyebrow">Realtime Sector Amount Board</p>
            <h2>섹터별 실시간 거래대금 보드</h2>
          </div>
          <span>{fmt(sectorBoard.length)}개 섹터</span>
        </div>
        <div className="sector-board-grid">
          {sectorBoard.map((sector, index) => (
            <button
              key={sector.name}
              className={`sector-board-card ${selectedSector === sector.name ? 'selected' : ''} ${sector.hotFlowCount ? 'alerting' : ''}`}
              onClick={() => setSelectedSector(sector.name)}
            >
              <div className="board-rank">#{index + 1}</div>
              <div className="board-main">
                <strong>{sector.name}</strong>
                <em>{fmtTradeAmount(sector.tradeAmountMillion)}</em>
                <small>{fmt(sector.volume)}주 · 1분 {fmtTradeAmount(sector.flow60sTradeAmountMillion)} · 3분 {fmtTradeAmount(sector.flow180sTradeAmountMillion)}</small>
              </div>
              <BuySellBar buy={sector.buyRatio} sell={sector.sellRatio} net={sector.netBuyRatio} />
            </button>
          ))}
        </div>
      </section>

      <section className="sector-grid">
        {(snapshot.sectors || []).map((sector, index) => (
          <button key={sector.name} className={`sector-card ${selectedSector === sector.name ? 'selected' : ''}`} onClick={() => setSelectedSector(sector.name)}>
            <div className="sector-head">
              <span className="rank">#{index + 1}</span>
              <div>
                <h2>{sector.name}</h2>
                <p>{fmtTradeAmount(sector.tradeAmountMillion)} · {fmt(sector.volume)}주</p>
              </div>
            </div>
            <div className="stock-mini-list">
              {(sector.stocks || []).slice(0, 5).map((stock, stockIndex) => (
                <div key={stock.code} className={`mini-row ${stock.flowHot ? 'hot-flow' : ''}`}>
                  <span>{stockIndex + 1}</span>
                  <strong>{stock.name}</strong>
                  <em>{stock.flowHot ? '10억↑' : sort === 'volume' ? `${fmt(stock.volume)}주` : fmtTradeAmount(stock.tradeAmountMillion)}</em>
                </div>
              ))}
            </div>
          </button>
        ))}
      </section>

      <section className="detail-panel">
        <div className="detail-title">
          <div>
            <p className="eyebrow">Selected Sector</p>
            <h2>{selected?.name || '섹터 없음'}</h2>
          </div>
          {selected && <span>{fmtTradeAmount(selected.tradeAmountMillion)} / {fmt(selected.volume)}주</span>}
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>순위</th>
                <th>종목</th>
                <th>코드</th>
                <th>현재가</th>
                <th>등락률</th>
                <th>일일 거래량</th>
                <th>일일 거래대금</th>
                <th>1분</th>
                <th>3분</th>
                <th>순매수비율</th>
                <th>데이터 기준</th>
                <th>수신</th>
              </tr>
            </thead>
            <tbody>
              {(selected?.stocks || []).map((stock, index) => (
                <tr key={stock.code} className={stock.flowHot ? 'hot-row' : ''}>
                  <td>{index + 1}</td>
                  <td className="stock-name">{stock.name}</td>
                  <td>{stock.code}</td>
                  <td>{fmtPrice(stock.price)}</td>
                  <td className={Number(stock.changeRate) >= 0 ? 'up' : 'down'}>{fmtRate(stock.changeRate)}</td>
                  <td>{fmt(stock.volume)}</td>
                  <td title={tradeAmountTitle(stock)}>{fmtTradeAmount(stock.tradeAmountMillion)}</td>
                  <td>{fmtTradeAmount(stock.flow60sTradeAmountMillion)}</td>
                  <td>{fmtTradeAmount(stock.flow180sTradeAmountMillion)}</td>
                  <td><span className={Number(stock.netBuyRatio) >= 0 ? 'ratio-up' : 'ratio-down'}>{fmtSignedRatio(stock.netBuyRatio)}</span></td>
                  <td><span className={`source-badge ${stock.isRealtime ? 'realtime' : 'provisional'}`}>{stock.sourceLabel || (stock.isRealtime ? '실시간 FID' : '키움현재가TR')}</span></td>
                  <td>{stock.updatedAt ? new Date(stock.updatedAt).toLocaleTimeString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function MarketOverview({ overview }) {
  const items = overview?.items || [];
  return (
    <section className="overview-strip">
      {items.length ? items.map((item) => (
        <div key={item.key} className={`overview-card ${Number(item.changeRate) >= 0 ? 'up-card' : 'down-card'}`}>
          <div className="overview-head">
            <strong>{item.label}</strong>
            <span>{fmtOverviewValue(item)}</span>
          </div>
          <Sparkline points={item.points || []} />
          <small>{fmtSigned(item.change)} · {fmtSignedRatio(item.changeRate)} · {item.ok ? item.source : '대기'}</small>
        </div>
      )) : (
        <div className="overview-card loading">상단 지수/환율 수신 대기</div>
      )}
    </section>
  );
}

function FlowAlertPanel({ alerts }) {
  if (!alerts.length) return null;
  return (
    <section className="flow-panel alert-panel">
      <div className="detail-title compact">
        <div>
          <p className="eyebrow">Amount Flow Alert</p>
          <h2>1분/3분 거래대금 10억 이상</h2>
        </div>
        <span>{fmt(alerts.length)}건</span>
      </div>
      <div className="flow-list alert-list">
        {alerts.slice(0, 16).map((alert) => (
          <div key={alert.key} className="flow-card pulse-alert">
            <strong>{alert.name}</strong>
            <span>{alert.sector} · {alert.windowLabel}</span>
            <em>{fmtTradeAmount(alert.tradeAmountMillion)}</em>
            <small>{fmt(alert.volume)}주 · {fmtPrice(alert.price)} · {alert.detectedAt ? new Date(alert.detectedAt).toLocaleTimeString() : '-'}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function BuySellBar({ buy = 0, sell = 0, net = 0 }) {
  const buyWidth = Math.max(0, Math.min(100, Number(buy) || 0));
  return (
    <div className="buy-sell-box">
      <div className="buy-sell-label"><span>매수 {fmtRatio(buy)}</span><span>매도 {fmtRatio(sell)}</span></div>
      <div className="buy-sell-track"><span style={{ width: `${buyWidth}%` }} /></div>
      <small className={Number(net) >= 0 ? 'ratio-up' : 'ratio-down'}>순매수 {fmtSignedRatio(net)}</small>
    </div>
  );
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

function Metric({ label, value }) {
  return <div className="metric-card"><small>{label}</small><strong>{value}</strong></div>;
}

function statusLabel(status) {
  switch (status) {
    case 'online': return '키움 브릿지 연결됨';
    case 'bridge-error': return '브릿지 응답 오류';
    case 'stream-error': return '스트림 재연결 대기';
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
  if (!Number.isFinite(number) || number === 0) return '-';
  return `${number.toLocaleString('ko-KR')}원`;
}

function fmtRate(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '-';
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function fmtRatio(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '-';
  return `${number.toFixed(1)}%`;
}

function fmtSignedRatio(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '-';
  return `${number > 0 ? '+' : ''}${number.toFixed(1)}%`;
}

function fmtSigned(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return '-';
  return `${number > 0 ? '+' : ''}${number.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}`;
}

function fmtOverviewValue(item) {
  const value = Number(item?.value || 0);
  if (!Number.isFinite(value) || value === 0) return '-';
  if (item?.key === 'USD_KRW') return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}원`;
  return value.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
}

function fmtTradeAmount(value) {
  const million = Number(value || 0);
  if (!Number.isFinite(million) || million <= 0) return '-';
  const eok = million / 100;
  if (eok >= 10000) return `${(eok / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조`;
  if (eok >= 1) return `${eok.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
  return `${million.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}백만`;
}

function tradeAmountTitle(stock) {
  const parts = [];
  if (stock?.tradeAmountSource) parts.push(`source=${stock.tradeAmountSource}`);
  if (stock?.tradeAmountUnitFix) parts.push(`unit=${stock.tradeAmountUnitFix}`);
  if (stock?.tradeAmountRawMillion != null) parts.push(`rawMillion=${stock.tradeAmountRawMillion}`);
  if (stock?.tradeAmountEstimatedMillion != null) parts.push(`estimatedMillion=${stock.tradeAmountEstimatedMillion}`);
  return parts.join(' / ');
}
