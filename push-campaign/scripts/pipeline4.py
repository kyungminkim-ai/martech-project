"""Pipeline 4 — LLM Red Team 메시지 품질 검토.

생성된 메시지를 생성 규칙과 독립된 관점에서 LLM이 재검토한다.
수신자 경험·브랜드 적합성·마케팅 효과 관점에서 score/verdict/notes를 추가하고,
warning/fail 판정 시 needs_review 플래그를 상향한다.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from llm_client import review_message, set_current_row
from rules import lookup_brand_name

logger = logging.getLogger(__name__)

VERDICT_FAIL = "fail"
VERDICT_WARN = "warning"
_MAX_WORKERS = 6


def run_pipeline4(result_df: pd.DataFrame, brand_df: pd.DataFrame) -> pd.DataFrame:
    logger.info(f"Pipeline 4 시작 — {len(result_df)}건 LLM 검토")

    total = len(result_df)
    rows_indexed = list(result_df.iterrows())

    def _review(args):
        i, (_, row) = args
        row_id     = str(row.get("id", f"idx-{i}"))
        set_current_row(row_id)
        brand_id   = str(row.get("brand_id", "") or "")
        brand_name = lookup_brand_name(brand_id, brand_df)
        result = review_message(
            title    = str(row.get("title", "") or ""),
            contents = str(row.get("contents", "") or ""),
            brand    = brand_name,
            promotion_content = str(row.get("promotion_content", "") or ""),
            target   = str(row.get("target", "") or ""),
        )
        return i, result

    result_map: dict = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {executor.submit(_review, (i, item)): i for i, item in enumerate(rows_indexed, 1)}
        for future in as_completed(futures):
            i, result = future.result()
            result_map[i] = result
            if i % 10 == 0:
                logger.info(f"  진행: {i}/{total}")

    scores, verdicts, notes_list, issues_list = [], [], [], []
    for i in range(1, total + 1):
        r = result_map.get(i, {})
        scores.append(r.get("score"))
        verdicts.append(r.get("verdict", VERDICT_WARN))
        notes_list.append(r.get("notes", ""))
        issues_list.append(", ".join(r.get("issues", [])))

    df = result_df.copy()
    df["review_score"]   = scores
    df["review_verdict"] = verdicts
    df["review_notes"]   = notes_list
    df["review_issues"]  = issues_list

    prev_review = df.get("needs_review", pd.Series([False] * len(df)))
    df["needs_review"] = prev_review | df["review_verdict"].isin([VERDICT_FAIL, VERDICT_WARN])

    pass_cnt = verdicts.count("pass")
    warn_cnt = verdicts.count(VERDICT_WARN)
    fail_cnt = verdicts.count(VERDICT_FAIL)
    logger.info(f"LLM 검토 완료 — pass={pass_cnt} / warning={warn_cnt} / fail={fail_cnt}")

    return df
