import re
from typing import Any, Dict, List, Optional, Tuple

SECTOR_KEYWORD_RULES: List[Tuple[str, List[str]]] = [
    ('반도체', ['반도체', 'HBM', 'D램', 'DRAM', '낸드', 'NAND', '파운드리', '웨이퍼', '식각', '증착', '노광', 'OSAT', '패키징', '후공정', '전공정', '소부장', '메모리', '비메모리', '시스템반도체', '온디바이스AI']),
    ('AI·로봇', ['AI', '인공지능', '로봇', '로보틱스', '휴머노이드', '스마트팩토리', '자동화', '머신비전']),
    ('2차전지', ['2차전지', '이차전지', '배터리', '전고체', '양극재', '음극재', '전해액', '분리막', '리튬', '니켈', '폐배터리']),
    ('바이오·제약', ['바이오', '제약', '신약', '항암', '세포치료', '유전자', 'ADC', 'CDMO', 'CMO', '의료기기', '진단', '헬스케어']),
    ('자동차', ['자동차', '전기차', '수소차', '자율주행', '차량용', '자동차부품', '타이어', '모빌리티']),
    ('전기전자', ['전기전자', '전자', '디스플레이', 'OLED', 'LCD', 'MLCC', 'PCB', '카메라모듈', '스마트폰', '가전', '전장']),
    ('인터넷·게임', ['인터넷', '게임', '모바일게임', '웹툰', '플랫폼', '콘텐츠', '엔터테인먼트', '음원', '미디어']),
    ('금융', ['은행', '증권', '보험', '금융', '카드', '지주', '핀테크']),
    ('조선·해운', ['조선', '선박', 'LNG선', '해운', '운송', '항만']),
    ('항공·우주', ['우주', '항공', '드론', '위성']),
    ('화학·소재', ['화학', '소재', '정유', '석유화학', '탄소섬유', '첨단소재', '유리기판', '페인트']),
    ('철강·금속', ['철강', '금속', '비철금속', '구리', '알루미늄', '희토류']),
    ('에너지', ['에너지', '원전', '원자력', '태양광', '풍력', '수소', '전력', '전선', 'ESS', 'LNG', '가스']),
    ('건설·건자재', ['건설', '건자재', '시멘트', '리모델링', '부동산', '인프라']),
    ('음식료·소비재', ['음식료', '식품', '화장품', '의류', '소비재', '유통', '편의점', '면세점', '패션']),
    ('통신', ['통신', '5G', '6G', '네트워크', '통신장비']),
]

NAME_HINTS: List[Tuple[str, str]] = [
    ('삼성전자', '반도체'), ('SK하이닉스', '반도체'), ('한미반도체', '반도체'), ('DB하이텍', '반도체'),
    ('리노공업', '반도체'), ('HPSP', '반도체'), ('이오테크닉스', '반도체'), ('주성엔지니어링', '반도체'),
    ('현대차', '자동차'), ('기아', '자동차'), ('현대모비스', '자동차'), ('HL만도', '자동차'),
    ('LG에너지솔루션', '2차전지'), ('삼성SDI', '2차전지'), ('에코프로', '2차전지'), ('엘앤에프', '2차전지'),
    ('카카오', '인터넷·게임'), ('엔씨소프트', '인터넷·게임'), ('크래프톤', '인터넷·게임'),
    ('셀트리온', '바이오·제약'), ('삼성바이오로직스', '바이오·제약'), ('유한양행', '바이오·제약'), ('한미약품', '바이오·제약'),
    ('KB금융', '금융'), ('신한지주', '금융'), ('하나금융지주', '금융'), ('우리금융지주', '금융'), ('삼성생명', '금융'),
    ('HD현대중공업', '조선·해운'), ('한화오션', '조선·해운'), ('삼성중공업', '조선·해운'), ('HMM', '조선·해운'),
    ('한화에어로스페이스', '항공·우주'), ('한국항공우주', '항공·우주'),
    ('POSCO', '철강·금속'), ('포스코', '철강·금속'), ('현대제철', '철강·금속'),
    ('LG화학', '화학·소재'), ('롯데케미칼', '화학·소재'), ('금호석유', '화학·소재'),
    ('한국전력', '에너지'), ('두산에너빌리티', '에너지'), ('LS ELECTRIC', '에너지'),
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
        theme_id = theme_id.strip()
        theme_name = theme_name.strip()
        if theme_id and theme_name:
            groups.append((theme_id, theme_name))
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
        for keyword in keywords:
            if keyword.upper() in upper_text:
                return sector
    return None


def pick_sector(raw_info: str, name: str, themes: Optional[List[str]] = None) -> Dict[str, Any]:
    themes = themes or []
    sector = sector_from_keywords(compact_text(*themes))
    if sector:
        return {'sector': sector, 'sectorSource': 'kiwoom-theme', 'themes': themes}

    info = parse_master_info(raw_info)
    sector = sector_from_keywords(compact_text(*info.values(), raw_info))
    if sector:
        return {'sector': sector, 'sectorSource': 'kiwoom-master-info', 'themes': themes}

    upper_name = str(name or '').upper()
    for hint, mapped_sector in NAME_HINTS:
        if hint.upper() in upper_name:
            return {'sector': mapped_sector, 'sectorSource': 'kiwoom-name-hint', 'themes': themes}

    sector = sector_from_keywords(name)
    if sector:
        return {'sector': sector, 'sectorSource': 'kiwoom-name-keyword', 'themes': themes}

    return {'sector': '미분류', 'sectorSource': 'unclassified', 'themes': themes}
