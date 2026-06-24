from typing import Any, Dict

import kiwoom_bridge as base
import kiwoom_bridge_kiwoom_only as ko
from kiwoom_flow import FLOW_AMOUNT_THRESHOLD_MILLION, FLOW_WINDOWS_SEC, FlowDetector


class KiwoomFlowController(ko.KiwoomOnlyController):
    def __init__(self) -> None:
        super().__init__()
        self.flow_detector = FlowDetector()

    def health(self) -> Dict[str, Any]:
        payload = super().health()
        payload['coverageMode'] = {
            'maxRealtimeCodes': base.MAX_REALTIME_CODES,
            'candidateRefreshMs': base.CANDIDATE_REFRESH_MS,
            'currentQuotePollMs': base.CURRENT_QUOTE_POLL_MS,
            'currentQuoteBatchLimit': base.CURRENT_QUOTE_BATCH_LIMIT,
            'trDelayMs': base.TR_DELAY_MS,
        }
        payload['flowDetector'] = self.flow_detector.config()
        payload['flowDetector']['activeEventCount'] = len(self.flow_detector.events())
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
        payload['flowAlerts'] = events
        return payload

    def _on_receive_real_data(self, code, real_type, real_data) -> None:
        super()._on_receive_real_data(code, real_type, real_data)
        normalized = base.clean_code(code)
        quote = self.quotes.get(normalized)
        if quote:
            self.flow_detector.add_sample(normalized, quote, self.master.get(normalized, {}))

    def _normalize_stock(self, code: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        stock = super()._normalize_stock(code, quote)
        stock.update(self.flow_detector.metrics_for(code))
        return stock


base.KiwoomController = KiwoomFlowController

if __name__ == '__main__':
    base.main()
