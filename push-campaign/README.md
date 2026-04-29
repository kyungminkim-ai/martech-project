# push-campaign — 앱푸시 캠페인 소재 자동화 에이전트

무신사 앱푸시 소재 선별부터 캠페인메타엔진 시트 출력까지 전 과정을 자동화하는 파이프라인.

---

## 빠른 시작

```bash
# 내일 날짜 기준 실행 (기본 — Databricks 자동 연결)
python3 scripts/run.py

# 날짜 지정
python3 scripts/run.py --date 2026-05-01

# 파일 모드 (Databricks 없이 bizest_raw.csv 직접 사용)
python3 scripts/run.py --source file --date 2026-05-01

# 기간 범위 (복수 날짜 일괄)
python3 scripts/run.py --from 2026-04-28 --to 2026-05-04

# 기존 selection_report에서 P2~5 재실행 (P1 생략)
python3 scripts/run.py --from-selection-report output/selection_report_20260427_20260507.csv

# 주간 소재 선별만 (Pipeline 1)
python3 scripts/run.py --week
```

### ANTHROPIC_API_KEY 없을 때 (Claude Code 모드)

```bash
# 1단계: Pipeline 1 실행 → pending_jobs_{date}.json 생성
python3 scripts/run.py --source file --date 2026-05-01

# 2단계: Claude Code가 pending_jobs를 읽고 llm_responses_{date}.json 생성

# 3단계: 응답 파일 자동 감지 후 Pipeline 2~5 완료
python3 scripts/run.py --source file --date 2026-05-01
```

---

## 처리 파이프라인

```
[데이터 소스]
  Databricks SQL (--source auto/databricks)
  └── 실패 시 종료코드 10 → 스킬이 파일 fallback 확인
  또는 input/bizest_raw.csv (--source file)
      │
[공통 보조 입력]
  input/brand_list.csv + input/category_selector.csv
      │
[Pipeline 1] 소재 선별
      ├── 취소 필터 (remarks 취소/CANCEL 키워드)
      ├── 이미선정 제외 (광고진행 계열 — 파일 모드 전용)
      ├── 캠메엔 중복 방지 (campaign_meta_sync — GSheets 우선, 로컬 폴백)
      ├── 발송 윈도우 검증 (D-1 10:00 ~ D-0 10:00)
      ├── 랜딩 URL 유효성 검증
      └── 전사캠페인 전용: 취소 확인 후 send_dt+URL 조합 미등록이면 무조건 선별
      │  → selection_report CSV 저장 + Google Sheets 자동 업로드
      │
[Pipeline 2] 메타데이터 & 메시지 생성
      ├── Rule-based: send_dt, target, priority, ad_code, content_type, category_id 등
      ├── 소재 성격 분류 (content_nature): 콜라보레이션 / 단독선발매 / 신규발매 / 프로모션
      ├── 혜택 유형 분류 (benefit_type): Edition / Gift / Price
      └── LLM(Claude): title + contents — 분류 결과 기반 전략 힌트 주입
      │
[Pipeline 3] 검수 검증 + 자동 수정
      ├── 필수 필드·길이·형식 검증 (17개 룰)
      ├── 동사형 종결 / 제목-본문 중복 → LLM 자동 수정 (최대 2회)
      └── 할인율 정합성 검증
      │
[Pipeline 4] LLM Red Team 검토
      └── 독립 관점 품질 평가 (score 1~5, pass/warning/fail)
      │
[Pipeline 5] 발송일 분배 + 광고코드 최종 할당
      ├── P5-A: 날짜별 밀집 소재 인접 날짜로 재배치 (MAX_PER_DATE=5)
      └── P5-B: campaign_meta_sync 기준 마지막 ad_code 이후부터 순차 재할당
      │  → campaign_meta CSV 저장 + Google Sheets 자동 업로드
      │
[출력] output/campaign_meta_{date}_{timestamp}.csv
```

---

## 데이터 소스

### Databricks (권장)

환경 변수 설정 시 `--source auto`(기본)로 자동 연결.  
`scripts/bizest_query.sql`의 쿼리로 날짜별 비제스트 RAW를 직접 조회합니다.

| 종료 코드 | 의미 | 처리 |
|-----------|------|------|
| 0 | 정상 완료 | 다음 단계 진행 |
| 10 | Databricks 연결 실패 또는 환경변수 미설정 | 스킬이 파일 fallback 확인 요청 |
| 1 | 기타 오류 | 오류 보고 후 중단 |

### 파일 모드 (`--source file`)

`input/bizest_raw.csv`를 직접 읽습니다. Databricks 미설정 환경이나 수동 테스트에 사용.

---

## 입력 파일

| 파일 | 설명 | 필수 여부 |
|------|------|----------|
| `input/brand_list.csv` | 브랜드 ID → 이름·성별 매핑 | 필수 |
| `input/bizest_raw.csv` | 비제스트 RAW — Databricks 미연동 시만 필요 | 조건부 |
| `input/category_selector.csv` | 카테고리 코드 목록 (LLM 유추용) | 선택 |
| `input/campaign_meta_sync.csv` | 기등록 소재 URL (GSheets 미연동 시 로컬 폴백) | 선택 |

`campaign_meta_sync`는 Google Sheets 연동 시 자동 동기화되므로 별도 파일 관리 불필요.

---

## 출력 컬럼 구조

### 캠페인메타엔진 운영 컬럼 (Braze 등록용)

| 컬럼 | 생성 방식 |
|------|----------|
| `send_dt`, `send_time` | release_start_date_time 파싱 / 고정 11:00 |
| `target` | 팀명 키워드 → brand_list.gender → 전체 |
| `priority` | 팀명 기반 (전사캠페인=1, 카테고리마케팅=2, 기타=3) |
| `ad_code` | `APSCMCD` + BASE36 순번 (P5-B에서 최종 재할당) |
| `content_type` | landing_url 패턴 (캠페인/콘텐츠/브랜드) |
| `category_id` | 팀명 매핑 + LLM 유추 (최대 3개, 쉼표 구분) |
| `title` | main_title 재사용 or LLM 재생성 (5~40자) |
| `contents` | LLM 생성 — 분류 전략 힌트 기반 (광고) 시작, 25~60자 |
| `push_url` | landing_url + UTM 파라미터 (P5-B에서 ad_code와 동기화) |
| `braze_campaign_name` | 자동 생성 (`YYMMDD_11시_ADCODE_정기_GMV_...`) |

### 검수용 컬럼 (Braze 등록 시 제외)

| 컬럼 | 내용 |
|------|------|
| `[검수용] content_nature` | 소재 성격 분류 결과 (콜라보레이션/단독선발매/신규발매/프로모션/기타) |
| `[검수용] benefit_type` | 혜택 유형 분류 결과 (Edition/Gift/Price) |
| `[검수용] title_source` | 제목 출처 (original/llm/fallback) |
| `[검수용] confidence_v1/v2/v3` | LLM 신뢰도 점수 (1.0~5.0) |
| `[검수용] error_flag` | 생성 실패 여부 |
| `[검수용] needs_review` | 담당자 검수 필요 여부 |
| `[검수용] validation_notes` | Pipeline 3 검수 이슈 목록 |
| `[검수용] review_score` | Pipeline 4 Red Team 점수 (1.0~5.0) |
| `[검수용] review_verdict` | pass / warning / fail |
| `[검수용] review_notes` | Red Team 핵심 피드백 |

---

## 소재 분류 체계 (contents.md 기반)

Pipeline 2에서 소재를 자동 분류하고 메시지 생성 전략에 반영합니다.

### 소재 성격 (content_nature)

| 분류 | 판단 기준 | 메시지 전략 |
|------|----------|-----------|
| 콜라보레이션 | `BrandA X BrandB` 패턴 감지 | 제목에 두 이름 필수, 본문은 발매·혜택만 |
| 단독선발매 | "단독", "선발매", "선론칭" 키워드 | 본문에 "무신사 단독/선발매" 표현 강제 |
| 신규발매 | "드롭", "발매", "SS/FW", "컬렉션" 등 | 발매·드롭·출시·컬렉션 표현 유도 |
| 프로모션 | "%", "쿠폰", "할인", "세일" 키워드 | 혜택 기간·조건 구체적 서술 |
| 기타 | 위 조건 미해당 | 브랜드·제품 차별성 감성 표현 |

### 혜택 유형 (benefit_type)

| 유형 | 판단 기준 | 메시지 전략 |
|------|----------|-----------|
| Edition | "한정판", "에디션", "굿즈", "한정" | 희소성·한정성 강조 |
| Gift | "사은품", "키링", "기프트" | 증정 표현 명시 |
| Price | "%", "쿠폰", "할인", "특가" | 할인율 수치 반드시 포함 |

**조합 전략**: `단독선발매 × Edition` → "무신사 단독 한정 발매" 패턴 최우선 적용.

---

## 소재 선별 기준 (Pipeline 1)

**일반 소재 우선순위 순서:**

1. **취소 제외**: remarks에 `취소/CANCEL` 포함 시 제외
2. **이미선정 제외**: 광고진행 계열 상태 제외 (`--source file` 모드 전용)
3. **캠메엔 중복**: `campaign_meta_sync`에 등록된 `landing_url` → `CAMPAIGN_META_REGISTERED` 탈락
4. **기간내 중복**: 동일 실행 내 id/URL 중복 (기간·주간 배치 전용)
5. **발송 윈도우**: `D-1 10:00 ≤ release_start_date_time < D-0 10:00`
6. **랜딩 URL 유효성**: musinsa.com 도메인, https, 유효한 ID 포함

**전사캠페인 전용 로직** (`register_team_name`에 `전사캠페인` 포함):
- ① 취소 여부만 확인 후, `landing_url + send_dt` 조합이 `campaign_meta_sync`에 없으면 무조건 선별
- 발송 윈도우, URL 검증 등 나머지 조건 모두 생략

---

## 메시지 생성 원칙

**제목 = 주어(정체성)**, **본문 = 서술어(행동/혜택)** — 두 문장이 이어져 하나의 완성된 문장.

| 역할 | 담당 내용 | 금지 |
|------|---------|------|
| 제목 (5~40자) | 브랜드명·콜라보 대상·상품명·훅 문구 | 발매/선론칭/할인 등 행동어 |
| 본문 (25~60자) | 발매·기간·혜택·긴급성 | 제목 단어 반복 |

본문은 `(광고) `로 시작, 명사형 종결 필수.

---

## 설정 파일

| 파일 | 역할 |
|------|------|
| `.env` / `.env.example` | `ANTHROPIC_API_KEY`, Databricks, Google Sheets 설정 |
| `scripts/config.py` | 파이프라인 상수 (모델명, 길이 제한, `MAX_PER_DATE` 등) |
| `scripts/bizest_query.sql` | Databricks 비제스트 RAW 조회 쿼리 |
| `references/` | 분류 정책, 메시지 정책, 브랜드 가이드라인 문서 |

### 주요 환경 변수

```bash
ANTHROPIC_API_KEY=sk-ant-...          # Claude API (필수)

# Databricks
DATABRICKS_HOST=adb-xxxx.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxx
DATABRICKS_TOKEN=dapi...

# Google Sheets
GOOGLE_SHEET_ID=...                   # 스프레드시트 ID
GOOGLE_SHEET_GID=...                  # 선별 리포트 탭 GID
GOOGLE_SHEET_CAMPAIGN_GID=...         # 캠페인 메타 탭 GID
GOOGLE_SHEET_CAMPAIGN_META_GID=...    # campaign_meta_sync 탭 GID
GOOGLE_SHEET_CREDS_PATH=...           # 서비스 계정 JSON 경로
```

---

## 디렉터리 구조

```
push-campaign/
├── input/             # 입력 파일 (bizest_raw.csv 등)
├── output/            # 캠페인메타 산출물 CSV
├── data/              # 체크포인트·pending_jobs·LLM 응답 파일
├── logs/              # 실행 로그 JSON
├── scripts/
│   ├── run.py         # 메인 실행 진입점
│   ├── pipeline1.py   # 소재 선별 (Databricks/GSheets 연동 포함)
│   ├── pipeline2.py   # 메타데이터 & 메시지 생성
│   ├── pipeline3.py   # 검수 검증 + 자동 수정
│   ├── pipeline4.py   # LLM Red Team 검토
│   ├── pipeline5.py   # 발송일 분배 + 광고코드 최종 할당
│   ├── gsheets.py     # Google Sheets 연동 (업로드/다운로드)
│   ├── bizest_query.sql  # Databricks 비제스트 조회 쿼리
│   ├── rules.py       # Rule-based 로직 (분류·생성·검증)
│   ├── prompts.py     # LLM 프롬프트 템플릿
│   ├── llm_client.py  # Claude API 클라이언트
│   ├── config.py      # 설정 상수
│   └── run_logger.py  # 실행 로그
└── references/        # 정책 문서
```

---

## 오류 처리

| 상황 | 처리 |
|------|------|
| Databricks 연결 실패 (종료코드 10) | 스킬이 사용자에게 파일 fallback 확인 요청 |
| Databricks 환경변수 미설정 (종료코드 10) | 스킬이 사용자에게 파일 fallback 확인 요청 |
| 파일 모드 + bizest_raw.csv 없음 | "파일 없음" 안내 후 종료, 업로드 요청 |
| Google Sheets 업로드 실패 | 경고 로그만 남기고 파이프라인 계속 실행 |
| Claude API 3회 실패 | title/contents=null, error_flag=True, 이후 소재 계속 처리 |
| API 키 없음 | Pipeline 1 실행 후 pending_jobs 생성 → Claude Code 모드 |
| 입력 파일 없음 | 오류 메시지 출력 후 종료 |
| 선별 소재 0건 | 경고 메시지 출력 (정상 0건과 구분) |
| 동사형 종결 감지 | LLM 재호출 자동 수정 (최대 2회) |
| P5 이동 불가 소재 | needs_review=True + validation_notes 기록 |
