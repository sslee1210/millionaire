import os
import re
import sys
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PyQt5.QtCore import QObject, QEventLoop, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
import uvicorn

from kiwoom_amount import normalize_trade_amount_million

FID_PRICE = 10
FID_CHANGE_RATE = 12
FID_VOLUME = 13
FID_TRADE_AMOUNT = 14
FID_TRADE_VOLUME = 15
FID_TIME = 20
FID_STRENGTH = 228
REAL_FIDS = '10;12;13;14;15;20;228'

BRIDGE_HOST = os.getenv('KIWOOM_BRIDGE_HOST', '127.0.0.1')
BRIDGE_PORT = int(os.getenv('KIWOOM_BRIDGE_PORT', '8765'))
MAX_REALTIME_CODES = int(os.getenv('MAX_REALTIME_CODES', '80'))
CANDIDATE_REFRESH_MS = int(os.getenv('CANDIDATE_REFRESH_MS', '60000'))
CURRENT_QUOTE_POLL_MS = int(os.getenv('CURRENT_QUOTE_POLL_MS', '120000'))
CURRENT_QUOTE_BATCH_LIMIT = int(os.getenv('CURRENT_QUOTE_BATCH_LIMIT', str(MAX_REALTIME_CODES)))
TR_DELAY_MS = int(os.getenv('TR_DELAY_MS', '750'))
SCREEN_BASE = int(os.getenv('KIWOOM_SCREEN_BASE', '9100'))
SCREEN_CAPACITY = int(os.getenv('KIWOOM_SCREEN_CAPACITY', '80'))
MARKETS = [item.strip() for item in os.getenv('KIWOOM_RANKING_MARKETS', '001,101').split(',') if item.strip()]
AMOUNT_RANK_MARKETS = [item.strip() for item in os.getenv('KIWOOM_AMOUNT_RANK_MARKETS', '000').split(',') if item.strip()]
STRICT_REALTIME_ONLY = os.getenv('KIWOOM_STRICT_REALTIME', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
ALLOW_CURRENT_TR_FALLBACK = os.getenv('KIWOOM_ALLOW_CURRENT_TR_FALLBACK', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
DISPLAY_RANKING_BASELINE = os.getenv('KIWOOM_DISPLAY_RANKING_BASELINE', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
EXCHANGE_TYPE = os.getenv('KIWOOM_EXCHANGE_TYPE', '3').strip() or '3'

EXCLUDE_NAME_RE = re.compile(r'(ETF|ETN|ELW|스팩|기업인수목적|리츠|KODEX|TIGER|ACE|SOL|RISE|KOSEF|HANARO|KBSTAR|ARIRANG|TIMEFOLIO|히어로즈)', re.I)


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def clean_code(value: str) -> str:
    return re.sub(r'[^0-9]', '', str(value or '')).zfill(6)[-6:]


def to_number(value: Any) -> float:
    text = str(value or '').strip().replace(',', '').replace('+', '')
    text = text.replace('%', '')
    if text in {'', '-', '--'}:
        return 0
    try:
        return float(text)
    except ValueError:
        return 0


def to_int(value: Any) -> int:
    return int(abs(to_number(value)))


def env_bool(name: str, default: bool = False) -> bool:
    fallback = '1' if default else '0'
    value = os.getenv(name, fallback).strip().lower()
    return value not in {'0', 'false', 'no', 'off'}


def parse_master_info(raw: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for token in str(raw or '').split(';'):
        token = token.strip()
        if not token:
            continue
        for sep in ['|', ':', '=']:
            if sep in token:
                key, value = token.split(sep, 1)
                result[key.strip()] = value.strip()
                break
    return result


def pick_sector(raw_info: str, name: str) -> str:
    info = parse_master_info(raw_info)
    for key in ['업종', '업종명', '업종구분', '대분류', '중분류', '섹터']:
        value = info.get(key)
        if value:
            return value
    return '미분류'


class RefreshRequest(BaseModel):
    maxRealtimeCodes: Optional[int] = None
    candidateRefreshMs: Optional[int] = None


class KiwoomController(QObject):
    refresh_signal = pyqtSignal(int)
    current_quote_signal = pyqtSignal(int)
    bridge_call_signal = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.ocx = QAxWidget('KHOPENAPI.KHOpenAPICtrl.1')
        self.ocx.OnEventConnect.connect(self._on_event_connect)
        self.ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.ocx.OnReceiveRealData.connect(self._on_receive_real_data)

        self.login = False
        self.login_error: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_candidate_refresh_at: Optional[str] = None
        self.last_current_quote_refresh_at: Optional[str] = None
        self.last_real_event_at: Optional[str] = None
        self.registered_codes: List[str] = []
        self.screens: List[str] = []
        # 주식체결 실시간 FID 수신값이다. 정규장 중에는 이 값이 최우선이다.
        self.quotes: Dict[str, Dict[str, Any]] = {}
        # 장마감/체결 이벤트 공백 구간에서 영웅문 현재가 화면의 일일 누적값에 맞추기 위한 키움 단건 TR 보정값이다.
        self.current_quotes: Dict[str, Dict[str, Any]] = {}
        self.master: Dict[str, Dict[str, Any]] = {}
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self._tr_loop: Optional[QEventLoop] = None
        self._tr_rows: List[Dict[str, Any]] = []
        self._tr_error: Optional[str] = None
        self._tr_fields: List[str] = []
        self._tr_rqname: str = ''
        self._tr_trcode: str = ''
        self._refreshing = False
        self._refreshing_current = False

        self.refresh_signal.connect(self.refresh_candidates)
        self.current_quote_signal.connect(self.refresh_current_quotes)
        self.bridge_call_signal.connect(self._handle_bridge_call)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh_candidates(MAX_REALTIME_CODES))
        self.timer.start(CANDIDATE_REFRESH_MS)

        self.current_quote_timer = QTimer(self)
        self.current_quote_timer.timeout.connect(lambda: self.refresh_current_quotes(CURRENT_QUOTE_BATCH_LIMIT))
        if ALLOW_CURRENT_TR_FALLBACK:
            self.current_quote_timer.start(max(30000, CURRENT_QUOTE_POLL_MS))

    @pyqtSlot(object)
    def _handle_bridge_call(self, payload: Dict[str, Any]) -> None:
        try:
            payload['result'] = payload['fn']()
        except Exception as exc:
            payload['error'] = str(exc)
            self.last_error = str(exc)
        finally:
            payload['event'].set()

    def connect_login(self) -> None:
        self.ocx.dynamicCall('CommConnect()')

    def health(self) -> Dict[str, Any]:
        return {
            'ok': self.login,
            'provider': 'Kiwoom OpenAPI+ only',
            'login': self.login,
            'loginError': self.login_error,
            'lastError': self.last_error,
            'registeredCount': len(self.registered_codes),
            'registeredCodes': self.registered_codes,
            'candidateCount': len(self.candidates),
            'quoteCount': len(self.quotes) + len(self.current_quotes),
            'realtimeQuoteCount': len(self.quotes),
            'currentTrQuoteCount': len(self.current_quotes),
            'lastCandidateRefreshAt': self.last_candidate_refresh_at,
            'lastCurrentQuoteRefreshAt': self.last_current_quote_refresh_at,
            'lastRealEventAt': self.last_real_event_at,
            'strictRealtimeOnly': STRICT_REALTIME_ONLY,
            'currentTrFallback': ALLOW_CURRENT_TR_FALLBACK,
            'exchangeType': EXCHANGE_TYPE,
            'exchangeTypeLabel': {'1': 'KRX', '2': 'NXT', '3': '통합'}.get(EXCHANGE_TYPE, EXCHANGE_TYPE),
            'currentQuotePollMs': CURRENT_QUOTE_POLL_MS,
            'rankingMarkets': MARKETS,
            'fid': {
                'price': FID_PRICE,
                'changeRate': FID_CHANGE_RATE,
                'volume': FID_VOLUME,
                'tradeAmount': FID_TRADE_AMOUNT,
                'tradeVolume': FID_TRADE_VOLUME,
                'time': FID_TIME,
                'strength': FID_STRENGTH,
            },
        }

    def snapshot(self, sector_limit: int, stocks_per_sector: int, sort_key: str) -> Dict[str, Any]:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        realtime_ready = 0
        current_tr_ready = 0
        provisional_count = 0

        for code in self.registered_codes:
            real_quote = self.quotes.get(code)
            current_quote = self.current_quotes.get(code)
            candidate_quote = self.candidates.get(code)
            quote = real_quote or current_quote or candidate_quote
            if not quote:
                continue

            stock = self._normalize_stock(code, quote)
            if stock['excluded']:
                continue

            if stock['isRealtime']:
                realtime_ready += 1
            elif stock['isCurrentTr']:
                current_tr_ready += 1
            else:
                provisional_count += 1

            # 화면 표시값은 기본적으로 키움 실시간 FID 또는 키움 현재가 TR만 사용한다.
            # opt10030/opt10032 랭킹 TR은 후보 선정용이며, KIWOOM_STRICT_REALTIME=0일 때만 임시 표시한다.
            if STRICT_REALTIME_ONLY and not (stock['isRealtime'] or stock['isCurrentTr']):
                continue

            grouped[stock['sector']].append(stock)

        sectors = []
        for sector_name, stocks in grouped.items():
            sorted_stocks = sort_stocks(stocks, sort_key)[:stocks_per_sector]
            if not sorted_stocks:
                continue
            sectors.append({
                'name': sector_name,
                'volume': sum(stock['volume'] for stock in sorted_stocks),
                'tradeAmountMillion': sum(stock['tradeAmountMillion'] for stock in sorted_stocks),
                'stocks': sorted_stocks,
            })

        if sort_key == 'volume':
            sectors.sort(key=lambda item: (item['volume'], item['tradeAmountMillion']), reverse=True)
        else:
            sectors.sort(key=lambda item: (item['tradeAmountMillion'], item['volume']), reverse=True)

        limited = sectors[:sector_limit]
        return {
            'ok': self.login,
            'provider': 'Kiwoom OpenAPI+ only',
            'updatedAt': now_iso(),
            'sort': sort_key,
            'sectors': limited,
            'message': None if limited else self._empty_message(),
            'stats': {
                'registeredCount': len(self.registered_codes),
                'candidateCount': len(self.candidates),
                'quoteCount': len(self.quotes) + len(self.current_quotes),
                'realtimeQuoteCount': len(self.quotes),
                'currentTrQuoteCount': len(self.current_quotes),
                'realtimeReadyCount': realtime_ready,
                'currentTrReadyCount': current_tr_ready,
                'provisionalCount': provisional_count,
                'visibleStockCount': sum(len(sector['stocks']) for sector in limited),
                'sectorCount': len(sectors),
                'maxRealtimeCodes': MAX_REALTIME_CODES,
                'candidateRefreshMs': CANDIDATE_REFRESH_MS,
                'currentQuotePollMs': CURRENT_QUOTE_POLL_MS,
                'strictRealtimeOnly': STRICT_REALTIME_ONLY,
                'currentTrFallback': ALLOW_CURRENT_TR_FALLBACK,
                'lastCandidateRefreshAt': self.last_candidate_refresh_at,
                'lastCurrentQuoteRefreshAt': self.last_current_quote_refresh_at,
                'lastRealEventAt': self.last_real_event_at,
            },
        }

    @pyqtSlot(int)
    def refresh_candidates(self, max_codes: int = MAX_REALTIME_CODES) -> None:
        if self._refreshing:
            return
        if not self.login:
            return
        self._refreshing = True
        try:
            ranking_rows: Dict[str, Dict[str, Any]] = {}
            for market in MARKETS:
                self._merge_rank_rows(ranking_rows, self._request_volume_rank(market), 'volumeRank', market)
                pause(TR_DELAY_MS)
                self._merge_rank_rows(ranking_rows, self._request_amount_rank(market), 'amountRank', market)
                pause(TR_DELAY_MS)

            ranked = rank_candidates(list(ranking_rows.values()))
            limit = max(1, min(int(max_codes or MAX_REALTIME_CODES), 300))
            selected = ranked[:limit]

            self.candidates = {item['code']: item for item in selected}
            self._hydrate_master(list(self.candidates.keys()))
            self._subscribe_realtime(list(self.candidates.keys()))

            selected_codes = set(self.candidates.keys())
            self.quotes = {code: quote for code, quote in self.quotes.items() if code in selected_codes}
            self.current_quotes = {code: quote for code, quote in self.current_quotes.items() if code in selected_codes}

            self.last_candidate_refresh_at = now_iso()
            self.last_error = None
            if ALLOW_CURRENT_TR_FALLBACK:
                QTimer.singleShot(1000, lambda: self.current_quote_signal.emit(CURRENT_QUOTE_BATCH_LIMIT))
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._refreshing = False

    @pyqtSlot(int)
    def refresh_current_quotes(self, max_codes: int = CURRENT_QUOTE_BATCH_LIMIT) -> None:
        if not ALLOW_CURRENT_TR_FALLBACK:
            return
        if self._refreshing_current:
            return
        if not self.login or not self.registered_codes:
            return
        self._refreshing_current = True
        try:
            limit = max(1, min(int(max_codes or CURRENT_QUOTE_BATCH_LIMIT), len(self.registered_codes)))
            for code in self.registered_codes[:limit]:
                quote = self._request_current_quote(code)
                if quote:
                    self.current_quotes[code] = quote
                pause(TR_DELAY_MS)
            self.last_current_quote_refresh_at = now_iso()
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._refreshing_current = False

    def _merge_rank_rows(self, target: Dict[str, Dict[str, Any]], rows: List[Dict[str, Any]], rank_key: str, market: str) -> None:
        for rank, row in enumerate(rows, start=1):
            code = clean_code(row.get('종목코드') or row.get('code'))
            if not code or code == '000000':
                continue

            name = row.get('종목명') or row.get('name') or self._code_name(code)
            if is_excluded_name(name):
                continue

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

            row_price = to_int(row.get('현재가'))
            row_volume = to_int(row.get('거래량') or row.get('현재거래량'))
            item[rank_key] = min(rank, int(item.get(rank_key, rank))) if item.get(rank_key) else rank
            item['price'] = row_price or item['price']
            if rank_key == 'amountRank':
                item['volume'] = row_volume
            else:
                item['volume'] = item['volume'] or row_volume
            trade_amount_million, amount_meta = normalize_trade_amount_million(
                row.get('거래대금'),
                price=item['price'],
                volume=item['volume'],
                source='ranking-tr-opt10030-opt10032',
            )
            if rank_key == 'amountRank':
                item['tradeAmountMillion'] = trade_amount_million
            else:
                item['tradeAmountMillion'] = item['tradeAmountMillion'] or trade_amount_million
            item['changeRate'] = item.get('changeRate') or to_number(row.get('등락률') or row.get('등락율'))
            item.update(amount_meta)
            item['rawTr'] = row
            item['rankingBasis'] = 'amount' if rank_key == 'amountRank' else item.get('rankingBasis') or 'volume'

    def _request_volume_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '정렬구분': '1',
            '관리종목포함': '0',
            '신용구분': '0',
            '거래량구분': '0',
            '가격구분': '0',
            '거래소구분': EXCHANGE_TYPE,
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '현재거래량', '거래대금']
        return self._request_tr('volume_rank', 'opt10030', inputs, fields)

    def _request_amount_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '관리종목포함': '0',
            '거래소구분': EXCHANGE_TYPE,
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '현재거래량', '거래대금']
        return self._request_tr('amount_rank', 'opt10032', inputs, fields)

    def daily_amount_rank(self, limit: int = 50, markets: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.login:
            return {'ok': False, 'error': 'Kiwoom login required'}

        ranking_rows: Dict[str, Dict[str, Any]] = {}
        market_list = [item for item in (markets or AMOUNT_RANK_MARKETS) if item]
        for market in market_list:
            self._merge_rank_rows(ranking_rows, self._request_amount_rank(market), 'amountRank', market)
            pause(TR_DELAY_MS)
        if not ranking_rows and market_list != MARKETS:
            market_list = MARKETS
            for market in market_list:
                self._merge_rank_rows(ranking_rows, self._request_amount_rank(market), 'amountRank', market)
                pause(TR_DELAY_MS)

        rows = sorted(
            ranking_rows.values(),
            key=lambda item: (int(item.get('tradeAmountMillion') or 0), int(item.get('volume') or 0)),
            reverse=True,
        )
        rows = rows[:max(1, min(int(limit or 50), 100))]
        self._hydrate_master([row['code'] for row in rows])

        items = []
        for index, row in enumerate(rows, start=1):
            master = self.master.get(row['code'], {})
            items.append({
                **row,
                'rank': index,
                'sector': master.get('sector') or '미분류',
                'sourceLabel': '키움거래대금순TR',
                'updatedAt': now_iso(),
            })

        return {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+ opt10032',
            'updatedAt': now_iso(),
            'exchangeType': EXCHANGE_TYPE,
            'exchangeTypeLabel': {'1': 'KRX', '2': 'NXT', '3': '통합'}.get(EXCHANGE_TYPE, EXCHANGE_TYPE),
            'criteria': {
                'rank': 'daily-trade-amount',
                'limit': limit,
                'markets': market_list,
            },
            'items': items,
            'stats': {
                'count': len(items),
                'totalTradeAmountMillion': sum(int(item.get('tradeAmountMillion') or 0) for item in items),
            },
        }

    def _request_daily_trade_detail(self, code: str, start_date: str) -> List[Dict[str, Any]]:
        normalized_code = clean_code(code)
        fields = [
            '일자', '종가', '현재가', '전일대비기호', '전일대비', '등락률', '등락율',
            '거래량', '거래대금', '장전거래량', '장전거래비중', '장중거래량',
            '장중거래비중', '장후거래량', '장후거래비중', '합계3', '기간중거래량',
            '체결강도', '외인보유', '외인비중', '외인순매수', '기관순매수',
            '개인순매수', '외국계', '신용잔고율', '프로그램', '장전거래대금',
            '장전거래대금비중', '장중거래대금', '장중거래대금비중', '장후거래대금',
            '장후거래대금비중',
        ]
        return self._request_tr(
            f'daily_trade_detail_{normalized_code}',
            'opt10015',
            {'종목코드': normalized_code, '시작일자': start_date},
            fields,
        )

    def daily_detail_amount_rank(self, limit: int = 50, max_codes: int = 120, start_date: str = '') -> Dict[str, Any]:
        if not self.login:
            return {'ok': False, 'error': 'Kiwoom login required'}

        date_text = re.sub(r'[^0-9]', '', str(start_date or ''))[:8] or datetime.now().strftime('%Y%m%d')
        desired_scan_limit = max(1, min(int(max_codes or 120), 220))
        codes = list(self.registered_codes or self.candidates.keys())
        if len(codes) < desired_scan_limit:
            self.refresh_candidates(max(desired_scan_limit, MAX_REALTIME_CODES))
            codes = list(self.registered_codes or self.candidates.keys())

        scan_limit = max(1, min(desired_scan_limit, len(codes), 220))
        self._hydrate_master(codes[:scan_limit])

        items: List[Dict[str, Any]] = []
        for code in codes[:scan_limit]:
            master = self.master.get(code, {})
            name = master.get('name') or self._code_name(code)
            if bool(master.get('excluded')) or is_excluded_name(name):
                continue

            rows = self._request_daily_trade_detail(code, date_text)
            if not rows:
                pause(TR_DELAY_MS)
                continue

            row = rows[0]
            price = to_int(row.get('종가') or row.get('현재가'))
            volume = to_int(row.get('거래량'))
            trade_amount_million, amount_meta = normalize_trade_amount_million(
                row.get('거래대금'),
                price=price,
                volume=volume,
                source='daily-detail-tr-opt10015',
            )
            regular_amount_million, _ = normalize_trade_amount_million(
                row.get('장중거래대금'),
                price=price,
                volume=to_int(row.get('장중거래량')) or volume,
                source='daily-detail-regular-tr-opt10015',
            )

            if trade_amount_million <= 0 and regular_amount_million > 0:
                trade_amount_million = regular_amount_million

            items.append({
                'code': code,
                'name': name,
                'sector': master.get('sector') or '미분류',
                'price': price,
                'changeRate': to_number(row.get('등락률') or row.get('등락율')),
                'volume': volume,
                'tradeAmountMillion': trade_amount_million,
                'regularTradeAmountMillion': regular_amount_million,
                'preMarketTradeAmountRaw': row.get('장전거래대금'),
                'postMarketTradeAmountRaw': row.get('장후거래대금'),
                'date': str(row.get('일자') or date_text).strip(),
                'source': 'kiwoom-daily-detail-tr-opt10015',
                'sourceLabel': '키움일별거래상세TR',
                'updatedAt': now_iso(),
                'rawDailyDetail': row,
                **amount_meta,
            })
            pause(TR_DELAY_MS)

        items.sort(key=lambda item: (int(item.get('tradeAmountMillion') or 0), int(item.get('volume') or 0)), reverse=True)
        limited = items[:max(1, min(int(limit or 50), 100))]
        for index, item in enumerate(limited, start=1):
            item['rank'] = index

        return {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+ opt10015',
            'updatedAt': now_iso(),
            'criteria': {
                'rank': 'daily-detail-trade-amount',
                'date': date_text,
                'limit': limit,
                'scannedCodes': scan_limit,
            },
            'items': limited,
            'stats': {
                'count': len(limited),
                'scannedCodes': scan_limit,
                'totalTradeAmountMillion': sum(int(item.get('tradeAmountMillion') or 0) for item in limited),
            },
        }

    def _request_current_quote(self, code: str) -> Optional[Dict[str, Any]]:
        normalized_code = clean_code(code)
        candidate = self.candidates.get(normalized_code, {})
        inputs = {'종목코드': normalized_code}
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '등락율', '거래량', '거래대금']
        rows = self._request_tr(f'current_quote_{normalized_code}', 'opt10001', inputs, fields)
        if not rows:
            return None

        row = rows[0]
        master = self.master.get(normalized_code, {})
        name = row.get('종목명') or master.get('name') or candidate.get('name') or self._code_name(normalized_code)
        price = to_int(row.get('현재가')) or int(candidate.get('price') or 0)
        volume = to_int(row.get('거래량')) or int(candidate.get('volume') or 0)
        tr_amount, amount_meta = normalize_trade_amount_million(
            row.get('거래대금'),
            price=price,
            volume=volume,
            source='current-price-tr-opt10001',
        )
        amount_from = 'opt10001-normalized'
        if tr_amount <= 0:
            tr_amount, amount_meta = normalize_trade_amount_million(
                candidate.get('tradeAmountRaw') or candidate.get('tradeAmountMillion'),
                price=price,
                volume=volume,
                source='ranking-candidate-fallback',
            )
            amount_from = 'ranking-candidate-normalized-fallback'

        change_rate = to_number(row.get('등락률') or row.get('등락율')) or float(candidate.get('changeRate') or 0)
        payload = {
            'code': normalized_code,
            'name': name,
            'price': price,
            'changeRate': change_rate,
            'volume': volume,
            'tradeAmountMillion': tr_amount,
            'tradeVolume': 0,
            'time': None,
            'strength': 0,
            'updatedAt': now_iso(),
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

    def stock_detail(self, code: str, candle_days: int = 80) -> Dict[str, Any]:
        normalized_code = clean_code(code)
        self._hydrate_master([normalized_code])
        cached_quote = self.quotes.get(normalized_code) or self.current_quotes.get(normalized_code) or self.candidates.get(normalized_code)
        quote = self._request_current_quote(normalized_code) or cached_quote or {}
        if quote:
            self.current_quotes[normalized_code] = quote
        stock = self._normalize_stock(normalized_code, quote) if quote else {
            'code': normalized_code,
            'name': self._code_name(normalized_code),
            'sector': self.master.get(normalized_code, {}).get('sector') or '미분류',
            'price': 0,
            'changeRate': 0,
            'volume': 0,
            'tradeAmountMillion': 0,
            'market': '-',
            'sourceLabel': '키움마스터',
            'updatedAt': now_iso(),
        }
        candles = self.daily_candles(normalized_code, candle_days)
        return {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+',
            'updatedAt': now_iso(),
            'stock': stock,
            'candles': candles,
            'company': self.company_stub(normalized_code, stock),
            'financials': {'quarter': [], 'year': []},
            'peers': self.peer_rows(stock),
            'news': [],
            'unavailable': ['뉴스', '상세 기업개요 일부', '재무제표'],
        }

    def ranking_debug(self, code: str) -> Dict[str, Any]:
        normalized_code = clean_code(code)
        amount_rows = []
        volume_rows = []
        for market in MARKETS:
            amount_rows.extend([{**row, '_market': market} for row in self._request_amount_rank(market)])
            pause(TR_DELAY_MS)
            volume_rows.extend([{**row, '_market': market} for row in self._request_volume_rank(market)])
            pause(TR_DELAY_MS)

        def match_row(row: Dict[str, Any]) -> bool:
            return clean_code(row.get('종목코드') or row.get('code')) == normalized_code

        amount_match = next((row for row in amount_rows if match_row(row)), None)
        volume_match = next((row for row in volume_rows if match_row(row)), None)
        candidate = self.candidates.get(normalized_code)
        quote = self.quotes.get(normalized_code)
        current = self.current_quotes.get(normalized_code)
        display_quote = quote or current or candidate or {}
        display_stock = self._normalize_stock(normalized_code, display_quote) if display_quote else None

        return {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+ debug',
            'updatedAt': now_iso(),
            'code': normalized_code,
            'markets': MARKETS,
            'displayRankingBaseline': DISPLAY_RANKING_BASELINE,
            'amountRankRaw': amount_match,
            'volumeRankRaw': volume_match,
            'candidate': candidate,
            'currentTrQuote': current,
            'realtimeQuote': quote,
            'displayStock': display_stock,
            'amountTop10': amount_rows[:10],
            'volumeTop10': volume_rows[:10],
            'note': 'Compare amountRankRaw.거래량 with Kiwoom [0186] 거래대금상위. If it differs here, bridge TR inputs/session differ from the visible Hero screen. If it matches here but displayStock differs, display merging is wrong.',
        }

    def daily_candles(self, code: str, days: int = 80) -> List[Dict[str, Any]]:
        normalized_code = clean_code(code)
        fields = ['일자', '시가', '고가', '저가', '현재가', '거래량', '거래대금']
        rows = self._request_tr(
            f'daily_candles_{normalized_code}',
            'opt10081',
            {'종목코드': normalized_code, '기준일자': datetime.now().strftime('%Y%m%d'), '수정주가구분': '1'},
            fields,
        )
        candles: List[Dict[str, Any]] = []
        for row in rows[:max(1, int(days or 80))]:
            date_text = str(row.get('일자') or '').strip()
            open_price = to_int(row.get('시가'))
            close_price = to_int(row.get('현재가'))
            volume = to_int(row.get('거래량'))
            amount_million, amount_meta = normalize_trade_amount_million(
                row.get('거래대금'),
                price=close_price,
                volume=volume,
                source='daily-candle-tr-opt10081',
            )
            candles.append({
                'date': f'{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}' if len(date_text) == 8 else date_text,
                'open': open_price,
                'high': to_int(row.get('고가')),
                'low': to_int(row.get('저가')),
                'close': close_price,
                'volume': volume,
                'amountMillion': amount_million,
                'amountEok': amount_million / 100,
                'rate': ((close_price - open_price) / open_price * 100) if open_price else 0,
                'raw': row,
                **amount_meta,
            })
        return sorted(candles, key=lambda item: item.get('date') or '')

    def screener(self, lookback_days: int, threshold_rate: float, threshold_amount_eok: float, max_codes: int, sector: str, sort: str) -> Dict[str, Any]:
        if not self.login:
            return {'ok': False, 'error': 'Kiwoom login required'}
        codes = list(self.registered_codes or self.candidates.keys())
        if not codes:
            self.refresh_candidates(max_codes)
            codes = list(self.registered_codes or self.candidates.keys())
        rows: List[Dict[str, Any]] = []
        for code in codes[:max(1, min(int(max_codes or 40), 120))]:
            self._hydrate_master([code])
            master = self.master.get(code, {})
            sector_name = master.get('sector') or '미분류'
            if sector and sector != 'all' and sector_name != sector:
                continue
            candles = self.daily_candles(code, lookback_days + 5)[-lookback_days:]
            events = [
                candle for candle in candles
                if float(candle.get('rate') or 0) >= float(threshold_rate or 15)
                and float(candle.get('amountEok') or 0) >= float(threshold_amount_eok or 500)
            ]
            if not events:
                pause(TR_DELAY_MS)
                continue
            top_event = sorted(events, key=lambda item: (item.get('date') or '', float(item.get('rate') or 0)), reverse=True)[0]
            quote = self.quotes.get(code) or self.current_quotes.get(code) or self.candidates.get(code) or {}
            rows.append({
                'code': code,
                'name': master.get('name') or quote.get('name') or self._code_name(code),
                'sector': sector_name,
                'market': quote.get('market') or '-',
                'price': quote.get('price') or top_event.get('close') or 0,
                'changeRate': quote.get('changeRate') or 0,
                'volume': quote.get('volume') or 0,
                'amountEok': (quote.get('tradeAmountMillion') or 0) / 100,
                'events': events,
                'topEvent': top_event,
                'candles': candles[-14:],
                'sourceLabel': '키움일봉TR',
            })
            pause(TR_DELAY_MS)
        rows = sort_screener(rows, sort)
        return {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+ opt10081',
            'updatedAt': now_iso(),
            'criteria': {
                'lookbackDays': lookback_days,
                'thresholdRate': threshold_rate,
                'thresholdAmountEok': threshold_amount_eok,
                'maxCodes': max_codes,
            },
            'sectors': sorted({row['sector'] for row in rows}),
            'items': rows,
            'stats': screener_stats(rows),
        }

    def company_stub(self, code: str, stock: Dict[str, Any]) -> Dict[str, Any]:
        master = self.master.get(code, {})
        return {
            'sector': stock.get('sector') or master.get('sector') or '미분류',
            'market': stock.get('market') or '-',
            'summary': '키움 OpenAPI+ 기본/시세 데이터로 구성되었습니다. 뉴스, 대표자, 재무 데이터 공급자를 연결하면 상세 정보가 확장됩니다.',
            'rawInfo': master.get('rawInfo'),
        }

    def peer_rows(self, stock: Dict[str, Any]) -> List[Dict[str, Any]]:
        sector = stock.get('sector')
        rows = []
        for code in self.registered_codes:
            quote = self.quotes.get(code) or self.current_quotes.get(code) or self.candidates.get(code)
            master = self.master.get(code, {})
            if not quote or master.get('sector') != sector:
                continue
            rows.append({
                'code': code,
                'name': master.get('name') or quote.get('name') or code,
                'amountEok': int(quote.get('tradeAmountMillion') or 0) / 100,
                'me': code == stock.get('code'),
            })
        return sorted(rows, key=lambda item: item['amountEok'], reverse=True)[:6]

    def _request_tr(self, rqname: str, trcode: str, inputs: Dict[str, str], fields: List[str]) -> List[Dict[str, Any]]:
        for key, value in inputs.items():
            self.ocx.dynamicCall('SetInputValue(QString, QString)', key, str(value))

        screen = str(SCREEN_BASE + 90)
        self._tr_rows = []
        self._tr_error = None
        self._tr_fields = fields
        self._tr_rqname = rqname
        self._tr_trcode = trcode

        result = self.ocx.dynamicCall('CommRqData(QString, QString, int, QString)', rqname, trcode, 0, screen)
        if int(result or 0) != 0:
            raise RuntimeError(f'CommRqData failed: {trcode} result={result}')

        self._tr_loop = QEventLoop()
        QTimer.singleShot(8000, self._tr_loop.quit)
        self._tr_loop.exec_()
        rows = list(self._tr_rows)
        error = self._tr_error
        self._tr_loop = None

        if error:
            raise RuntimeError(error)
        return rows

    def _hydrate_master(self, codes: List[str]) -> None:
        for code in codes:
            if code in self.master:
                continue
            name = self._code_name(code)
            raw_info = str(self.ocx.dynamicCall('GetMasterStockInfo(QString)', code) or '')
            self.master[code] = {
                'code': code,
                'name': name,
                'rawInfo': raw_info,
                'sector': pick_sector(raw_info, name),
                'excluded': is_excluded_name(name) or is_excluded_info(raw_info),
            }

    def _code_name(self, code: str) -> str:
        return str(self.ocx.dynamicCall('GetMasterCodeName(QString)', code) or '').strip()

    def _subscribe_realtime(self, codes: List[str]) -> None:
        for screen in self.screens:
            self.ocx.dynamicCall('DisconnectRealData(QString)', screen)
        self.screens = []
        self.registered_codes = []

        chunks = [codes[index:index + SCREEN_CAPACITY] for index in range(0, len(codes), SCREEN_CAPACITY)]
        for index, chunk in enumerate(chunks):
            if not chunk:
                continue

            screen = str(SCREEN_BASE + index)
            code_list = ';'.join(chunk)
            result = self.ocx.dynamicCall(
                'SetRealReg(QString, QString, QString, QString)',
                screen,
                code_list,
                REAL_FIDS,
                '0',
            )
            if int(result or 0) != 0:
                self.last_error = f'SetRealReg failed screen={screen} result={result}'
            self.screens.append(screen)
            self.registered_codes.extend(chunk)

    def _normalize_stock(self, code: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        master = self.master.get(code, {})
        candidate = self.candidates.get(code, {})
        name = quote.get('name') or master.get('name') or self._code_name(code)
        sector = master.get('sector') or quote.get('sector') or '미분류'
        excluded = bool(master.get('excluded')) or is_excluded_name(name)
        is_realtime = bool(quote.get('isRealtime'))
        is_current_tr = bool(quote.get('isCurrentTr'))

        stock = {
            'code': code,
            'name': name,
            'sector': sector,
            'price': int(quote.get('price') or 0),
            'changeRate': float(quote.get('changeRate') or 0),
            'volume': int(quote.get('volume') or 0),
            'tradeAmountMillion': int(quote.get('tradeAmountMillion') or 0),
            'tradeVolume': int(quote.get('tradeVolume') or 0),
            'strength': float(quote.get('strength') or 0),
            'updatedAt': quote.get('updatedAt'),
            'time': quote.get('time'),
            'source': quote.get('source') or 'kiwoom',
            'sourceLabel': quote.get('sourceLabel') or ('실시간 FID' if is_realtime else '키움현재가TR' if is_current_tr else 'TR후보'),
            'isRealtime': is_realtime,
            'isCurrentTr': is_current_tr,
            'tradeAmountSource': quote.get('tradeAmountSource'),
            'excluded': excluded,
        }

        if candidate:
            stock['rankingVolume'] = int(candidate.get('volume') or 0)
            stock['rankingTradeAmountMillion'] = int(candidate.get('tradeAmountMillion') or 0)
            stock['rankingUpdatedAt'] = candidate.get('updatedAt') or self.last_candidate_refresh_at
            stock['rankingBasis'] = candidate.get('rankingBasis')
            stock['displayVolumeSource'] = 'ranking-tr-opt10032' if DISPLAY_RANKING_BASELINE else stock['source']
            stock['displayTradeAmountSource'] = 'ranking-tr-opt10032' if DISPLAY_RANKING_BASELINE else stock.get('tradeAmountSource')
            if DISPLAY_RANKING_BASELINE:
                stock['realtimeVolume'] = stock['volume']
                stock['realtimeTradeAmountMillion'] = stock['tradeAmountMillion']
                stock['volume'] = stock['rankingVolume'] or stock['volume']
                stock['tradeAmountMillion'] = stock['rankingTradeAmountMillion'] or stock['tradeAmountMillion']
                stock['sourceLabel'] = '0186랭킹TR'
        return stock

    def _empty_message(self) -> str:
        if STRICT_REALTIME_ONLY and self.registered_codes and not self.quotes and not self.current_quotes:
            return '키움 실시간 FID/현재가 TR 수신 대기 중입니다. 정규장 중에는 체결 발생 종목부터 실시간 FID로, 장마감 후에는 현재가 TR 보정값으로 표시됩니다.'
        if not self.registered_codes:
            return '실시간 등록 종목이 아직 없습니다. 후보군 갱신을 기다리거나 /api/refresh를 호출하세요.'
        return '표시 가능한 종목이 없습니다.'

    def _on_event_connect(self, err_code: int) -> None:
        self.login_error = int(err_code)
        self.login = int(err_code) == 0
        if self.login:
            QTimer.singleShot(1000, lambda: self.refresh_candidates(MAX_REALTIME_CODES))
        else:
            self.last_error = f'login failed err_code={err_code}'

    def _on_receive_tr_data(self, screen_no, rqname, trcode, record_name, prev_next, data_len, err_code, msg1, msg2) -> None:
        try:
            repeat_count = int(self.ocx.dynamicCall('GetRepeatCnt(QString, QString)', trcode, rqname))
            rows: List[Dict[str, Any]] = []
            fields = getattr(self, '_tr_fields', [])
            row_count = repeat_count if repeat_count > 0 else 1
            for row_index in range(row_count):
                row = {}
                for field in fields:
                    value = self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, row_index, field)
                    row[field] = str(value or '').strip()
                if repeat_count > 0 or any(str(value or '').strip() for value in row.values()):
                    rows.append(row)
            self._tr_rows = rows
        except Exception as exc:
            self._tr_error = str(exc)
            self.last_error = str(exc)
        finally:
            if self._tr_loop is not None:
                self._tr_loop.quit()

    def _on_receive_real_data(self, code, real_type, real_data) -> None:
        if str(real_type) != '주식체결':
            return

        code = clean_code(code)
        master = self.master.get(code, {})
        candidate = self.candidates.get(code, {})
        name = master.get('name') or candidate.get('name') or self._code_name(code)

        raw_price = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_PRICE)
        raw_change_rate = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_CHANGE_RATE)
        raw_volume = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_VOLUME)
        raw_trade_amount = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_TRADE_AMOUNT)
        raw_trade_volume = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_TRADE_VOLUME)
        raw_time = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_TIME)
        raw_strength = self.ocx.dynamicCall('GetCommRealData(QString, int)', code, FID_STRENGTH)

        price = to_int(raw_price)
        volume = to_int(raw_volume)
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
            'changeRate': to_number(raw_change_rate),
            'volume': volume,
            'tradeAmountMillion': trade_amount_million,
            'tradeVolume': to_int(raw_trade_volume),
            'time': str(raw_time or '').strip(),
            'strength': to_number(raw_strength),
            'updatedAt': now_iso(),
            'source': 'kiwoom-realtime-fid-stock-trade',
            'sourceLabel': '실시간 FID',
            'isRealtime': True,
            'isCurrentTr': False,
            'tradeAmountSource': 'realtime-fid-14',
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


def pause(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


def is_excluded_name(name: str) -> bool:
    return bool(EXCLUDE_NAME_RE.search(str(name or '')))


def is_excluded_info(info: str) -> bool:
    text = str(info or '')
    return any(keyword in text.upper() for keyword in ['ETF', 'ETN', 'ELW'])


def rank_candidates(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def score(item: Dict[str, Any]) -> tuple:
        amount_rank = int(item.get('amountRank') or 9999)
        volume_rank = int(item.get('volumeRank') or 9999)
        amount = int(item.get('tradeAmountMillion') or 0)
        volume = int(item.get('volume') or 0)

        # 거래대금/거래량 랭킹 후보를 동시에 잡되, 거래대금 랭킹을 우선한다.
        return (amount_rank, volume_rank, -amount, -volume)

    return sorted(rows, key=score)


def sort_stocks(stocks: List[Dict[str, Any]], sort_key: str) -> List[Dict[str, Any]]:
    if sort_key == 'volume':
        return sorted(stocks, key=lambda item: (item['volume'], item['tradeAmountMillion']), reverse=True)
    return sorted(stocks, key=lambda item: (item['tradeAmountMillion'], item['volume']), reverse=True)


def sort_screener(rows: List[Dict[str, Any]], sort_key: str) -> List[Dict[str, Any]]:
    if sort_key == 'rate':
        return sorted(rows, key=lambda item: float((item.get('topEvent') or {}).get('rate') or 0), reverse=True)
    if sort_key == 'amount':
        return sorted(rows, key=lambda item: float((item.get('topEvent') or {}).get('amountEok') or 0), reverse=True)
    if sort_key == 'count':
        return sorted(rows, key=lambda item: len(item.get('events') or []), reverse=True)
    return sorted(rows, key=lambda item: str((item.get('topEvent') or {}).get('date') or ''), reverse=True)


def screener_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    events = [event for row in rows for event in row.get('events', [])]
    rates = [float(event.get('rate') or 0) for event in events]
    recent_count = 0
    today = datetime.now().date()
    for row in rows:
        for event in row.get('events', []):
            try:
                event_date = datetime.strptime(str(event.get('date')), '%Y-%m-%d').date()
                if (today - event_date).days <= 7:
                    recent_count += 1
                    break
            except ValueError:
                continue
    return {
        'stockCount': len(rows),
        'eventCount': len(events),
        'avgRate': sum(rates) / len(rates) if rates else 0,
        'recentCount': recent_count,
    }


def run_controller_call(fn: Callable[[], Dict[str, Any]], timeout_sec: int = 90) -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    event = threading.Event()
    payload: Dict[str, Any] = {'fn': fn, 'event': event}
    controller.bridge_call_signal.emit(payload)
    if not event.wait(timeout_sec):
        return {'ok': False, 'error': f'Kiwoom request timed out after {timeout_sec}s'}
    if payload.get('error'):
        return {'ok': False, 'error': payload['error']}
    result = payload.get('result')
    return result if isinstance(result, dict) else {'ok': True, 'result': result}


controller: Optional[KiwoomController] = None
api = FastAPI(title='Millionaire Kiwoom Bridge')
api.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@api.get('/health')
def health() -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    return controller.health()


@api.get('/snapshot')
def snapshot(
    sectorLimit: int = 10,
    stocksPerSector: int = 8,
    sort: str = 'tradeAmount',
    maxRealtimeCodes: int = MAX_REALTIME_CODES,
    candidateRefreshMs: int = CANDIDATE_REFRESH_MS,
) -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    if candidateRefreshMs and candidateRefreshMs != CANDIDATE_REFRESH_MS:
        controller.timer.setInterval(max(15000, int(candidateRefreshMs)))
    if controller.login and not controller.registered_codes:
        controller.refresh_signal.emit(maxRealtimeCodes)
    sort_key = 'volume' if sort == 'volume' else 'tradeAmount'
    return controller.snapshot(max(1, sectorLimit), max(1, stocksPerSector), sort_key)


@api.get('/debug/{code}')
def debug_code(code: str) -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    normalized_code = clean_code(code)
    return {
        'ok': True,
        'code': normalized_code,
        'master': controller.master.get(normalized_code),
        'candidate': controller.candidates.get(normalized_code),
        'quote': controller.quotes.get(normalized_code),
        'currentTrQuote': controller.current_quotes.get(normalized_code),
    }


@api.get('/ranking-debug/{code}')
def ranking_debug(code: str) -> Dict[str, Any]:
    normalized_code = clean_code(code)
    return run_controller_call(lambda: controller.ranking_debug(normalized_code), 90)


@api.get('/daily-amount-rank')
def daily_amount_rank(limit: int = 50, markets: str = '') -> Dict[str, Any]:
    market_list = [item.strip() for item in str(markets or '').split(',') if item.strip()] or None
    return run_controller_call(lambda: controller.daily_amount_rank(max(1, min(int(limit or 50), 100)), market_list), 90)


@api.get('/daily-detail-rank')
def daily_detail_rank(limit: int = 50, maxCodes: int = 120, date: str = '') -> Dict[str, Any]:
    scan_limit = max(1, min(int(maxCodes or 120), 220))
    return run_controller_call(
        lambda: controller.daily_detail_amount_rank(max(1, min(int(limit or 50), 100)), scan_limit, str(date or '')),
        max(120, scan_limit * 3),
    )


@api.get('/stock/{code}')
def stock_detail(code: str, candleDays: int = 80) -> Dict[str, Any]:
    normalized_code = clean_code(code)
    return run_controller_call(lambda: controller.stock_detail(normalized_code, max(20, min(int(candleDays or 80), 160))), 90)


@api.get('/candles/{code}')
def daily_candles(code: str, days: int = 80) -> Dict[str, Any]:
    normalized_code = clean_code(code)
    return run_controller_call(
        lambda: {
            'ok': True,
            'provider': 'Kiwoom OpenAPI+ opt10081',
            'updatedAt': now_iso(),
            'code': normalized_code,
            'candles': controller.daily_candles(normalized_code, max(20, min(int(days or 80), 240))),
        },
        90,
    )


@api.get('/screener')
def screener(
    lookbackDays: int = 63,
    thresholdRate: float = 15,
    thresholdAmountEok: float = 500,
    maxCodes: int = 40,
    sector: str = 'all',
    sort: str = 'recent',
) -> Dict[str, Any]:
    return run_controller_call(
        lambda: controller.screener(
            max(5, min(int(lookbackDays or 63), 180)),
            float(thresholdRate or 15),
            float(thresholdAmountEok or 500),
            max(1, min(int(maxCodes or 40), 120)),
            str(sector or 'all'),
            str(sort or 'recent'),
        ),
        max(90, int(maxCodes or 40) * 3),
    )


@api.post('/refresh')
def refresh(body: RefreshRequest) -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    max_codes = int(body.maxRealtimeCodes or MAX_REALTIME_CODES)
    controller.refresh_signal.emit(max_codes)
    return {'ok': True, 'message': 'refresh requested', 'maxRealtimeCodes': max_codes}


@api.post('/refresh-current')
def refresh_current() -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    controller.current_quote_signal.emit(CURRENT_QUOTE_BATCH_LIMIT)
    return {'ok': True, 'message': 'current quote refresh requested', 'maxCodes': CURRENT_QUOTE_BATCH_LIMIT}


def run_api() -> None:
    uvicorn.run(api, host=BRIDGE_HOST, port=BRIDGE_PORT, log_level='warning')


def main() -> None:
    global controller
    app = QApplication(sys.argv)
    controller = KiwoomController()
    thread = threading.Thread(target=run_api, daemon=True)
    thread.start()
    controller.connect_login()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
