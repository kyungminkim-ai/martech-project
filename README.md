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

`setup.sh` 가 Python 버전 확인, 패키지 설치, `.env` 파일 생성, 디렉터리 구조 생성을 자동으로 수행합니다.

설치 후 `.env` 에 API 키를 입력합니다:

```
# match-salespush-automation/push-campaign/.env
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 개요

마케터의 소재 선별·메시지 작성 공수를 줄이고, 발송 가능한 품질의 문구를 자동으로 생성한다.
채널별 캠페인이 독립적으로 동작하며, 루트 `CLAUDE.md`가 진입점이 된다.

---

## 아키텍처

```
martech-project/
├── CLAUDE.md                                   # 플랫폼 진입점 & 에이전트 라우팅
├── README.md                                   # 이 파일
├── setup.sh                                    # 원클릭 설치 스크립트
├── .python-version                             # Python 3.10+
├── .claude/
│   └── settings.json                           # Claude Code 권한 설정
└── match-salespush-automation/
    ├── push-campaign/                          # ✅ 앱푸시 캠페인 자동화
    │   ├── CLAUDE.md                           # 에이전트 오케스트레이터
    │   ├── .claude/
    │   │   ├── settings.json                   # 에이전트 Claude Code 권한
    │   │   └── skills/push-campaign/SKILL.md
    │   ├── scripts/                            # 4-phase pipeline 구현
    │   ├── references/                         # 선별·메시지·브랜드 정책
    │   ├── docs/                               # 플로우 다이어그램, 백로그
    │   ├── input/                              # 비제스트 RAW (gitignored)
    │   ├── output/                             # 캠페인메타엔진 CSV (gitignored)
    │   ├── data/                               # 파이프라인 중간 파일 (gitignored)
    │   └── logs/                               # 실행 로그 (gitignored)
    ├── email-campaign/                         # 🔲 이메일 캠페인 (미구현)
    └── sms-campaign/                           # 🔲 SMS 캠페인 (미구현)
```

---

## 에이전트 현황

| 에이전트 | 채널 | 스킬 | 상태 |
|---------|------|------|------|
| push-campaign | 앱푸시 | `/push-campaign` | ✅ 운영 중 |
| email-campaign | 이메일 | `/email-campaign` | 🔲 미구현 |
| sms-campaign | SMS | `/sms-campaign` | 🔲 미구현 |

---

## 공통 파이프라인 구조

모든 캠페인은 동일한 4-phase 구조를 따른다:

```
Pipeline 1 — 소재 선별     규칙 기반 필터링 (취소·중복·오픈 조건)
     ↓
Pipeline 2 — 소재 생성     Rule-based 메타데이터 + LLM 메시지 생성 (V1·V2·V3)
                           5건마다 체크포인트 저장 (중단 후 재실행 시 자동 이어서 처리)
     ↓
Pipeline 3 — 검수 검증     길이·형식·UTM·할인율·goods_id 등 QA 자동 검증
     ↓
Pipeline 4 — Red Team     독립적 LLM 재검토 → score(1~5) + verdict(pass/warning/fail)
     ↓
Output CSV              [검수용] 컬럼 포함 — 행 삭제 없이 플래그만 기록
```

> 결과물은 모두 `output/` 에 저장되며, 담당자가 `[검수용]` 컬럼을 확인 후 Braze에 등록한다.

---

## 앱푸시 캠페인 실행

### 1. 입력 파일 준비

```
match-salespush-automation/push-campaign/input/
├── bizest_raw.csv          # 비제스트 RAW — 소재 요청 원본 (필수)
├── brand_list.csv          # 브랜드 목록 (brand_id ↔ 브랜드명 매핑) (필수)
└── category_selector.csv   # 카테고리 코드 목록 (선택)
```

입력 파일 컬럼 스펙은 [match-salespush-automation/push-campaign/docs/flow_overview.md](match-salespush-automation/push-campaign/docs/flow_overview.md) 참조.

### 2. Claude Code에서 실행

```
/push-campaign --date 2026-05-01
```

또는 날짜 없이 실행하면 내일 날짜가 자동 적용됩니다:

```
/push-campaign
```

### 3. 결과 확인

```
match-salespush-automation/push-campaign/output/campaign_meta_{YYYYMMDD}_{timestamp}.csv
```

---

## 데이터 정책

실제 데이터 파일은 모두 gitignore 처리된다. 운영 데이터를 절대 커밋하지 않는다.

| 경로 | 설명 | Git |
|------|------|-----|
| `**/input/*.csv` | 소재 요청 원본 | ❌ 제외 |
| `**/output/` | 생성된 캠페인 CSV | ❌ 제외 |
| `**/data/` | 파이프라인 중간 파일 | ❌ 제외 |
| `**/logs/` | 실행 로그 | ❌ 제외 |
| `**/input/ad_code_seed.txt` | AD 코드 순번 시드 | ✅ 포함 |
| `.env` | API 키 | ❌ 제외 |

---

## Phase 로드맵

```
Phase 1 (완료)   push-campaign 운영
                  Pipeline 1·2·3·4 + 실행 로그
                  goods_id 자동 추출 (H-2)
                  Pipeline 2 체크포인트 (중단 후 재실행)

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

`match-salespush-automation/push-campaign/`를 참고 구현으로 삼아 동일한 디렉터리 구조와 4-phase pipeline을 따른다.
추가 시 루트 `CLAUDE.md` 라우팅 테이블과 이 파일의 에이전트 현황 표를 업데이트한다.
