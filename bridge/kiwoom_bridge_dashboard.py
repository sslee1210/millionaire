from typing import Any, Dict

import kiwoom_bridge as base
import kiwoom_bridge_kiwoom_only as ko
import kiwoom_side
from kiwoom_flow import FLOW_AMOUNT_THRESHOLD_MILLION, FLOW_WINDOWS_SEC, FlowDetector


class DashboardController(ko.KiwoomOnlyController):
    def __init__(self) -> None:
        super().__init__()
        self.flow_detector = FlowDetector()
        self.current_quote_cursor = 0
        self.side_store = kiwoom_side.new_store()

    def health(self) -> Dict[str, Any]:
        payload = super().health()
        payload['coverageMode'] = {
            'maxRealtimeCodes': base.MAX_REALTIME_CODES,
            'candidateRefreshMs': base.CANDIDATE_REFRESH_MS,
            'currentQuotePollMs': base.CURRENT_QUOTE_POLL_MS,
            'currentQuoteBatchLimit': base.CURRENT_QUOTE_BATCH_LIMIT,
            'currentQuoteCursor': self.current_quote_cursor,
            'trDelayMs': base.TR_DELAY_MS,
            'currentQuoteMode': 'rotating batch across registered codes',
        }
        payload['flowDetector'] = self.flow_detector.config()
        payload['flowDetector']['activeEventCount'] = len(self.flow_detector.events())
        payload['buySellRatioPolicy'] = 'estimated from signed Kiwoom FID 15 trade volume after bridge start'
        return payload

    def snapshot(self, sector_limit: int, stocks_per_sector: int, sort_key: str) -> Dict[str, Any]:
        payload = super().snapshot(sector_limit, stocks_per_sector, sort_key)
        events = self.flow_detector.events()
        stats = payload.setdefault('stats', {})
        stats['flowThresholdMillion'] = FLOW_AMOUNT_THRESHOLD_MILLION
        stats['flowWindowsSec'] = list(FLOW_WINDOWS_SEC)
        stats['flowEventCount'] = len(events)
        stats['maxRealtimeCodes'] = base.MAX_REALTIME_CODES
        stats['currentQuoteBatchLimit'] = base.CURRENT_QUOTE_BATCH_LIMIT
        stats['currentQuoteMode'] = 'rotating'
        stats['buySellRatioPolicy'] = 'estimated from signed Kiwoom FID 15 trade volume after bridge start'
        for sector in payload.get('sectors', []):
            sector.update(kiwoom_side.aggregate(sector.get('stocks', [])))
        payload['flowAlerts'] = events
        payload['sectorFlowBoard'] = sorted(
            payload.get('sectors', []),
            key=lambda item: (float(item.get('tradeAmountMillion') or 0), float(item.get('flow60sTradeAmountMillion') or 0)),
            reverse=True,
        )[:sector_limit]
        return payload

    def refresh_current_quotes(self, max_codes: int = base.CURRENT_QUOTE_BATCH_LIMIT) -> None:
        if not base.ALLOW_CURRENT_TR_FALLBACK or self._refreshing_current or not self.login or not self.registered_codes:
            return
        self._refreshing_current = True
        try:
            total = len(self.registered_codes)
            limit = max(1, min(int(max_codes or base.CURRENT_QUOTE_BATCH_LIMIT), total))
            start = self.current_quote_cursor % total
            ordered = self.registered_codes[start:] + self.registered_codes[:start]
            for code in ordered[:limit]:
                quote = self._request_current_quote(code)
                if quote:
                    self.current_quotes[code] = quote
                base.pause(base.TR_DELAY_MS)
            self.current_quote_cursor = (start + limit) % total
            self.last_current_quote_refresh_at = base.now_iso()
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._refreshing_current = False

    def _on_receive_real_data(self, code, real_type, real_data) -> None:
        super()._on_receive_real_data(code, real_type, real_data)
        normalized = base.clean_code(code)
        quote = self.quotes.get(normalized)
        if quote:
            self.flow_detector.add_sample(normalized, quote, self.master.get(normalized, {}))
            kiwoom_side.add_trade(self.side_store, normalized, quote, base.to_number)

    def _normalize_stock(self, code: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        stock = super()._normalize_stock(code, quote)
        stock.update(self.flow_detector.metrics_for(code))
        stock.update(kiwoom_side.metrics(self.side_store, code))
        return stock


base.KiwoomController = DashboardController

if __name__ == '__main__':
    base.main()
