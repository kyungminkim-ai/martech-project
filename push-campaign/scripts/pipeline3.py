"""Pipeline 3 — 검수 검증 (Validation QA) + 자동 수정.

규칙 위반(동사형 종결, 제목-본문 중복)은 LLM 재호출로 자동 수정한다.
나머지 이슈는 플래그로 기록하여 담당자가 최종 판단하도록 한다.
행은 제거하지 않는다.
"""
import re
import logging
from typing import Optional
import pandas as pd
from config import (
    TITLE_MIN_LEN, TITLE_MAX_LEN, UNSUBSCRIBE_TEXT,
    AD_CODE_PREFIX, CONFIDENCE_THRESHOLD,
    CONTENTS_MIN_LEN, CONTENTS_MAX_LEN,
)
from rules import lookup_brand_names, extract_title_keywords, append_unsubscribe, detect_collab_pair, _split_collab_pair

logger = logging.getLogger(__name__)

# 금지 동사형 종결 패턴 (본문 끝부분 검사)
_VERB_ENDING_RE = re.compile(
    r'(?:서둘러요|해보세요|놓치지\s*마세요|받아보세요|만나보세요|느껴보세요|'
    r'확인하세요|경험해보세요|가세요|오세요|사세요|하세요|마세요)[!.]?\s*$'
)


def _strip_unsubscribe(text: str) -> str:
    return re.sub(r'\n수신거부.*$', '', text or '', flags=re.DOTALL).strip()


def _extract_discount_rates(text: str) -> set:
    return {int(m) for m in re.findall(r'(\d+)\s*%', text)}


def _has_verb_ending(contents: str) -> bool:
    """본문(수신거부 제외)이 금지 동사형 종결로 끝나면 True."""
    body = _strip_unsubscribe(contents)
    return bool(_VERB_ENDING_RE.search(body))


def _check_title_body_overlap(title: str, body: str, collab_pair: str = "") -> bool:
    """제목 어절(3자 이상) 중 2개 이상이 본문에 포함되면 True.

    콜라보 소재는 각 브랜드명이 개별로 본문에 등장하는 것은 허용하지만,
    쌍 전체(A x B 형태)가 본문에 그대로 반복되거나 개별 브랜드가 2개 이상
    동시에 등장하는 경우에만 위반으로 판정한다.
    """
    body_lower = body.lower()

    if collab_pair:
        left, right = _split_collab_pair(collab_pair)
        parts = [p.strip().lower() for p in [left, right] if p.strip()]
        # 쌍이 통째로 나타나면 위반
        if re.search(re.escape(collab_pair.lower()), body_lower):
            return True
        # 개별 브랜드 2개가 모두 본문에 반복되면 위반 (단독 1개는 허용)
        matched = sum(1 for p in parts if p in body_lower)
        return matched >= 2

    words = [w for w in re.split(r'[\s,·×\-]', title) if len(w) >= 3]
    if len(words) < 2:
        return False
    matches = sum(1 for w in words if w.lower() in body_lower)
    return matches >= 2


def _try_auto_fix(
    title: str, contents: str, row: pd.Series,
    violations: list, collab_pair: str = "", max_attempts: int = 2,
) -> str:
    """LLM을 호출해 위반 사항을 자동 수정. 실패 시 원본 반환."""
    try:
        from llm_client import regenerate_content_fix
        title_keywords = extract_title_keywords(title, collab_pair=collab_pair)
        promotion_content = str(row.get("promotion_content", "") or "")
        target = str(row.get("target", "전체") or "전체")

        for attempt in range(max_attempts):
            fixed_msg = regenerate_content_fix(
                title=title,
                promotion_content=promotion_content,
                target=target,
                original_content=contents,
                violations=violations,
                title_keywords=title_keywords,
            )
            if not fixed_msg:
                logger.debug(f"자동 수정 LLM 무응답 (시도 {attempt + 1})")
                continue
            fixed_with_unsub = append_unsubscribe(fixed_msg)
            still_verb = _has_verb_ending(fixed_with_unsub)
            still_overlap = _check_title_body_overlap(title, fixed_with_unsub, collab_pair)
            if not still_verb and not still_overlap:
                logger.info(f"자동 수정 성공 (시도 {attempt + 1}): {violations}")
                return fixed_with_unsub
            remaining = []
            if still_verb:
                remaining.append("verb_ending_in_contents")
            if still_overlap:
                remaining.append("title_body_overlap_in_contents")
            logger.debug(
                f"자동 수정 후 위반 잔존 (시도 {attempt + 1}): {remaining} | "
                f"생성된 본문: {fixed_msg[:60]!r}"
            )
    except Exception as e:
        logger.warning(f"자동 수정 실패: {e}")
    return contents


def _check_row(row: pd.Series, seen_ad_codes: set, brand_df: Optional[pd.DataFrame] = None) -> tuple:
    """단일 행에 대해 검증 이슈 목록과 brand_nm_verified를 반환한다."""
    issues = []

    title       = str(row.get("title", "") or "")
    contents    = str(row.get("contents", "") or "")
    land_url    = str(row.get("landing_url", "") or "")
    push_url    = str(row.get("push_url", "") or "")
    ad_code     = str(row.get("ad_code", "") or "")
    brand_id    = str(row.get("brand_id", "") or "")
    confidence  = row.get("confidence")
    brand_nm_verified = ""

    # ── 1. 필수 필드 누락 ────────────────────────────────────────────────
    if not title:
        issues.append("title_missing")
    if not contents:
        issues.append("contents_missing")
    if not land_url:
        issues.append("landing_url_missing")
    if not ad_code:
        issues.append("ad_code_missing")

    # ── 2. title 길이 범위 ───────────────────────────────────────────────
    if title and not (TITLE_MIN_LEN <= len(title) <= TITLE_MAX_LEN):
        issues.append(f"title_length_{len(title)}chars(expected {TITLE_MIN_LEN}-{TITLE_MAX_LEN})")

    # ── 3. 본문 길이 범위 (수신거부·접두어 제외) ─────────────────────────
    if contents:
        body_len = len(_strip_unsubscribe(contents).replace("(광고) ", "", 1))
        if not (CONTENTS_MIN_LEN <= body_len <= CONTENTS_MAX_LEN):
            issues.append(f"contents_length_{body_len}chars(expected {CONTENTS_MIN_LEN}-{CONTENTS_MAX_LEN})")

    # ── 4. (광고) 접두어 ─────────────────────────────────────────────────
    if contents and not contents.startswith("(광고)"):
        issues.append("missing_(광고)_prefix")

    # ── 5. 수신거부 문구 ─────────────────────────────────────────────────
    if contents and UNSUBSCRIBE_TEXT not in contents:
        issues.append("missing_unsubscribe_text")

    # ── 6. push_url UTM 파라미터 ─────────────────────────────────────────
    if push_url and "utm_source" not in push_url:
        issues.append("push_url_missing_utm")
    if push_url and f"utm_campaign={ad_code}" not in push_url:
        issues.append("push_url_campaign_mismatch")

    # ── 7. 할인율 0% 차단 ────────────────────────────────────────────────
    if contents and re.search(r'\b0\s*%', contents):
        issues.append("zero_percent_in_contents")

    # ── 8. landing_url https 형식 ────────────────────────────────────────
    if land_url and not land_url.startswith("https://"):
        issues.append("landing_url_not_https")

    # ── 9. ad_code 중복 ─────────────────────────────────────────────────
    if ad_code:
        if ad_code in seen_ad_codes:
            issues.append("ad_code_duplicate")
        else:
            seen_ad_codes.add(ad_code)

    # ── 10. brand_id 누락 + brand_nm_verified 수집 ───────────────────────
    if not brand_id:
        issues.append("brand_id_missing")
    elif brand_df is not None and not brand_df.empty:
        brand_nm_kr, _ = lookup_brand_names(brand_id, brand_df)
        if brand_nm_kr:
            brand_nm_verified = brand_nm_kr
        else:
            brand_nm_verified = "확인 필요"
            issues.append(f"brand_not_in_list({brand_id})")

    # ── 11. LLM confidence 임계값 미달 ──────────────────────────────────
    try:
        if confidence is not None and float(confidence) < CONFIDENCE_THRESHOLD:
            issues.append(f"low_confidence({confidence})")
    except (ValueError, TypeError):
        pass

    # ── 12. title_source fallback ────────────────────────────────────────
    if str(row.get("title_source", "")) == "fallback":
        issues.append("title_source_fallback")

    # ── 13. 브랜드명 포함 여부 ───────────────────────────────────────────
    if brand_df is not None and not brand_df.empty and brand_id:
        brand_nm_kr, brand_nm_en = lookup_brand_names(brand_id, brand_df)
        if brand_nm_kr or brand_nm_en:
            search_text = (title + " " + contents).lower()
            found_kr = bool(brand_nm_kr) and brand_nm_kr.lower() in search_text
            found_en = bool(brand_nm_en) and brand_nm_en.lower() in search_text
            if not (found_kr or found_en):
                issues.append(f"brand_name_not_in_message({brand_nm_kr or brand_nm_en})")

    # ── 14. 캠페인 소재 category_id 필수 ────────────────────────────────
    content_type = str(row.get("content_type", "") or "")
    category_id  = str(row.get("category_id",  "") or "")
    if content_type == "캠페인" and not category_id:
        issues.append("campaign_category_id_missing")

    # ── 15. 동사형 종결 위반 ─────────────────────────────────────────────
    if contents and _has_verb_ending(contents):
        issues.append("verb_ending_in_contents")

    # ── 16. 제목-본문 이어쓰기 중복 감지 ────────────────────────────────
    collab_pair = detect_collab_pair(str(row.get("event_name", "") or ""), title)
    if title and contents and _check_title_body_overlap(title, contents, collab_pair):
        issues.append("title_body_overlap_in_contents")

    # ── 19. 콜라보 제목 구분자 대소문자 검사 ────────────────────────────
    if re.search(r'\s[X×]\s', title):
        issues.append("collab_separator_not_lowercase_x")
    if re.search(r'\s[Ww][Ii][Tt][Hh]\s', title) and ' with ' not in title:
        issues.append("collab_separator_with_not_lowercase")

    # ── 17. 할인율 정합성 검증 ───────────────────────────────────────────
    promotion_content = str(row.get("promotion_content", "") or "")
    raw_rates = _extract_discount_rates(promotion_content)
    if contents:
        body_rates = _extract_discount_rates(contents)
        if body_rates:
            if raw_rates:
                max_raw = max(raw_rates)
                for rate in body_rates:
                    if rate > max_raw:
                        issues.append(f"discount_rate_inflated({rate}%>raw_max_{max_raw}%)")
                unverified = body_rates - raw_rates
                for rate in sorted(unverified):
                    issues.append(f"discount_rate_unverified({rate}%_not_in_raw)")
            else:
                for rate in sorted(body_rates):
                    issues.append(f"discount_rate_unverified({rate}%_not_in_raw)")

    # ── 18. image_url 유효성 ─────────────────────────────────────────────
    image_url = str(row.get("image_url", "") or "")
    if not image_url:
        issues.append("image_url_missing")
    elif not image_url.startswith("https://"):
        issues.append("image_url_not_https")

    return issues, brand_nm_verified


_FIXABLE_VIOLATIONS = {"verb_ending_in_contents", "title_body_overlap_in_contents"}
_BLOCKING_SUFFIXES = ("_missing",)


def run_pipeline3(result_df: pd.DataFrame, brand_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Pipeline 3 검수 검증 + 자동 수정 실행."""
    logger.info(f"Pipeline 3 시작 — {len(result_df)}건 검증")

    df = result_df.copy()
    seen_ad_codes: set = set()
    all_notes      = []
    all_needs      = []
    all_errors     = []
    all_brand_nms  = []
    fix_count      = 0

    for idx, row in df.iterrows():
        issues, brand_nm_verified = _check_row(row, seen_ad_codes, brand_df)

        # ── 자동 수정 (동사형 종결 / 제목-본문 중복) ─────────────────────
        fixable = [i for i in issues if i in _FIXABLE_VIOLATIONS]
        if fixable:
            title      = str(row.get("title", "") or "")
            contents   = str(row.get("contents", "") or "")
            row_collab = detect_collab_pair(str(row.get("event_name", "") or ""), title)
            fixed = _try_auto_fix(title, contents, row, fixable, collab_pair=row_collab)
            if fixed != contents:
                df.at[idx, "contents"] = fixed
                fix_count += 1
                # 수정 후 fixable 이슈만 재평가
                remaining_fixable = []
                if _has_verb_ending(fixed):
                    remaining_fixable.append("verb_ending_in_contents")
                if _check_title_body_overlap(title, fixed, row_collab):
                    remaining_fixable.append("title_body_overlap_in_contents")
                issues = [i for i in issues if i not in _FIXABLE_VIOLATIONS] + remaining_fixable

        notes = ", ".join(issues) if issues else ""
        is_blocking = any(
            any(issue.endswith(s) for s in _BLOCKING_SUFFIXES)
            for issue in issues
        )

        prev_error  = bool(row.get("error_flag",   False))
        prev_review = bool(row.get("needs_review", False))

        all_notes.append(notes)
        all_errors.append(is_blocking or prev_error)
        all_needs.append(bool(issues) or prev_review)
        all_brand_nms.append(brand_nm_verified)

    df["validation_notes"]  = all_notes
    df["error_flag"]        = all_errors
    df["needs_review"]      = all_needs
    df["brand_nm_verified"] = all_brand_nms

    pass_cnt  = sum(1 for n in all_notes if not n)
    issue_cnt = sum(1 for n in all_notes if n)
    block_cnt = sum(1 for e in all_errors if e)

    logger.info(f"검증 통과: {pass_cnt}건")
    logger.info(f"이슈 발견: {issue_cnt}건 (blocking: {block_cnt}건)")
    logger.info(f"자동 수정: {fix_count}건")

    return df
