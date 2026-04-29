#!/usr/bin/env python3
"""
메인 실행 스크립트 — 소재 선별 + 메시지 생성 + 검수 검증 + 캠페인메타엔진 시트 출력.

사용법:
    python3 scripts/run.py --date 2026-05-01
    python3 scripts/run.py --date 2026-05-01 --input input/my_raw.csv
    python3 scripts/run.py --stage pipeline1 --date 2026-05-01
    python3 scripts/run.py --stage pipeline2 --date 2026-05-01
"""
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 스크립트 디렉터리를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    BIZEST_RAW_PATH, BRAND_LIST_PATH, CATEGORY_SEL_PATH, OUTPUT_DIR, DATA_DIR,
    LLM_API_AVAILABLE, get_pending_jobs_path, get_responses_path,
    PROCESSED_URLS_PATH, AD_STATUS_COLUMN, MAX_PER_DATE,
    DATABRICKS_CONFIGURED, DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN,
    BIZEST_SQL_PATH, EXIT_DATABRICKS_UNAVAILABLE,
    GOOGLE_SHEET_ID, GOOGLE_SHEET_CREDS, GOOGLE_SHEET_GID, GOOGLE_SHEET_CAMPAIGN_GID,
    GOOGLE_SHEETS_ENABLED,
)
from gsheets import upload_to_sheet as _gsheets_upload
from pipeline1 import load_campaign_meta_sync, run_pipeline1
import llm_client
from pipeline2 import run_pipeline2
from pipeline3 import run_pipeline3
from pipeline4 import run_pipeline4
from pipeline5 import run_pipeline5
from rules import is_title_valid, lookup_brand_name
from run_logger import RunLogger, URL_REJECTION_CODES, REASON_CAMPAIGN_META_REGISTERED

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# 캠페인메타엔진 운영 시트 컬럼 순서 (target CSV와 동일)
OUTPUT_COLUMNS = [
    "id",
    "send_dt", "send_time", "target", "priority", "ad_code",
    "content_type", "goods_id", "category_id", "brand_id", "team_id",
    "braze_campaign_name", "title", "contents", "landing_url", "image_url",
    "push_url", "feed_url", "webhook_contents",
    # 검수용 컬럼 (담당자 검토용 — Braze 등록 시 제외)
    "[검수용] brand_nm_verified",
    "[검수용] title_source", "[검수용] confidence",
    "[검수용] content_nature", "[검수용] benefit_type",
    "[검수용] error_flag", "[검수용] needs_review",
    "[검수용] validation_notes",
    "[검수용] review_score", "[검수용] review_verdict",
    "[검수용] review_notes", "[검수용] review_issues",
]

# rename_map: pipeline 내부 컬럼명 → 출력 CSV 컬럼명
_OUTPUT_RENAME_MAP = {
    "brand_nm_verified": "[검수용] brand_nm_verified",
    "title_source":      "[검수용] title_source",
    "confidence":        "[검수용] confidence",
    "content_nature":    "[검수용] content_nature",
    "benefit_type":      "[검수용] benefit_type",
    "error_flag":        "[검수용] error_flag",
    "needs_review":      "[검수용] needs_review",
    "validation_notes":  "[검수용] validation_notes",
    "review_score":      "[검수용] review_score",
    "review_verdict":    "[검수용] review_verdict",
    "review_notes":      "[검수용] review_notes",
    "review_issues":     "[검수용] review_issues",
}

# pending_jobs JSON에 포함되는 LLM 작업 지침 (단일 날짜 & 범위 공통)
_PENDING_JOBS_MESSAGE_RULES = {
    "title":          "5~40자, 명사형 종결, 브랜드명+정체성 표현 (행동어 금지)",
    "contents":       "(광고) 시작, 25~60자, 혜택·감성 균형, 명사형 종결",
    "review":         "score 1-5, verdict: pass≥3.5/warning 2.5-3.4/fail≤2.4",
    "category_codes": "소재 내용 기반 카테고리 코드 최대 3개 (없으면 빈 리스트)",
}

def _build_pending_instructions(response_filename: str) -> str:
    return (
        "각 job에 대해 title/contents/review/category_codes를 생성하고 "
        f"{response_filename}으로 저장하세요. "
        "수신거부 문구는 포함하지 마세요 (Python이 별도 추가). "
        "응답 형식: {\"<id>\": {\"title\": ..., \"title_source\": \"llm\"|\"original\", "
        "\"contents\": \"(광고) ...\", \"confidence\": 4.0, "
        "\"review_score\": 4.0, \"review_verdict\": \"pass\"|\"warning\"|\"fail\", "
        "\"review_notes\": \"\", \"review_issues\": [], "
        "\"category_codes\": [\"코드1\", \"코드2\"]}}"
    )


def _load_bizest_raw(path: Path) -> pd.DataFrame:
    """비제스트 RAW CSV 로드 — 2행 헤더(한국어+영문) 자동 감지.
    'Unnamed: 0' (한국어: 선정 여부) → AD_STATUS_COLUMN 으로 리네임.
    """
    df = pd.read_csv(path, dtype=str, nrows=2)
    if "id" not in df.columns and any(str(v) == "id" for v in df.iloc[0].values):
        df = pd.read_csv(path, header=1, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)
    if "id" in df.columns:
        df["id"] = df["id"].astype(str)
    # 선정 여부 컬럼 이름 정규화
    if "Unnamed: 0" in df.columns:
        df = df.rename(columns={"Unnamed: 0": AD_STATUS_COLUMN})
    return df


def _check_databricks() -> bool:
    """Databricks 연결 가능 여부를 가벼운 쿼리로 확인."""
    try:
        from databricks import sql as dbsql
        conn = dbsql.connect(
            server_hostname=DATABRICKS_HOST,
            http_path=DATABRICKS_HTTP_PATH,
            access_token=DATABRICKS_TOKEN,
        )
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"Databricks 연결 실패: {e}")
        return False


def _fetch_bizest_from_databricks(send_dt: str) -> pd.DataFrame:
    """Databricks에서 bizest RAW 데이터를 날짜별로 조회."""
    from databricks import sql as dbsql

    query_template = BIZEST_SQL_PATH.read_text(encoding="utf-8")
    query = query_template.replace("{send_dt}", send_dt)

    logger.info(f"Databricks 조회 시작: send_dt={send_dt}")
    conn = dbsql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        df = pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()

    logger.info(f"Databricks 조회 완료: {len(df)}건")
    return df


def load_inputs(raw_path: Path, brand_path: Path, source: str = "file"):
    if source == "file":
        if not raw_path.exists():
            logger.error(f"비제스트 RAW 파일 없음: {raw_path}")
            sys.exit(1)
        raw_df = _load_bizest_raw(raw_path)
        logger.info(f"비제스트 RAW 로드 (파일): {len(raw_df)}건 ({raw_path})")
    else:
        # source == "databricks" — 날짜는 호출 후 raw_df["send_dt"]로 설정됨
        # (날짜별 루프에서 send_dt를 별도로 넘기므로 여기선 빈 DataFrame 반환)
        raw_df = pd.DataFrame()
        logger.info("Databricks 모드: 날짜별 루프에서 직접 조회")

    brand_df = pd.DataFrame()
    if brand_path.exists():
        brand_df = pd.read_csv(brand_path, dtype={"brand_id": str})
        logger.info(f"브랜드 목록 로드: {len(brand_df)}건")
    else:
        logger.warning(f"브랜드 목록 없음: {brand_path} — 브랜드명 ID로 대체")

    category_df = pd.DataFrame()
    if CATEGORY_SEL_PATH.exists():
        try:
            category_df = pd.read_csv(CATEGORY_SEL_PATH, header=1, dtype=str)
            category_df = category_df.dropna(subset=["구분", "코드"])
            logger.info(f"카테고리 목록 로드: {len(category_df)}건")
        except Exception as e:
            logger.warning(f"카테고리 목록 로드 실패: {e}")
    else:
        logger.warning(f"카테고리 목록 없음: {CATEGORY_SEL_PATH}")

    return raw_df, brand_df, category_df


def save_pipeline1(selected_df: pd.DataFrame, send_dt: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"pipeline1_output_{send_dt.replace('-', '')}.csv"
    selected_df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"Pipeline 1 출력 저장: {path}")
    return path


# 선별 리포트에 포함할 컬럼 (선별/탈락 모두)
_SELECTION_REPORT_COLS = [
    "selected", "id", "send_dt", AD_STATUS_COLUMN, "register_team_name", "sourceBrandId",
    "main_title", "landing_url", "release_start_date_time",
    "requested_start_date_time", "remarks",
    "selection_reason", "rejection_reason", "rejection_detail",
]

# 기간/주단위 통합 후보군 리포트 컬럼 (send_dt → 발송예정일)
_WEEKLY_REPORT_COLS = [
    "selected", "id", "발송예정일", "register_team_name", "sourceBrandId",
    "main_title", "landing_url", "release_start_date_time",
    "selection_reason", "rejection_reason", "rejection_detail",
]

# selection_report 정렬 우선순위: 통과 → CAMPAIGN_META_REGISTERED → URL탈락
_REPORT_INCLUDE_CODES = URL_REJECTION_CODES | {REASON_CAMPAIGN_META_REGISTERED}


def _report_sort_key(row) -> int:
    if row.get("selected"):
        return 0
    if row.get("rejection_reason") == REASON_CAMPAIGN_META_REGISTERED:
        return 1
    return 2


def _dedup_campaign_meta_registered(df: pd.DataFrame) -> pd.DataFrame:
    """CAMPAIGN_META_REGISTERED 탈락 항목을 id당 1건으로 압축.

    release_start_date_time과 발송예정일이 일치하는 행 우선, 없으면 가장 이른 날짜.
    """
    if df.empty or "rejection_reason" not in df.columns:
        return df
    meta_mask = df["rejection_reason"] == REASON_CAMPAIGN_META_REGISTERED
    if not meta_mask.any():
        return df

    meta_df = df[meta_mask].copy()
    other_df = df[~meta_mask]

    date_col = "발송예정일" if "발송예정일" in meta_df.columns else "send_dt"
    if "release_start_date_time" in meta_df.columns:
        meta_df["_rel_date"] = meta_df["release_start_date_time"].astype(str).str[:10]
        meta_df["_match"] = meta_df[date_col].astype(str) == meta_df["_rel_date"]
        deduped = (
            meta_df
            .sort_values(["_match", date_col], ascending=[False, True])
            .drop_duplicates(subset=["id"], keep="first")
            .drop(columns=["_rel_date", "_match"])
        )
    else:
        deduped = meta_df.drop_duplicates(subset=["id"], keep="first")

    return pd.concat([other_df, deduped], ignore_index=True)


def save_selection_report(
    selected_df: pd.DataFrame,
    rejected_df: pd.DataFrame,
    send_dt: str,
) -> Path:
    """선별+윈도우탈락+URL탈락 전체를 output/selection_report_{date}.csv 로 저장.

    정렬: 통과(selected=True) → CAMPAIGN_META_REGISTERED → URL탈락 → 기타 탈락
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    combined = pd.concat([selected_df, rejected_df], ignore_index=True)
    combined["_sort"] = combined.apply(_report_sort_key, axis=1)
    combined = combined.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    cols = [c for c in _SELECTION_REPORT_COLS if c in combined.columns]
    path = OUTPUT_DIR / f"selection_report_{send_dt.replace('-', '')}.csv"
    combined[cols].to_csv(path, index=False, encoding="utf-8-sig")

    n_url = int(
        rejected_df["rejection_reason"].isin(URL_REJECTION_CODES).sum()
    ) if not rejected_df.empty and "rejection_reason" in rejected_df.columns else 0
    logger.info(
        f"선별 리포트 저장: {path} "
        f"(통과={len(selected_df)}, URL탈락={n_url}, 기타탈락={len(rejected_df)-n_url})"
    )
    return path


def _upload_report_to_gsheets(df: pd.DataFrame) -> None:
    """선별 리포트를 Google Sheets에 전체 덮어쓰기. 설정 미비 시 조용히 건너뜀."""
    if not GOOGLE_SHEETS_ENABLED:
        return
    _gsheets_upload(df=df, spreadsheet_id=GOOGLE_SHEET_ID,
                    sheet_gid=GOOGLE_SHEET_GID, creds_path=GOOGLE_SHEET_CREDS)


def _upload_campaign_meta_to_gsheets(df: pd.DataFrame) -> None:
    """캠페인 메타 최종 산출물을 Google Sheets(gid=0)에 전체 덮어쓰기. 설정 미비 시 조용히 건너뜀."""
    if not GOOGLE_SHEETS_ENABLED:
        return
    out = df.rename(columns=_OUTPUT_RENAME_MAP)
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    _gsheets_upload(df=out[OUTPUT_COLUMNS], spreadsheet_id=GOOGLE_SHEET_ID,
                    sheet_gid=GOOGLE_SHEET_CAMPAIGN_GID, creds_path=GOOGLE_SHEET_CREDS)


def save_weekly_report(all_candidates: list, week_start: str) -> Path:
    """주단위 후보군 리포트 — 발송 윈도우 통과 전체 (통과+URL탈락), 날짜 순 정렬."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not all_candidates:
        logger.warning("주단위 선별: 후보군 0건")
        return None

    merged = pd.concat(all_candidates, ignore_index=True)
    merged = merged.rename(columns={"send_dt": "발송예정일"})
    merged = _dedup_campaign_meta_registered(merged)
    merged["_sort"] = merged.apply(
        lambda r: (r.get("발송예정일", ""), _report_sort_key(r)), axis=1
    )
    merged = merged.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)

    cols = [c for c in _WEEKLY_REPORT_COLS if c in merged.columns]
    path = OUTPUT_DIR / f"selection_report_week_{week_start.replace('-', '')}.csv"
    merged[cols].to_csv(path, index=False, encoding="utf-8-sig")

    n_pass = int(merged["selected"].sum()) if "selected" in merged.columns else 0
    n_fail = len(merged) - n_pass
    logger.info(f"주단위 후보군 리포트 저장: {path} (통과={n_pass}건, URL탈락={n_fail}건)")
    return path


def run_weekly(week_dates: list, raw_path: Path, brand_path: Path) -> None:
    """주단위 소재 선별 — 날짜간 중복 제거 후 통합 후보군 리포트 생성."""
    raw_df_base, _, _ = load_inputs(raw_path, brand_path)
    week_start = week_dates[0]

    campaign_meta_map, campaign_meta_date_url_set = load_campaign_meta_sync()

    all_candidates = []       # 윈도우 통과 전체 (통과 + CAMPAIGN_META_REGISTERED + URL탈락)
    week_selected_ids:  set = set()
    week_selected_urls: set = set()

    print(f"\n{'='*60}")
    print(f"[주단위 선별] {week_dates[0]} ~ {week_dates[-1]}")
    print(f"{'='*60}\n")

    for send_dt in week_dates:
        raw_df = raw_df_base.copy()
        raw_df["send_dt"] = send_dt

        selected_df, rejected_df = run_pipeline1(
            raw_df, send_dt,
            campaign_meta_map=campaign_meta_map,
            campaign_meta_date_url_set=campaign_meta_date_url_set,
            extra_processed_ids=week_selected_ids,
            extra_processed_urls=week_selected_urls,
        )

        # 리포트 포함 탈락 항목 (CAMPAIGN_META_REGISTERED + URL탈락)
        report_rejected_df = rejected_df[
            rejected_df["rejection_reason"].isin(_REPORT_INCLUDE_CODES)
        ].copy() if not rejected_df.empty and "rejection_reason" in rejected_df.columns else pd.DataFrame()

        url_rejected_df = rejected_df[
            rejected_df["rejection_reason"].isin(URL_REJECTION_CODES)
        ].copy() if not rejected_df.empty and "rejection_reason" in rejected_df.columns else pd.DataFrame()

        n_pass = len(selected_df)
        n_url  = len(url_rejected_df)
        day_name = ["월", "화", "수", "목", "금", "토", "일"][
            datetime.strptime(send_dt, "%Y-%m-%d").weekday()
        ]
        print(f"  {send_dt} ({day_name}) → 통과 {n_pass}건 / URL탈락 {n_url}건")

        if n_pass:
            for _, r in selected_df.iterrows():
                print(f"    ✅ {r.get('register_team_name','')} | {str(r.get('main_title',''))[:30]}")
            ids  = selected_df["id"].dropna().astype(str).tolist()
            urls = selected_df["landing_url"].dropna().str.strip().tolist()
            week_selected_ids  |= set(ids)
            week_selected_urls |= set(u for u in urls if u)

        if n_url:
            for _, r in url_rejected_df.iterrows():
                print(f"    ❌ [{r.get('rejection_reason','')}] {r.get('register_team_name','')} | {str(r.get('main_title',''))[:30]}")

        # 후보군 수집 (통과 + CAMPAIGN_META_REGISTERED + URL탈락)
        day_candidates = []
        if n_pass:
            day_candidates.append(selected_df)
        if not report_rejected_df.empty:
            day_candidates.append(report_rejected_df)
        if day_candidates:
            all_candidates.append(pd.concat(day_candidates, ignore_index=True))

    weekly_path = save_weekly_report(all_candidates, week_start)

    total_pass  = sum(
        int(df["selected"].sum()) for df in all_candidates
        if "selected" in df.columns
    )
    total_total = sum(len(df) for df in all_candidates)
    print(f"\n{'='*60}")
    print(f"총 후보군: {total_total}건 (통과={total_pass}건 / URL탈락={total_total - total_pass}건)")
    if weekly_path:
        print(f"📁 통합 리포트: {weekly_path}")
    print(f"{'='*60}\n")


def save_processed_urls(selected_df: pd.DataFrame, send_dt: str) -> None:
    """완료된 소재의 landing_url을 processed_urls.csv에 누적 저장."""
    if "landing_url" not in selected_df.columns:
        return
    urls = selected_df["landing_url"].dropna().str.strip()
    urls = urls[urls != ""].unique()
    if len(urls) == 0:
        return

    now = datetime.now().isoformat()
    new_rows = pd.DataFrame({
        "landing_url": urls,
        "send_dt":     send_dt,
        "processed_at": now,
    })

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if PROCESSED_URLS_PATH.exists():
        existing = pd.read_csv(PROCESSED_URLS_PATH, dtype=str)
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset=["landing_url"], keep="first")
    else:
        combined = new_rows

    combined.to_csv(PROCESSED_URLS_PATH, index=False, encoding="utf-8-sig")
    logger.info(f"processed_urls 갱신: {len(urls)}건 추가 → 누적 {len(combined)}건")


def save_final(result_df: pd.DataFrame, send_dt: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%H%M%S")
    date = send_dt.replace("-", "")
    path = OUTPUT_DIR / f"campaign_meta_{date}_{ts}.csv"

    out = result_df.rename(columns=_OUTPUT_RENAME_MAP)
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    out[OUTPUT_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"최종 산출물 저장: {path}")
    return path


def generate_pending_jobs(selected_df: pd.DataFrame, brand_df: pd.DataFrame, send_dt: str) -> Path:
    """API 키 없을 때 Claude Code가 처리할 LLM 작업 목록을 JSON으로 생성."""
    jobs = []
    for _, row in selected_df.iterrows():
        brand_id = str(row.get("sourceBrandId", "") or "")
        brand    = lookup_brand_name(brand_id, brand_df)
        orig_title = str(row.get("main_title", "") or "")
        remarks_raw = str(row.get("remarks", "") or "")
        jobs.append({
            "id":                str(row.get("id", "")),
            "brand":             brand,
            "promotion_content": str(row.get("promotion_content", "") or ""),
            "target":            str(row.get("register_team_name", "") or ""),
            "content_type":      str(row.get("landing_url", "") or ""),
            "original_title":    orig_title,
            "needs_title_regen": not is_title_valid(orig_title),
            "remarks":           remarks_raw,
        })

    response_filename = f"llm_responses_{send_dt.replace('-', '')}.json"
    payload = {
        "send_dt":   send_dt,
        "total":     len(jobs),
        "generated_at": datetime.now().isoformat(),
        "instructions": _build_pending_instructions(response_filename),
        "message_rules": _PENDING_JOBS_MESSAGE_RULES,
        "jobs": jobs,
    }

    jobs_path = get_pending_jobs_path(send_dt)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"Pending jobs 저장: {jobs_path} ({len(jobs)}건)")
    return jobs_path


def print_summary(result_df: pd.DataFrame, send_dt: str, output_path: Path,
                  selection_report_path: Path = None):
    total   = len(result_df)
    v1_ok   = result_df["contents"].notna().sum() if "contents" in result_df.columns else 0
    err_col = "[검수용] error_flag" if "[검수용] error_flag" in result_df.columns else "error_flag"
    rev_col = "[검수용] needs_review" if "[검수용] needs_review" in result_df.columns else "needs_review"
    errors  = int(result_df[err_col].sum()) if err_col in result_df.columns else 0
    reviews = int(result_df[rev_col].sum()) if rev_col in result_df.columns else 0

    needs_review_ids = []
    flag_col = "[검수용] needs_review" if "[검수용] needs_review" in result_df.columns else "needs_review"
    if flag_col in result_df.columns:
        needs_review_ids = result_df[result_df[flag_col] == True]["id"].tolist()

    print("\n" + "="*60)
    print(f"[push-campaign 완료] send_dt={send_dt}")
    print("="*60)
    print(f"\n📊 처리 결과:")
    print(f"  선별 소재:      {total}건")
    print(f"  LLM 생성 성공: {v1_ok}건")
    print(f"  오류:          {errors}건")
    print(f"  검수 필요:     {reviews}건")
    print(f"\n📁 산출물:")
    print(f"  캠페인 메타:  {output_path}")
    if selection_report_path:
        print(f"  선별 리포트:  {selection_report_path}")
    if needs_review_ids:
        print(f"\n⚠️  검수 필요 항목 (id): {needs_review_ids[:10]}")
        if len(needs_review_ids) > 10:
            print(f"   ... 외 {len(needs_review_ids)-10}건")
    print("="*60 + "\n")


def _dates_in_range(date_from: str, date_to: str) -> list:
    """date_from ~ date_to 사이 날짜 목록 반환 (양 끝 포함)."""
    d_from = datetime.strptime(date_from, "%Y-%m-%d")
    d_to   = datetime.strptime(date_to,   "%Y-%m-%d")
    n_days = (d_to - d_from).days + 1
    if n_days <= 0:
        raise ValueError(f"--from({date_from}) 이 --to({date_to}) 보다 늦습니다.")
    return [(d_from + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days)]


def save_final_range(result_df: pd.DataFrame, date_from: str, date_to: str) -> Path:
    """기간 범위 통합 캠페인 메타 저장 — send_dt 컬럼으로 날짜 구분."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%H%M%S")
    d_f   = date_from.replace("-", "")
    d_t   = date_to.replace("-", "")
    path  = OUTPUT_DIR / f"campaign_meta_{d_f}_{d_t}_{ts}.csv"

    out = result_df.rename(columns=_OUTPUT_RENAME_MAP)
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    out = out.sort_values("send_dt").reset_index(drop=True)
    out[OUTPUT_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"기간 범위 통합 산출물 저장: {path} ({len(out)}건)")
    return path


def run_range(
    date_from: str,
    date_to: str,
    raw_path: Path,
    brand_path: Path,
    source: str = "file",
) -> None:
    """기간 범위 전체 파이프라인 실행 — P1(날짜별 선별+중복제거) → P2~P4(통합) → 단일 파일 출력."""
    databricks_mode = (source == "databricks")
    dates = _dates_in_range(date_from, date_to)
    raw_df_base, brand_df, category_df = load_inputs(raw_path, brand_path, source=source)

    campaign_meta_map, campaign_meta_date_url_set = load_campaign_meta_sync()

    all_selected:   list = []
    all_rejected:   list = []
    range_ids:      set  = set()
    range_urls:     set  = set()

    print(f"\n{'='*60}")
    print(f"[기간 범위 선별] {date_from} ~ {date_to} ({len(dates)}일)")
    print(f"{'='*60}\n")

    # ── Phase 1: 날짜별 Pipeline 1 ──────────────────────────────────────
    run_log = RunLogger(send_dt=f"{date_from}~{date_to}", input_file=str(raw_path))

    for send_dt in dates:
        if databricks_mode:
            raw_df = _fetch_bizest_from_databricks(send_dt)
            raw_df["send_dt"] = send_dt
        else:
            raw_df = raw_df_base.copy()
            raw_df["send_dt"] = send_dt

        selected_df, rejected_df = run_pipeline1(
            raw_df, send_dt,
            campaign_meta_map=campaign_meta_map,
            campaign_meta_date_url_set=campaign_meta_date_url_set,
            extra_processed_ids=range_ids,
            extra_processed_urls=range_urls,
            databricks_mode=databricks_mode,
        )

        day_name = ["월", "화", "수", "목", "금", "토", "일"][
            datetime.strptime(send_dt, "%Y-%m-%d").weekday()
        ]
        print(f"  {send_dt} ({day_name}) → 통과 {len(selected_df)}건 / 탈락 {len(rejected_df)}건")

        if len(selected_df):
            range_ids  |= set(selected_df["id"].dropna().astype(str))
            range_urls |= set(
                u for u in selected_df["landing_url"].dropna().str.strip() if u
            )
            all_selected.append(selected_df)
        all_rejected.append(rejected_df)

    if not all_selected:
        print("\n⚠️  선별된 소재 0건 — 처리 종료")
        return

    combined_selected = pd.concat(all_selected, ignore_index=True)
    combined_rejected = pd.concat(all_rejected, ignore_index=True)
    run_log.record_pipeline1(combined_selected, combined_rejected)

    # 날짜별 선별 리포트 저장 (기간 후보군 통합)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    d_f = date_from.replace("-", "")
    d_t = date_to.replace("-", "")
    # 통과 + CAMPAIGN_META_REGISTERED + URL탈락 포함 후보군 리포트
    report_rejected = combined_rejected[
        combined_rejected["rejection_reason"].isin(_REPORT_INCLUDE_CODES)
    ] if "rejection_reason" in combined_rejected.columns else pd.DataFrame()
    candidate_df = pd.concat([combined_selected, report_rejected], ignore_index=True)
    if not candidate_df.empty:
        candidate_df = candidate_df.rename(columns={"send_dt": "발송예정일"})
        candidate_df = _dedup_campaign_meta_registered(candidate_df)
        candidate_df["_sort"] = candidate_df.apply(
            lambda r: (r.get("발송예정일", ""), _report_sort_key(r)), axis=1
        )
        candidate_df = candidate_df.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
        candidate_cols = [c for c in _WEEKLY_REPORT_COLS if c in candidate_df.columns]
        cand_path = OUTPUT_DIR / f"selection_report_{d_f}_{d_t}.csv"
        candidate_df[candidate_cols].to_csv(cand_path, index=False, encoding="utf-8-sig")
        logger.info(f"기간 범위 후보군 리포트: {cand_path}")
        _upload_report_to_gsheets(candidate_df[candidate_cols])

    total_selected = len(combined_selected)
    print(f"\n  → 총 통과: {total_selected}건 ({date_from}~{date_to})")

    # ── Phase 2: API 키 없을 때 응답 파일 자동 탐지 → 없으면 pending_jobs 생성 ──
    if not LLM_API_AVAILABLE and llm_client._file_responses is None:
        responses_path = get_responses_path(f"{d_f}_{d_t}")
        if responses_path.exists():
            with open(responses_path, encoding="utf-8") as f:
                llm_client.init_file_mode(json.load(f))
            logger.info(f"Claude Code 모드 — 범위 응답 파일 로드: {responses_path}")

    if not LLM_API_AVAILABLE and llm_client._file_responses is None:
        # 응답 파일 없음 → pending_jobs 생성 후 중단
        range_key = f"{d_f}_{d_t}"
        jobs_path = get_pending_jobs_path(range_key)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        jobs = []
        for _, row in combined_selected.iterrows():
            brand_id = str(row.get("sourceBrandId", "") or "")
            brand    = lookup_brand_name(brand_id, brand_df)
            orig_title = str(row.get("main_title", "") or "")
            remarks_raw = str(row.get("remarks", "") or "")
            jobs.append({
                "id":                str(row.get("id", "")),
                "send_dt":           str(row.get("send_dt", "")),
                "brand":             brand,
                "promotion_content": str(row.get("promotion_content", "") or ""),
                "target":            str(row.get("register_team_name", "") or ""),
                "content_type":      str(row.get("landing_url", "") or ""),
                "original_title":    orig_title,
                "needs_title_regen": not is_title_valid(orig_title),
                "remarks":           remarks_raw,
            })
        response_filename = f"llm_responses_{range_key}.json"
        payload = {
            "date_from": date_from, "date_to": date_to,
            "total": len(jobs),
            "generated_at": datetime.now().isoformat(),
            "instructions": _build_pending_instructions(response_filename),
            "message_rules": _PENDING_JOBS_MESSAGE_RULES,
            "jobs": jobs,
        }
        with open(jobs_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        responses_path = get_responses_path(range_key)
        print("\n" + "="*60)
        print("⚠️  ANTHROPIC_API_KEY 미설정 — Claude Code 모드")
        print(f"📋 Pending jobs: {jobs_path}")
        print(f"응답 저장 위치: {responses_path}")
        print(f"완료 후 재실행: python3 scripts/run.py --from {date_from} --to {date_to}")
        print("="*60 + "\n")
        return

    # ── Phase 3: Pipeline 2~4 통합 실행 ────────────────────────────────
    result_df = run_pipeline2(combined_selected, brand_df, category_df, send_dt=f"{d_f}_{d_t}")
    run_log.record_pipeline2(result_df)

    result_df = run_pipeline3(result_df, brand_df)
    run_log.record_pipeline3(result_df)

    result_df = run_pipeline4(result_df, brand_df)
    run_log.record_pipeline4(result_df)

    result_df = run_pipeline5(result_df, max_per_date=MAX_PER_DATE, date_range=dates)

    output_path = save_final_range(result_df, date_from, date_to)
    save_processed_urls(result_df, date_from)
    _upload_campaign_meta_to_gsheets(result_df)

    log_path = run_log.finalize(str(output_path))

    # 결과 요약
    contents_ok = result_df["contents"].notna().sum() if "contents" in result_df.columns else 0
    reviews  = int(result_df.get("needs_review", pd.Series([False]*len(result_df))).sum())
    needs_review_ids = (
        result_df[result_df["needs_review"] == True]["id"].tolist()
        if "needs_review" in result_df.columns else []
    )

    print(f"\n{'='*60}")
    print(f"[push-campaign 완료] {date_from} ~ {date_to}")
    print(f"{'='*60}")
    print(f"\n📊 처리 결과:")
    print(f"  총 선별:        {total_selected}건")
    print(f"  LLM 생성 성공: {contents_ok}건")
    print(f"  검수 필요:     {reviews}건")
    print(f"\n📁 산출물:")
    print(f"  캠페인 메타:  {output_path}")
    if not candidate_df.empty:
        print(f"  후보군 리포트: {cand_path}")
    if needs_review_ids:
        print(f"\n⚠️  검수 필요 항목 (id): {needs_review_ids[:10]}")
    print(f"{'='*60}\n")

    run_log.print_log_summary()


def run_from_selection_report(
    report_path: Path,
    raw_path: Path,
    brand_path: Path,
) -> None:
    """기존 selection_report CSV에서 Pipeline 2~5를 실행.

    selection_report는 선별 결과 요약본이므로 pipeline2에 필요한 컬럼
    (promotion_content, event_name, img_url, remarks)이 없다.
    bizest_raw.csv와 id 기준으로 join해 누락 컬럼을 복원한 뒤 P2~P5를 실행한다.
    """
    logger.info(f"[from-selection-report] 로드: {report_path}")

    # 1. selection_report 로드 → selected 행만
    report_df = pd.read_csv(report_path, dtype=str)
    selected_df = report_df[report_df["selected"].str.strip() == "True"].copy()
    if selected_df.empty:
        logger.warning("선별된 소재 0건 — 처리 종료")
        print("\n⚠️  선별된 소재가 없습니다.")
        return

    # '발송예정일' 컬럼이 있으면 'send_dt'로 rename
    if "발송예정일" in selected_df.columns and "send_dt" not in selected_df.columns:
        selected_df = selected_df.rename(columns={"발송예정일": "send_dt"})

    # 2. bizest_raw.csv 로드 → id 기준 left join으로 누락 컬럼 복원
    if not raw_path.exists():
        logger.error(f"bizest_raw 파일 없음: {raw_path} — --input 옵션으로 경로를 지정하세요.")
        sys.exit(1)

    raw_df = _load_bizest_raw(raw_path)
    raw_df["id"] = raw_df["id"].astype(str)
    selected_df["id"] = selected_df["id"].astype(str)

    # selection_report에 없는 컬럼만 raw에서 보충
    extra_cols = [c for c in raw_df.columns if c not in selected_df.columns]
    if extra_cols:
        selected_df = selected_df.merge(
            raw_df[["id"] + extra_cols], on="id", how="left"
        )
        logger.info(f"bizest_raw join 완료 — 보충 컬럼: {extra_cols}")

    logger.info(f"P2~P5 대상 소재: {len(selected_df)}건")

    # 3. date_from / date_to 자동 결정
    dates_sorted = sorted(selected_df["send_dt"].dropna().unique().tolist())
    date_from, date_to = dates_sorted[0], dates_sorted[-1]
    d_f, d_t = date_from.replace("-", ""), date_to.replace("-", "")
    dates = _dates_in_range(date_from, date_to)

    print(f"\n{'='*60}")
    print(f"[from-selection-report] {date_from} ~ {date_to} ({len(selected_df)}건)")
    print(f"{'='*60}\n")

    # 4. LLM 응답 파일 자동 탐지 (API 키 없을 때)
    if not LLM_API_AVAILABLE and llm_client._file_responses is None:
        rp = get_responses_path(f"{d_f}_{d_t}")
        if rp.exists():
            with open(rp, encoding="utf-8") as f:
                llm_client.init_file_mode(json.load(f))
            logger.info(f"Claude Code 모드 — 응답 파일 자동 로드: {rp}")

    # 5. brand / category 로드
    brand_df = pd.DataFrame()
    if brand_path.exists():
        brand_df = pd.read_csv(brand_path, dtype={"brand_id": str})
    category_df = pd.DataFrame()
    if CATEGORY_SEL_PATH.exists():
        try:
            category_df = pd.read_csv(CATEGORY_SEL_PATH, header=1, dtype=str)
            category_df = category_df.dropna(subset=["구분", "코드"])
        except Exception:
            pass

    # 6. API 키 없고 응답 파일도 없으면 pending_jobs 재생성 후 중단
    if not LLM_API_AVAILABLE and llm_client._file_responses is None:
        jobs_path = generate_pending_jobs(selected_df, brand_df, date_from)
        responses_path = get_responses_path(f"{d_f}_{d_t}")
        print("\n" + "="*60)
        print("⚠️  ANTHROPIC_API_KEY 미설정 — Claude Code 모드")
        print(f"📋 Pending jobs: {jobs_path}")
        print(f"응답 저장 위치: {responses_path}")
        print(f"완료 후 재실행: python3 scripts/run.py --from-selection-report {report_path}")
        print("="*60 + "\n")
        return

    # 7. P2~P5 실행
    run_log = RunLogger(send_dt=f"{d_f}_{d_t}", input_file=str(report_path))

    result_df = run_pipeline2(selected_df, brand_df, category_df, send_dt=f"{d_f}_{d_t}")
    run_log.record_pipeline2(result_df)

    result_df = run_pipeline3(result_df, brand_df)
    run_log.record_pipeline3(result_df)

    result_df = run_pipeline4(result_df, brand_df)
    run_log.record_pipeline4(result_df)

    result_df = run_pipeline5(result_df, max_per_date=MAX_PER_DATE, date_range=dates)

    output_path = save_final_range(result_df, date_from, date_to)
    save_processed_urls(result_df, date_from)
    _upload_campaign_meta_to_gsheets(result_df)

    log_path = run_log.finalize(str(output_path))

    contents_ok = result_df["contents"].notna().sum() if "contents" in result_df.columns else 0
    reviews = int(result_df.get("needs_review", pd.Series([False] * len(result_df))).sum())

    print(f"\n{'='*60}")
    print(f"[push-campaign 완료] {date_from} ~ {date_to}")
    print(f"{'='*60}")
    print(f"\n📊 처리 결과:")
    print(f"  총 선별:        {len(selected_df)}건")
    print(f"  LLM 생성 성공: {contents_ok}건")
    print(f"  검수 필요:     {reviews}건")
    print(f"\n📁 산출물:")
    print(f"  캠페인 메타:  {output_path}")
    print(f"{'='*60}\n")
    run_log.print_log_summary()


def _next_monday() -> str:
    """다음 주 월요일 날짜 반환 (YYYY-MM-DD)."""
    today = datetime.now().date()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _week_dates(anchor: str) -> list:
    """anchor 날짜가 속한 주의 월~일 7일 날짜 목록 반환."""
    dt = datetime.strptime(anchor, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def main():
    parser = argparse.ArgumentParser(description="앱푸시 캠페인 소재 선별 & 메시지 생성")
    parser.add_argument("--date",  type=str, help="발송일 YYYY-MM-DD (기본: 내일)")
    parser.add_argument("--from",  dest="date_from", type=str, metavar="YYYY-MM-DD",
                        help="기간 시작 발송일 (--to와 함께 사용)")
    parser.add_argument("--to",    dest="date_to",   type=str, metavar="YYYY-MM-DD",
                        help="기간 종료 발송일 (--from과 함께 사용)")
    parser.add_argument("--week",  type=str, nargs="?", const="next",
                        metavar="YYYY-MM-DD",
                        help="주단위 선별 (해당 날짜가 속한 주 월~일, 기본: 다음 주)")
    parser.add_argument("--input", type=str, help="비제스트 RAW CSV 경로")
    parser.add_argument("--stage", type=str,
                        choices=["pipeline1", "pipeline2", "pipeline3", "pipeline4", "all"],
                        default="all", help="실행 단계 (기본: all)")
    parser.add_argument("--from-responses", type=str, metavar="PATH",
                        help="LLM 응답 파일 경로 (Claude Code 모드 — API 키 불필요)")
    parser.add_argument("--from-selection-report", dest="from_selection_report",
                        type=str, metavar="PATH",
                        help="기존 selection_report CSV에서 Pipeline 2~5 실행 (P1 생략)")
    parser.add_argument("--source", type=str,
                        choices=["auto", "databricks", "file"], default="auto",
                        help="데이터 소스: auto(Databricks 우선 → 실패 시 종료), "
                             "databricks(강제), file(bizest_raw.csv 직접 사용)")
    args = parser.parse_args()

    raw_path   = Path(args.input) if args.input else BIZEST_RAW_PATH
    brand_path = BRAND_LIST_PATH

    # ── selection_report 재실행 모드 (--from-selection-report) ───────────
    # Databricks 소스 결정보다 먼저 처리 — 이 모드는 Databricks 불필요
    if args.from_selection_report:
        rp_path = Path(args.from_selection_report)
        if not rp_path.exists():
            logger.error(f"selection_report 파일 없음: {rp_path}")
            sys.exit(1)
        if args.from_responses:
            resp_path = Path(args.from_responses)
            if not resp_path.exists():
                logger.error(f"응답 파일 없음: {resp_path}")
                sys.exit(1)
            with open(resp_path, encoding="utf-8") as f:
                llm_client.init_file_mode(json.load(f))
            logger.info(f"Claude Code 모드 — 응답 파일 로드: {resp_path}")
        run_from_selection_report(rp_path, raw_path, BRAND_LIST_PATH)
        return

    # ── 데이터 소스 결정 ─────────────────────────────────────────────────
    source = args.source
    if source == "auto":
        if DATABRICKS_CONFIGURED:
            if _check_databricks():
                source = "databricks"
                logger.info("데이터 소스: Databricks")
            else:
                print("DATABRICKS_UNAVAILABLE")
                print("Databricks 연결에 실패했습니다. --source file 로 재실행하거나 스킬에서 파일 모드를 선택하세요.")
                sys.exit(EXIT_DATABRICKS_UNAVAILABLE)
        else:
            print("DATABRICKS_NOT_CONFIGURED")
            print("Databricks 환경 변수(DATABRICKS_HOST/HTTP_PATH/TOKEN)가 설정되어 있지 않습니다.")
            print("--source file 로 재실행하거나 스킬에서 파일 모드를 선택하세요.")
            sys.exit(EXIT_DATABRICKS_UNAVAILABLE)
    elif source == "databricks" and not DATABRICKS_CONFIGURED:
        logger.error("--source databricks 지정 but 환경변수 미설정")
        sys.exit(1)

    databricks_mode = (source == "databricks")

    # ── 기간 범위 모드 (--from / --to) ──────────────────────────────────
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            logger.error("--from 과 --to 를 함께 지정해야 합니다.")
            sys.exit(1)
        # 범위 응답 파일 명시 지정
        if args.from_responses:
            rp = Path(args.from_responses)
            if not rp.exists():
                logger.error(f"응답 파일 없음: {rp}")
                sys.exit(1)
            with open(rp, encoding="utf-8") as f:
                llm_client.init_file_mode(json.load(f))
            logger.info(f"Claude Code 모드 — 응답 파일 로드: {rp}")
        else:
            d_f = args.date_from.replace("-", "")
            d_t = args.date_to.replace("-", "")
            rp  = get_responses_path(f"{d_f}_{d_t}")
            if rp.exists():
                with open(rp, encoding="utf-8") as f:
                    llm_client.init_file_mode(json.load(f))
                logger.info(f"Claude Code 모드 — 범위 응답 파일 자동 로드: {rp}")
        run_range(args.date_from, args.date_to, raw_path, brand_path, source=source)
        return

    # ── 주단위 선별 모드 (--week, Pipeline 1 only) ──────────────────────
    if args.week is not None:
        anchor  = _next_monday() if args.week == "next" else args.week
        dates   = _week_dates(anchor)
        run_weekly(dates, raw_path, brand_path)
        return

    send_dt = args.date or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        datetime.strptime(send_dt, "%Y-%m-%d")
    except ValueError:
        logger.error(f"날짜 형식 오류: {send_dt} (YYYY-MM-DD 형식 필요)")
        sys.exit(1)

    logger.info(f"실행 시작 — send_dt={send_dt}, stage={args.stage}")

    if databricks_mode:
        raw_df = _fetch_bizest_from_databricks(send_dt)
        raw_df["send_dt"] = send_dt
        _, brand_df, category_df = load_inputs(raw_path, brand_path, source="file")
    else:
        raw_df, brand_df, category_df = load_inputs(raw_path, brand_path, source="file")
        raw_df["send_dt"] = send_dt

    # ── LLM 모드 결정 ─────────────────────────────────────────────────
    if args.from_responses:
        responses_path = Path(args.from_responses)
        if not responses_path.exists():
            logger.error(f"응답 파일 없음: {responses_path}")
            sys.exit(1)
        with open(responses_path, encoding="utf-8") as f:
            llm_client.init_file_mode(json.load(f))
        logger.info(f"Claude Code 모드 — 응답 파일 로드: {responses_path}")
    elif not LLM_API_AVAILABLE:
        responses_path = get_responses_path(send_dt)
        if responses_path.exists():
            with open(responses_path, encoding="utf-8") as f:
                llm_client.init_file_mode(json.load(f))
            logger.info(f"Claude Code 모드 — 응답 파일 자동 로드: {responses_path}")
        else:
            logger.warning("ANTHROPIC_API_KEY 미설정 — Pipeline 1만 실행 후 pending_jobs 생성")

    run_log = RunLogger(send_dt=send_dt, input_file=str(raw_path))
    selection_report_path = None

    # ── Pipeline 1 ────────────────────────────────────────────────────
    campaign_meta_map, campaign_meta_date_url_set = load_campaign_meta_sync()

    if args.stage in ("pipeline1", "all"):
        selected_df, rejected_df = run_pipeline1(
            raw_df, send_dt,
            campaign_meta_map=campaign_meta_map,
            campaign_meta_date_url_set=campaign_meta_date_url_set,
            databricks_mode=databricks_mode,
        )
        run_log.record_pipeline1(selected_df, rejected_df)

        if len(selected_df) == 0:
            logger.warning("선별 소재 0건 — 처리 종료")
            print("\n⚠️  선별된 소재가 없습니다. 입력 파일과 발송일을 확인하세요.")
            log_path = run_log.finalize(None)
            run_log.print_log_summary()
            return

        save_pipeline1(selected_df, send_dt)
        selection_report_path = save_selection_report(selected_df, rejected_df, send_dt)

        report_df = pd.read_csv(selection_report_path, dtype=str)
        _upload_report_to_gsheets(report_df)

        if args.stage == "pipeline1":
            log_path = run_log.finalize(None)
            run_log.print_log_summary()
            logger.info(f"Pipeline 1 완료 — 선별 리포트: {selection_report_path}")
            return

        # API 키 없고 응답 파일도 없으면 pending_jobs 생성 후 종료
        if not LLM_API_AVAILABLE and llm_client._file_responses is None:
            jobs_path = generate_pending_jobs(selected_df, brand_df, send_dt)
            print("\n" + "="*60)
            print("⚠️  ANTHROPIC_API_KEY 미설정 — Claude Code 모드로 전환하세요")
            print("="*60)
            print(f"\n📋 생성된 작업 파일: {jobs_path}")
            print("\n다음 단계:")
            print("  1. Claude Code에게 아래 파일을 열어 LLM 응답을 생성해달라고 요청하세요:")
            print(f"     {jobs_path}")
            print(f"  2. 응답 파일 저장 위치: {get_responses_path(send_dt)}")
            print(f"  3. 완료 후 재실행: python3 scripts/run.py --date {send_dt}")
            print("="*60 + "\n")
            log_path = run_log.finalize(None)
            return

    # ── Pipeline 2 ────────────────────────────────────────────────────
    if args.stage in ("pipeline2", "all"):
        if args.stage == "pipeline2":
            p1_path = DATA_DIR / f"pipeline1_output_{send_dt.replace('-', '')}.csv"
            if not p1_path.exists():
                logger.error(f"Pipeline 1 출력 파일 없음: {p1_path}")
                sys.exit(1)
            selected_df = pd.read_csv(p1_path, dtype={"id": str})
            logger.info(f"Pipeline 1 출력 로드: {len(selected_df)}건")

        result_df = run_pipeline2(selected_df, brand_df, category_df, send_dt=send_dt)
        run_log.record_pipeline2(result_df)

    # ── Pipeline 3 ────────────────────────────────────────────────────
    if args.stage in ("pipeline3", "all"):
        if args.stage == "pipeline3":
            p1_path = DATA_DIR / f"pipeline1_output_{send_dt.replace('-', '')}.csv"
            if not p1_path.exists():
                logger.error(f"Pipeline 1 출력 파일 없음: {p1_path}")
                sys.exit(1)
            selected_df = pd.read_csv(p1_path, dtype={"id": str})
            result_df = run_pipeline2(selected_df, brand_df, category_df)
            run_log.record_pipeline2(result_df)

        result_df = run_pipeline3(result_df, brand_df)
        run_log.record_pipeline3(result_df)

    # ── Pipeline 4 ────────────────────────────────────────────────────
    if args.stage in ("pipeline4", "all"):
        result_df = run_pipeline4(result_df, brand_df)
        run_log.record_pipeline4(result_df)

    # ── Pipeline 5 (발송일 분배 + 광고코드 최종 할당) ────────────────────
    if args.stage == "all":
        result_df = run_pipeline5(result_df, max_per_date=MAX_PER_DATE, date_range=[send_dt])

    output_path = save_final(result_df, send_dt)
    save_processed_urls(result_df, send_dt)
    _upload_campaign_meta_to_gsheets(result_df)
    log_path    = run_log.finalize(str(output_path))
    print_summary(result_df, send_dt, output_path, selection_report_path)
    run_log.print_log_summary()


if __name__ == "__main__":
    main()
