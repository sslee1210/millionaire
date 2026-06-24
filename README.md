# Millionaire

Kiwoom OpenAPI+ only sector board.

이 프로젝트는 키움 OpenAPI+ 로컬 브릿지만 사용해서 국내 주식의 일일 누적 거래량과 일일 누적 거래대금을 섹터별로 표시하는 대시보드입니다.

## 핵심 원칙

- 외부 시세 공급자 사용 금지
- 종목명, 현재가, 거래량, 거래대금은 키움 OpenAPI+ 기준
- 일일 거래량 기준: 키움 실시간 FID 13 누적거래량
- 일일 거래대금 기준: 키움 실시간 FID 14 누적거래대금
- 실시간 표시: 후보 종목만 SetRealReg로 등록
- 조회 제한 회피: TR 조회는 저빈도 후보군 갱신용으로만 사용
- 기본 정렬: 거래대금 내림차순
- 보조 정렬: 거래량 내림차순

## 구조

```text
bridge/kiwoom_bridge.py  # Windows 키움 OpenAPI+ 브릿지
server.js                # React 정적 파일 제공 및 브릿지 프록시
src/App.jsx              # 화면
src/styles.css           # UI
```

## 실행 전 조건

키움 OpenAPI+는 Windows 환경에서 동작합니다. 키움 OpenAPI+가 설치된 Windows PC에서 실행해야 하며, 브릿지는 키움 로그인 세션을 필요로 합니다.

## 설치

```bash
npm install
```

브릿지 Python 패키지 설치:

```bash
cd bridge
pip install -r requirements.txt
```

## 실행

1. 키움 브릿지 실행

```bash
start-bridge.bat
```

또는:

```bash
cd bridge
python kiwoom_bridge.py
```

2. 프론트/서버 실행

```bash
npm run server
```

3. 접속

```text
http://localhost:4173
```

## 환경변수

`.env.example`을 참고해서 필요 시 수정합니다.

```text
KIWOOM_BRIDGE_URL=http://127.0.0.1:8765
POLL_MS=1000
MAX_REALTIME_CODES=80
CANDIDATE_REFRESH_MS=60000
SECTOR_LIMIT=10
STOCKS_PER_SECTOR=8
```

## 호출량 제한 회피 방식

전 종목을 계속 조회하지 않습니다.

1. 거래량 상위 TR과 거래대금 상위 TR을 일정 주기로 조회합니다.
2. 두 랭킹을 합쳐 실시간 감시 후보군을 만듭니다.
3. `MAX_REALTIME_CODES` 이내의 후보만 실시간 등록합니다.
4. 화면은 실시간 수신값만 기준으로 섹터별 재정렬합니다.

이 구조는 반복 조회를 최소화하고, 숫자 갱신은 키움 실시간 이벤트에 맡깁니다.

## API

```text
GET /api/provider
GET /api/health
GET /api/snapshot
GET /api/stream
```
