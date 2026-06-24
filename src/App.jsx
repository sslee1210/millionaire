import { useEffect, useMemo, useState } from 'react';

const DEFAULT_SNAPSHOT = {
  ok: false,
  sectors: [],
  stats: {},
  provider: 'Kiwoom OpenAPI+ only',
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

    source.onerror = () => {
      setStatus('stream-error');
    };

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

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Millionaire · Kiwoom Only</p>
          <h1>키움 일일 거래량·거래대금 섹터 보드</h1>
          <p className="hero-copy">
            종목명, 현재가, 일일 누적 거래량, 일일 누적 거래대금, 섹터 분류는 키움 OpenAPI+ 브릿지 기준만 사용합니다.
            종목명 클릭 시 외부 증권 사이트로 이동하지 않습니다.
          </p>
        </div>
        <div className={`status-card ${status}`}>
          <span className="status-dot" />
          <strong>{statusLabel(status)}</strong>
          <small>{snapshot.message || snapshot.error || snapshot.provider}</small>
        </div>
      </header>

      <section className="toolbar">
        <div className="metric-grid">
          <Metric label="실시간 등록" value={`${fmt(snapshot.stats?.registeredCount)}종목`} />
          <Metric label="FID 수신" value={`${fmt(snapshot.stats?.realtimeReadyCount)}종목`} />
          <Metric label="표시 종목" value={`${fmt(snapshot.stats?.visibleStockCount)}종목`} />
          <Metric label="최종 갱신" value={snapshot.updatedAt ? new Date(snapshot.updatedAt).toLocaleTimeString() : '-'} />
        </div>
        <div className="sort-tabs">
          {SORT_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={sort === option.value ? 'active' : ''}
              onClick={() => setSort(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </section>

      {(!snapshot.ok || snapshot.message) && (
        <section className="notice">
          <strong>{snapshot.ok ? '데이터 수신 상태' : '키움 브릿지 연결 필요'}</strong>
          <p>
            {snapshot.message || snapshot.error || '`start-bridge.bat`을 먼저 실행하고 키움 로그인을 완료하세요.'}
          </p>
        </section>
      )}

      <section className="sector-grid">
        {(snapshot.sectors || []).map((sector, index) => (
          <button
            key={sector.name}
            className={`sector-card ${selectedSector === sector.name ? 'selected' : ''}`}
            onClick={() => setSelectedSector(sector.name)}
          >
            <div className="sector-head">
              <span className="rank">#{index + 1}</span>
              <div>
                <h2>{sector.name}</h2>
                <p>{fmtTradeAmount(sector.tradeAmountMillion)} · {fmt(sector.volume)}주</p>
              </div>
            </div>
            <div className="stock-mini-list">
              {(sector.stocks || []).slice(0, 5).map((stock, stockIndex) => (
                <div key={stock.code} className="mini-row">
                  <span>{stockIndex + 1}</span>
                  <strong>{stock.name}</strong>
                  <em>{sort === 'volume' ? `${fmt(stock.volume)}주` : fmtTradeAmount(stock.tradeAmountMillion)}</em>
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
                <th>데이터 기준</th>
                <th>수신</th>
              </tr>
            </thead>
            <tbody>
              {(selected?.stocks || []).map((stock, index) => (
                <tr key={stock.code}>
                  <td>{index + 1}</td>
                  <td className="stock-name">{stock.name}</td>
                  <td>{stock.code}</td>
                  <td>{fmtPrice(stock.price)}</td>
                  <td className={Number(stock.changeRate) >= 0 ? 'up' : 'down'}>{fmtRate(stock.changeRate)}</td>
                  <td>{fmt(stock.volume)}</td>
                  <td title={tradeAmountTitle(stock)}>{fmtTradeAmount(stock.tradeAmountMillion)}</td>
                  <td>
                    <span className={`source-badge ${stock.isRealtime ? 'realtime' : 'provisional'}`} title={stock.tradeAmountUnitFix || ''}>
                      {stock.sourceLabel || (stock.isRealtime ? '실시간 FID' : '키움현재가TR')}
                    </span>
                  </td>
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

function Metric({ label, value }) {
  return (
    <div className="metric-card">
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
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

function fmtTradeAmount(value) {
  const million = Number(value || 0);
  if (!Number.isFinite(million) || million <= 0) return '-';

  const eok = million / 100;
  if (eok >= 10000) {
    const jo = eok / 10000;
    return `${jo.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조`;
  }
  if (eok >= 1) {
    return `${eok.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
  }
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
