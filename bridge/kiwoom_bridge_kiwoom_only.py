from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import kiwoom_bridge as base
from kiwoom_sector_map import parse_code_list, parse_theme_groups, pick_sector


TRADE_AMOUNT_UNIT_POLICY = 'million-krw-with-price-volume-sanity-check'


def normalize_trade_amount_million(raw_value: Any, price: int = 0, volume: int = 0) -> Tuple[int, Dict[str, Any]]:
    """Return trade amount in million KRW.

    Kiwoom TR/FID trade amount values are treated as million KRW. Some screens/events can
    return a value that is 10/100/1000x larger than the price*volume sanity range. In that
    case we keep the Kiwoom-only source but correct the display unit instead of mixing in
    any external parser.
    """
    raw_million = base.to_int(raw_value)
    estimated_million = int((int(price or 0) * int(volume or 0)) / 1_000_000) if price and volume else 0
    normalized = raw_million
    unit_fix = 'raw-million-krw'
    ratio: Optional[float] = None

    if raw_million <= 0 and estimated_million > 0:
        normalized = estimated_million
        unit_fix = 'estimated-from-price-volume'
    elif raw_million > 0 and estimated_million > 0:
        ratio = raw_million / max(estimated_million, 1)
        if 7.5 <= ratio <= 12.5:
            normalized = int(round(raw_million / 10))
            unit_fix = 'raw-divide-10-by-sanity-check'
        elif 75 <= ratio <= 125:
            normalized = int(round(raw_million / 100))
            unit_fix = 'raw-divide-100-by-sanity-check'
        elif 750 <= ratio <= 1250:
            normalized = int(round(raw_million / 1000))
            unit_fix = 'raw-divide-1000-by-sanity-check'
        elif ratio > 1250:
            normalized = estimated_million
            unit_fix = 'estimated-from-price-volume-outlier'

    meta = {
        'tradeAmountRawMillion': raw_million,
        'tradeAmountEstimatedMillion': estimated_million,
        'tradeAmountUnitPolicy': TRADE_AMOUNT_UNIT_POLICY,
        'tradeAmountUnitFix': unit_fix,
        'tradeAmountRawToEstimateRatio': ratio,
    }
    return max(0, int(normalized or 0)), meta


class KiwoomOnlyController(base.KiwoomController):
    def __init__(self) -> None:
        super().__init__()
        self.theme_by_code: Dict[str, List[str]] = {}
        self.theme_group_count = 0
        self.theme_loaded = False
        self.last_theme_refresh_at = None

    def health(self) -> Dict[str, Any]:
        payload = super().health()
        payload['tradeAmountUnit'] = {
            'displayUnit': 'million KRW internally, formatted as KRW 조/억 in UI',
            'policy': TRADE_AMOUNT_UNIT_POLICY,
            'source': 'Kiwoom OpenAPI+ only',
        }
        payload['sectorMapping'] = {
            'provider': 'Kiwoom OpenAPI+ only',
            'priority': ['kiwoom-theme', 'kiwoom-master-info', 'kiwoom-name-hint', 'kiwoom-name-keyword'],
            'themeLoaded': self.theme_loaded,
            'themeGroupCount': self.theme_group_count,
            'themeCodeCount': len(self.theme_by_code),
            'lastThemeRefreshAt': self.last_theme_refresh_at,
        }
        return payload

    def snapshot(self, sector_limit: int, stocks_per_sector: int, sort_key: str) -> Dict[str, Any]:
        payload = super().snapshot(sector_limit, stocks_per_sector, sort_key)
        stats = payload.setdefault('stats', {})
        stats['themeLoaded'] = self.theme_loaded
        stats['themeGroupCount'] = self.theme_group_count
        stats['themeCodeCount'] = len(self.theme_by_code)
        stats['lastThemeRefreshAt'] = self.last_theme_refresh_at
        stats['unclassifiedCount'] = sum(
            1 for code in self.registered_codes
            if self.master.get(code, {}).get('sector') == '미분류'
        )
        stats['tradeAmountUnitPolicy'] = TRADE_AMOUNT_UNIT_POLICY
        return payload

    def _ensure_theme_map(self) -> None:
        if self.theme_loaded:
            return
        try:
            raw_groups = str(self.ocx.dynamicCall('GetThemeGroupList(int)', 1) or '')
            groups = parse_theme_groups(raw_groups)
            self.theme_group_count = len(groups)
            theme_by_code: Dict[str, List[str]] = defaultdict(list)
            for theme_id, theme_name in groups:
                raw_codes = str(self.ocx.dynamicCall('GetThemeGroupCode(QString)', theme_id) or '')
                for code in parse_code_list(raw_codes, base.clean_code):
                    if theme_name and theme_name not in theme_by_code[code]:
                        theme_by_code[code].append(theme_name)
            self.theme_by_code = dict(theme_by_code)
            self.theme_loaded = True
            self.last_theme_refresh_at = base.now_iso()
        except Exception as exc:
            self.theme_loaded = False
            self.last_error = f'Kiwoom theme mapping failed: {exc}'

    def _hydrate_master(self, codes: List[str]) -> None:
        self._ensure_theme_map()
        for code in codes:
            if code in self.master:
                continue
            name = self._code_name(code)
            raw_info = str(self.ocx.dynamicCall('GetMasterStockInfo(QString)', code) or '')
            themes = self.theme_by_code.get(code, [])
            sector_info = pick_sector(raw_info, name, themes)
            self.master[code] = {
                'code': code,
                'name': name,
                'rawInfo': raw_info,
                'sector': sector_info['sector'],
                'sectorSource': sector_info['sectorSource'],
                'themes': sector_info['themes'],
                'excluded': base.is_excluded_name(name) or base.is_excluded_info(raw_info),
            }

    def _merge_rank_rows(self, target: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]], rank_key: str, market: str) -> None:
        for rank, row in enumerate(rows, start=1):
            code = base.clean_code(row.get('종목코드') or row.get('code'))
            if not code or code == '000000':
                continue

            name = row.get('종목명') or row.get('name') or self._code_name(code)
            if base.is_excluded_name(name):
                continue

            price = base.to_int(row.get('현재가'))
            volume = base.to_int(row.get('거래량') or row.get('현재거래량'))
            trade_amount_million, amount_meta = normalize_trade_amount_million(row.get('거래대금'), price, volume)

            item = target.setdefault(code, {
                'code': code,
                'name': name,
                'price': 0,
                'volume': 0,
                'tradeAmountMillion': 0,
                'changeRate': 0,
                'market': market,
                'isRealtime': False,
                'isCurrentTr': False,
                'source': 'kiwoom-tr-ranking-candidate',
                'sourceLabel': 'TR후보',
                'updatedAt': self.last_candidate_refresh_at,
            })

            item[rank_key] = min(rank, int(item.get(rank_key, rank))) if item.get(rank_key) else rank
            item['price'] = item['price'] or price
            item['volume'] = max(item.get('volume', 0), volume)
            item['tradeAmountMillion'] = max(item.get('tradeAmountMillion', 0), trade_amount_million)
            item['changeRate'] = item.get('changeRate') or base.to_number(row.get('등락률') or row.get('등락율'))
            item['tradeAmountSource'] = 'ranking-tr-normalized'
            item.update(amount_meta)
            item['rawTr'] = row

    def _request_current_quote(self, code: str) -> Optional[Dict[str, Any]]:
        normalized_code = base.clean_code(code)
        candidate = self.candidates.get(normalized_code, {})
        inputs = {'종목코드': normalized_code}
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '거래대금']
        rows = self._request_tr(f'current_quote_{normalized_code}', 'opt10001', inputs, fields)
        if not rows:
            return None

        row = rows[0]
        master = self.master.get(normalized_code, {})
        name = row.get('종목명') or master.get('name') or candidate.get('name') or self._code_name(normalized_code)
        price = base.to_int(row.get('현재가')) or int(candidate.get('price') or 0)
        volume = base.to_int(row.get('거래량')) or int(candidate.get('volume') or 0)
        trade_amount_million, amount_meta = normalize_trade_amount_million(row.get('거래대금'), price, volume)
        amount_from = 'opt10001-normalized'

        if trade_amount_million <= 0:
            trade_amount_million = int(candidate.get('tradeAmountMillion') or 0)
            amount_from = 'ranking-candidate-normalized'

        change_rate = base.to_number(row.get('등락률') or row.get('등락율')) or float(candidate.get('changeRate') or 0)
        payload = {
            'code': normalized_code,
            'name': name,
            'price': price,
            'changeRate': change_rate,
            'volume': volume,
            'tradeAmountMillion': trade_amount_million,
            'tradeVolume': 0,
            'time': None,
            'strength': 0,
            'updatedAt': base.now_iso(),
            'source': 'kiwoom-current-tr-opt10001',
            'sourceLabel': '키움현재가TR',
            'isRealtime': False,
            'isCurrentTr': True,
            'tradeAmountSource': amount_from,
            'rawCurrentTr': row,
            'rankCandidate': candidate,
        }
        payload.update(amount_meta)
        return payload

    def _on_receive_real_data(self, code, real_type, real_data) -> None:
        if str(real_type) != '주식체결':
            return

        code = base.clean_code(code)
        master = self.master.get(code, {})
        candidate = self.candidates.get(code, {})
        name = master.get('name') or candidate.get('name') or self._code_name(code)

        raw_price = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_PRICE)
        raw_change_rate = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_CHANGE_RATE)
        raw_volume = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_VOLUME)
        raw_trade_amount = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_TRADE_AMOUNT)
        raw_trade_volume = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_TRADE_VOLUME)
        raw_time = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_TIME)
        raw_strength = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, base.FID_STRENGTH)

        price = base.to_int(raw_price)
        volume = base.to_int(raw_volume)
        trade_amount_million, amount_meta = normalize_trade_amount_million(raw_trade_amount, price, volume)

        quote = {
            'code': code,
            'name': name,
            'price': price,
            'changeRate': base.to_number(raw_change_rate),
            'volume': volume,
            'tradeAmountMillion': trade_amount_million,
            'tradeVolume': base.to_int(raw_trade_volume),
            'time': str(raw_time or '').strip(),
            'strength': base.to_number(raw_strength),
            'updatedAt': base.now_iso(),
            'source': 'kiwoom-realtime-fid-stock-trade',
            'sourceLabel': '실시간 FID',
            'isRealtime': True,
            'isCurrentTr': False,
            'tradeAmountSource': 'realtime-fid-14-normalized',
            'rawRealtime': {
                'price': str(raw_price or '').strip(),
                'changeRate': str(raw_change_rate or '').strip(),
                'volume': str(raw_volume or '').strip(),
                'tradeAmount': str(raw_trade_amount or '').strip(),
                'tradeVolume': str(raw_trade_volume or '').strip(),
                'time': str(raw_time or '').strip(),
                'strength': str(raw_strength or '').strip(),
            },
        }
        quote.update(amount_meta)
        self.quotes[code] = quote
        self.last_real_event_at = quote['updatedAt']

    def _normalize_stock(self, code: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        stock = super()._normalize_stock(code, quote)
        master = self.master.get(code, {})
        stock['sectorSource'] = master.get('sectorSource') or 'unknown'
        stock['themes'] = master.get('themes') or []
        stock['tradeAmountSource'] = quote.get('tradeAmountSource')
        stock['tradeAmountUnitFix'] = quote.get('tradeAmountUnitFix')
        stock['tradeAmountRawMillion'] = quote.get('tradeAmountRawMillion')
        stock['tradeAmountEstimatedMillion'] = quote.get('tradeAmountEstimatedMillion')
        stock['tradeAmountRawToEstimateRatio'] = quote.get('tradeAmountRawToEstimateRatio')
        return stock


base.KiwoomController = KiwoomOnlyController
base.main()
