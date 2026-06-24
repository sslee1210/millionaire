import os
import re
import sys
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from PyQt5.QtCore import QObject, QEventLoop, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication
from PyQt5.QAxContainer import QAxWidget
import uvicorn

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
TR_DELAY_MS = int(os.getenv('TR_DELAY_MS', '750'))
SCREEN_BASE = int(os.getenv('KIWOOM_SCREEN_BASE', '9100'))
SCREEN_CAPACITY = int(os.getenv('KIWOOM_SCREEN_CAPACITY', '80'))
MARKETS = [item.strip() for item in os.getenv('KIWOOM_RANKING_MARKETS', '001,101').split(',') if item.strip()]
STRICT_REALTIME_ONLY = os.getenv('KIWOOM_STRICT_REALTIME', '1').strip().lower() not in {'0', 'false', 'no', 'off'}

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
        self.last_real_event_at: Optional[str] = None
        self.registered_codes: List[str] = []
        self.screens: List[str] = []
        self.quotes: Dict[str, Dict[str, Any]] = {}
        self.master: Dict[str, Dict[str, Any]] = {}
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self._tr_loop: Optional[QEventLoop] = None
        self._tr_rows: List[Dict[str, Any]] = []
        self._tr_error: Optional[str] = None
        self._tr_fields: List[str] = []
        self._tr_rqname: str = ''
        self._tr_trcode: str = ''
        self._refreshing = False

        self.refresh_signal.connect(self.refresh_candidates)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh_candidates(MAX_REALTIME_CODES))
        self.timer.start(CANDIDATE_REFRESH_MS)

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
            'quoteCount': len(self.quotes),
            'lastCandidateRefreshAt': self.last_candidate_refresh_at,
            'lastRealEventAt': self.last_real_event_at,
            'strictRealtimeOnly': STRICT_REALTIME_ONLY,
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
        provisional_count = 0

        for code in self.registered_codes:
            real_quote = self.quotes.get(code)
            candidate_quote = self.candidates.get(code)
            quote = real_quote or candidate_quote
            if not quote:
                continue

            stock = self._normalize_stock(code, quote)
            if stock['excluded']:
                continue

            if stock['isRealtime']:
                realtime_ready += 1
            else:
                provisional_count += 1

            # 영웅문 일일거래량/거래대금과 맞추는 기본 모드:
            # 화면 표시 숫자는 주식체결 실시간 FID 13/14가 수신된 종목만 사용한다.
            # TR 값은 후보 선정용이며, STRICT_REALTIME_ONLY=0일 때만 임시 표시한다.
            if STRICT_REALTIME_ONLY and not stock['isRealtime']:
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
                'quoteCount': len(self.quotes),
                'realtimeReadyCount': realtime_ready,
                'provisionalCount': provisional_count,
                'visibleStockCount': sum(len(sector['stocks']) for sector in limited),
                'sectorCount': len(sectors),
                'maxRealtimeCodes': MAX_REALTIME_CODES,
                'candidateRefreshMs': CANDIDATE_REFRESH_MS,
                'strictRealtimeOnly': STRICT_REALTIME_ONLY,
                'lastCandidateRefreshAt': self.last_candidate_refresh_at,
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

            # 후보군은 TR 랭킹으로 만들지만, 화면 기본값은 실시간 FID 수신 종목만 표시한다.
            self.candidates = {item['code']: item for item in selected}
            self._hydrate_master(list(self.candidates.keys()))
            self._subscribe_realtime(list(self.candidates.keys()))

            # 후보군이 바뀌었을 때 기존 실시간 quote 중 등록 대상에서 빠진 종목은 제거한다.
            selected_codes = set(self.candidates.keys())
            self.quotes = {code: quote for code, quote in self.quotes.items() if code in selected_codes}

            self.last_candidate_refresh_at = now_iso()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._refreshing = False

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
                'source': 'kiwoom-tr-ranking-candidate',
                'sourceLabel': 'TR후보',
                'updatedAt': self.last_candidate_refresh_at,
            })

            item[rank_key] = min(rank, int(item.get(rank_key, rank))) if item.get(rank_key) else rank
            item['price'] = item['price'] or to_int(row.get('현재가'))
            item['volume'] = max(item.get('volume', 0), to_int(row.get('거래량') or row.get('현재거래량')))
            item['tradeAmountMillion'] = max(item.get('tradeAmountMillion', 0), to_int(row.get('거래대금')))
            item['changeRate'] = item.get('changeRate') or to_number(row.get('등락률'))
            item['rawTr'] = row

    def _request_volume_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '정렬구분': '1',
            '관리종목포함': '0',
            '신용구분': '0',
            '거래량구분': '0',
            '가격구분': '0',
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '거래량', '거래대금']
        return self._request_tr('volume_rank', 'opt10030', inputs, fields)

    def _request_amount_rank(self, market: str) -> List[Dict[str, Any]]:
        inputs = {
            '시장구분': market,
            '관리종목포함': '0',
        }
        fields = ['종목코드', '종목명', '현재가', '전일대비', '등락률', '거래량', '현재거래량', '거래대금']
        return self._request_tr('amount_rank', 'opt10032', inputs, fields)

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
        name = quote.get('name') or master.get('name') or self._code_name(code)
        sector = master.get('sector') or quote.get('sector') or '미분류'
        excluded = bool(master.get('excluded')) or is_excluded_name(name)
        is_realtime = bool(quote.get('isRealtime'))

        return {
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
            'sourceLabel': quote.get('sourceLabel') or ('실시간 FID' if is_realtime else 'TR후보'),
            'isRealtime': is_realtime,
            'excluded': excluded,
        }

    def _empty_message(self) -> str:
        if STRICT_REALTIME_ONLY and self.registered_codes and not self.quotes:
            return '실시간 FID 수신 대기 중입니다. 체결이 발생한 종목부터 영웅문 기준 일일 누적거래량/거래대금으로 표시됩니다.'
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
            for row_index in range(repeat_count):
                row = {}
                for field in fields:
                    value = self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, row_index, field)
                    row[field] = str(value or '').strip()
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

        quote = {
            'code': code,
            'name': name,
            'price': to_int(raw_price),
            'changeRate': to_number(raw_change_rate),
            'volume': to_int(raw_volume),
            'tradeAmountMillion': to_int(raw_trade_amount),
            'tradeVolume': to_int(raw_trade_volume),
            'time': str(raw_time or '').strip(),
            'strength': to_number(raw_strength),
            'updatedAt': now_iso(),
            'source': 'kiwoom-realtime-fid-stock-trade',
            'sourceLabel': '실시간 FID',
            'isRealtime': True,
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
    }


@api.post('/refresh')
def refresh(body: RefreshRequest) -> Dict[str, Any]:
    if controller is None:
        return {'ok': False, 'error': 'controller not ready'}
    max_codes = int(body.maxRealtimeCodes or MAX_REALTIME_CODES)
    controller.refresh_signal.emit(max_codes)
    return {'ok': True, 'message': 'refresh requested', 'maxRealtimeCodes': max_codes}


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
