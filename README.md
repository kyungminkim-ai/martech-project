# Martech AI Automation Platform

> 무신사 마케팅 자동화 플랫폼 — AI가 소재를 생성하고, 사람이 판단한다.

---

## 개요

마케터의 소재 선별·메시지 작성 공수를 줄이고, 발송 가능한 품질의 문구를 자동으로 생성한다.
채널별 에이전트가 독립적으로 동작하며, 루트 `CLAUDE.md`가 진입점이 된다.

---

## 아키텍처

```
martech-project/
├── CLAUDE.md                           # 플랫폼 진입점 & 에이전트 라우팅
├── README.md                           # 이 파일
├── .claude/
│   └── settings.json                   # Claude Code 권한 설정
└── ai-copywriting/
    ├── match-push-agent/               # ✅ 앱푸시 캠페인 자동화
    │   ├── CLAUDE.md                   # 에이전트 오케스트레이터
    │   ├── .claude/skills/push-campaign/SKILL.md
    │   ├── scripts/                    # 4-phase pipeline 구현
    │   ├── references/                 # 선별·메시지·브랜드 정책
    │   ├── input/                      # 비제스트 RAW (gitignored)
    │   ├── output/                     # 캠페인메타엔진 CSV (gitignored)
    │   ├── data/                       # 파이프라인 중간 파일 (gitignored)
    │   └── logs/                       # 실행 로그 (gitignored)
    ├── email-agent/                    # 🔲 이메일 캠페인 (미구현)
    └── sms-agent/                      # 🔲 SMS 캠페인 (미구현)
```

---

## 에이전트 현황

| 에이전트 | 채널 | 스킬 | 상태 |
|---------|------|------|------|
| match-push-agent | 앱푸시 | `/push-campaign` | ✅ 운영 중 |
| email-agent | 이메일 | `/email-campaign` | 🔲 미구현 |
| sms-agent | SMS | `/sms-campaign` | 🔲 미구현 |

---

## 공통 파이프라인 구조

모든 에이전트는 동일한 4-phase 구조를 따른다:

```
Pipeline 1 — 소재 선별     규칙 기반 필터링 (취소·중복·오픈 조건)
     ↓
Pipeline 2 — 소재 생성     Rule-based 메타데이터 + LLM 메시지 생성 (V1·V2)
     ↓
Pipeline 3 — 검수 검증     길이·형식·UTM·할인율 등 QA 자동 검증
     ↓
Pipeline 4 — Red Team     독립적 LLM 재검토 → score(1~5) + verdict(pass/warning/fail)
     ↓
Output CSV              [검수용] 컬럼 포함 — 행 삭제 없이 플래그만 기록
```

> 결과물은 모두 `output/` 에 저장되며, 담당자가 `[검수용]` 컬럼을 확인 후 Braze에 등록한다.

---

## 빠른 시작 — 앱푸시 캠페인

### 1. 환경 설정

```bash
cd ai-copywriting/match-push-agent
pip install -r requirements.txt
cp .env.example .env          # ANTHROPIC_API_KEY 입력
```

### 2. 입력 파일 준비

```
input/bizest_raw.csv          # 비제스트 RAW (필수)
input/brand_list.csv          # 브랜드 목록 (필수)
input/category_selector.csv   # 카테고리 코드 (선택)
```

### 3. 실행 (Claude Code)

```
/push-campaign --date 2026-05-01
```

또는 날짜 없이 실행하면 내일 날짜가 자동 적용됩니다:

```
/push-campaign
```

### 4. 결과 확인

```
output/campaign_meta_{YYYYMMDD}_{timestamp}.csv
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
Phase 1 (현재)   match-push-agent 운영
                  Pipeline 1·2·3·4 + 실행 로그

Phase 1.5        H-1 contents 길이 검증
                  H-2 goods_id 자동 추출
                  H-3 이미지 URL 유효성 검사

Phase 2          Google Spreadsheet 연동
                  Slack 검수 알림
                  이메일·SMS 에이전트 추가

Phase 3          Databricks 연동 (성과 피드백 루프)
                  3-variant 자동 선택
                  브랜드별 가이드라인 개인화
```

---

## 신규 에이전트 추가

`ai-copywriting/match-push-agent/`를 참고 구현으로 삼아 동일한 디렉터리 구조와 4-phase pipeline을 따른다.
추가 시 루트 `CLAUDE.md` 라우팅 테이블과 이 파일의 에이전트 현황 표를 업데이트한다.
