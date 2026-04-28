"""설정 파일 — 환경변수 및 상수 관리."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_MODEL = "claude-sonnet-4-6"
LLM_MAX_TOKENS = 512
LLM_MAX_RETRIES = 3

# Databricks (Phase 2+ 연동 시 사용)
DATABRICKS_HOST      = os.getenv("DATABRICKS_HOST", "")
DATABRICKS_HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")
DATABRICKS_TOKEN     = os.getenv("DATABRICKS_TOKEN", "")

# Google Sheets (Phase 2+ 연동 시 사용)
GOOGLE_SHEET_ID      = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SHEET_CREDS   = os.getenv("GOOGLE_SHEET_CREDS_PATH", "")

# 소재 선별 설정
MARKETING_TEAM_KEYWORDS = ["전사캠페인", "카테고리마케팅"]
CANCEL_KEYWORDS         = ["취소", "CANCEL", "cancel"]
SEND_HOUR               = 10  # 발송 윈도우 종료 시각 (D-0 10:00 KST)
SEND_WINDOW_START_HOUR  = 10  # 발송 윈도우 시작 시각 (D-1 10:00 KST)

# 광고 코드 설정
AD_CODE_PREFIX = "APSCMCD"
AD_CODE_SEED_FILE = BASE_DIR / "input" / "ad_code_seed.txt"

# 파일 경로
INPUT_DIR   = BASE_DIR / "input"
OUTPUT_DIR  = BASE_DIR / "output"
DATA_DIR    = BASE_DIR / "data"
LOGS_DIR    = BASE_DIR / "logs"

BIZEST_RAW_PATH        = INPUT_DIR / "bizest_raw.csv"
BRAND_LIST_PATH        = INPUT_DIR / "brand_list.csv"
CATEGORY_SEL_PATH      = INPUT_DIR / "category_selector.csv"
CAMPAIGN_META_SYNC_PATH = INPUT_DIR / "campaign_meta_sync.csv"
PROCESSED_IDS_PATH     = DATA_DIR / "processed_ids.csv"

# 제목 적합성 기준
TITLE_MIN_LEN = 5
TITLE_MAX_LEN = 40

# 본문 길이 기준 (수신거부 문구·(광고) 접두어 제외 순수 본문 기준)
CONTENTS_MIN_LEN = 25
CONTENTS_MAX_LEN = 60
MEANINGLESS_TITLES = {"테스트", "123", "123123", "asdf", "test", "제목", ""}

# 수신거부 문구
UNSUBSCRIBE_TEXT = "수신거부 : 메인 상단 알림 > 설정 > 알림 OFF"

# LLM confidence 임계값 (미만이면 검수 필요 플래그)
CONFIDENCE_THRESHOLD = 3.0

# 소재 선별 윈도우 (발송일로부터 N일 이내에 오픈된 소재만 선별)
SEND_WINDOW_DAYS = 1

# 선정 여부 컬럼명 (CSV 2행 헤더 로드 후 리네임된 이름)
AD_STATUS_COLUMN = "ad_status"

# 발송 이력 파일 (URL 기반 중복 방지)
PROCESSED_URLS_PATH = DATA_DIR / "processed_urls.csv"

# 선별 리포트 출력 경로
SELECTION_REPORT_DIR = OUTPUT_DIR

# LLM 모드: API 키가 있으면 API 호출, 없으면 Claude Code 파일 모드
LLM_API_AVAILABLE = bool(ANTHROPIC_API_KEY)


def get_pending_jobs_path(send_dt: str) -> Path:
    return DATA_DIR / f"pending_jobs_{send_dt.replace('-', '')}.json"


def get_responses_path(send_dt: str) -> Path:
    return DATA_DIR / f"llm_responses_{send_dt.replace('-', '')}.json"


def get_pipeline2_checkpoint_path(send_dt: str) -> Path:
    return DATA_DIR / f"pipeline2_checkpoint_{send_dt.replace('-', '')}.csv"
