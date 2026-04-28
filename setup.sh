#!/bin/bash
# 무신사 마케팅 자동화 플랫폼 — 설치 스크립트
# Usage: bash setup.sh

set -e

AGENT_DIR="match-salespush-automation/push-campaign"

echo "===================================================="
echo "  Musinsa Martech AI Platform — Setup"
echo "===================================================="

# ── Python 버전 확인 ────────────────────────────────────
echo ""
echo "[1/4] Python 버전 확인..."
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ 이 필요합니다."
    echo "  → https://www.python.org/downloads/"
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "ERROR: Python 3.10 이상이 필요합니다. (현재: $PY_VER)"
    exit 1
fi
echo "  ✅ Python $PY_VER"

# ── 의존성 설치 ─────────────────────────────────────────
echo ""
echo "[2/4] 의존성 설치..."
$PYTHON -m pip install -r "$AGENT_DIR/requirements.txt" --quiet
echo "  ✅ 패키지 설치 완료"

# ── .env 파일 생성 ──────────────────────────────────────
echo ""
echo "[3/4] 환경 설정..."
if [ ! -f "$AGENT_DIR/.env" ]; then
    cp "$AGENT_DIR/.env.example" "$AGENT_DIR/.env"
    echo "  ✅ .env 파일 생성됨 ($AGENT_DIR/.env)"
    echo "  ⚠️  ANTHROPIC_API_KEY를 .env에 입력하세요:"
    echo "       $AGENT_DIR/.env"
else
    echo "  ✅ .env 파일 이미 존재"
fi

# ── 디렉터리 생성 ───────────────────────────────────────
echo ""
echo "[4/4] 디렉터리 구조 생성..."
mkdir -p "$AGENT_DIR/input"
mkdir -p "$AGENT_DIR/data"
mkdir -p "$AGENT_DIR/output"
mkdir -p "$AGENT_DIR/logs"
echo "  ✅ input/ data/ output/ logs/ 생성 완료"

echo ""
echo "===================================================="
echo "  설치 완료!"
echo "===================================================="
echo ""
echo "다음 단계:"
echo "  1. API 키 설정: $AGENT_DIR/.env 에 ANTHROPIC_API_KEY 입력"
echo "  2. 입력 파일 준비:"
echo "       $AGENT_DIR/input/bizest_raw.csv    (필수)"
echo "       $AGENT_DIR/input/brand_list.csv    (필수)"
echo "       $AGENT_DIR/input/category_selector.csv  (선택)"
echo "  3. Claude Code에서 실행: /push-campaign"
echo ""
echo "입력 파일 컬럼 스펙: docs/input_spec.md"
echo "전체 문서: README.md"
echo "===================================================="
