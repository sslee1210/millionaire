import re
from typing import Any, Dict, List, Optional, Tuple

SECTOR_KEYWORD_RULES: List[Tuple[str, List[str]]] = [
    ('반도체', ['반도체', 'HBM', 'DRAM', 'NAND', '낸드', '메모리', '비메모리', '파운드리', '웨이퍼']),
    ('AI·로봇', ['AI', '인공지능', '로봇', '자동화', '머신비전']),
    ('2차전지', ['2차전지', '이차전지', '배터리', '전고체', '양극재', '음극재', '전해액', '리튬']),
    ('바이오·제약', ['바이오', '제약', '신약', '항암', '의료기기', '헬스케어', '백신']),
    ('자동차', ['자동차', '전기차', '자율주행', '자동차부품', '전장']),
    ('전기전자', ['전기전자', '전자', '디스플레이', 'OLED', '스마트폰', '가전', 'IT부품']),
    ('인터넷·게임', ['인터넷', '게임', '플랫폼', '콘텐츠', '엔터테인먼트', '미디어']),
    ('금융', ['은행', '증권', '보험', '금융', '카드', '지주']),
    ('조선·해운', ['조선', '선박', '해운', '운송']),
    ('방산·항공우주', ['방산', '방위산업', '우주', '항공', '드론', '위성']),
    ('화학·소재', ['화학', '소재', '정유', '석유화학', '첨단소재']),
    ('철강·금속', ['철강', '금속', '비철금속', '구리', '알루미늄']),
    ('에너지·전력', ['에너지', '원전', '태양광', '풍력', '수소', '전력', '전선']),
    ('건설·기계', ['건설', '건자재', '시멘트', '기계', '플랜트']),
    ('음식료·소비재', ['음식료', '식품', '화장품', '의류', '소비재', '유통']),
    ('통신·보안', ['통신', '5G', '네트워크', '보안', '클라우드']),
]

NAME_HINTS: List[Tuple[str, str]] = [
    ('삼성전자', '반도체'), ('SK하이닉스', '반도체'), ('한미반도체', '반도체'), ('이오테크닉스', '반도체'),
    ('현대차', '자동차'), ('기아', '자동차'), ('LG에너지솔루션', '2차전지'), ('삼성SDI', '2차전지'),
    ('카카오', '인터넷·게임'), ('셀트리온', '바이오·제약'), ('KB금융', '금융'), ('신한지주', '금융'),
    ('HD현대중공업', '조선·해운'), ('한화오션', '조선·해운'), ('한화에어로스페이스', '방산·항공우주'),
    ('POSCO', '철강·금속'), ('포스코', '철강·금속'), ('LG화학', '화학·소재'), ('한국전력', '에너지·전력'),
    ('두산에너빌리티', '에너지·전력'), ('삼성물산', '건설·기계'), ('농심', '음식료·소비재'), ('SK텔레콤', '통신·보안'),
]


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


def parse_theme_groups(raw_groups: str) -> List[Tuple[str, str]]:
    groups: List[Tuple[str, str]] = []
    for token in re.split(r'[;\n\r]+', str(raw_groups or '')):
        item = token.strip()
        if not item:
            continue
        if '|' in item:
            theme_id, theme_name = item.split('|', 1)
        elif '\t' in item:
            theme_id, theme_name = item.split('\t', 1)
        else:
            continue
        if theme_id.strip() and theme_name.strip():
            groups.append((theme_id.strip(), theme_name.strip()))
    return groups


def parse_code_list(raw_codes: str, clean_code) -> List[str]:
    codes: List[str] = []
    for token in re.split(r'[;|,\s]+', str(raw_codes or '')):
        code = clean_code(token)
        if code and code != '000000' and code not in codes:
            codes.append(code)
    return codes


def compact_text(*values: Any) -> str:
    return ' '.join(str(value or '') for value in values if str(value or '').strip())


def sector_from_keywords(text: str) -> Optional[str]:
    upper_text = str(text or '').upper()
    for sector, keywords in SECTOR_KEYWORD_RULES:
        if any(keyword.upper() in upper_text for keyword in keywords):
            return sector
    return None


def sector_from_name_hint(name: str) -> Optional[str]:
    upper_name = str(name or '').upper()
    for hint, mapped_sector in NAME_HINTS:
        if hint.upper() in upper_name:
            return mapped_sector
    return None


def pick_sector(raw_info: str, name: str, themes: Optional[List[str]] = None, code: Optional[str] = None) -> Dict[str, Any]:
    themes = themes or []
    for sector, source in [
        (sector_from_name_hint(name), 'kiwoom-name-hint'),
        (sector_from_keywords(compact_text(*themes)), 'kiwoom-theme'),
        (sector_from_keywords(compact_text(*parse_master_info(raw_info).values(), raw_info)), 'kiwoom-master-info'),
        (sector_from_keywords(name), 'kiwoom-name-keyword'),
    ]:
        if sector:
            return {'sector': sector, 'sectorSource': source, 'themes': themes}
    return {'sector': '테마·스몰캡', 'sectorSource': 'broad-fallback-no-etc', 'themes': themes}
