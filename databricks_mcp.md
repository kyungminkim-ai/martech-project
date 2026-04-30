Databricks MCP Server 연동 가이드



작성자 장성우/Tech Data Platform seongwoo.jang

111

thumbs up
clapping hands
smiling face with hearts
6
Prerequisites
설정 방법
1. Claude Code에 MCP Server 추가
2. Claude Code 재시작 및 연결 확인
제한사항
Workspace
읽기 전용
파티션/클러스터링 키 필수
결과 행 수 제한
Timeout 제한
트러블슈팅
연결이 안 되는 경우
PAT Token 관련
주의사항
문의
References
Prerequisites
MCP Server 연동을 위해 아래 사항이 준비되어야 한다.

Databricks 쿼리 권한(필수): 계정 신청, 스키마 권한 신청 두개가 완료되어야 한다. 

네트워크 (필수): 무신사 VPN

AI 툴(Claude Code): 로컬에 설치되어 있어야 한다

Databricks PAT Token: Databricks Workspace에서 발급한 Personal Access Token이 필요하다. 공용 WS에 접속해 발급한다. 발급 방법은 Databricks PAT 발급 가이드를 참고한다.

 

설정 방법
1. Claude Code에 MCP Server 추가
터미널에서 아래 명령어를 실행한다. <YOUR_PAT_TOKEN>을 본인의 Databricks PAT Token으로 교체한다.

MCP Server 등록 (한 줄 명령어)


claude mcp add databricks-mcp --transport http https://mcp.data.musinsa.com/databricks/mcp --header "X-Databricks-Token: <YOUR_PAT_TOKEN>" -s user
위 명령어 하나로 MCP Server 등록이 완료된다. 별도의 OAuth 인증 절차는 없다.

2. Claude Code 재시작 및 연결 확인
Claude Code를 재시작한 뒤 /mcp 명령어로 MCP Server 목록을 확인한다. databricks-mcp가 connected 상태이면 연결 성공이다.

 

제한사항
Workspace
현재 공용 WS에서만 사용 가능하도록 되어 있다. 반드시 아래 링크로 접속한 후 개인 토큰을 발급해 설정을 진행한다. 

https://musinsa-data-ws.cloud.databricks.com/?o=3626753574208338

 

읽기 전용
SELECT, DESCRIBE, SHOW 쿼리만 실행할 수 있다. DDL/DML 쿼리는 실행 전에 자동으로 차단된다.

허용

차단

SELECT, UNION, INTERSECT, EXCEPT

INSERT, UPDATE, DELETE, MERGE

DESCRIBE TABLE, DESCRIBE DETAIL

CREATE TABLE, ALTER TABLE

SHOW TABLES, SHOW COLUMNS

DROP TABLE, TRUNCATE

파티션/클러스터링 키 필수
파티션 키 또는 클러스터링 키가 설정된 테이블에 쿼리할 때, WHERE 절에 해당 키를 반드시 포함해야 한다. 미포함 시 쿼리가 차단되며 안내 메시지가 반환된다.

파티션 키 미사용 시 차단 예시


-- ❌ 차단됨 (파티션 키 target_date 미사용)
SELECT * FROM admin.cost.agent_runs LIMIT 10
-- ✅ 정상 실행 (파티션 키 포함)
SELECT * FROM admin.cost.agent_runs WHERE target_date = '2025-01-01' LIMIT 10
테이블의 파티션/클러스터링 키를 모른다면 describe_table 도구로 먼저 확인한다.

결과 행 수 제한
한 번의 쿼리로 최대 100,000건까지 반환된다. 초과 시 결과가 잘리며 truncated: true와 안내 메시지가 함께 반환된다. 필요 시 WHERE 조건을 추가하거나 LIMIT을 사용한다.

 

Timeout 제한
한 번의 쿼리는 최대 180초까지 실행 가능해 이 이상의 긴 쿼리는 쿼리를 개선해서 실행할 수 있도록 한다. 

 

트러블슈팅
연결이 안 되는 경우
VPN 연결 확인: 무신사 VPN이 활성화되어 있는지 확인한다. VPN 미연결 시 접속이 차단된다.

curl 테스트: 터미널에서 아래 명령어로 서버 상태를 확인한다.

서버 상태 확인


curl -s https://mcp.data.musinsa.com/databricks/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Databricks-Token: <YOUR_PAT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
정상 응답 시 serverInfo가 포함된 JSON이 반환된다.

DNS 확인: nslookup mcp.data.musinsa.com으로 ALB DNS가 정상 resolve 되는지 확인한다.

 

PAT Token 관련
인증 실패: PAT Token이 만료되었거나 잘못된 경우 쿼리 실행 시 인증 오류가 반환된다. Databricks Workspace에서 새 토큰을 발급받아 재등록한다.

토큰 재등록: 기존 MCP 설정을 삭제하고 다시 등록한다.

MCP Server 재등록


# 기존 등록 삭제
claude mcp remove databricks-mcp -s user
# 새 토큰으로 재등록
claude mcp add databricks-mcp --transport http https://mcp.data.musinsa.com/databricks/mcp --header "X-Databricks-Token: <NEW_PAT_TOKEN>" -s user
 

주의사항
무신사 VPN을 통해서만 접근 가능하다. 외부 네트워크에서는 접속할 수 없다.

토큰은 반드시 공용 WS에서 발급한다. 

각 사용자는 본인의 Databricks PAT Token으로 인증한다. Token에 부여된 Workspace 권한에 따라 접근 가능한 Catalog/Schema/Table이 달라진다.

읽기 전용 모드로 운영된다. DDL/DML은 차단되며, 파티션 키 미사용 쿼리도 차단된다.

PAT Token은 만료 기한이 있다. 만료 시 Databricks Workspace에서 새 토큰을 발급받아 재등록해야 한다.

 

문의
추가 문의는 Slack #tech-문의-데이터 채널로 문의주세요. 

 

References
Databricks Personal Access Token 발급 가이드

Claude Code MCP 설정 가이드

FastMCP 공식 문서

GTM MCP Server 연동 가이드 (같은 팀 MCP 가이드 참고)

MWAA MCP Server 로컬 연동 가이드