import os
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Tuple

FLOW_AMOUNT_THRESHOLD_MILLION = int(os.getenv('FLOW_AMOUNT_THRESHOLD_MILLION', '1000'))
FLOW_WINDOWS_SEC: Tuple[int, ...] = tuple(
    int(item.strip())
    for item in os.getenv('FLOW_WINDOWS_SEC', '60,180').split(',')
    if item.strip().isdigit()
) or (60, 180)
FLOW_KEEP_SEC = max(FLOW_WINDOWS_SEC) + int(os.getenv('FLOW_KEEP_EXTRA_SEC', '90'))
FLOW_EVENT_TTL_SEC = int(os.getenv('FLOW_EVENT_TTL_SEC', '900'))
FLOW_EVENT_LIMIT = int(os.getenv('FLOW_EVENT_LIMIT', '80'))


class FlowDetector:
    def __init__(self) -> None:
        self.samples: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)
        self.events_by_key: Dict[str, Dict[str, Any]] = {}

    def config(self) -> Dict[str, Any]:
        return {
            'windowsSec': list(FLOW_WINDOWS_SEC),
            'thresholdMillion': FLOW_AMOUNT_THRESHOLD_MILLION,
            'thresholdWon': FLOW_AMOUNT_THRESHOLD_MILLION * 1_000_000,
            'source': 'Kiwoom realtime FID cumulative trade amount delta',
        }

    def add_sample(self, code: str, quote: Dict[str, Any], master: Dict[str, Any]) -> None:
        ts = time.time()
        amount = int(quote.get('tradeAmountMillion') or 0)
        volume = int(quote.get('volume') or 0)
        if amount <= 0:
            return

        samples = self.samples[code]
        samples.append({'ts': ts, 'amount': amount, 'volume': volume})
        cutoff = ts - FLOW_KEEP_SEC
        while samples and samples[0]['ts'] < cutoff:
            samples.popleft()

        for window_sec in FLOW_WINDOWS_SEC:
            metric = self.metric(code, window_sec)
            if metric['amountMillion'] >= FLOW_AMOUNT_THRESHOLD_MILLION:
                self.events_by_key[f'{code}:{window_sec}'] = {
                    'key': f'{code}:{window_sec}',
                    'code': code,
                    'name': quote.get('name') or master.get('name') or code,
                    'sector': master.get('sector') or '기타',
                    'windowSec': window_sec,
                    'windowLabel': f'{window_sec // 60}분',
                    'tradeAmountMillion': metric['amountMillion'],
                    'volume': metric['volume'],
                    'price': int(quote.get('price') or 0),
                    'changeRate': float(quote.get('changeRate') or 0),
                    'detectedTs': ts,
                    'detectedAt': quote.get('updatedAt'),
                    'source': 'kiwoom-realtime-delta',
                }

    def metric(self, code: str, window_sec: int) -> Dict[str, Any]:
        samples = self.samples.get(code)
        if not samples or len(samples) < 2:
            return {'amountMillion': 0, 'volume': 0, 'sampleCount': len(samples or [])}
        cutoff = time.time() - window_sec
        window_samples = [sample for sample in samples if sample['ts'] >= cutoff]
        if len(window_samples) < 2:
            return {'amountMillion': 0, 'volume': 0, 'sampleCount': len(window_samples)}
        first = window_samples[0]
        last = window_samples[-1]
        return {
            'amountMillion': max(0, int(last['amount']) - int(first['amount'])),
            'volume': max(0, int(last['volume']) - int(first['volume'])),
            'sampleCount': len(window_samples),
        }

    def metrics_for(self, code: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        hot = False
        for window_sec in FLOW_WINDOWS_SEC:
            metric = self.metric(code, window_sec)
            prefix = f'flow{window_sec}s'
            result[f'{prefix}TradeAmountMillion'] = metric['amountMillion']
            result[f'{prefix}Volume'] = metric['volume']
            result[f'{prefix}SampleCount'] = metric['sampleCount']
            hot = hot or metric['amountMillion'] >= FLOW_AMOUNT_THRESHOLD_MILLION
        result['flowHot'] = hot
        return result

    def events(self) -> List[Dict[str, Any]]:
        cutoff = time.time() - FLOW_EVENT_TTL_SEC
        stale = [key for key, item in self.events_by_key.items() if float(item.get('detectedTs') or 0) < cutoff]
        for key in stale:
            self.events_by_key.pop(key, None)
        return sorted(
            self.events_by_key.values(),
            key=lambda item: (float(item.get('detectedTs') or 0), int(item.get('tradeAmountMillion') or 0)),
            reverse=True,
        )[:FLOW_EVENT_LIMIT]
