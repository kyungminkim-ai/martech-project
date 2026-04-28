# Martech AI Automation Platform

## 역할

무신사 마케팅 자동화 플랫폼의 AI 운영 진입점.
채널별 에이전트 스킬을 호출하고, 결과를 통합 보고하며, 신규 역량이 필요하면 설계를 제안한다.

---

## 라우팅 테이블

| 요청 유형 | 스킬 / 에이전트 |
|---------|----------------|
| 앱푸시 소재 선별 & 캠페인 메시지 생성 | Skill: `/push-campaign` |
| 앱푸시 에이전트 상세 설정 조회 | `match-salespush-automation/push-campaign/CLAUDE.md` |
| [향후] 이메일 캠페인 소재 생성 | 미구현 — 요청 시 설계 제안 |
| [향후] SMS 캠페인 소재 생성 | 미구현 — 요청 시 설계 제안 |
| [향후] 랜딩페이지 헤드라인 생성 | 미구현 — 요청 시 설계 제안 |

---

## 에이전트 디렉터리

| 에이전트 | 경로 | 상태 |
|---------|------|------|
| push-campaign | `match-salespush-automation/push-campaign/` | ✅ 운영 중 |
| email-campaign | `match-salespush-automation/email-campaign/` | 🔲 미구현 |
| sms-campaign | `match-salespush-automation/sms-campaign/` | 🔲 미구현 |

---

## 모델 선택

기본은 `claude-sonnet-4-6`. 등록된 스킬이 별도 모델을 지정한 경우 해당 스킬 지침을 따른다.

| 상황 | 모델 |
|------|------|
| 등록된 스킬이 모델을 지정한 경우 | 스킬 지침 따름 |
| 스킬 없는 일반 요청 | `claude-sonnet-4-6` |

---

## 에스컬레이션 규칙

| 상황 | 처리 |
|------|------|
| 요청이 모호함 | 핵심 의도 1개로 좁혀 확인 후 진행 |
| 담당 에이전트 없음 | "현재 [X] 역량이 없습니다. 새 에이전트를 설계할까요?" |
| 오류 발생 | 에이전트별 CLAUDE.md 오류 처리 섹션 참조 |

---

## 신규 에이전트 추가 컨벤션

새 채널 에이전트를 추가할 때 반드시 따라야 할 순서:

1. `match-salespush-automation/{channel}-campaign/` 디렉터리 생성
2. 4-phase pipeline 구조 유지 (선별 → 생성 → 검수 → Red Team)
3. `match-salespush-automation/{channel}-campaign/.claude/skills/{channel}-campaign/SKILL.md` 등록
4. 이 파일 라우팅 테이블 & 에이전트 디렉터리 업데이트
5. 루트 `README.md` 에이전트 현황 업데이트

참고 구현: `match-salespush-automation/push-campaign/`
