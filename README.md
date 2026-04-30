# Martech AI Automation Platform

> 무신사 마케팅 자동화 플랫폼 — AI가 소재를 생성하고, 사람이 판단한다.

---

## 프로젝트 소개

이 플랫폼은 무신사 마케팅팀이 실제 운영에 활용하는 AI 캠페인 자동화 시스템으로, **김경민 PM이 직접 설계·구현**한 프로토타입이다.

현재는 앱푸시 캠페인 소재 선별 및 메시지 생성에 집중되어 있으며, 운영 검증을 거쳐 **MAI(Marketing AI Assistant)** 에 통합하는 것을 궁극적인 목표로 한다. MAI는 마케터가 Claude Code 또는 전용 인터페이스를 통해 마케팅 작업 전반을 AI와 협업하는 인프라다.

```
현재 (로컬 CLI)               →   목표 (MAI 통합)
  Claude Code /push-campaign       MAI에서 채널별 자동화 일괄 실행
  수동 환경 설치 필요               마케팅팀 누구나 즉시 사용 가능
  앱푸시 단일 채널                  이메일 · SMS · 랜딩페이지 등 확장
```

---

## 에이전트 현황

| 에이전트 | 채널 | 스킬 | 상태 | 문서 |
|---------|------|------|------|------|
| push-campaign | 앱푸시 | `/push-campaign` | ✅ 운영 중 | [push-campaign/README.md](push-campaign/README.md) |
| email-campaign | 이메일 | `/email-campaign` | 🔲 미구현 | — |
| sms-campaign | SMS | `/sms-campaign` | 🔲 미구현 | — |

---

## Prerequisites

| 항목 | 버전 / 설명 |
|------|------------|
| [Claude Code CLI](https://claude.ai/download) | 최신 버전 |
| Python | 3.10 이상 |
| [Anthropic API Key](https://console.anthropic.com) | claude-sonnet-4-6 접근 권한 |
| Databricks PAT Token | 무신사 VPN 연결 + 공용 WS에서 발급 |
| Google Sheets 서비스 계정 JSON | 결과 자동 업로드 시 필요 (선택) |

---

## 1. 설치

```bash
git clone <this-repo>
cd martech-project
bash setup.sh
```

설치 후 `.env` 에 API 키를 입력한다:

```bash
# push-campaign/.env
ANTHROPIC_API_KEY=sk-ant-...
```

---

## 2. Databricks MCP 연동

Databricks에서 비제스트 RAW 데이터를 직접 조회하려면 MCP Server를 등록해야 한다.

### 사전 조건

- 무신사 VPN 연결 (필수)
- Databricks 쿼리 권한 (계정 신청 + 스키마 권한 신청 완료)
- Databricks PAT Token — [공용 WS](https://musinsa-data-ws.cloud.databricks.com/?o=3626753574208338)에서 발급

### MCP Server 등록

터미널에서 한 번 실행하면 등록이 완료된다:

```bash
claude mcp add databricks-mcp \
  --transport http https://mcp.data.musinsa.com/databricks/mcp \
  --header "X-Databricks-Token: <YOUR_PAT_TOKEN>" \
  -s user
```

등록 확인:

```bash
# Claude Code 재시작 후
/mcp
# databricks-mcp: connected 이면 완료
```

### 제한사항

| 항목 | 내용 |
|------|------|
| 권한 | SELECT, DESCRIBE, SHOW만 허용 — DDL/DML 차단 |
| 파티션 키 | WHERE 절에 파티션/클러스터링 키 필수 포함 |
| 결과 행 수 | 최대 100,000건 (초과 시 truncated) |
| Timeout | 최대 180초 |

### PAT Token 재등록

```bash
claude mcp remove databricks-mcp -s user
claude mcp add databricks-mcp \
  --transport http https://mcp.data.musinsa.com/databricks/mcp \
  --header "X-Databricks-Token: <NEW_PAT_TOKEN>" \
  -s user
```

### 연결 문제 해결

```bash
# VPN 연결 확인 후 서버 상태 테스트
curl -s https://mcp.data.musinsa.com/databricks/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Databricks-Token: <YOUR_PAT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

정상 응답 시 `serverInfo`가 포함된 JSON이 반환된다.

> 문의: Slack **#tech-문의-데이터** 채널

---

## 3. Google Sheets 연동

선별 리포트, 캠페인 메타, 비제스트 RAW를 Google Sheets에 자동 업로드하려면 서비스 계정을 설정한다.

### 서비스 계정 준비

1. [Google Cloud Console](https://console.cloud.google.com) → IAM → 서비스 계정 생성
2. Google Sheets API 활성화
3. 서비스 계정 JSON 키 다운로드
4. 해당 서비스 계정 이메일을 대상 스프레드시트에 편집자로 공유

### 환경변수 설정

```bash
# push-campaign/.env

GOOGLE_SHEET_ID=1FrE7ZIXiYuJEsMvFw_JTNedmYojNOW0xLv8courdr38
GOOGLE_SHEET_GID=1466233062               # 선별 리포트 탭
GOOGLE_SHEET_CAMPAIGN_GID=0               # 캠페인 메타 탭
GOOGLE_SHEET_CAMPAIGN_META_GID=315655952  # campaign_meta_sync 탭
GOOGLE_SHEET_BIZEST_GID=578734437         # bizest_raw 탭
GOOGLE_SHEET_CREDS_PATH=/path/to/service_account.json
```

Google Sheets 미연동 시에도 동작한다. 결과 CSV는 `push-campaign/output/`에 로컬 저장된다.

---

## 4. 전체 환경변수 설정

```bash
# push-campaign/.env (전체)

# ── 필수 ──────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── Databricks (선택 — 설정 시 bizest_raw 자동 수집) ──
DATABRICKS_HOST=adb-xxxx.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxxx
DATABRICKS_TOKEN=dapi...

# ── Google Sheets (선택 — 설정 시 결과 자동 업로드) ──
GOOGLE_SHEET_ID=...
GOOGLE_SHEET_GID=...
GOOGLE_SHEET_CAMPAIGN_GID=...
GOOGLE_SHEET_CAMPAIGN_META_GID=...
GOOGLE_SHEET_BIZEST_GID=578734437
GOOGLE_SHEET_CREDS_PATH=...
```

---

## 5. 빠른 실행

### 앱푸시 캠페인

```bash
# 단일 날짜
/push-campaign --date 2026-05-01

# 날짜 범위 (range 모드 — 중복 자동 제거)
/push-campaign-range --start 2026-04-27 --end 2026-05-07
/push-campaign-range 이번 주
/push-campaign-range 다음 주

# 기존 selection_report에서 P2~5 재실행
/push-campaign --from-selection-report output/selection_report_20260427_20260507.csv
```

날짜 미지정 시 내일 날짜가 자동 적용된다.

---

## 데이터 정책

운영 데이터는 모두 gitignore 처리된다. 커밋 금지.

| 경로 | Git |
|------|-----|
| `**/input/*.csv` | ❌ 제외 |
| `**/output/` | ❌ 제외 |
| `**/data/` | ❌ 제외 |
| `**/logs/` | ❌ 제외 |
| `**/input/ad_code_seed.txt` | ✅ 포함 (중복 발급 방지) |
| `.env` | ❌ 제외 |

---

## 신규 캠페인 에이전트 추가

`push-campaign/`을 참고 구현으로 삼아 동일한 4-phase pipeline 구조를 따른다:

1. `{channel}-campaign/` 디렉터리 생성
2. 선별 → 생성 → 검수 → Red Team 4-phase 구조 유지
3. `.claude/skills/{channel}-campaign/SKILL.md` 등록
4. 이 파일 에이전트 현황 표 + `CLAUDE.md` 라우팅 테이블 업데이트

---

## 프로젝트 구조

```
martech-project/
├── CLAUDE.md                          # 플랫폼 진입점 & 에이전트 라우팅
├── README.md                          # 이 파일 — 설치 & 연동 가이드
├── setup.sh                           # 원클릭 설치 스크립트
├── .claude/
│   ├── settings.json                  # Claude Code 권한 설정
│   └── skills/
│       └── push-campaign-range/       # /push-campaign-range 스킬
└── push-campaign/                     # ✅ 앱푸시 캠페인 자동화
    ├── README.md                      # 선별 정책·메시지 원칙·운영 가이드
    ├── CLAUDE.md                      # 에이전트 오케스트레이터
    ├── scripts/                       # 5-phase pipeline Python 구현
    ├── references/                    # 정책 문서 (selection/message/writing policy)
    └── docs/                          # 시스템 플로우 상세, 개선 백로그
```
