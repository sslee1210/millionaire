import json
import os
import re
import time
import urllib.request
from typing import Any, Dict, Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'sector_web_cache.json')
ENABLED = os.getenv('SECTOR_WEB_FALLBACK', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
TTL = int(os.getenv('SECTOR_WEB_CACHE_TTL_SEC', str(60 * 60 * 24 * 7)))
TIMEOUT = float(os.getenv('SECTOR_WEB_TIMEOUT_SEC', '2.0'))
HOST = ''.join(['api', '.', 'stock', '.', 'na', 'ver', '.', 'com'])
MHOST = ''.join(['m', '.', 'stock', '.', 'na', 'ver', '.', 'com'])

RULES = [
    ('반도체', ['반도체', 'hbm', 'dram', 'nand', 'pcb']),
    ('2차전지', ['2차전지', '배터리', '리튬', '양극재', '음극재']),
    ('바이오·제약', ['바이오', '제약', '의약', '헬스케어']),
    ('자동차', ['자동차', '전기차', '모빌리티']),
    ('전기전자', ['전자', '디스플레이', 'oled', '가전']),
    ('인터넷·게임', ['인터넷', '게임', '플랫폼', '콘텐츠']),
    ('금융', ['은행', '증권', '보험', '금융']),
    ('조선·해운', ['조선', '해운', '선박']),
    ('방산·항공우주', ['방산', '항공', '우주']),
    ('화학·소재', ['화학', '소재', '정유']),
    ('철강·금속', ['철강', '금속', '비철']),
    ('에너지·전력', ['에너지', '전력', '전선', '원전']),
    ('건설·기계', ['건설', '기계', '시멘트']),
    ('음식료·소비재', ['음식료', '식품', '화장품', '유통']),
    ('통신·보안', ['통신', '보안', '네트워크']),
    ('여행·레저', ['여행', '레저', '호텔']),
]

_cache: Optional[Dict[str, Any]] = None


def lookup_sector(code: str, name: str = '') -> Optional[Dict[str, str]]:
    if not ENABLED:
        return None
    code = re.sub(r'[^0-9]', '', str(code or '')).zfill(6)[-6:]
    if not code or code == '000000':
        return None
    cache = _load()
    now = int(time.time())
    hit = cache.get(code)
    if hit and now - int(hit.get('ts', 0)) < TTL and hit.get('sector'):
        return {'sector': hit['sector'], 'sectorSource': 'web-sector-cache'}
    blob = name + ' ' + _safe_fetch('https://' + HOST + '/stock/' + code + '/basic') + ' ' + _safe_fetch('https://' + MHOST + '/api/stock/' + code + '/integration')
    sector = _classify(blob)
    if sector:
        cache[code] = {'sector': sector, 'ts': now}
        _save(cache)
        return {'sector': sector, 'sectorSource': 'web-sector-fallback'}
    return None


def _classify(text: str) -> Optional[str]:
    blob = str(text or '').lower()
    for sector, keys in RULES:
        for key in keys:
            if key.lower() in blob:
                return sector
    return None


def _safe_fetch(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,*/*'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.read().decode('utf-8', errors='replace')[:5000]
    except Exception:
        return ''


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as handle:
            _cache = json.load(handle)
    except Exception:
        _cache = {}
    return _cache


def _save(cache: Dict[str, Any]) -> None:
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as handle:
            json.dump(cache, handle, ensure_ascii=False)
    except Exception:
        pass
