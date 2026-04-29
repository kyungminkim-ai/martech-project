"""Claude API 클라이언트 — 재시도·파싱 포함."""
import json
import time
import re
import logging
import threading
from typing import Optional
import anthropic
from config import LLM_MODEL, LLM_MAX_TOKENS, LLM_MAX_RETRIES
from prompts import (
    build_title_prompt,
    build_content_prompt,
    build_content_fix_prompt,
    build_review_prompt,
    build_category_infer_prompt,
)

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None

# Claude Code 파일 모드 (API 키 없을 때 사용)
_file_responses: Optional[dict] = None
# threading.local()로 스레드별 row_id 격리 — 전역 공유 시 병렬 처리에서 race condition 발생
_thread_local = threading.local()


def init_file_mode(responses: dict) -> None:
    """API 키 없이 미리 생성된 응답 파일을 LLM 대신 사용하도록 설정."""
    global _file_responses
    _file_responses = responses
    logger.info(f"LLM 파일 모드 활성화 — {len(responses)}건 로드")


def set_current_row(row_id: str) -> None:
    _thread_local.current_row_id = str(row_id)


def _get_file_value(key: str):
    row_id = getattr(_thread_local, "current_row_id", "")
    if _file_responses is None or not row_id:
        return None
    return _file_responses.get(row_id, {}).get(key)


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _call_claude(system_prompt: str, user_prompt: str) -> Optional[str]:
    """system_prompt는 cache_control로 캐시되고, user_prompt는 매 호출마다 새로 전송된다."""
    from config import LLM_API_AVAILABLE
    if not LLM_API_AVAILABLE:
        return None
    client = get_client()
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=LLM_MAX_TOKENS,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            wait = 2 ** attempt
            logger.warning(f"Rate limit, {wait}s 대기 후 재시도 ({attempt + 1}/{LLM_MAX_RETRIES})")
            time.sleep(wait)
        except anthropic.APIError as e:
            logger.error(f"API 오류 (시도 {attempt + 1}): {e}")
            time.sleep(2 ** attempt)
    return None


def _parse_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.error(f"JSON 파싱 실패: {(raw or '')[:100]}")
    return None


def regenerate_title(
    brand: str, promotion_content: str, target: str,
    remarks: str = "", collab_pair: str = "",
    content_nature: str = "", benefit_type: str = "",
) -> Optional[str]:
    cached = _get_file_value("title")
    if cached is not None:
        return str(cached)
    sys_p, usr_p = build_title_prompt(
        brand, promotion_content, target,
        remarks=remarks, collab_pair=collab_pair,
        content_nature=content_nature, benefit_type=benefit_type,
    )
    raw = _call_claude(sys_p, usr_p)
    parsed = _parse_json(raw)
    if parsed and "title" in parsed:
        return str(parsed["title"])
    return None


def generate_content(
    title: str, brand: str, promotion_content: str,
    content_type: str, target: str,
    title_keywords: list = None,
    collab_pair: str = "",
    remarks: str = "",
    content_nature: str = "",
    benefit_type: str = "",
) -> dict:
    cached = _get_file_value("contents")
    if cached is not None:
        return {"message": str(cached), "confidence": float(_get_file_value("confidence") or 4.0)}
    sys_p, usr_p = build_content_prompt(
        title, brand, promotion_content, content_type, target,
        title_keywords=title_keywords, collab_pair=collab_pair, remarks=remarks,
        content_nature=content_nature, benefit_type=benefit_type,
    )
    raw = _call_claude(sys_p, usr_p)
    parsed = _parse_json(raw)
    if parsed and "message" in parsed:
        return {
            "message":    str(parsed["message"]),
            "confidence": float(parsed.get("confidence", 0.0)),
        }
    return {"message": None, "confidence": None}


def regenerate_content_fix(
    title: str, promotion_content: str, target: str,
    original_content: str, violations: list,
    title_keywords: list = None,
) -> Optional[str]:
    """Pipeline 3 자동 수정용 — 위반 항목을 명시하여 재생성."""
    from config import LLM_API_AVAILABLE
    if not LLM_API_AVAILABLE:
        return None
    sys_p, usr_p = build_content_fix_prompt(
        title, promotion_content, target,
        original_content, violations,
        title_keywords=title_keywords,
    )
    raw = _call_claude(sys_p, usr_p)
    parsed = _parse_json(raw)
    if parsed and "message" in parsed:
        return str(parsed["message"])
    return None


def infer_category_ids(
    event_name: str,
    promotion_content: str,
    main_title: str,
    landing_url: str,
    category_list_str: str,
) -> list:
    """소재 내용 기반 카테고리 코드 유추 (최대 3개 리스트 반환)."""
    from config import LLM_API_AVAILABLE
    if not LLM_API_AVAILABLE:
        if _file_responses is not None:
            codes = _get_file_value("category_codes")
            if codes and isinstance(codes, list):
                return [str(c).strip() for c in codes if c]
        return []
    if not category_list_str:
        return []
    sys_p, usr_p = build_category_infer_prompt(
        event_name, promotion_content, main_title, landing_url, category_list_str,
    )
    raw = _call_claude(sys_p, usr_p)
    parsed = _parse_json(raw)
    if parsed and "codes" in parsed and isinstance(parsed["codes"], list):
        return [str(c).strip() for c in parsed["codes"] if c]
    return []


_VALID_ISSUE_CODES = frozenset({
    "fact_mismatch", "tone_off", "brand_inconsistency", "legal_risk", "other",
})


def _normalize_issues(raw_issues: list) -> list:
    result = []
    for item in raw_issues:
        code = str(item).strip()
        result.append(code if code in _VALID_ISSUE_CODES else "other")
    return result


def review_message(
    title: str, contents: str,
    brand: str, promotion_content: str, target: str,
) -> dict:
    score = _get_file_value("review_score")
    if score is not None:
        return {
            "score":   float(score),
            "verdict": str(_get_file_value("review_verdict") or "warning"),
            "notes":   str(_get_file_value("review_notes") or ""),
            "issues":  _normalize_issues(list(_get_file_value("review_issues") or [])),
        }
    sys_p, usr_p = build_review_prompt(title, contents, brand, promotion_content, target)
    raw = _call_claude(sys_p, usr_p)
    parsed = _parse_json(raw)
    if parsed and "score" in parsed:
        return {
            "score":   float(parsed.get("score", 3.0)),
            "verdict": str(parsed.get("verdict", "warning")),
            "notes":   str(parsed.get("notes", "")),
            "issues":  _normalize_issues(list(parsed.get("issues", []))),
        }
    return {"score": None, "verdict": "warning", "notes": "검토 실패", "issues": []}
