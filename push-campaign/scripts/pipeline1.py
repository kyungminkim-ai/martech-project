"""Pipeline 1 — 소재 선별 로직."""
import logging
from typing import Dict, Optional, Set, Tuple
import pandas as pd
from config import (
    CAMPAIGN_META_SYNC_PATH, AD_STATUS_COLUMN,
    GOOGLE_SHEET_ID, GOOGLE_SHEET_CREDS, GOOGLE_SHEET_CAMPAIGN_META_GID,
    GOOGLE_SHEETS_ENABLED,
)
from rules import is_cancelled, is_in_send_window, is_already_selected, make_sheet_key, validate_landing_url
from run_logger import (
    REASON_CANCELLED, REASON_ALREADY_SELECTED,
    REASON_ALREADY_PROC, REASON_URL_ALREADY_SENT,
    REASON_CAMPAIGN_META_REGISTERED, REASON_SHEET_DUPLICATE,
    REASON_LANDING_NOT_OPEN, REASON_LANDING_OPEN,
)

_CAMPAIGN_TEAM_KEYWORD = "전사캠페인"

logger = logging.getLogger(__name__)


def load_campaign_meta_sync() -> Tuple[Dict[str, str], Set[str]]:
    """campaign_meta_sync 데이터 로드 — Google Sheets 우선, 실패 시 로컬 파일 폴백.

    이 데이터가 크로스세션 중복 방지의 유일한 기준이다.

    Returns:
        url_map:      {landing_url: ad_code} — 일반 소재 중복 방지
        date_url_set: {"landing_url|send_dt"} — 전사캠페인 날짜+URL 중복 방지
    """
    df: Optional[pd.DataFrame] = None

    if GOOGLE_SHEETS_ENABLED:
        from gsheets import read_sheet_as_dataframe
        df = read_sheet_as_dataframe(
            spreadsheet_id=GOOGLE_SHEET_ID,
            sheet_gid=GOOGLE_SHEET_CAMPAIGN_META_GID,
            creds_path=GOOGLE_SHEET_CREDS,
        )
        if df is not None:
            logger.info(f"campaign_meta_sync GSheets 로드: {len(df)}건 (gid={GOOGLE_SHEET_CAMPAIGN_META_GID})")
        else:
            logger.warning("campaign_meta_sync GSheets 읽기 실패 — 로컬 파일 폴백")

    if df is None:
        try:
            df = pd.read_csv(CAMPAIGN_META_SYNC_PATH, dtype=str)
            logger.info(f"campaign_meta_sync 로컬 파일 로드: {len(df)}건 ({CAMPAIGN_META_SYNC_PATH})")
        except FileNotFoundError:
            logger.info(f"campaign_meta_sync 파일 없음: {CAMPAIGN_META_SYNC_PATH} — 중복 없음으로 처리")
            return {}, set()

    df = df.dropna(subset=["landing_url"]).copy()
    df["landing_url"] = df["landing_url"].str.strip()

    ad_codes = df["ad_code"].fillna("") if "ad_code" in df.columns else pd.Series("", index=df.index)
    url_map  = dict(zip(df["landing_url"], ad_codes))

    date_url_set: Set[str] = set()
    if "send_dt" in df.columns:
        df["send_dt"] = df["send_dt"].fillna("").str.strip()
        date_url_set  = set(df["landing_url"] + "|" + df["send_dt"])

    return url_map, date_url_set


def load_campaign_meta_map() -> Dict[str, str]:
    """하위 호환 alias — url_map만 반환."""
    url_map, _ = load_campaign_meta_sync()
    return url_map


def load_sheet_keys(sheet_df: pd.DataFrame, send_dt: str) -> Set[str]:
    if sheet_df is None or sheet_df.empty:
        return set()
    keys = (
        sheet_df.get("landing_url", pd.Series(dtype=str)).fillna("").astype(str)
        + "|"
        + sheet_df.get("brand_id", pd.Series(dtype=str)).fillna("").astype(str)
        + "|"
        + send_dt
    )
    return set(keys.tolist())


def apply_selection(
    df: pd.DataFrame,
    send_dt: str,
    campaign_meta_map: Dict[str, str],
    sheet_keys: Set[str],
    extra_processed_ids: Set[str] = None,
    extra_processed_urls: Set[str] = None,
    databricks_mode: bool = False,
    campaign_meta_date_url_set: Set[str] = None,
) -> pd.DataFrame:
    """각 행에 selected, selection_reason, rejection_reason, rejection_detail 컬럼을 추가.

    일반 소재 중복 판단 우선순위:
      1. 취소 여부
      2. 선정 여부 (광고진행 계열) — 파일 모드 전용
      3. 캠페인메타엔진 등록 URL — 유일한 크로스세션 중복 기준
      4. 동일 실행 내 id/URL 중복 (기간·주간 배치 전용)
      5. 시트 기반 당일 중복
      6. 발송 윈도우 (D-1 10:00 ~ D-0 10:00)
      7. 랜딩 URL 유효성

    전사캠페인 소재 (register_team_name에 '전사캠페인' 포함):
      - 조건 1(취소)만 적용 후, 동일 landing_url+send_dt 조합이 campaign_meta_sync에
        없으면 무조건 선별 — 발송 윈도우·URL 검증 등 나머지 조건 모두 생략.
    """
    eff_intra_ids  = extra_processed_ids  or set()
    eff_intra_urls = extra_processed_urls or set()
    eff_date_url   = campaign_meta_date_url_set or set()

    results = []

    for _, row in df.iterrows():
        row_id    = str(row.get("id", ""))
        team_name = str(row.get("register_team_name", "") or "")
        remarks   = str(row.get("remarks", "") or "")
        release   = row.get("release_start_date_time")
        brand_id  = str(row.get("sourceBrandId", "") or "")
        url       = str(row.get("landing_url", "") or "").strip()
        ad_status = row.get(AD_STATUS_COLUMN)
        sheet_key = make_sheet_key(url, brand_id, send_dt)

        # ── 전사캠페인 전용 로직 ───────────────────────────────────────────
        if _CAMPAIGN_TEAM_KEYWORD in team_name:
            # 조건 1: 취소 여부 (전사캠페인도 취소는 제외)
            if is_cancelled(remarks):
                results.append({
                    "selected": False,
                    "selection_reason": None,
                    "rejection_reason": REASON_CANCELLED,
                    "rejection_detail": f"remarks={remarks[:80]}",
                })
                continue

            # 조건 3': 동일 landing_url + send_dt 조합이 이미 등록돼 있으면 제외
            date_url_key = f"{url}|{send_dt}"
            if url and date_url_key in eff_date_url:
                prev_ad_code = campaign_meta_map.get(url, "")
                detail = f"url={url[:120]} | send_dt={send_dt}"
                if prev_ad_code:
                    detail += f" | ad_code={prev_ad_code}"
                results.append({
                    "selected": False,
                    "selection_reason": None,
                    "rejection_reason": REASON_CAMPAIGN_META_REGISTERED,
                    "rejection_detail": detail,
                })
                continue

            # 나머지 조건 생략 → 무조건 선별
            results.append({
                "selected": True,
                "selection_reason": REASON_LANDING_OPEN,
                "rejection_reason": None,
                "rejection_detail": None,
            })
            continue

        # ── 일반 소재 로직 ────────────────────────────────────────────────

        # 조건 1: 취소 여부
        if is_cancelled(remarks):
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_CANCELLED,
                "rejection_detail": f"remarks={remarks[:80]}",
            })
            continue

        # 조건 2: 선정 여부 = 광고진행 계열 (파일 모드 전용)
        if not databricks_mode and is_already_selected(ad_status):
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_ALREADY_SELECTED,
                "rejection_detail": f"ad_status={str(ad_status)[:40]}",
            })
            continue

        # 조건 3: 캠페인메타엔진 등록 URL (유일한 크로스세션 중복 기준)
        if url and url in campaign_meta_map:
            prev_ad_code = campaign_meta_map.get(url, "")
            detail = f"url={url[:120]}"
            if prev_ad_code:
                detail += f" | ad_code={prev_ad_code}"
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_CAMPAIGN_META_REGISTERED,
                "rejection_detail": detail,
            })
            continue

        # 조건 4-1: 동일 실행 내 id 중복 (기간/주간 배치 전용)
        if row_id in eff_intra_ids:
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_ALREADY_PROC,
                "rejection_detail": f"id={row_id}",
            })
            continue

        # 조건 4-2: 동일 실행 내 URL 중복 (기간/주간 배치 전용)
        if url and url in eff_intra_urls:
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_URL_ALREADY_SENT,
                "rejection_detail": f"url={url[:120]}",
            })
            continue

        # 조건 5: 시트 기반 당일 중복
        if sheet_key in sheet_keys:
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_SHEET_DUPLICATE,
                "rejection_detail": f"key={sheet_key[:120]}",
            })
            continue

        # 조건 6: 발송 윈도우 (D-1 10:00 ~ D-0 10:00)
        if not is_in_send_window(release, send_dt):
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": REASON_LANDING_NOT_OPEN,
                "rejection_detail": f"release={str(release)[:30]}",
            })
            continue

        # 조건 7: 랜딩 URL 유효성 검증
        url_reason = validate_landing_url(url)
        if url_reason:
            results.append({
                "selected": False,
                "selection_reason": None,
                "rejection_reason": url_reason,
                "rejection_detail": f"url={url[:120]}",
            })
        else:
            results.append({
                "selected": True,
                "selection_reason": REASON_LANDING_OPEN,
                "rejection_reason": None,
                "rejection_detail": None,
            })

    return pd.concat([df, pd.DataFrame(results, index=df.index)], axis=1)


def run_pipeline1(
    raw_df: pd.DataFrame,
    send_dt: str,
    campaign_meta_map: Dict[str, str] = None,
    sheet_df: pd.DataFrame = None,
    extra_processed_ids: Set[str] = None,
    extra_processed_urls: Set[str] = None,
    databricks_mode: bool = False,
    campaign_meta_date_url_set: Set[str] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """소재 선별 실행.

    Args:
        campaign_meta_map:          {landing_url: ad_code} 맵. None이면 자동 로드.
        campaign_meta_date_url_set: {"landing_url|send_dt"} 셋. 전사캠페인 중복 판단에 사용.
                                    None이면 campaign_meta_map과 함께 자동 로드.
        extra_processed_ids:        기간/주간 배치 시 이전 날 선별된 id set.
        extra_processed_urls:       기간/주간 배치 시 이전 날 선별된 url set.
        databricks_mode:            True이면 ad_status 체크(조건 2)를 skip한다.
    Returns:
        (selected_df, rejected_df)
    """
    logger.info(
        f"Pipeline 1 시작 — send_dt={send_dt}, 전체={len(raw_df)}건"
        f"{' [Databricks 모드]' if databricks_mode else ' [파일 모드]'}"
    )

    if campaign_meta_map is None or campaign_meta_date_url_set is None:
        _url_map, _date_url_set = load_campaign_meta_sync()
        if campaign_meta_map is None:
            campaign_meta_map = _url_map
        if campaign_meta_date_url_set is None:
            campaign_meta_date_url_set = _date_url_set

    sheet_keys = load_sheet_keys(sheet_df, send_dt)

    result_df = apply_selection(
        raw_df, send_dt, campaign_meta_map, sheet_keys,
        extra_processed_ids, extra_processed_urls,
        databricks_mode=databricks_mode,
        campaign_meta_date_url_set=campaign_meta_date_url_set,
    )

    selected_mask = result_df["selected"] == True
    selected_df   = result_df[selected_mask].copy().reset_index(drop=True)
    rejected_df   = result_df[~selected_mask].copy().reset_index(drop=True)

    logger.info(f"선별: {len(selected_df)}건 / 탈락: {len(rejected_df)}건")
    if len(rejected_df):
        reason_counts = rejected_df["rejection_reason"].value_counts()
        for reason, cnt in reason_counts.items():
            logger.info(f"  탈락 사유 [{reason}]: {cnt}건")

    return selected_df, rejected_df
