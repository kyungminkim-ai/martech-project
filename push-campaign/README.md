# push-campaign — 앱푸시 캠페인 소재 자동화 에이전트

무신사 앱푸시 소재 선별부터 캠페인메타엔진 시트 출력까지 전 과정을 자동화하는 파이프라인.  
AI가 소재를 선별·생성·검증하고, 사람이 최종 판단한다.

---

## 목차

1. [이 시스템이 하는 일](#1-이-시스템이-하는-일)
2. [소재 선별 정책 — Pipeline 1](#2-소재-선별-정책--pipeline-1)
3. [소재 분류 체계 — Pipeline 2 전처리](#3-소재-분류-체계--pipeline-2-전처리)
4. [메시지 생성 원칙 — Pipeline 2](#4-메시지-생성-원칙--pipeline-2)
5. [검수 QA 기준 — Pipeline 3](#5-검수-qa-기준--pipeline-3)
6. [Red Team 평가 — Pipeline 4](#6-red-team-평가--pipeline-4)
7. [발송일 분배 & 광고코드 할당 — Pipeline 5](#7-발송일-분배--광고코드-할당--pipeline-5)
8. [AI 미적용 영역 — 마케터 직접 조작](#8-ai-미적용-영역--마케터-직접-조작)
---
9. [운영 명령어](#9-운영-명령어)
10. [출력 컬럼 구조](#10-출력-컬럼-구조)
11. [검수 가이드](#11-검수-가이드)
12. [설정 & 환경변수](#12-설정--환경변수)
13. [오류 처리](#13-오류-처리)
14. [디렉터리 구조](#14-디렉터리-구조)

---

## 1. 이 시스템이 하는 일

비제스트 RAW(앱푸시 발송 운영 시트)에 올라온 소재 요청을 받아, 발송 가능한 소재를 자동 선별하고, 캠페인메타엔진에 바로 등록할 수 있는 형식의 CSV를 생성한다.

```
비제스트 RAW (Databricks / 파일)
        │
  [Pipeline 1]  소재 선별 — 규칙 기반 필터링
        │
  [Pipeline 2]  메타데이터 & 메시지 생성 — Rule + LLM(Claude)
        │
  [Pipeline 3]  검수 QA — 17개 항목 자동 검증 + 자동 수정
        │
  [Pipeline 4]  Red Team — LLM 독립 재검토
        │
  [Pipeline 5]  발송일 분배 + 광고코드 최종 할당
        │
  campaign_meta_YYYYMMDD_HHmmss.csv
  → Google Sheets 자동 업로드 → 마케터 검수 → Braze 등록
```

**시스템의 역할 분담:**

| 담당 | 영역 |
|------|------|
| AI (자동) | 소재 선별·분류, 메시지 초안 생성, 형식 검증, 품질 평가 |
| 마케터 (판단) | `[검수용]` 컬럼 확인, V1·V2 문구 선택, Braze 등록 |

---

## 2. 소재 선별 정책 — Pipeline 1

비제스트 RAW 전체 소재 중 발송 가능한 소재만 추출한다. **행을 수정하지 않고 선별/탈락 판정만 내린다.**

### 일반 소재 — 우선순위 순서

| 단계 | 조건 | 처리 | 이유 |
|------|------|------|------|
| ① | `remarks`에 `취소` / `CANCEL` 포함 | 제외 | 담당자가 명시적으로 취소한 소재 |
| ② | 광고진행 계열 상태 (`--source file` 전용) | 제외 | 이미 캠페인 진행 중인 소재 재발송 방지 |
| ③ | `campaign_meta_sync`에 등록된 `landing_url` | `CAMPAIGN_META_REGISTERED` 탈락 | 크로스세션 중복 방지 — 가장 중요한 기준 |
| ④ | 동일 실행 내 id/URL 중복 (기간·주간 배치 전용) | 제외 | 기간 일괄 처리 시 날짜 간 이중 발송 방지 |
| ⑤ | `landing_url + sourceBrandId + send_dt` 조합 중복 | 제외 | 당일 동일 소재 중복 발송 방지 |
| ⑥ | `release_start_date_time` ≥ `send_dt 11:00` | 제외 | 발송 시점에 랜딩이 아직 오픈 안 된 소재 |
| ⑦ | musinsa.com 도메인, https, 유효한 ID 포함 검증 | 제외 | 깨진 링크 발송 방지 |

> **③이 핵심 중복 기준이다.** campaign_meta_sync는 Google Sheets 연동 시 자동 동기화, 미연동 시 `input/campaign_meta_sync.csv`를 로컬 폴백으로 사용한다.

### 전사캠페인 전용 로직

`register_team_name`에 `전사캠페인` 또는 `카테고리마케팅` 포함 시 별도 처리:

- ① 취소 여부만 확인
- `landing_url + send_dt` 조합이 `campaign_meta_sync`에 없으면 **무조건 선별**
- 발송 윈도우, URL 검증 등 나머지 조건 전부 생략

> 전사캠페인은 팀이 직접 관리하는 소재로 시스템이 윈도우·URL 조건으로 탈락시키지 않는다.

---

## 3. 소재 분류 체계 — Pipeline 2 전처리

Pipeline 2 메시지 생성 전에 소재를 자동 분류하고, 분류 결과를 LLM 프롬프트에 전략 힌트로 주입한다.

### 소재 성격 (content_nature)

| 분류 | 판단 기준 | 메시지 전략 |
|------|----------|-----------|
| **콜라보레이션** | `BrandA X BrandB` 패턴 감지 | 제목에 두 브랜드명 필수, 본문은 발매·혜택만 |
| **단독선발매** | "단독", "선발매", "선론칭" 키워드 | 본문에 "무신사 단독/선발매" 표현 강제 |
| **신규발매** | "드롭", "발매", "SS/FW", "컬렉션" 등 | 발매·드롭·출시·컬렉션 표현 유도 |
| **프로모션** | "%", "쿠폰", "할인", "세일" 키워드 | 혜택 기간·조건 구체적 서술 |
| **기타** | 위 조건 미해당 | 브랜드·제품 차별성 감성 표현 |

### 혜택 유형 (benefit_type)

| 유형 | 판단 기준 | 메시지 전략 |
|------|----------|-----------|
| **Edition** | "한정판", "에디션", "굿즈", "한정" | 희소성·한정성 강조 |
| **Gift** | "사은품", "키링", "기프트" | 증정 표현 명시 |
| **Price** | "%", "쿠폰", "할인", "특가" | 할인율 수치 반드시 포함 |

### 조합 전략

| 조합 | 적용 전략 |
|------|----------|
| `단독선발매 × Edition` | "무신사 단독 한정 발매" 패턴 최우선 |
| `프로모션 × Price` | 할인율 + 기간 동시 명시 |
| `콜라보레이션 × Gift` | 콜라보 대상 + 증정 조건 병기 |

---

## 4. 메시지 생성 원칙 — Pipeline 2

### 제목-본문 이어쓰기 구조

제목은 **주어(정체성)**, 본문은 **서술어(행동/혜택)**. 두 문장이 이어져 하나의 완성된 문장이 된다.

| 역할 | 담당 내용 | 금지 |
|------|---------|------|
| **제목** (5~40자) | 브랜드명·콜라보 대상·상품명·훅 문구 | 발매/선론칭/할인 등 행동어 |
| **본문** (25~60자) | 발매·기간·혜택·긴급성 | 제목 단어·문구 반복 |

**예시:**
> 제목: `더마토리 X 톡신 공동개발 블랙세럼`  
> 본문: `(광고) 4/27~29 단 3일 선론칭`

본문은 `(광고) `로 시작, 명사형 종결 필수.

### 제목 생성 기준

| 상황 | 처리 |
|------|------|
| `main_title`이 5~40자이고 명사형 종결 | 원본 그대로 사용 |
| `main_title` 40자 초과 | 쉼표 앞 훅 부분 자동 추출 |
| 콜라보 소재이고 `BrandA x BrandB` 형식이 아님 | LLM 재생성 |
| 그 외 규격 미달 | LLM 재생성 |

### 본문 생성 — V1 / V2

| 버전 | 전략 | 길이 |
|------|------|------|
| `contents` (V1) | 혜택 수치 강조 — 분류 힌트 기반 | 25~60자 |
| `[검수용] contents_v2` (V2) | 브랜드 감성 표현 | 25~45자 |

마케터는 V1·V2를 비교해 더 나은 문구를 `contents` 컬럼에 수동 복사한다.

### Rule-based 생성 컬럼

| 컬럼 | 생성 방식 |
|------|----------|
| `target` | 팀명 기반 성별 (여성팀→`여성`, 남성팀→`남성`, 그 외→`전체`) |
| `priority` | 전사캠페인→`1`, 카테고리마케팅→`2`, 그 외→`3` |
| `ad_code` | `APSCMCD` + BASE36 순번 (P5-B에서 최종 재할당) |
| `content_type` | URL 패턴: `/campaign/`→`캠페인`, `/content/`→`콘텐츠` |
| `push_url` | `landing_url?utm_source=app_push&utm_medium=cr&utm_campaign={ad_code}` |
| `braze_campaign_name` | `YYMMDD_11시_ADCODE_정기_GMV_...` 자동 생성 |

---

## 5. 검수 QA 기준 — Pipeline 3

**행을 제거하지 않는다.** 문제를 발견해도 `[검수용]` 컬럼에 이슈 코드를 기록하고 계속 진행한다. 자동 수정이 가능한 항목은 LLM 재호출로 수정을 시도한다.

### 검증 항목 (17개 룰)

| # | 항목 | 이슈 코드 | 수준 | 자동 수정 |
|---|------|----------|------|----------|
| 1 | 필수 필드 누락 (title, contents, landing_url, ad_code) | `*_missing` | ⛔ 오류 | — |
| 2 | title 길이 범위 (5~40자) | `title_length_N chars` | ⚠️ 검수 | — |
| 3 | `(광고)` 접두어 누락 | `missing_(광고)_prefix` | ⚠️ 검수 | — |
| 4 | 수신거부 문구 누락 | `missing_unsubscribe_text` | ⚠️ 검수 | — |
| 5 | push_url UTM 파라미터 누락 | `push_url_missing_utm` | ⚠️ 검수 | — |
| 6 | push_url UTM campaign ≠ ad_code | `push_url_campaign_mismatch` | ⚠️ 검수 | — |
| 7 | contents에 0% 표기 | `zero_percent_in_contents` | ⚠️ 검수 | — |
| 8 | landing_url https 미적용 | `landing_url_not_https` | ⚠️ 검수 | — |
| 9 | ad_code 중복 | `ad_code_duplicate` | ⚠️ 검수 | — |
| 10 | brand_id 누락 | `brand_id_missing` | ⛔ 오류 | — |
| 11 | LLM confidence 임계값 미달 (기준: 3.0) | `low_confidence_v1/v2(N)` | ⚠️ 검수 | — |
| 12 | title LLM 생성 실패 (fallback) | `title_source_fallback` | ⚠️ 검수 | — |
| 13 | 동사형 종결 감지 | — | 자동 수정 | ✅ LLM 재호출 (최대 2회) |
| 14 | 제목-본문 단어 중복 | — | 자동 수정 | ✅ LLM 재호출 (최대 2회) |
| 15 | 할인율 정합성 (비제스트 RAW 수치 vs 생성 본문) | `discount_rate_mismatch` | ⚠️ 검수 | — |
| 16 | contents 길이 (25~60자) | `contents_length_N chars` | ⚠️ 검수 | — |
| 17 | content_type 미분류 | `content_type_unknown` | ⚠️ 검수 | — |

### 자동 수정 대상

- **동사형 종결**: "출시했어요", "확인하세요" → LLM에 명사형 종결 재요청 (최대 2회)
- **제목-본문 중복**: 본문이 제목의 단어를 그대로 반복할 때 → LLM 재생성

---

## 6. Red Team 평가 — Pipeline 4

생성 규칙과 독립된 관점에서 LLM이 소재를 재검토한다. Pipeline 3 검증자와 동일한 모델이 다른 프롬프트로 독립적으로 평가한다.

### 평가 기준

| 기준 | 설명 |
|------|------|
| **정확성** | 혜택 수치·조건이 `promotion_content`와 일치하는지 |
| **수신자 반응** | 클릭 유도력, 명확성, 과장 표현 여부 |
| **브랜드 일관성** | 무신사 톤앤매너 적합성 |
| **차별성** | 유사 소재 대비 메시지 차별화 수준 |
| **문제 여부** | 허위·과장 광고, 금칙어, 규제 위반 가능성 |

### 평가 결과

| 등급 | 기준 | 처리 |
|------|------|------|
| `pass` | score ≥ 3.5 | 정상 출력 |
| `warning` | 2.5 ≤ score < 3.5 | `needs_review = True` 플래그 |
| `fail` | score < 2.5 | `needs_review = True` 플래그 |

> `warning`/`fail` 소재는 행을 제거하지 않고 `[검수용] review_score/verdict/notes` 컬럼에 기록해 마케터가 판단한다.

---

## 7. 발송일 분배 & 광고코드 할당 — Pipeline 5

Pipeline 1~4 완료 후 후처리로 실행된다.

### P5-A: 발송일 분배

날짜별 소재 밀집도를 분석하고 `MAX_PER_DATE=5` 초과 시 낮은 우선순위 소재를 인접 날짜로 재배치한다.

- **이동 대상**: `priority` 숫자가 큰 소재 (낮은 우선순위) 먼저
- **이동 조건**: 인접 날짜 중 여유가 있고 동일 `brand_id`가 없는 날짜
- **이동 불가 시**: `needs_review = True` + `validation_notes` 기록

### P5-B: 광고코드 최종 할당

`campaign_meta_sync`와 `ad_code_seed.txt` 기준으로 마지막 `ad_code` 이후부터 순차 재할당한다.

- **정렬**: `send_dt ASC → priority ASC → id ASC`
- `push_url`의 `utm_campaign`/`utm_source` 파라미터도 함께 갱신

---

## 8. AI 미적용 영역 — 마케터 직접 조작

| 항목 | 내용 |
|------|------|
| `[대괄호 태그]` 제목 패턴 | `[오늘 종료]`, `[남단]`, `[무퀴즈]` 등 — 마케터가 직접 추가하는 강조 태그. AI는 생성하지 않음. |
| V1·V2 문구 최종 선택 | AI가 생성한 두 버전 중 마케터가 판단해 선택 |
| Braze 등록 | `[검수용]` 컬럼 제거 후 Braze에 수동 등록 |

---

---

## 9. 운영 명령어

### 기본 실행

```bash
# 내일 날짜 기준 (기본 — Databricks 자동 연결)
python3 scripts/run.py

# 날짜 지정
python3 scripts/run.py --date 2026-05-01

# 파일 모드 (Databricks 없이 bizest_raw.csv 직접 사용)
python3 scripts/run.py --source file --date 2026-05-01
```

### 기간 범위 (range 모드)

```bash
# 날짜 직접 지정
python3 scripts/run.py --from 2026-04-28 --to 2026-05-04

# Claude Code 스킬로 실행
/push-campaign --date 2026-05-01
/push-campaign-range --start 2026-04-27 --end 2026-05-07
/push-campaign-range 이번 주
/push-campaign-range 다음 주
```

> **range 모드는 날짜 간 중복 소재를 자동 제거한다.** 기간 일괄 처리 시 반드시 range 모드를 사용한다.

### selection_report에서 재실행 (P1 생략)

```bash
# 기존 선별 결과에서 P2~5만 재실행
python3 scripts/run.py --from-selection-report output/selection_report_20260427_20260507.csv

# Claude Code 스킬
/push-campaign --from-selection-report output/selection_report_20260427_20260507.csv
```

### ANTHROPIC_API_KEY 없을 때 (Claude Code 모드)

```bash
# 1단계: Pipeline 1 실행 → pending_jobs_{date}.json 생성
python3 scripts/run.py --source file --date 2026-05-01

# 2단계: Claude Code가 pending_jobs를 읽고 llm_responses_{date}.json 생성

# 3단계: 응답 파일 자동 감지 후 Pipeline 2~5 완료
python3 scripts/run.py --source file --date 2026-05-01
```

### 데이터 소스 선택 (`--source`)

| 값 | 동작 |
|----|------|
| `auto` (기본) | Databricks 연결 시도 → 실패(종료코드 10) 시 파일 fallback 확인 |
| `databricks` | Databricks 강제 사용 |
| `file` | `bizest_raw.csv` 직접 사용 |

---

## 10. 출력 컬럼 구조

### 캠페인메타엔진 운영 컬럼 (Braze 등록용)

| 컬럼 | 생성 방식 | 자동 여부 |
|------|----------|:--------:|
| `send_dt` | release_start_date_time 파싱 | ✅ |
| `send_time` | 고정 `11:00` | ✅ |
| `target` | 팀명 → 성별 매핑 | ✅ |
| `priority` | 팀명 기반 (1/2/3) | ✅ |
| `ad_code` | `APSCMCD` + BASE36 순번 (P5-B 최종) | ✅ P5 |
| `content_type` | landing_url 패턴 (캠페인/콘텐츠/브랜드) | ✅ |
| `category_id` | 팀명 매핑 + LLM 유추 (최대 3개) | ✅ |
| `brand_id` | sourceBrandId 직접 복사 | ✅ |
| `title` | main_title 재사용 or LLM 재생성 | ✅ LLM |
| `contents` | LLM 생성 V1 — 분류 전략 힌트 기반 | ✅ LLM |
| `landing_url` / `image_url` | RAW 직접 복사 | ✅ |
| `push_url` | landing_url + UTM (P5-B에서 ad_code와 동기화) | ✅ P5 |
| `braze_campaign_name` | `YYMMDD_11시_ADCODE_정기_GMV_...` | ✅ |

### 검수용 컬럼 (Braze 등록 시 제외)

| 컬럼 | 내용 |
|------|------|
| `[검수용] contents_v2` | 브랜드 감성 전략 V2 문구 |
| `[검수용] content_nature` | 소재 성격 분류 (콜라보/단독선발매/신규발매/프로모션/기타) |
| `[검수용] benefit_type` | 혜택 유형 (Edition/Gift/Price) |
| `[검수용] title_source` | 제목 출처 (original/llm/fallback) |
| `[검수용] confidence_v1/v2` | LLM 신뢰도 점수 (1.0~5.0) |
| `[검수용] error_flag` | 생성 실패 여부 |
| `[검수용] needs_review` | 담당자 검수 필요 여부 |
| `[검수용] validation_notes` | Pipeline 3 검수 이슈 목록 |
| `[검수용] review_score` | Red Team 점수 (1.0~5.0) |
| `[검수용] review_verdict` | pass / warning / fail |
| `[검수용] review_notes` | Red Team 핵심 피드백 |

### 출력 파일명

```
# 단일 날짜
push-campaign/output/campaign_meta_YYYYMMDD_HHmmss.csv

# 날짜 범위
push-campaign/output/campaign_meta_{from}_{to}_HHmmss.csv
push-campaign/output/selection_report_{from}_{to}.csv
```

---

## 11. 검수 가이드

결과 CSV를 열어 아래 순서로 확인한다:

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
| `discount_rate_mismatch` | 생성 본문의 할인율이 RAW 수치와 불일치 | 할인율 수동 수정 |
| `review_verdict: fail` | Red Team 평가 기준 미달 | 문구 전면 재검토 |

---

## 12. 설정 & 환경변수

```bash
# push-campaign/.env

ANTHROPIC_API_KEY=sk-ant-...          # Claude API (필수)

# Databricks (선택 — 설정 시 bizest_raw 자동 수집)
DATABRICKS_HOST=adb-xxxx.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxx
DATABRICKS_TOKEN=dapi...

# Google Sheets (선택 — 설정 시 선별 리포트 / 캠페인 메타 자동 업로드)
GOOGLE_SHEET_ID=1FrE7ZIXiYuJEsMvFw_JTNedmYojNOW0xLv8courdr38
GOOGLE_SHEET_GID=1466233062               # 선별 리포트 탭
GOOGLE_SHEET_CAMPAIGN_GID=0               # 캠페인 메타 탭
GOOGLE_SHEET_CAMPAIGN_META_GID=315655952  # campaign_meta_sync 탭
GOOGLE_SHEET_BIZEST_GID=578734437         # bizest_raw 탭
GOOGLE_SHEET_CREDS_PATH=/path/to/service_account.json
```

### 주요 설정 파일

| 파일 | 역할 |
|------|------|
| `scripts/config.py` | 파이프라인 상수 (모델명, 길이 제한, `MAX_PER_DATE` 등) |
| `scripts/bizest_query.sql` | Databricks 비제스트 RAW 조회 쿼리 |
| `references/selection_policy.md` | 소재 선별 세부 기준 |
| `references/message_policy.md` | 메시지 생성 세부 규칙 |
| `references/writing_policy.md` | 문구 포맷 스펙 |
| `references/brand_guidelines.md` | 무신사 V&T, 금칙어 목록 |

---

## 13. 오류 처리

| 상황 | 처리 |
|------|------|
| Databricks 연결 실패 (종료코드 10) | 스킬이 사용자에게 파일 fallback 확인 요청 |
| 파일 모드 + `bizest_raw.csv` 없음 | "파일 없음" 안내 후 종료 |
| Google Sheets 업로드 실패 | 경고 로그만 남기고 파이프라인 계속 실행 |
| Claude API 3회 실패 | `title/contents=null`, `error_flag=True`, 이후 소재 계속 처리 |
| API 키 없음 | Pipeline 1 실행 후 `pending_jobs` 생성 → Claude Code 모드 |
| 선별 소재 0건 | 경고 메시지 출력 (정상 0건과 구분) |
| 동사형 종결 감지 | LLM 재호출 자동 수정 (최대 2회) |
| P5 이동 불가 소재 | `needs_review=True` + `validation_notes` 기록 |

---

## 14. 디렉터리 구조

```
push-campaign/
├── input/             # 입력 파일 (gitignored)
│   ├── brand_list.csv
│   ├── bizest_raw.csv         # Databricks 미연동 시 필수
│   ├── category_selector.csv  # 선택
│   └── ad_code_seed.txt       # 광고코드 중복 방지 기준 (git 포함)
├── output/            # 캠페인메타 산출물 CSV (gitignored)
├── data/              # 체크포인트·pending_jobs·LLM 응답 (gitignored)
├── logs/              # 실행 로그 JSON (gitignored)
├── scripts/
│   ├── run.py         # 메인 실행 진입점
│   ├── pipeline1.py   # 소재 선별
│   ├── pipeline2.py   # 메타데이터 & 메시지 생성
│   ├── pipeline3.py   # 검수 검증 + 자동 수정
│   ├── pipeline4.py   # LLM Red Team 검토
│   ├── pipeline5.py   # 발송일 분배 + 광고코드 최종 할당
│   ├── gsheets.py     # Google Sheets 연동
│   ├── bizest_query.sql
│   ├── rules.py / prompts.py / llm_client.py / config.py
│   └── run_logger.py
└── references/        # 정책 문서
    ├── selection_policy.md
    ├── message_policy.md
    ├── writing_policy.md
    ├── classification_policy.md
    └── brand_guidelines.md
```
