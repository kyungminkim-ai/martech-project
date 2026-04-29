"""Pipeline 5 — 발송일 분배 (P5-A) + 광고코드 최종 할당 (P5-B).

P5-A: P1~P4 완료 후 날짜별 소재 밀집도를 분석하고 낮은 우선순위 소재를
      인접 날짜로 재배치한다. 이동 불가 시 needs_review 플래그 추가.

P5-B: campaign_meta_sync.csv 기준 마지막 ad_code 이후부터 순차 재할당.
      정렬 기준: send_dt ASC → priority ASC → id ASC.
      push_url의 utm_campaign/source 파라미터도 함께 갱신.
"""
import logging
from datetime import datetime

import pandas as pd

from config import AD_CODE_PREFIX, AD_CODE_SEED_FILE, CAMPAIGN_META_SYNC_PATH
from rules import build_push_url, generate_ad_code

logger = logging.getLogger(__name__)


# ── 공통 유틸 ──────────────────────────────────────────────────────────────

def _base36_val(code: str) -> int:
    suffix = code[len(AD_CODE_PREFIX):]
    val = 0
    for c in suffix.upper():
        val = val * 36 + int(c, 36)
    return val


def _load_last_ad_code() -> str:
    """campaign_meta_sync.csv + seed 파일 중 더 큰 코드를 반환."""
    candidates = []

    try:
        df = pd.read_csv(CAMPAIGN_META_SYNC_PATH, usecols=["ad_code"])
        codes = df["ad_code"].dropna()
        codes = codes[codes.str.startswith(AD_CODE_PREFIX)]
        if not codes.empty:
            candidates.append(max(codes, key=_base36_val))
    except Exception:
        pass

    try:
        seed = AD_CODE_SEED_FILE.read_text().strip()
        if seed.startswith(AD_CODE_PREFIX):
            candidates.append(seed)
    except FileNotFoundError:
        pass

    return max(candidates, key=_base36_val) if candidates else f"{AD_CODE_PREFIX}000"


def _save_last_ad_code(code: str) -> None:
    AD_CODE_SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AD_CODE_SEED_FILE.with_suffix(".tmp")
    tmp.write_text(code)
    tmp.replace(AD_CODE_SEED_FILE)


# ── P5-A: 발송일 분배 ──────────────────────────────────────────────────────

def _redistribute_send_dates(
    df: pd.DataFrame,
    max_per_date: int,
    date_range: list,
) -> pd.DataFrame:
    """날짜별 소재 밀집 시 낮은 우선순위 소재를 인접 날짜로 분배.

    이동 우선순위:
      - 낮은 priority(숫자 큰 쪽)부터 이동 대상으로 선별
      - 인접 날짜 중 여유 있고 동일 brand_id 없는 날짜로 이동
      - 이동 불가 시 needs_review=True + validation_notes 기록
    """
    df = df.copy()

    date_counts = df.groupby("send_dt").size()
    crowded = sorted(date_counts[date_counts > max_per_date].index.tolist())

    if not crowded:
        logger.info("P5-A 발송일 분배: 밀집 없음 — 변경 없음")
        return df

    logger.info(f"P5-A 발송일 분배: 밀집 날짜 {crowded} (max_per_date={max_per_date})")
    moved_total = 0
    flagged_total = 0

    for crowded_date in crowded:
        overflow_count = len(df[df["send_dt"] == crowded_date]) - max_per_date

        # 이동 대상: 낮은 priority(숫자 큰 값) → 같은 priority면 id 큰 것부터
        day_df = df[df["send_dt"] == crowded_date].copy()
        day_df["_p"] = pd.to_numeric(day_df.get("priority", 0), errors="coerce").fillna(999)
        day_df["_i"] = pd.to_numeric(day_df.get("id", 0), errors="coerce").fillna(999999)
        overflow_ids = (
            day_df.sort_values(["_p", "_i"], ascending=[False, False])
            .head(overflow_count)["id"]
            .tolist()
        )

        # 인접 날짜를 거리 순으로 정렬
        crowded_dt = datetime.strptime(crowded_date, "%Y-%m-%d")
        adjacent = sorted(
            [d for d in date_range if d != crowded_date],
            key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d") - crowded_dt).days),
        )

        for item_id in overflow_ids:
            item_mask = df["id"] == item_id
            item_brand = str(df.loc[item_mask, "brand_id"].values[0]
                            if item_mask.any() else "")

            placed = False
            for cand_date in adjacent:
                if len(df[df["send_dt"] == cand_date]) >= max_per_date:
                    continue
                # 동일 날짜에 같은 brand_id가 이미 있으면 skip
                if item_brand and item_brand in df.loc[df["send_dt"] == cand_date, "brand_id"].tolist():
                    continue

                df.loc[item_mask, "send_dt"] = cand_date
                moved_total += 1
                placed = True
                logger.info(f"  이동: id={item_id}  {crowded_date} → {cand_date}")
                break

            if not placed:
                note = f"send_dt_overflow({crowded_date}:이동불가)"
                df.loc[item_mask, "needs_review"] = True
                existing = str(df.loc[item_mask, "validation_notes"].values[0] or "")
                df.loc[item_mask, "validation_notes"] = (
                    f"{existing} | {note}" if existing else note
                )
                flagged_total += 1
                logger.info(f"  플래그: id={item_id}  {crowded_date} 이동 불가")

    logger.info(f"P5-A 완료: 이동={moved_total}건, 검수플래그={flagged_total}건")
    return df


# ── P5-B: 광고코드 최종 할당 ───────────────────────────────────────────────

def _assign_final_ad_codes(df: pd.DataFrame) -> pd.DataFrame:
    """send_dt ASC → priority ASC → id ASC 정렬 후 ad_code 순차 재할당.

    campaign_meta_sync.csv 기준 마지막 등록 코드 다음 번호부터 시작.
    push_url의 utm_campaign / source 파라미터도 함께 갱신.
    """
    if df.empty:
        return df

    df = df.copy()

    last_code = _load_last_ad_code()
    logger.info(f"P5-B 기준 코드: {last_code}")

    # 정렬용 임시 컬럼 (원본 id·priority가 문자열일 수 있으므로 수치 변환)
    df["_sort_p"] = pd.to_numeric(df.get("priority", 0), errors="coerce").fillna(999)
    df["_sort_i"] = pd.to_numeric(df.get("id", 0), errors="coerce").fillna(999999)
    df = (
        df.sort_values(["send_dt", "_sort_p", "_sort_i"])
        .reset_index(drop=True)
        .drop(columns=["_sort_p", "_sort_i"])
    )

    # 순차 재할당
    current = last_code
    new_codes = []
    for _ in range(len(df)):
        current = generate_ad_code(current)
        new_codes.append(current)

    df["ad_code"] = new_codes

    # push_url 갱신 (landing_url 기반 재빌드)
    if "landing_url" in df.columns:
        df["push_url"] = df.apply(
            lambda r: build_push_url(
                str(r.get("landing_url", "") or ""), r["ad_code"]
            ),
            axis=1,
        )

    _save_last_ad_code(new_codes[-1])
    logger.info(
        f"P5-B 완료: {new_codes[0]} ~ {new_codes[-1]} ({len(new_codes)}건)"
    )
    return df


# ── 진입점 ─────────────────────────────────────────────────────────────────

def run_pipeline5(
    result_df: pd.DataFrame,
    max_per_date: int = 5,
    date_range: list = None,
) -> pd.DataFrame:
    """P5-A 발송일 분배 → P5-B 광고코드 최종 할당.

    Args:
        result_df:    P4까지 완료된 전체 결과 DataFrame
        max_per_date: 날짜당 최대 소재 수 (초과 시 분배)
        date_range:   유효 발송일 목록 (None이면 result_df 내 고유 날짜 사용)
    """
    logger.info(
        f"Pipeline 5 시작 — {len(result_df)}건 / max_per_date={max_per_date}"
    )

    if date_range is None:
        date_range = sorted(result_df["send_dt"].dropna().unique().tolist())

    result_df = _redistribute_send_dates(
        result_df, max_per_date=max_per_date, date_range=date_range
    )
    result_df = _assign_final_ad_codes(result_df)

    return result_df
