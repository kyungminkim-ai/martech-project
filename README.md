# Martech AI Automation Platform

> 무신사 마케팅 자동화 플랫폼 — AI가 소재를 생성하고, 사람이 판단한다.

---

## Prerequisites

| 항목 | 버전 / 설명 |
|------|------------|
| [Claude Code CLI](https://claude.ai/download) | 최신 버전 |
| Python | 3.10 이상 |
| [Anthropic API Key](https://console.anthropic.com) | claude-sonnet-4-6 접근 권한 |

---

## 빠른 설치

```bash
git clone <this-repo>
cd martech-project
bash setup.sh
```

설치 후 `.env` 에 API 키를 입력합니다:

```
# push-campaign/.env
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 에이전트 현황

| 에이전트 | 채널 | 스킬 | 상태 |
|---------|------|------|------|
| push-campaign | 앱푸시 | `/push-campaign` | ✅ 운영 중 |
| email-campaign | 이메일 | `/email-campaign` | 🔲 미구현 |
| sms-campaign | SMS | `/sms-campaign` | 🔲 미구현 |

---

## 앱푸시 캠페인 — 동작 방식

### 전체 데이터 플로우

```
📄 bizest_raw.csv          📄 brand_list.csv
  ([앱푸시 발송 운영] 시트)       (브랜드 목록)
         │                          │
         └──────────┬───────────────┘
                    ▼
           [입력 검증 — 파일 존재 확인]
                    │
          ┌─────────▼──────────┐
          │  Pipeline 1        │
          │  소재 선별          │
          │  (규칙 기반 필터링)  │
          └─────────┬──────────┘
                    │ 선별된 소재
          ┌─────────▼──────────┐
          │  Pipeline 2        │
          │  메타데이터 생성     │
          │  + LLM 메시지 생성  │
          │  (Claude API)      │
          └─────────┬──────────┘
                    │
          ┌─────────▼──────────┐
          │  Pipeline 3        │
          │  검수 검증 QA       │
          │  (12개 항목 자동    │
          │   검증)             │
          └─────────┬──────────┘
                    │
          ┌─────────▼──────────┐
          │  Pipeline 4        │
          │  LLM Red Team      │
          │  독립 재검토         │
          └─────────┬──────────┘
                    ▼
     📊 campaign_meta_YYYYMMDD_HHmmss.csv
       (캠페인메타엔진 운영 시트 형식)
                    │
                    ▼
          👤 마케팅 담당자
          [검수용] 컬럼 확인 후 Braze 등록
```

---

### Pipeline 1 — 소재 선별

비제스트 RAW(`bizest_raw.csv`)에서 발송 가능한 소재만 추출합니다.

| 단계 | 조건 | 처리 |
|------|------|------|
| ① 취소 필터 | `remarks`에 `취소`, `CANCEL` 포함 | 제외 |
| ② 중복 제거 | `landing_url + sourceBrandId + send_dt` 조합 중복 | id 오름차순 첫 번째만 유지 |
| ③ 랜딩 오픈 검증 | `release_start_date_time < send_dt 11:00` 미충족 | 제외 |
| ④ 마케팅팀 예외 | `register_team_name`에 `전사마케팅` 또는 `카테고리마케팅` 포함 | ③ 조건 무관하게 선별 |

중간 결과: `push-campaign/data/pipeline1_output_YYYYMMDD.csv`

---

### Pipeline 2 — 메타데이터 & 메시지 생성

선별된 소재에 대해 캠페인메타엔진 운영 시트의 컬럼을 자동으로 채웁니다.

#### Rule-based 생성 컬럼

| 컬럼 | 생성 방식 |
|------|----------|
| `send_dt` | `--date` 인수 또는 내일 날짜 자동 사용 |
| `send_time` | 고정 `11:00` |
| `target` | 팀명 기반 성별 (여성팀→`여성`, 남성팀→`남성`, 그 외→`전체`) |
| `priority` | 전사마케팅→`1`, 카테고리마케팅→`2`, 그 외→`3` |
| `ad_code` | `APSCMCD` + BASE36 순번 (이전 코드에서 +1 자동 채번) |
| `content_type` | URL 패턴: `/campaign/`→`캠페인`, `/content/`→`콘텐츠` |
| `brand_id` | `sourceBrandId` 직접 복사 |
| `push_url` | `landing_url?utm_source=app_push&utm_medium=cr&utm_campaign={ad_code}` |

#### LLM (Claude API) 생성 컬럼

| 컬럼 | 규격 | 생성 조건 |
|------|------|----------|
| `title` | 15~40자, 명사형 종결 | `main_title`이 규격 미달 시 LLM 재생성, 적합 시 원본 사용 |
| `contents` (V1 혜택강조) | `(광고) ` 시작, 40~60자, 혜택 수치 강조 | 모든 선별 소재 |
| `[검수용] contents_v2` (V2 브랜드감성) | `(광고) ` 시작, 25~45자, 브랜드 감성 | 모든 선별 소재 |

> Pipeline 2는 5건마다 체크포인트를 저장합니다. 중단 후 재실행 시 자동으로 이어서 처리됩니다.

---

### Pipeline 3 — 검수 검증 (Validation QA)

발송 전 문제가 될 수 있는 항목을 자동 검증합니다. **행을 제거하지 않고** `[검수용]` 컬럼에 이슈를 기록합니다.

| # | 검증 항목 | 이슈 코드 | 수준 |
|---|----------|----------|------|
| 1 | 필수 필드 누락 (title, contents, landing_url, ad_code) | `*_missing` | ⛔ 오류 |
| 2 | title 길이 범위 (15~40자) | `title_length_N chars` | ⚠️ 검수 |
| 3 | `(광고)` 접두어 누락 | `missing_(광고)_prefix` | ⚠️ 검수 |
| 4 | 수신거부 문구 누락 | `missing_unsubscribe_text` | ⚠️ 검수 |
| 5 | push_url UTM 파라미터 누락 | `push_url_missing_utm` | ⚠️ 검수 |
| 6 | push_url UTM campaign ≠ ad_code | `push_url_campaign_mismatch` | ⚠️ 검수 |
| 7 | contents에 0% 표기 | `zero_percent_in_contents` | ⚠️ 검수 |
| 8 | landing_url https 미적용 | `landing_url_not_https` | ⚠️ 검수 |
| 9 | ad_code 중복 | `ad_code_duplicate` | ⚠️ 검수 |
| 10 | brand_id 누락 | `brand_id_missing` | ⛔ 오류 |
| 11 | LLM confidence 임계값 미달 (기준: 3.0) | `low_confidence_v1/v2(N)` | ⚠️ 검수 |
| 12 | title LLM 생성 실패 (fallback) | `title_source_fallback` | ⚠️ 검수 |

---

### Pipeline 4 — LLM Red Team 검토

생성 규칙과 독립된 관점에서 LLM이 소재를 재검토합니다.

| 평가 기준 | 내용 |
|----------|------|
| 정확성 | 혜택 수치·조건이 `promotion_content`와 일치하는지 |
| 수신자 반응 | 클릭 유도력, 명확성, 과장 표현 여부 |
| 브랜드 일관성 | 무신사 톤앤매너 적합성 |
| 차별성 | 유사 소재 대비 메시지 차별화 수준 |
| 문제 여부 | 허위·과장 광고, 금칙어, 규제 위반 가능성 |

출력: `review_score`(1.0~5.0), `review_verdict`(`pass` ≥3.5 / `warning` 2.5~3.4 / `fail` ≤2.4)

---

### AI 미적용 — 마케터 직접 조작 영역

AI 자동 생성 범위에서 의도적으로 제외된 패턴. 마케터가 직접 관리.

| 항목 | 내용 |
|------|------|
| `[대괄호 태그]` 제목 패턴 | `[오늘 종료]`, `[남단]`, `[무퀴즈]` 등 — 마케터가 직접 제목에 추가하는 강조 태그. AI는 생성하지 않음. |

---

## 출력 파일 구조

```
# 단일 날짜
push-campaign/output/campaign_meta_YYYYMMDD_HHmmss.csv

# 날짜 범위 (range 모드)
push-campaign/output/campaign_meta_{from}_{to}_HHmmss.csv
push-campaign/output/selection_report_{from}_{to}.csv
```

| 컬럼 | 내용 | 자동 생성 |
|------|------|:--------:|
| `send_dt` | 발송일 | ✅ |
| `send_time` | 발송 시각 (고정 11:00) | ✅ |
| `target` | 발송 대상 (여성/남성/전체) | ✅ |
| `priority` | 우선순위 (1/2/3) | ✅ |
| `ad_code` | 광고 코드 (APSCMCD + BASE36) | ✅ |
| `content_type` | 콘텐츠 유형 (캠페인/콘텐츠) | ✅ |
| `brand_id` | 브랜드 ID | ✅ |
| `title` | 푸시 제목 (15~40자) | ✅ LLM |
| `contents` | 푸시 본문 V1 혜택강조 | ✅ LLM |
| `landing_url` / `image_url` / `push_url` | URL 정보 | ✅ |
| `[검수용] contents_v2` | 푸시 본문 V2 브랜드감성 | ✅ LLM |
| `[검수용] confidence_v1/v2` | LLM 신뢰도 (1~5) | ✅ |
| `[검수용] error_flag` | 오류 여부 | ✅ |
| `[검수용] needs_review` | 검수 필요 여부 | ✅ |
| `[검수용] validation_notes` | 검증 이슈 상세 | ✅ |
| `[검수용] review_score/verdict/notes` | Red Team 결과 | ✅ LLM |

---

## 실행 방법

### 1. 입력 파일 준비

```
push-campaign/input/
├── bizest_raw.csv          # 비제스트 RAW (필수)
├── brand_list.csv          # 브랜드 목록 (필수)
└── category_selector.csv   # 카테고리 코드 (선택)
```

### 2. Claude Code에서 실행

단일 날짜:

```
/push-campaign --date 2026-05-01
```

날짜 범위 (기간 일괄 처리):

```
/push-campaign-range --start 2026-04-27 --end 2026-05-07
/push-campaign-range 이번 주
/push-campaign-range 다음 주
```

> **range 모드는 날짜 간 중복 소재를 자동 제거한다.** 동일 URL/소재가 여러 날짜에 걸쳐 이중 발송되는 것을 방지하기 위해 기간 실행은 반드시 range 모드(`--from`/`--to`)를 사용한다.

날짜 미지정 시 내일 날짜가 자동 적용됩니다.

### 3. 실행 결과 예시

단일 날짜:

```
[push-campaign 완료] send_dt=2026-05-01
============================================================
📊 처리 결과:
  선별 소재:      5건
  LLM 생성 성공: 5건
  검수 필요:     1건

📁 산출물:
  push-campaign/output/campaign_meta_20260501_143022.csv

⚠️ 검수 필요 항목: [1023]
============================================================
```

날짜 범위 (range 모드):

```
[push-campaign 완료] 2026-04-27 ~ 2026-05-07
============================================================
📊 처리 결과:
  총 선별:       12건
  LLM 생성 성공: 12건
  검수 필요:     3건

📁 산출물:
  캠페인 메타:  push-campaign/output/campaign_meta_20260427_20260507_143022.csv
  선별 리포트:  push-campaign/output/selection_report_20260427_20260507.csv
============================================================
```

---

## 검수 가이드

결과 CSV를 열어 아래 순서로 확인합니다:

1. **`[검수용] error_flag = True`** 행 우선 확인 → 필수 필드 누락, 수동 작성 필요
2. **`[검수용] needs_review = True`** 행 확인 → `validation_notes` 이슈 코드 검토
3. **`[검수용] contents_v2`** 확인 → V1과 비교 후 더 나은 문구를 `contents` 컬럼에 수동 복사
4. Braze 등록 시 `[검수용]` 컬럼 전체 제거

### 자주 나오는 이슈 코드

| 이슈 코드 | 원인 | 조치 |
|----------|------|------|
| `title_missing` | LLM API 오류로 제목 생성 실패 | 수동 제목 작성 |
| `title_length_N chars` | LLM이 범위 밖 제목 생성 | 직접 수정 또는 삭제 |
| `zero_percent_in_contents` | 할인율 0% 표기 | 내용 수정 또는 행 제외 |
| `low_confidence_v1(N)` | LLM 신뢰도 낮음 | 문구 직접 검토 및 수정 |
| `title_source_fallback` | 원본·LLM 제목 모두 부적합 | 제목 수동 작성 |

---

## 데이터 정책

운영 데이터는 모두 gitignore 처리됩니다. 커밋 금지.

| 경로 | Git |
|------|-----|
| `**/input/*.csv` | ❌ 제외 |
| `**/output/` | ❌ 제외 |
| `**/data/` | ❌ 제외 |
| `**/logs/` | ❌ 제외 |
| `**/input/ad_code_seed.txt` | ✅ 포함 (중복 발급 방지) |
| `.env` | ❌ 제외 |

---

## 프로젝트 구조

```
martech-project/
├── CLAUDE.md                          # 플랫폼 진입점 & 에이전트 라우팅
├── README.md                          # 이 파일
├── setup.sh                           # 원클릭 설치 스크립트
├── .claude/
│   └── settings.json                  # Claude Code 권한 설정
│   └── skills/
│       └── push-campaign-range/       # /push-campaign-range 스킬
│           └── SKILL.md
└── push-campaign/                     # ✅ 앱푸시 캠페인 자동화
    ├── CLAUDE.md                      # 에이전트 오케스트레이터
    ├── .claude/
    │   ├── settings.json              # 에이전트 Claude Code 권한
    │   └── skills/push-campaign/      # /push-campaign 스킬
    │       └── SKILL.md
    ├── scripts/                       # 4-phase pipeline Python 구현
    │   ├── run.py                     # 메인 실행 진입점
    │   ├── pipeline1.py ~ pipeline4.py
    │   ├── config.py / rules.py / prompts.py / llm_client.py
    │   └── run_logger.py / regenerate_v3.py
    ├── references/                    # 정책 문서
    │   ├── selection_policy.md        # 소재 선별 기준
    │   ├── message_policy.md          # 메시지 생성 규칙
    │   ├── writing_policy.md          # 문구 포맷 스펙
    │   ├── classification_policy.md   # 분류 규칙
    │   └── brand_guidelines.md        # 무신사 V&T, 금칙어
    ├── docs/                          # 문서
    │   ├── flow_overview.md           # 시스템 플로우 상세 (이 문서의 원본)
    │   └── improvement_backlog.md     # 개선 백로그
    ├── input/                         # 입력 파일 (gitignored)
    ├── output/                        # 생성된 캠페인 CSV (gitignored)
    ├── data/                          # 파이프라인 중간 파일 (gitignored)
    └── logs/                          # 실행 로그 (gitignored)
```

---

## Phase 로드맵

```
Phase 1 (완료)   push-campaign 운영
                  Pipeline 1·2·3·4 + 실행 로그
                  goods_id 자동 추출
                  Pipeline 2 체크포인트

Phase 1.5        H-3 이미지 URL 유효성 검사
                  Databricks 연동 — 비제스트 RAW 자동 수집

Phase 2          Google Spreadsheet 연동
                  Slack 검수 알림
                  이메일·SMS 캠페인 추가

Phase 3          성과 데이터 피드백 루프 (Databricks RAG)
                  3-variant 자동 선택
                  브랜드별 가이드라인 개인화
```

---

## 신규 캠페인 추가

`push-campaign/`을 참고 구현으로 삼아 동일한 디렉터리 구조와 4-phase pipeline을 따른다.
추가 시 루트 `CLAUDE.md` 라우팅 테이블과 이 파일의 에이전트 현황 표를 업데이트한다.
