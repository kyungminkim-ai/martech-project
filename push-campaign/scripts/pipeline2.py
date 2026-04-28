"""Pipeline 2 — Rule-based 메타데이터 생성 + LLM 메시지 생성."""
import logging
import pandas as pd
from config import (
    CONFIDENCE_THRESHOLD, AD_CODE_SEED_FILE, AD_CODE_PREFIX,
    CAMPAIGN_META_SYNC_PATH, DATA_DIR, TITLE_MIN_LEN, TITLE_MAX_LEN,
    get_pipeline2_checkpoint_path,
)
from rules import (
    classify_target, get_content_type, get_priority, get_category_id,
    is_title_valid, sanitize_title, generate_ad_code, build_push_url,
    append_unsubscribe, lookup_brand_name, build_category_list_str,
    build_braze_campaign_name, build_feed_url, build_webhook_contents,
    extract_goods_id, extract_title_keywords,
    detect_collab_pair, title_has_collab_pair,
    detect_content_nature, detect_benefit_type,
)
from llm_client import regenerate_title, generate_content, set_current_row, infer_category_ids

logger = logging.getLogger(__name__)


def _base36_val(code: str) -> int:
    """APSCMCD 코드의 suffix를 Base36 정수로 변환."""
    suffix = code[len(AD_CODE_PREFIX):]
    val = 0
    for c in suffix.upper():
        val = val * 36 + int(c, 36)
    return val


def _load_last_ad_code() -> str:
    """campaign_meta_sync.csv 최댓값과 seed 파일 중 더 큰 APSCMCD 코드를 반환."""
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
    AD_CODE_SEED_FILE.write_text(code)


def _merge_category_ids(base_cat: str, llm_cats: list) -> str:
    """팀명 매핑(base_cat)과 LLM 유추(llm_cats)를 합쳐 최대 3개 반환."""
    seen = []
    if base_cat:
        seen.append(base_cat)
    for c in llm_cats:
        if c and c not in seen:
            seen.append(c)
    return ",".join(seen[:3])


def _trim_long_title(original_title: str) -> str:
    """40자 초과 제목에서 쉼표 앞 훅 문구 추출. 분리 불가 시 원본 반환 (LLM이 재생성).

    예) "빵처럼 맛있는 쉐이크 발견, 테이크핏 브레드밀 단독 출시"
        → "빵처럼 맛있는 쉐이크 발견" (15자 미만이면 원본 반환)
    """
    if len(original_title) <= TITLE_MAX_LEN:
        return original_title
    parts = original_title.split(",", 1)
    if len(parts) > 1:
        front = parts[0].strip()
        if len(front) >= TITLE_MIN_LEN:
            return front
    return original_title


def process_row(row: pd.Series, brand_df: pd.DataFrame, current_ad_code: str, category_df=None) -> dict:
    brand_id          = str(row.get("sourceBrandId", "") or "")
    promotion_content = str(row.get("promotion_content", "") or "")
    landing_url       = str(row.get("landing_url", "") or "").strip()
    original_title    = sanitize_title(str(row.get("main_title", "") or ""))
    team_name         = str(row.get("register_team_name", "") or "")
    event_name        = str(row.get("event_name", "") or "")
    img_url           = str(row.get("img_url", "") or "")
    send_dt           = str(row.get("send_dt", "") or "")
    remarks           = str(row.get("remarks", "") or "")

    brand_name = lookup_brand_name(brand_id, brand_df)

    result = {}

    # ── category_id: 팀명 매핑 + LLM 유추 ──────────────────────────────
    base_cat = get_category_id(team_name)
    cat_list_str = build_category_list_str(category_df) if category_df is not None else ""
    llm_cats = infer_category_ids(event_name, promotion_content, original_title, landing_url, cat_list_str)
    merged_category_id = _merge_category_ids(base_cat, llm_cats)

    # ── Rule-based ──────────────────────────────────────────────────────
    result["send_dt"]      = send_dt
    result["send_time"]    = "11:00"
    result["target"]       = classify_target(team_name, brand_id=brand_id, brand_df=brand_df)
    result["priority"]     = get_priority(team_name, landing_url)
    result["content_type"] = get_content_type(landing_url)
    result["brand_id"]     = brand_id
    result["category_id"]  = merged_category_id
    result["landing_url"]  = landing_url
    result["image_url"]    = img_url
    result["goods_id"]     = extract_goods_id(landing_url)
    result["team_id"]      = team_name
    result["stopped"]      = ""

    # ── 광고 코드 & push_url ─────────────────────────────────────────────
    ad_code = generate_ad_code(current_ad_code)
    result["ad_code"]   = ad_code
    result["push_url"]  = build_push_url(landing_url, ad_code)

    # ── 콜라보 감지 & 소재 성격/혜택 유형 분류 ───────────────────────────
    collab_pair    = detect_collab_pair(event_name, original_title)
    content_nature = detect_content_nature(event_name, promotion_content, original_title, collab_pair)
    benefit_type   = detect_benefit_type(promotion_content, event_name)

    result["content_nature"] = content_nature
    result["benefit_type"]   = benefit_type

    # ── 제목 처리 ────────────────────────────────────────────────────────
    # 1) 40자 초과 시 쉼표 앞 훅 부분 추출 시도
    candidate_title = _trim_long_title(original_title)

    # 2) 콜라보 소재는 제목에 "BrandA X BrandB" 쌍이 반드시 있어야 유효
    _title_format_ok  = is_title_valid(candidate_title)
    _title_collab_ok  = title_has_collab_pair(candidate_title, collab_pair)

    if _title_format_ok and _title_collab_ok:
        result["title"]        = candidate_title
        result["title_source"] = "original"
    else:
        regen = regenerate_title(
            brand_name, promotion_content, result["target"],
            remarks=remarks, collab_pair=collab_pair,
            content_nature=content_nature, benefit_type=benefit_type,
        )
        if regen:
            result["title"]        = regen
            result["title_source"] = "llm"
        else:
            result["title"]        = original_title
            result["title_source"] = "fallback"

    # ── content 생성 (단일 호출) ─────────────────────────────────────────
    title_keywords = extract_title_keywords(result["title"], collab_pair=collab_pair)
    content_result = generate_content(
        title=result["title"],
        brand=brand_name,
        promotion_content=promotion_content,
        content_type=result["content_type"] or "",
        target=result["target"],
        title_keywords=title_keywords,
        collab_pair=collab_pair,
        remarks=remarks,
        content_nature=content_nature,
        benefit_type=benefit_type,
    )
    result["contents"]    = append_unsubscribe(content_result["message"]) if content_result["message"] else None
    result["confidence"]  = content_result["confidence"]

    # ── 자동 생성 컬럼 ────────────────────────────────────────────────────
    result["braze_campaign_name"] = build_braze_campaign_name(
        send_dt=result["send_dt"],
        send_time=result["send_time"],
        ad_code=ad_code,
        title=result["title"],
        target=result["target"],
        content_type=result["content_type"],
    )
    result["feed_url"]         = build_feed_url(landing_url, ad_code)
    result["webhook_contents"] = build_webhook_contents(result["contents"] or "")

    # ── 검수 플래그 ──────────────────────────────────────────────────────
    result["error_flag"] = result["contents"] is None
    result["needs_review"] = result["error_flag"] or any([
        (content_result["confidence"] or 0) < CONFIDENCE_THRESHOLD,
        result["title_source"] == "fallback",
    ])

    return result, ad_code


_VALID_TARGETS    = {"여성", "남성", "전체"}
_VALID_PRIORITIES = {1, 2, 3}
_VALID_CONTENT_TYPES = {"캠페인", "콘텐츠", "브랜드", None}


def _postprocess_columns(result: dict) -> dict:
    """Pipeline 2 출력 컬럼을 정규화·보정한다."""
    result["title"] = sanitize_title(result.get("title") or "")

    if result.get("send_time") not in ("11:00",):
        result["send_time"] = "11:00"

    if result.get("target") not in _VALID_TARGETS:
        logger.debug(f"target 정규화: {result.get('target')!r} → '전체'")
        result["target"] = "전체"

    try:
        prio = int(result.get("priority", 3))
    except (TypeError, ValueError):
        prio = 3
    if prio not in _VALID_PRIORITIES:
        prio = 3
    result["priority"] = prio

    ct = result.get("content_type")
    if ct not in _VALID_CONTENT_TYPES:
        logger.debug(f"content_type 정규화: {ct!r} → None")
        result["content_type"] = None

    return result


_CHECKPOINT_INTERVAL = 5


def run_pipeline2(selected_df: pd.DataFrame, brand_df: pd.DataFrame, category_df=None, send_dt: str = None) -> pd.DataFrame:
    logger.info(f"Pipeline 2 시작 — {len(selected_df)}건 처리")

    current_ad_code = _load_last_ad_code()
    rows: list = []
    total = len(selected_df)

    checkpoint_path = get_pipeline2_checkpoint_path(send_dt) if send_dt else None
    processed_ids: set = set()

    if checkpoint_path and checkpoint_path.exists():
        try:
            ckpt_df = pd.read_csv(checkpoint_path, dtype=str)
            rows = ckpt_df.to_dict("records")
            processed_ids = {str(r.get("id", "")) for r in rows if r.get("id")}
            if rows and rows[-1].get("ad_code"):
                current_ad_code = str(rows[-1]["ad_code"])
            logger.info(f"체크포인트 로드 — {len(rows)}건 이어서 처리")
        except Exception as e:
            logger.warning(f"체크포인트 로드 실패, 처음부터 시작: {e}")
            rows = []
            processed_ids = set()

    for i, (_, row) in enumerate(selected_df.iterrows(), 1):
        row_id = str(row.get("id", f"idx-{i}"))
        if row_id in processed_ids:
            logger.info(f"[{i}/{total}] id={row_id} 체크포인트 스킵")
            continue
        set_current_row(row_id)
        logger.info(f"[{i}/{total}] id={row_id} 처리 중...")
        try:
            meta, new_ad_code = process_row(row, brand_df, current_ad_code, category_df)
            meta = _postprocess_columns(meta)
            current_ad_code = new_ad_code
            rows.append({**row.to_dict(), **meta})
        except Exception as e:
            logger.error(f"id={row_id} 처리 실패: {e}")
            rows.append({**row.to_dict(), "error_flag": True, "needs_review": True})

        if checkpoint_path and len(rows) % _CHECKPOINT_INTERVAL == 0:
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(rows).to_csv(checkpoint_path, index=False, encoding="utf-8-sig")
            except Exception:
                pass

    _save_last_ad_code(current_ad_code)

    result_df = pd.DataFrame(rows)

    if checkpoint_path and checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except Exception:
            pass

    content_ok = result_df["contents"].notna().sum() if "contents" in result_df.columns else 0
    errors  = int(result_df.get("error_flag", pd.Series([False] * len(result_df))).sum())
    reviews = int(result_df.get("needs_review", pd.Series([False] * len(result_df))).sum())

    logger.info(f"content 생성 완료: {content_ok}건")
    logger.info(f"오류: {errors}건")
    logger.info(f"검수 필요: {reviews}건")

    return result_df
