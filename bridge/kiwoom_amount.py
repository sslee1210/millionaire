from math import log
from typing import Any, Dict, List, Tuple


def _to_number(value: Any) -> float:
    text = str(value or '').strip().replace(',', '').replace('+', '').replace('%', '')
    if text in {'', '-', '--'}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def to_int(value: Any) -> int:
    return int(abs(_to_number(value)))


def normalize_trade_amount_million(raw_value: Any, price: int = 0, volume: int = 0, source: str = 'kiwoom') -> Tuple[int, Dict[str, Any]]:
    """Normalize Kiwoom trade amount to million KRW.

    No external source is used. The only sanity check is Kiwoom price * Kiwoom volume.
    Kiwoom TR/FID screens may expose trade amount with different visible units depending
    on request/screen context, so the closest Kiwoom-only unit interpretation is selected.
    """
    raw = to_int(raw_value)
    estimated = int((int(price or 0) * int(volume or 0)) / 1_000_000) if price and volume else 0

    if raw <= 0 and estimated > 0:
        selected_name = 'estimated-from-kiwoom-price-volume'
        selected_value = estimated
    elif raw > 0 and estimated > 0:
        candidates: List[Tuple[str, int]] = [
            ('raw-as-million-krw', raw),
            ('raw-times-100-assume-eok-krw', raw * 100),
            ('raw-times-10-assume-ten-million-krw', raw * 10),
            ('raw-divide-10', max(1, round(raw / 10))),
            ('raw-divide-100', max(1, round(raw / 100))),
            ('raw-divide-1000', max(1, round(raw / 1000))),
        ]

        def distance(item: Tuple[str, int]) -> float:
            _, value = item
            ratio = max(value, 1) / max(estimated, 1)
            return abs(log(max(ratio, 1e-9)))

        selected_name, selected_value = min(candidates, key=distance)
    elif raw > 0:
        selected_name = 'raw-as-million-krw-no-estimate'
        selected_value = raw
    else:
        selected_name = 'zero'
        selected_value = 0

    ratio = (selected_value / estimated) if estimated else None
    return int(max(0, selected_value or 0)), {
        'tradeAmountRaw': raw,
        'tradeAmountRawField': str(raw_value or '').strip(),
        'tradeAmountEstimatedMillion': estimated,
        'tradeAmountUnitFix': selected_name,
        'tradeAmountSelectedToEstimateRatio': ratio,
        'tradeAmountSource': source,
    }
