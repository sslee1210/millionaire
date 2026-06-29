from collections import defaultdict
from typing import Any, Dict, List, Optional
import os

import kiwoom_bridge as base
from kiwoom_amount import normalize_trade_amount_million
from kiwoom_sector_map import parse_code_list, parse_theme_groups, pick_sector


TRADE_AMOUNT_UNIT_POLICY = 'kiwoom-only-price-volume-sanity-normalization'

# Determine once whether Naver sector fallback is enabled.
ALLOW_NAVER_SECTOR = str(os.getenv('ALLOW_NAVER_SECTOR', '0')).lower() in ('1', 'true', 'yes')


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
            'rule': 'raw Kiwoom trade amount is normalized against Kiwoom price * Kiwoom volume only',
        }
        # Sector mapping metadata. Include Naver fallback in provider and priority if enabled.
        provider = 'Kiwoom OpenAPI+ with Naver fallback' if ALLOW_NAVER_SECTOR else 'Kiwoom OpenAPI+ only'
        priority = ['kiwoom-theme', 'kiwoom-master-info', 'kiwoom-name-hint', 'kiwoom-name-keyword']
        if ALLOW_NAVER_SECTOR:
            priority.append('naver')
        payload['sectorMapping'] = {
            'provider': provider,
            'priority': priority,
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
        # Count unclassified stocks including the broad fallback bucket
        stats['unclassifiedCount'] = sum(
            1 for code in self.registered_codes
            if self.master.get(code, {}).get('sector') in {'미분류', '기타', '테마·스몰캡'}
        )
        stats['tradeAmountUnitPolicy'] = TRADE_AMOUNT_UNIT_POLICY
        # Data boundary description reflecting whether Naver fallback is used
        if ALLOW_NAVER_SECTOR:
            stats['dataBoundary'] = 'Kiwoom OpenAPI+ with Naver sector fallback: no external price provider or trade data'
        else:
            stats['dataBoundary'] = 'Kiwoom OpenAPI+ only: no Naver, no external securities link, no external price parser'
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
            # Pass the code to pick_sector so that Naver fallback can be used if enabled
            sector_info = pick_sector(raw_info, name, themes, code)
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
            trade_amount_million, amount_meta = normalize_trade_amount_million(
                row.get('거래대금'),
                price=price,
                volume=volume,
                source='ranking-tr-opt10030-opt10032',
            )

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
            item['price'] = price or item['price']
            if rank_key == 'amountRank':
                item['volume'] = volume
                item['tradeAmountMillion'] = trade_amount_million
            else:
                item['volume'] = item['volume'] or volume
                item['tradeAmountMillion'] = item['tradeAmountMillion'] or trade_amount_million
            item['changeRate'] = item.get('changeRate') or base.to_number(row.get('등락률') or row.get('등락율'))
            item['tradeAmountSource'] = 'ranking-tr-normalized'
            item.update(amount_meta)
            item['rawTr'] = row
            item['rankingBasis'] = 'amount' if rank_key == 'amountRank' else item.get('rankingBasis') or 'volume'

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
        trade_amount_million, amount_meta = normalize_trade_amount_million(
            row.get('거래대금'),
            price=price,
            volume=volume,
            source='current-price-tr-opt10001',
        )
        amount_from = 'opt10001-normalized'

        if trade_amount_million <= 0:
            fallback_raw = candidate.get('tradeAmountRaw') or candidate.get('tradeAmountRawMillion') or candidate.get('tradeAmountMillion')
            trade_amount_million, amount_meta = normalize_trade_amount_million(
                fallback_raw,
                price=price,
                volume=volume,
                source='ranking-candidate-fallback',
            )
            amount_from = 'ranking-candidate-normalized-fallback'

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

    def _request_volume_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '정렬구분': '1',
            '관리종목포함': '0',
            '신용구분': '0',
            '거래량구분': '0',
            '가격구분': '0',
            '거래소구분': base.EXCHANGE_TYPE,
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '현재거래량', '거래대금']
        return self._request_tr('volume_rank', 'opt10030', inputs, fields)

    def _request_amount_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '관리종목포함': '0',
            '거래소구분': base.EXCHANGE_TYPE,
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '현재거래량', '거래대금']
        return self._request_tr('amount_rank', 'opt10032', inputs, fields)

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
        trade_amount_million, amount_meta = normalize_trade_amount_million(
            raw_trade_amount,
            price=price,
            volume=volume,
            source='realtime-fid-14',
        )

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
        stock['tradeAmountRaw'] = quote.get('tradeAmountRaw')
        stock['tradeAmountRawField'] = quote.get('tradeAmountRawField')
        stock['tradeAmountRawMillion'] = quote.get('tradeAmountRaw')
        stock['tradeAmountEstimatedMillion'] = quote.get('tradeAmountEstimatedMillion')
        stock['tradeAmountSelectedToEstimateRatio'] = quote.get('tradeAmountSelectedToEstimateRatio')
        return stock


base.KiwoomController = KiwoomOnlyController

if __name__ == '__main__':
    base.main()
