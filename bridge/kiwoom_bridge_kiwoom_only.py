from collections import defaultdict
from typing import Any, Dict, List

import kiwoom_bridge as base
from kiwoom_sector_map import parse_code_list, parse_theme_groups, pick_sector


class KiwoomOnlyController(base.KiwoomController):
    def __init__(self) -> None:
        super().__init__()
        self.theme_by_code: Dict[str, List[str]] = {}
        self.theme_group_count = 0
        self.theme_loaded = False
        self.last_theme_refresh_at = None

    def health(self) -> Dict[str, Any]:
        payload = super().health()
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

    def _normalize_stock(self, code: str, quote: Dict[str, Any]) -> Dict[str, Any]:
        stock = super()._normalize_stock(code, quote)
        master = self.master.get(code, {})
        stock['sectorSource'] = master.get('sectorSource') or 'unknown'
        stock['themes'] = master.get('themes') or []
        return stock


base.KiwoomController = KiwoomOnlyController
base.main()
