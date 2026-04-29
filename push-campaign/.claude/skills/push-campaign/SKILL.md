# /push-campaign — 앱푸시 캠페인 소재 선별 & 메시지 생성

## 모델

`claude-sonnet-4-6`

## 개요

마케팅 구좌관리 요청(비제스트 RAW)에서 발송 가능한 소재를 선별하고,
캠페인메타엔진 운영 시트 형식으로 메타데이터 + LLM 메시지를 생성하는 스킬.

> **실행 위치**: 프로젝트 루트(`martech-project/`)에서 실행.
> 모든 스크립트 명령 앞에 `cd push-campaign &&` 를 붙여 실행.

---

## 사용법

```
/push-campaign
/push-campaign --date 2026-05-01
/push-campaign --date 2026-05-01 --input input/my_raw.csv
```

---

## 실행 규칙

### Step 0: 데이터 소스 결정 (Databricks 우선 → 실패 시 사용자 확인)

**0-A. Databricks 연결 시도**
```bash
cd push-campaign && python3 scripts/run.py --source auto --stage pipeline1 --date {YYYY-MM-DD}
```

스크립트 종료 코드별 처리:

| 종료 코드 | 출력 키워드 | 의미 | 처리 |
|-----------|------------|------|------|
| 0 | — | 정상 완료 | 다음 단계 진행 |
| 10 | `DATABRICKS_UNAVAILABLE` | Databricks 연결 실패 | → **0-B 실행** |
| 10 | `DATABRICKS_NOT_CONFIGURED` | 환경 변수 미설정 | → **0-B 실행** |
| 1 | — | 기타 오류 | 오류 내용 사용자에게 보고 후 중단 |

**0-B. 파일 fallback — 사용자 확인 필수**

종료 코드 10이 반환되면 파이프라인을 즉시 중단하고 아래 메시지를 사용자에게 표시한 뒤 확인을 받는다:

```
⚠️  Databricks 연결 불가 (또는 환경 변수 미설정)

input/bizest_raw.csv 파일로 대신 진행할까요?
파일 경로: push-campaign/input/bizest_raw.csv

진행하시려면 "예" 또는 "파일로 진행"이라고 답해주세요.
취소하려면 "아니오" 또는 "중단"이라고 답해주세요.
```

사용자가 확인하면:
```bash
cd push-campaign && python3 scripts/run.py --source file --stage pipeline1 --date {YYYY-MM-DD}
```

사용자가 거부하면: 작업 중단, 원인 및 해결 방법 안내.

> **파일 모드 전제 조건** (사용자 확인 전 체크):
> - `input/bizest_raw.csv` 존재 여부 확인
> - 없으면 "파일이 없습니다. 업로드 후 다시 시도해주세요" 안내

**공통 보조 파일 확인 (소스와 무관)**
```python
INPUT_FILES = [
    "input/brand_list.csv",       # 브랜드 목록 (필수)
]
# category_selector.csv는 선택 (없으면 category_id 공란)
```

send_dt 미지정 시 → 내일 날짜(D+1) 자동 사용

### Step 1: Pipeline 1 — 소재 선별

Step 0에서 결정된 소스(`databricks` 또는 `file`)를 그대로 사용한다.
Step 0의 `--source auto` 실행에서 이미 Pipeline 1이 완료되었으므로 별도 재실행 불필요.
단, `--stage pipeline1` 단독 실행이 필요한 경우:
```bash
cd push-campaign && python3 scripts/run.py --source {databricks|file} --stage pipeline1 --date {YYYY-MM-DD}
# 출력: push-campaign/data/pipeline1_output_{date}.csv
```

선별 기준 (순서대로 적용):
1. remarks에 취소 키워드 → 제외
2. 처리 이력 중복 → 제외
3. 시트 기입력 중복 → 제외
4. 마케팅팀(전사마케팅/카테고리마케팅) → 무조건 선별
5. release_start_date_time < send_dt 11:00 → 선별
6. 그 외 → 제외

완료 후 선별 통계 출력:
```
[Pipeline 1 완료]
전체 소재: {N}건
선별: {M}건 / 제외: {N-M}건
제외 사유: 취소={a}건, 미오픈={b}건, 중복={c}건
```

### Step 2: Pipeline 2~4 — 메시지 생성 + 검수 + Red Team
```bash
cd push-campaign && python3 scripts/run.py --stage all --date {YYYY-MM-DD}
# 입력: data/pipeline1_output_{date}.csv (Pipeline 1 결과)
# 출력: output/campaign_meta_{YYYYMMDD}_{timestamp}.csv
```

개별 파이프라인 단계 실행:
```bash
cd push-campaign && python3 scripts/run.py --stage pipeline2 --date {YYYY-MM-DD}
cd push-campaign && python3 scripts/run.py --stage pipeline3 --date {YYYY-MM-DD}
cd push-campaign && python3 scripts/run.py --stage pipeline4 --date {YYYY-MM-DD}
```

Pipeline 2 처리 (소재별):
1. Rule-based 컬럼 생성 (send_dt, target, priority, content_type, brand_id, goods_id, ad_code 등)
2. 제목 적합성 판단 → 부적합 시 LLM 재생성
3. 단일 contents 생성 (LLM — 혜택·감성 균형)

> Pipeline 2는 5건마다 체크포인트를 저장한다. 중단 후 재실행 시 자동으로 이어서 처리.

Pipeline 3 처리 (검수 검증 QA):
- 필수 필드 누락, title 길이, (광고) 접두어, 본문 길이, UTM 정합성, 0% 차단 등 검증
- 행 제거 없이 `[검수용]` 컬럼에 이슈 기록

Pipeline 4 처리 (LLM Red Team):
- 생성 규칙과 독립된 관점에서 LLM이 재검토
- `review_score`(1.0~5.0), `review_verdict`(pass/warning/fail) 컬럼 추가
- warning/fail → `[검수용] needs_review` 플래그 상향

### Step 3: 검수 요약 보고
스킬 완료 후 다음 형식으로 사용자에게 보고:

```
[push-campaign 완료]

📊 처리 결과:
  전체 소재: {N}건 → 선별: {M}건
  LLM 생성 성공: {K}건 / 실패: {N-K}건
  검수 필요: {P}건  (낮은 confidence / validation 이슈 / Red Team warning·fail)

📁 산출물:
  output/campaign_meta_{YYYYMMDD}_{timestamp}.csv
  logs/run_{timestamp}.json

⚠️ 검수 필요 항목: [{목록}]
```

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| Databricks 연결 실패 (종료코드 10) | 사용자에게 파일 fallback 확인 요청 (Step 0-B) |
| Databricks 환경변수 미설정 (종료코드 10) | 사용자에게 파일 fallback 확인 요청 (Step 0-B) |
| 파일 모드 + bizest_raw.csv 없음 | "파일 없음" 안내 후 중단, 업로드 요청 |
| 필수 입력 파일 없음 (brand_list.csv 등) | 오류 메시지 + 파일 위치 안내 후 중단 |
| 선별 소재 0건 | 경고 출력 후 빈 CSV 생성 |
| Claude API 3회 연속 실패 | `error_flag=True` 표시, 해당 소재만 스킵 |
| 잘못된 날짜 형식 | 형식 오류 안내 (YYYY-MM-DD 요구) |
| Pipeline 2 중단 후 재실행 | 체크포인트 자동 로드, 이어서 처리 |

---

## 참조 문서

- 소재 선별 정책: `push-campaign/references/selection_policy.md`
- 메시지 생성 정책: `push-campaign/references/message_policy.md`
- 무신사 브랜드 가이드: `push-campaign/references/brand_guidelines.md`
- 오케스트레이터: `CLAUDE.md`
