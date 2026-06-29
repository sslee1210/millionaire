import { useEffect, useMemo, useState } from 'react';

const UI_REVISION = '통합 시세';
const DEFAULT_SNAPSHOT = {
  ok: false,
  sectors: [],
  sectorFlowBoard: [],
  flowAlerts: [],
  overview: { items: [] },
  stats: {},
  provider: 'Kiwoom OpenAPI+',
};
const MISC_SECTOR_NAMES = new Set(['기타', '미분류', '기타업종', '기타 제조', '기타제조']);
const DASHBOARD_SORT_OPTIONS = [
  { value: 'tradeAmount', label: '거래대금 순' },
  { value: 'volume', label: '거래량 순' },
  { value: 'rate', label: '등락률 순' },
  { value: 'score', label: '섹터 점수 순' },
];
const SCREENER_SORT_OPTIONS = [
  { value: 'recent', label: '최근 발생일 순' },
  { value: 'rate', label: '상승률 높은 순' },
  { value: 'amount', label: '거래대금 높은 순' },
  { value: 'count', label: '발생 횟수 많은 순' },
];

export default function App() {
  const route = useRoute();

  if (route.name === 'screener') return <ScreenerPage />;
  if (route.name === 'stock') return <StockDetailPage code={route.code} />;
  return <DashboardPage />;
}

function DashboardPage() {
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
      sectorLimit: '16',
      stocksPerSector: '5',
      maxRealtimeCodes: '100',
    });
    const source = new EventSource(`/api/stream?${params.toString()}`);

    source.addEventListener('snapshot', (event) => {
      try {
        const next = JSON.parse(event.data);
        setSnapshot(next);
        setStatus(next.ok ? 'online' : 'bridge-error');
      } catch {
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

  const stocks = useMemo(() => flattenStocks(sectors), [sectors]);
  const tableRows = useMemo(() => {
    let rows = [...stocks];
    if (sectorFilter !== 'all') rows = rows.filter((stock) => stock.sector === sectorFilter);
    rows = sortDashboardStocks(rows, sort);
    return rows.slice(0, top20Only ? 20 : 100);
  }, [stocks, sectorFilter, sort, top20Only]);

  const stats = useMemo(() => {
    const totalTradeAmount = sectors.reduce((sum, sector) => sum + number(sector.tradeAmountMillion), 0);
    const topTradeAmount = stocks
      .slice()
      .sort((a, b) => number(b.tradeAmountMillion) - number(a.tradeAmountMillion))
      .slice(0, 100)
      .reduce((sum, stock) => sum + number(stock.tradeAmountMillion), 0);
    return {
      totalTradeAmount,
      topTradeAmount,
      upCount: stocks.filter((stock) => number(stock.changeRate) > 0).length,
      downCount: stocks.filter((stock) => number(stock.changeRate) < 0).length,
    };
  }, [sectors, stocks]);

  const manualRefresh = async () => {
    setStatus('connecting');
    try {
      await fetch('/api/refresh', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ maxRealtimeCodes: 100 }),
      });
    } finally {
      setStreamRevision((value) => value + 1);
    }
  };

  return (
    <main className="app-shell wide-shell">
      <header className="page-header">
        <div className="brand">
          <div className="brand-row">
            <button className="logo-button" type="button" onClick={() => go('/')}>MoneyBoard</button>
            <span className={`live-dot ${status}`}><span className="dot" />{statusLabel(status)}</span>
          </div>
          <p className="subtitle">
            섹터별 거래대금 현황 · {UI_REVISION} · Kiwoom OpenAPI+ · <b>{snapshot.updatedAt ? timeText(snapshot.updatedAt) : '수신 대기'}</b>
          </p>
        </div>

        <div className="stat-strip">
          <Metric label="전체 거래대금" value={fmtTradeAmount(stats.totalTradeAmount)} />
          <Metric label="상위 표시 거래대금" value={`${fmtTradeAmount(stats.topTradeAmount)} (${ratio(stats.topTradeAmount, stats.totalTradeAmount)})`} />
          <Metric label="상승 종목 수" value={`${fmt(stats.upCount)}개`} tone="up" />
          <Metric label="하락 종목 수" value={`${fmt(stats.downCount)}개`} tone="down" />
          <button className="tool-button" onClick={manualRefresh} type="button" title="새로고침" aria-label="새로고침">↻</button>
          <button className="primary-button" onClick={() => go('/screener')} type="button">장대양봉 스크리너</button>
        </div>
      </header>

      {(!snapshot.ok || snapshot.message) && (
        <section className="notice">
          <strong>{snapshot.ok ? '데이터 수신 상태' : '키움 브릿지 연결 필요'}</strong>
          <p>{snapshot.message || snapshot.error || '`start-bridge.bat` 실행 후 키움 로그인을 완료하세요.'}</p>
        </section>
      )}

      <MarketOverview snapshot={snapshot} />

      <div className="dashboard-layout">
        <div className="main-col">
          <div className="section-heading">
            <h2>섹터별 거래대금</h2>
            <span>거래대금 상위 섹터와 대표 종목</span>
          </div>
          <section className="sector-grid" aria-label="섹터별 거래대금 보드">
            {sectors.length ? sectors.map((sector, index) => (
              <SectorCard
                key={sector.name}
                sector={sector}
                rank={index + 1}
                selected={sectorFilter === sector.name}
                onSelect={() => setSectorFilter(sector.name)}
              />
            )) : <EmptyPanel text="키움 실시간/현재가 TR 수신 대기 중입니다." />}
          </section>

          <section className="legend-row">
            <span>섹터 점수 = 거래대금 비중 + 상승/하락 강도 + 순매수/체결강도 보정</span>
            <span><b>★★</b> 1위 · <b>★☆</b> 2~3위 · ☆☆ 그 외</span>
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
                {DASHBOARD_SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
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
                )) : <tr><td colSpan="13" className="empty-cell">표시 가능한 종목이 없습니다.</td></tr>}
              </tbody>
            </table>
          </section>
        </div>

        <aside className="side-col">
          <BurstPanel alerts={snapshot.flowAlerts || []} threshold={snapshot.stats?.flowAlertThresholdMillion} />
          <RuntimePanel snapshot={snapshot} stocks={stocks} sectors={sectors} />
        </aside>
      </div>
    </main>
  );
}

function ScreenerPage() {
  const [payload, setPayload] = useState({ ok: false, items: [], sectors: [], stats: {}, criteria: {} });
  const [sectorFilter, setSectorFilter] = useState('all');
  const [sort, setSort] = useState('recent');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({
      sector: sectorFilter,
      sort,
      lookbackDays: '63',
      thresholdRate: '15',
      thresholdAmountEok: '500',
      maxCodes: '40',
    });
    setLoading(true);
    fetchJson(`/api/screener?${params.toString()}`)
      .then((next) => {
        if (!cancelled) setPayload(next);
      })
      .catch((error) => {
        if (!cancelled) setPayload({ ok: false, items: [], sectors: [], stats: {}, message: String(error.message || error) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [sectorFilter, sort, revision]);

  const rows = payload.items || [];
  const sectors = payload.sectors?.length ? payload.sectors : uniqueValues(rows.map((row) => row.sector));

  return (
    <main className="app-shell screener-shell">
      <button className="back-link" type="button" onClick={() => go('/')}>← 대시보드로 돌아가기</button>

      <header className="page-header">
        <div className="brand">
          <div className="logo">장대양봉 스크리너</div>
          <p className="subtitle">최근 3개월 내 조건을 만족한 종목을 키움 일봉 데이터로 탐색합니다.</p>
        </div>
        <button className="tool-button" type="button" onClick={() => setRevision((value) => value + 1)} title="다시 조회">↻</button>
      </header>

      <section className="condition-card">
        <Condition label="탐색 기간" value={`${payload.criteria?.lookbackDays || 63}거래일`} />
        <Condition label="장대양봉 기준" value={`시가 대비 +${payload.criteria?.thresholdRate || 15}% 이상`} />
        <Condition label="해당일 거래대금" value={`${fmt(payload.criteria?.thresholdAmountEok || 500)}억원 이상`} />
        <Condition label="데이터" value={providerText(payload.provider)} />
      </section>

      {payload.message && <section className="notice"><strong>데이터 상태</strong><p>{payload.message}</p></section>}

      <section className="stat-row">
        <Metric label="조건 충족 종목" value={`${fmt(payload.stats?.stockCount || rows.length)}개`} />
        <Metric label="총 발생 횟수" value={`${fmt(payload.stats?.eventCount || 0)}건`} />
        <Metric label="평균 상승률" value={fmtRate(payload.stats?.avgRate || 0)} tone="up" />
        <Metric label="최근 7일 발생" value={`${fmt(payload.stats?.recentCount || 0)}개`} />
      </section>

      <section className="table-toolbar compact">
        <h2>{loading ? '조회 중' : `검색 결과 ${fmt(rows.length)}개`}</h2>
        <div className="table-controls">
          <select value={sectorFilter} onChange={(event) => setSectorFilter(event.target.value)}>
            <option value="all">전체 섹터</option>
            {sectors.map((sector) => <option key={sector} value={sector}>{sector}</option>)}
          </select>
          <select value={sort} onChange={(event) => setSort(event.target.value)}>
            {SCREENER_SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      </section>

      <section className="result-grid">
        {rows.length ? rows.map((stock) => <ScreenerCard key={stock.code} stock={stock} />) : <EmptyPanel text={loading ? '키움 일봉을 조회하는 중입니다.' : '조건을 만족한 종목이 없습니다.'} />}
      </section>
    </main>
  );
}

function StockDetailPage({ code }) {
  const [payload, setPayload] = useState({ ok: false, stock: { code }, candles: [], company: {}, financials: { quarter: [], year: [] }, peers: [], news: [] });
  const [period, setPeriod] = useState('quarter');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchJson(`/api/stock/${encodeURIComponent(code || '000000')}`)
      .then((next) => {
        if (!cancelled) setPayload(next);
      })
      .catch((error) => {
        if (!cancelled) setPayload({ ok: false, stock: { code }, candles: [], company: {}, financials: { quarter: [], year: [] }, peers: [], news: [], message: String(error.message || error) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [code]);

  const stock = payload.stock || {};
  const company = payload.company || {};
  const candles = payload.candles || [];
  const financialRows = payload.financials?.[period] || [];

  return (
    <main className="app-shell detail-shell">
      <button className="back-link" type="button" onClick={() => go('/')}>← 대시보드로 돌아가기</button>

      {payload.message && <section className="notice"><strong>데이터 상태</strong><p>{payload.message}</p></section>}

      <section className="head-card">
        <div className="head-top">
          <div>
            <div className="head-name-row">
              <span className="head-name">{stock.name || (loading ? '조회 중' : '종목명 없음')}</span>
              <span className="head-code">{stock.code || code}</span>
            </div>
            <div className="head-tags">
              <span className="tag">{stock.sector || company.sector || '-'}</span>
              <span className="tag">{stock.market || company.market || '-'}</span>
              <span className="tag">{providerText(stock.sourceLabel || payload.provider || 'Kiwoom')}</span>
            </div>
          </div>
          <div className="head-price-block">
            <div className="head-price">{fmtPrice(stock.price)}</div>
            <div className={`head-chg ${tone(stock.changeRate)}`}>{fmtRate(stock.changeRate)}</div>
          </div>
        </div>
        <div className="head-stats">
          <HeadStat label="거래량" value={fmt(stock.volume)} />
          <HeadStat label="거래대금" value={fmtTradeAmount(stock.tradeAmountMillion)} />
          <HeadStat label="체결강도" value={stock.strength ? `${fmtDecimal(stock.strength, 1)}%` : '-'} />
          <HeadStat label="일봉 수" value={`${fmt(candles.length)}개`} />
          <HeadStat label="갱신" value={stock.updatedAt ? timeText(stock.updatedAt) : '-'} />
        </div>
      </section>

      <div className="detail-grid">
        <div>
          <section className="panel">
            <h2 className="panel-title"><span className="panel-dot" />일봉 흐름</h2>
            <CandleChart candles={candles} />
          </section>

          <section className="panel">
            <div className="panel-title-row">
              <h2 className="panel-title"><span className="panel-dot" />매출/영업이익 추이</h2>
              <div className="segmented">
                <button className={period === 'quarter' ? 'active' : ''} type="button" onClick={() => setPeriod('quarter')}>분기</button>
                <button className={period === 'year' ? 'active' : ''} type="button" onClick={() => setPeriod('year')}>연도</button>
              </div>
            </div>
            <FinancialChart rows={financialRows} />
            {!financialRows.length && <p className="muted-text">키움 브리지에 재무 데이터 공급자를 연결하면 이 영역이 채워집니다.</p>}
          </section>
        </div>

        <div>
          <section className="panel">
            <h2 className="panel-title"><span className="panel-dot" />기업 정보</h2>
            <p className="summary-text">{company.summary || '기업개요 데이터 대기 중입니다.'}</p>
            <InfoRow label="섹터" value={stock.sector || company.sector || '-'} />
            <InfoRow label="시장" value={stock.market || company.market || '-'} />
            <InfoRow label="원천" value={providerText(payload.provider)} />
            <InfoRow label="제한 항목" value={(payload.unavailable || []).join(', ') || '-'} />
          </section>

          <section className="panel">
            <h2 className="panel-title"><span className="panel-dot" />주요 뉴스</h2>
            <NewsList rows={payload.news || []} stock={stock} />
          </section>

          <section className="panel">
            <h2 className="panel-title"><span className="panel-dot" />섹터 내 동종업계 비교</h2>
            <PeerList rows={payload.peers || []} currentCode={stock.code} />
          </section>
        </div>
      </div>
    </main>
  );
}

function MarketOverview({ snapshot }) {
  const items = snapshot.overview?.items?.length ? snapshot.overview.items : [
    { key: 'registered', label: '실시간 등록', value: snapshot.stats?.registeredCount, suffix: '종목', ok: snapshot.ok },
    { key: 'fid', label: 'FID 수신', value: snapshot.stats?.realtimeQuoteCount, suffix: '종목', ok: snapshot.ok },
    { key: 'visible', label: '표시 종목', value: snapshot.stats?.visibleStockCount, suffix: '종목', ok: snapshot.ok },
    { key: 'alerts', label: '1/3분 포착', value: snapshot.stats?.flowEventCount, suffix: '건', ok: snapshot.ok },
  ];

  return (
    <section className="overview-strip">
      {items.map((item) => (
        <div key={item.key} className={`overview-card ${item.ok === false ? 'waiting' : ''}`}>
          <div className="overview-head">
            <strong>{item.label}</strong>
            <span>{fmtOverviewValue(item)}</span>
          </div>
          <Sparkline points={item.points || []} />
          <small>{providerText(item.source || snapshot.provider)}</small>
        </div>
      ))}
    </section>
  );
}

function SectorCard({ sector, rank, selected, onSelect }) {
  const stocks = (sector.stocks || []).slice(0, 3);
  return (
    <button className={`sector-card ${selected ? 'selected' : ''} ${sector.hotFlowCount ? 'alerting' : ''}`} onClick={onSelect} type="button">
      <div className="sc-top">
        <div className="sc-name"><span className="sc-rank">{rank}</span>{sector.name}</div>
        {sector.hotFlowCount ? <span className="flow-pill">포착 {fmt(sector.hotFlowCount)}</span> : null}
      </div>
      <div className="sc-amount-row">
        <span className="sc-amount">{fmtTradeAmount(sector.tradeAmountMillion)}</span>
        <span className={`sc-change ${tone(sector.changeRate)}`}>{fmtRate(sector.changeRate)}</span>
      </div>
      <div className="sc-meta">
        <span>표시 <b>{fmt(stocks.length)}개</b></span>
        <span>순매수 <b className={tone(sector.netBuyRatio)}>{fmtSignedRatio(sector.netBuyRatio)}</b></span>
      </div>
      <div className="sc-top5-label">TOP3</div>
      <div className="stock-mini-list">
        {stocks.length ? stocks.map((stock, index) => (
          <button key={stock.code} className={`sc-stock-row ${index === 0 ? 'top1' : ''}`} type="button" onClick={(event) => { event.stopPropagation(); go(`/stock/${stock.code}`); }}>
            <span className="idx">{index + 1}</span>
            <span className="name">{stock.name}</span>
            <span className="amt">{fmtTradeAmount(stock.tradeAmountMillion)}</span>
            <span className={`chg ${tone(stock.changeRate)}`}>{fmtRate(stock.changeRate)}</span>
          </button>
        )) : <div className="mini-empty">수신 대기</div>}
      </div>
    </button>
  );
}

function StockRow({ stock, index }) {
  const rankLabel = `#${index + 1}`;
  return (
    <tr className={stock.flowHot ? 'hot-row' : ''}>
      <td className={index < 3 ? 'rank-cell medal' : 'rank-cell'}>{rankLabel}</td>
      <td className="code-cell">{stock.code}</td>
      <td className="name-cell"><button type="button" onClick={() => go(`/stock/${stock.code}`)}>{stock.name}</button></td>
      <td><span className="sector-tag">{stock.sector}</span></td>
      <td className="num strong">{fmtPrice(stock.price)}</td>
      <td className={`num strong ${tone(stock.changeRate)}`}>{fmtRate(stock.changeRate)}</td>
      <td className="num">{fmt(stock.volume)}</td>
      <td className="num">{fmtTradeAmount(stock.tradeAmountMillion)}</td>
      <td className="num flow-cell">{fmtTradeAmount(stock.flow60sTradeAmountMillion)}</td>
      <td className="num flow-cell">{fmtTradeAmount(stock.flow180sTradeAmountMillion)}</td>
      <td className={`num strong ${tone(stock.netBuyRatio)}`}>{fmtSignedRatio(stock.netBuyRatio)}</td>
      <td className="num"><span className="score-badge">{fmt(stock.sectorScore)}점</span></td>
      <td><span className={`source-badge ${stock.isRealtime ? 'realtime' : 'provisional'}`}>{providerText(stock.sourceLabel || '-')}</span></td>
    </tr>
  );
}

function ScreenerCard({ stock }) {
  const event = stock.topEvent || stock.events?.[0] || {};
  return (
    <article className="result-card">
      <div className="rc-top">
        <div>
          <button className="rc-name" type="button" onClick={() => go(`/stock/${stock.code}`)}>{stock.name}</button>
          <div className="rc-meta">{stock.code} · {stock.sector}</div>
        </div>
        <span className="rc-badge">장대양봉</span>
      </div>
      <div className="rc-bar-row">
        <span className="rc-rate">{fmtRate(event.rate)}</span>
        <span className="rc-date">{event.date || '-'}</span>
      </div>
      <CandleStrip candles={stock.candles || stock.events || []} targetDate={event.date} />
      <div className="rc-detail-grid">
        <span className="rc-detail-label">거래대금</span><span className="rc-detail-value">{fmtEok(event.amountEok)}</span>
        <span className="rc-detail-label">발생 횟수</span><span className="rc-detail-value">{fmt(stock.events?.length || 0)}회</span>
        <span className="rc-detail-label">현재가</span><span className="rc-detail-value">{fmtPrice(stock.price)}</span>
        <span className="rc-detail-label">원천</span><span className="rc-detail-value">{providerText(stock.sourceLabel || '-')}</span>
      </div>
    </article>
  );
}

function BurstPanel({ alerts, threshold }) {
  const rows = [...alerts].sort((a, b) => new Date(b.detectedAt || 0) - new Date(a.detectedAt || 0)).slice(0, 60);
  return (
    <section className="side-panel">
      <div className="side-head">
        <h2>거래대금 포착</h2>
        <span>{fmt(rows.length)}건</span>
      </div>
      <p className="side-sub">1분 또는 3분 거래대금 {fmtTradeAmount(threshold || 1000)} 이상</p>
      <div className="burst-list">
        {rows.length ? rows.map((alert) => (
          <div className="burst-row" key={alert.key || `${alert.code}-${alert.windowLabel}-${alert.detectedAt}`}>
            <div>
              <strong>{alert.name}</strong>
              <small>{alert.windowLabel} · {alert.sector} · {alert.code}</small>
            </div>
            <span className="burst-amt">{fmtTradeAmount(alert.tradeAmountMillion)}</span>
            <span className="burst-count">{fmt(alert.count || 1)}회</span>
          </div>
        )) : <div className="empty-small">아직 포착된 종목이 없습니다.</div>}
      </div>
    </section>
  );
}

function RuntimePanel({ snapshot, stocks, sectors }) {
  return (
    <section className="side-panel">
      <div className="side-head"><h2>실시간 수신 상태</h2></div>
      <dl className="runtime-list">
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

function CandleChart({ candles }) {
  const rows = (candles || []).slice(-44);
  if (!rows.length) return <EmptyPanel text="일봉 데이터가 아직 없습니다." />;
  const max = Math.max(...rows.map((row) => Math.max(number(row.high), number(row.close), number(row.open))), 1);
  const min = Math.min(...rows.map((row) => Math.min(nonZero(row.low), nonZero(row.close), nonZero(row.open))).filter(Boolean), max);
  const range = max - min || 1;
  return (
    <div className="candle-chart">
      {rows.map((row) => {
        const open = number(row.open);
        const close = number(row.close);
        const high = number(row.high) || Math.max(open, close);
        const low = number(row.low) || Math.min(open, close);
        const top = ((max - high) / range) * 100;
        const wickHeight = Math.max(4, ((high - low) / range) * 100);
        const bodyHeight = Math.max(4, (Math.abs(close - open) / range) * 100);
        const bodyTop = ((max - Math.max(open, close)) / range) * 100;
        const up = close >= open;
        return (
          <div className="candle-col" key={row.date} title={`${row.date} ${fmtRate(row.rate)} ${fmtEok(row.amountEok)}`}>
            <span className="wick" style={{ top: `${top}%`, height: `${wickHeight}%` }} />
            <span className={`body ${up ? 'up-bg' : 'down-bg'}`} style={{ top: `${bodyTop}%`, height: `${bodyHeight}%` }} />
          </div>
        );
      })}
    </div>
  );
}

function CandleStrip({ candles, targetDate }) {
  const rows = (candles || []).slice(-14);
  if (!rows.length) return <div className="rc-candle empty-strip" />;
  const maxAbs = Math.max(...rows.map((row) => Math.abs(number(row.rate))), 15);
  return (
    <div className="rc-candle">
      {rows.map((row, index) => {
        const height = Math.max(4, Math.abs(number(row.rate)) / maxAbs * 54);
        const target = row.date === targetDate || index === rows.length - 1;
        return <span key={`${row.date}-${index}`} className={`rc-candle-bar ${target ? 'target' : ''}`} style={{ height }} title={`${row.date || ''} ${fmtRate(row.rate)}`} />;
      })}
    </div>
  );
}

function FinancialChart({ rows }) {
  if (!rows.length) return <div className="empty-chart" />;
  const max = Math.max(...rows.map((row) => Math.max(number(row.revenue), Math.abs(number(row.profit)))), 1);
  return (
    <div className="bar-chart">
      {rows.map((row) => (
        <div className="bar-col" key={row.label}>
          <span className="bar-val">{fmtEok(number(row.revenue))}</span>
          <span className="bar" style={{ height: `${Math.max(4, number(row.revenue) / max * 150)}px` }} />
          <span className={`bar profit ${number(row.profit) < 0 ? 'neg' : ''}`} style={{ height: `${Math.max(4, Math.abs(number(row.profit)) / max * 150)}px` }} />
          <span className="bar-label">{row.label}</span>
        </div>
      ))}
    </div>
  );
}

function NewsList({ rows, stock }) {
  if (!rows.length) {
    const url = `https://search.naver.com/search.naver?where=news&query=${encodeURIComponent(stock.name || stock.code || '')}`;
    return <a className="news-item" href={url} target="_blank" rel="noreferrer">뉴스 공급자가 연결되지 않았습니다. 포털 뉴스 검색 열기</a>;
  }
  return rows.map((row) => (
    <a className="news-item" href={row.url || '#'} target="_blank" rel="noreferrer" key={row.url || row.title}>
      <strong>{row.title}</strong>
      <small>{row.source || '-'} · {row.time || '-'}</small>
    </a>
  ));
}

function PeerList({ rows, currentCode }) {
  if (!rows.length) return <p className="muted-text">동종업계 비교 데이터가 아직 없습니다.</p>;
  return rows.map((row) => (
    <button className="peer-row" key={row.code || row.name} type="button" onClick={() => row.code && go(`/stock/${row.code}`)}>
      <span className={row.me || row.code === currentCode ? 'me' : ''}>{row.name}</span>
      <strong>{fmtEok(row.amountEok)}</strong>
    </button>
  ));
}

function Metric({ label, value, tone: metricTone }) {
  return <div className="stat-card"><div className="stat-label">{label}</div><div className={`stat-value ${metricTone || ''}`}>{value}</div></div>;
}

function Condition({ label, value }) {
  return <div className="cond-item"><span>{label}</span><strong>{value}</strong></div>;
}

function HeadStat({ label, value }) {
  return <div><div className="hstat-label">{label}</div><div className="hstat-value">{value}</div></div>;
}

function InfoRow({ label, value }) {
  return <div className="info-row"><span>{label}</span><strong>{value || '-'}</strong></div>;
}

function EmptyPanel({ text }) {
  return <div className="empty-panel">{text}</div>;
}

function Sparkline({ points }) {
  const values = (points || []).map((point) => number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return <div className="sparkline empty"><span /></div>;
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

function useRoute() {
  const [path, setPath] = useState(() => window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    window.addEventListener('moneyboard:navigate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('moneyboard:navigate', onPop);
    };
  }, []);
  if (path.startsWith('/screener')) return { name: 'screener' };
  const stockMatch = path.match(/^\/stock\/([^/]+)/);
  if (stockMatch) return { name: 'stock', code: decodeURIComponent(stockMatch[1]) };
  return { name: 'dashboard' };
}

function go(path) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new Event('moneyboard:navigate'));
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { accept: 'application/json' } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok && !payload.message && !payload.error) throw new Error(`HTTP ${response.status}`);
  return payload;
}

function flattenStocks(sectors) {
  const rows = [];
  sectors.forEach((sector, sectorIndex) => {
    (sector.stocks || []).forEach((stock) => {
      rows.push({
        ...stock,
        sector: stock.sector || sector.name,
        sectorRank: sectorIndex + 1,
        sectorScore: stock.sectorScore ?? sector.score ?? calcSectorScore(sector),
      });
    });
  });
  return rows;
}

function sortDashboardStocks(rows, sort) {
  const sorted = [...rows];
  if (sort === 'volume') sorted.sort((a, b) => number(b.volume) - number(a.volume));
  else if (sort === 'rate') sorted.sort((a, b) => number(b.changeRate) - number(a.changeRate));
  else if (sort === 'score') sorted.sort((a, b) => number(b.sectorScore) - number(a.sectorScore));
  else sorted.sort((a, b) => number(b.tradeAmountMillion) - number(a.tradeAmountMillion));
  return sorted;
}

function calcSectorScore(sector) {
  const amount = number(sector.tradeAmountMillion);
  const rate = Math.max(0, number(sector.changeRate));
  const net = Math.max(0, number(sector.netBuyRatio));
  return Math.round(Math.min(100, amount / 1000 + rate * 8 + net * 0.6));
}

function statusLabel(status) {
  if (status === 'online') return '실시간';
  if (status === 'bridge-error') return '브릿지 오류';
  if (status === 'stream-error') return '재연결 대기';
  if (status === 'parse-error') return '데이터 오류';
  return '연결 중';
}

function fmt(value) {
  const next = number(value);
  if (!Number.isFinite(next)) return '-';
  return next.toLocaleString('ko-KR');
}

function fmtDecimal(value, digits = 2) {
  const next = number(value);
  if (!Number.isFinite(next)) return '-';
  return next.toLocaleString('ko-KR', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtPrice(value) {
  const next = number(value);
  if (!Number.isFinite(next) || next <= 0) return '-';
  return `${next.toLocaleString('ko-KR')}원`;
}

function fmtRate(value) {
  const next = number(value);
  if (!Number.isFinite(next) || next === 0) return '0.00%';
  return `${next > 0 ? '+' : ''}${next.toFixed(2)}%`;
}

function fmtSignedRatio(value) {
  const next = number(value);
  if (!Number.isFinite(next) || next === 0) return '0.0%';
  return `${next > 0 ? '+' : ''}${next.toFixed(1)}%`;
}

function fmtTradeAmount(value) {
  const million = number(value);
  if (!Number.isFinite(million) || million <= 0) return '-';
  const eok = million / 100;
  if (eok >= 10000) return `${(eok / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}조`;
  if (eok >= 1) return `${eok.toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억`;
  return `${million.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}백만`;
}

function fmtEok(value) {
  const eok = number(value);
  if (!Number.isFinite(eok) || eok <= 0) return '-';
  if (eok >= 10000) return `${fmtDecimal(eok / 10000, 2)}조원`;
  return `${fmtDecimal(eok, 1)}억원`;
}

function fmtOverviewValue(item) {
  const value = number(item?.value);
  if (!Number.isFinite(value)) return '-';
  return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}${item?.suffix || ''}`;
}

function providerText(value) {
  const text = String(value || 'Kiwoom OpenAPI+');
  return text
    .replace('Kiwoom OpenAPI+ only', 'Kiwoom OpenAPI+')
    .replace('Kiwoom OpenAPI+ snapshot fallback', 'Kiwoom 스냅샷')
    .replace('Kiwoom OpenAPI+ opt10081', '키움 일봉 TR')
    .replace('Kiwoom OpenAPI+ debug', '키움 디버그');
}

function ratio(part, total) {
  const p = number(part);
  const t = number(total);
  if (!p || !t) return '0.0%';
  return `${((p / t) * 100).toFixed(1)}%`;
}

function tone(value) {
  return number(value) >= 0 ? 'up' : 'down';
}

function number(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function nonZero(value) {
  const next = number(value);
  return next > 0 ? next : null;
}

function timeText(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString('ko-KR', { hour12: false });
}

function uniqueValues(values) {
  return [...new Set(values.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'ko-KR'));
}
