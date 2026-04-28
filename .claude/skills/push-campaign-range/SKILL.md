# /push-campaign-range — 기간별 앱푸시 캠페인 일괄 실행

## 모델

`claude-sonnet-4-6`

## 개요

지정된 날짜 범위에 대해 `run.py --from ... --to ...` (range 모드)를 실행한다.
Pipeline 1은 날짜 간 중복을 제거하며 통합 선별 리포트를 생성하고,
Pipeline 2~4는 전체 선별 소재를 한 번에 처리해 단일 campaign_meta CSV를 출력한다.

> **날짜별 `--date` 반복 실행은 사용하지 않는다.**
> range 모드만이 날짜 간 중복 제거(same URL/ID 이중 발송 방지)를 보장한다.

---

## 사용법

```
/push-campaign-range --start 2026-04-27 --end 2026-05-07
/push-campaign-range 이번 주
/push-campaign-range 다음 주
/push-campaign-range 4월 4주차
```

---

## 실행 규칙

### Step 0: 날짜 범위 파싱

| 표현 | 해석 |
|------|------|
| `--start X --end Y` | X ~ Y 범위 (양 끝 포함) |
| `이번 주` | 이번 주 월요일 ~ 일요일 |
| `다음 주` | 다음 주 월요일 ~ 일요일 |
| `N월 M주차` | 해당 주 월요일 ~ 일요일 |

날짜 범위가 30일을 초과하면 의도 확인 후 진행.

파싱 결과를 `YYYY-MM-DD` 형식으로 확정한 뒤 다음 단계로 진행.

---

### Step 1: 입력 파일 확인

```python
INPUT_FILES = [
    "push-campaign/input/bizest_raw.csv",
    "push-campaign/input/brand_list.csv",
]
```

파일 누락 시 오류 메시지 출력 후 중단.

---

### Step 2: range 모드 첫 실행 (Pipeline 1)

```bash
cd push-campaign && python3 scripts/run.py --from {start} --to {end}
```

실행 결과로 두 파일이 생성된다:

| 파일 | 설명 |
|------|------|
| `data/pending_jobs_{df}_{dt}.json` | 선별된 소재 전체의 LLM 작업 목록 (날짜 정보 포함) |
| `output/selection_report_{df}_{dt}.csv` | 통합 선별 리포트 (`발송예정일` 컬럼) |

선별 소재 0건이면 경고 출력 후 종료.

---

### Step 3: LLM 응답 생성 (Claude Code 모드)

`pending_jobs_{df}_{dt}.json`을 읽고 각 job에 대해 메시지를 생성한 뒤
`data/llm_responses_{df}_{dt}.json`으로 저장한다.

#### pending_jobs 포맷 (range 모드)

```json
{
  "date_from": "2026-04-27",
  "date_to":   "2026-05-07",
  "jobs": [
    {
      "id": "...",
      "send_dt": "2026-04-27",
      "brand": "...",
      "promotion_content": "...",
      "target": "...",
      "content_type": "...",
      "original_title": "...",
      "needs_title_regen": false,
      "remarks": "..."
    }
  ]
}
```

> `send_dt` 필드가 단일 날짜 모드와의 차이점. 각 job이 어느 날짜 소재인지 명시.

#### 응답 규칙 (`writing_policy.md` 기준)

| 필드 | 규칙 |
|------|------|
| `title` | 5~40자, 명사형 종결, 브랜드명+정체성 |
| `title_source` | `"original"` (원본 유효) 또는 `"llm"` (재생성) |
| `contents` | `(광고) ` 시작, 25~60자, 명사형 종결, 수신거부 문구 제외 |
| `confidence` | 1.0~5.0 |
| `review_score` | 1.0~5.0 (pass ≥3.5 / warning 2.5~3.4 / fail ≤2.4) |
| `review_verdict` | `"pass"` \| `"warning"` \| `"fail"` |
| `review_notes` | 검토 의견 요약 |
| `review_issues` | 이슈 목록 (없으면 `[]`) |
| `category_codes` | 소재 내용 기반 카테고리 코드 최대 3개 (없으면 `[]`) |

#### 콜라보 소재 제목 강제 규칙

`promotion_content`나 `original_title`에 `X / x / ×` 패턴이 있으면:
- 제목에 양측 브랜드/아티스트 이름 **모두** 포함
- 본문에서 해당 이름들 **완전 배제**

```json
// 잘못된 예
"title": "오정규 협업 발매",   // 알리스 누락

// 올바른 예
"title": "알리스 x 오정규 콜라보 컬렉션"
```

#### 응답 파일 저장

```
push-campaign/data/llm_responses_{df}_{dt}.json
```

예시 (`{df}=20260427`, `{dt}=20260507`):
```
push-campaign/data/llm_responses_20260427_20260507.json
```

---

### Step 4: range 모드 재실행 (Pipeline 2~4)

응답 파일이 준비되면 동일한 명령을 다시 실행한다:

```bash
cd push-campaign && python3 scripts/run.py --from {start} --to {end}
```

스크립트가 `llm_responses_{df}_{dt}.json`을 자동 감지해 Pipeline 2~4를 완료하고
단일 통합 파일을 생성한다:

| 파일 | 설명 |
|------|------|
| `output/campaign_meta_{df}_{dt}_{ts}.csv` | 전 기간 통합 캠페인 메타 (날짜 오름차순) |
| `output/selection_report_{df}_{dt}.csv` | 통합 선별 리포트 (이미 생성됨) |

---

### Step 5: 통합 리포트 출력

```
[push-campaign-range 완료]
기간: {start} ~ {end} ({N}일)
============================================================
📊 선별 결과 (날짜별):
  {YYYY-MM-DD} ({요일}): 통과 {M}건 / URL탈락 {U}건
  ...

📊 전체 합계:
  총 선별:      {total}건
  LLM 생성:    {ok}건
  검수 필요:   {review}건

📁 산출물:
  캠페인 메타:  output/campaign_meta_{df}_{dt}_{ts}.csv
  선별 리포트:  output/selection_report_{df}_{dt}.csv
============================================================
```

---

## 에러 처리

| 상황 | 처리 |
|------|------|
| 날짜 범위 30일 초과 | 경고 후 확인 요청 |
| 입력 파일 없음 | 오류 메시지 출력 후 중단 |
| 전체 선별 소재 0건 | 경고 출력 후 종료 |
| 스크립트 오류 | 오류 메시지 출력, 원인 확인 요청 |

---

## 산출물 파일명 규칙

`{df}` = date_from (YYYYMMDD), `{dt}` = date_to (YYYYMMDD), `{ts}` = HHmmss

| 파일 | 경로 |
|------|------|
| 통합 캠페인 메타 | `output/campaign_meta_{df}_{dt}_{ts}.csv` |
| 통합 선별 리포트 | `output/selection_report_{df}_{dt}.csv` |
| pending_jobs | `data/pending_jobs_{df}_{dt}.json` |
| llm_responses | `data/llm_responses_{df}_{dt}.json` |

---

## 참조

- `/push-campaign`: 단일 날짜 실행
- `push-campaign/CLAUDE.md`: 에이전트 상세 설정 및 파이프라인 정책
- `push-campaign/references/writing_policy.md`: 메시지 작성 규칙
