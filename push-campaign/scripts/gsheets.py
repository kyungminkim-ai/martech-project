"""Google Sheets 연동 — DataFrame을 지정 워크시트에 전체 덮어쓰기."""
import logging
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_GSHEETS_AVAILABLE = False
try:
    import gspread
    from google.oauth2.service_account import Credentials
    _GSHEETS_AVAILABLE = True
except ImportError:
    pass

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_client(creds_path: str):
    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def upload_to_sheet(
    df: pd.DataFrame,
    spreadsheet_id: str,
    sheet_gid: int,
    creds_path: str,
) -> bool:
    """DataFrame으로 Google Sheets 워크시트를 전체 덮어쓰기(update).

    시트를 clear 후 헤더 + 데이터를 새로 씀.
    실패 시 로그 경고만 남기고 False를 반환 — 메인 파이프라인은 계속 실행된다.
    """
    if not _GSHEETS_AVAILABLE:
        logger.warning(
            "gspread 미설치 — Google Sheets 업로드 건너뜀. "
            "pip install gspread google-auth 실행 후 재시도."
        )
        return False

    if not spreadsheet_id or not creds_path:
        logger.warning("GOOGLE_SHEET_ID 또는 GOOGLE_SHEET_CREDS_PATH 미설정 — 업로드 건너뜀")
        return False

    try:
        client = _get_client(creds_path)
        worksheet = client.open_by_key(spreadsheet_id).get_worksheet_by_id(sheet_gid)

        rows = [df.columns.tolist()] + df.fillna("").astype(str).values.tolist()
        worksheet.clear()
        worksheet.update(rows, value_input_option="RAW")

        logger.info(f"Google Sheets 업데이트 완료 (gid={sheet_gid}): {len(rows) - 1}건")
        return True

    except Exception as e:
        logger.warning(f"Google Sheets 업로드 실패 (gid={sheet_gid}): {e}")
        return False


def read_sheet_as_dataframe(
    spreadsheet_id: str,
    sheet_gid: int,
    creds_path: str,
) -> "Optional[pd.DataFrame]":
    """Google Sheets 워크시트를 DataFrame으로 읽기.

    성공 시 DataFrame 반환, 실패(미설치·설정 누락·네트워크 오류 등) 시 None 반환.
    호출부에서 None을 받으면 로컬 파일 폴백을 수행해야 한다.
    """
    if not _GSHEETS_AVAILABLE:
        logger.warning("gspread 미설치 — GSheets 읽기 불가")
        return None
    if not spreadsheet_id or not creds_path:
        return None
    try:
        client = _get_client(creds_path)
        worksheet = client.open_by_key(spreadsheet_id).get_worksheet_by_id(sheet_gid)
        data = worksheet.get_all_values()
        if not data:
            return pd.DataFrame()
        header = data[0]
        rows   = data[1:]
        return pd.DataFrame(rows, columns=header)
    except Exception as e:
        logger.warning(f"Google Sheets 읽기 실패 (gid={sheet_gid}): {e}")
        return None


# 하위 호환 alias
def upload_selection_report(
    df: pd.DataFrame,
    spreadsheet_id: str,
    sheet_gid: int,
    creds_path: str,
    send_dts: Optional[List[str]] = None,  # 미사용 — 시그니처 호환용
) -> bool:
    return upload_to_sheet(df, spreadsheet_id, sheet_gid, creds_path)
