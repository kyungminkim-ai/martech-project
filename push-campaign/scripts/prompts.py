"""LLM 프롬프트 템플릿 — 단일 content 생성 구조.

각 build_* 함수는 (system_prompt, user_prompt) 튜플을 반환한다.
system_prompt는 정책·규칙 블록 (cache_control ephemeral 대상),
user_prompt는 소재별 가변 데이터.
"""
import re


def _strip_unsubscribe(text: str) -> str:
    """수신거부 문구를 제거해 순수 메시지 본문만 반환."""
    return re.sub(r"\n수신거부.*$", "", text or "").strip()


# ── 제목 프롬프트 ──────────────────────────────────────────────────────────

_TITLE_SYSTEM = """\
당신은 무신사 앱푸시 제목 작성 전문가입니다.

## 제목 작성 원칙

**행동어 금지** — 발매·출시·드롭·단독·론칭은 제목에 쓰지 않습니다. 그것은 본문의 몫입니다.
제목은 **정체성**(브랜드·콜라보·상품명) 또는 **훅 문구**(호기심 유발 감성 문장) 중 하나로 씁니다.

올바른 예:
- "알리스 x 오정규" ← 정체성 (콜라보 쌍만)
- "더마토리 x 톡신 공동개발 블랙세럼" ← 정체성 (콜라보 + 상품명)
- "빵처럼 맛있는 쉐이크 발견" ← 훅 문구 (정체성 없이)

잘못된 예:
- "알리스 x 오정규 발매" ← 행동어 "발매" 금지
- "오정규 협업 발매" ← 콜라보 쌍 누락 + 행동어

## 작성 규칙
1. 길이: 5~40자 (공백 포함)
2. 브랜드명은 한국어 표기 우선
3. 감탄사·명령조 금지

## 비콜라보 소재 유형별 패턴
- 단독/신상:       "브랜드 [상품/시즌명]"     예) 시에라디자인 25FW 컬렉션
- 단순 할인:       "브랜드 최대 N% 할인"      예) 리드볼트 최대 20% 할인
- 대형 이벤트:     "[최대 N%] 이벤트명"       예) [최대 80%] 티셔츠 페스티벌
- 훅 필요 시:      감성·호기심 유발 문구      예) 빵처럼 맛있는 쉐이크 발견

## 출력 형식 (JSON만, 다른 텍스트 없이)
{"title": "생성된 제목"}"""


def _build_title_nature_hint(content_nature: str, benefit_type: str) -> str:
    if not content_nature or content_nature in ("콜라보레이션", "기타"):
        return ""
    lines = []
    if content_nature == "단독선발매":
        lines.append("이 소재는 **단독/선발매** 입니다. 제목은 브랜드명·상품명을 담고, '단독·선발매' 표현은 본문에 맡기세요.")
        lines.append("예) '시에라디자인 25FW 컬렉션' / '더마토리 블랙세럼 에디션'")
    elif content_nature == "신규발매":
        lines.append("이 소재는 **신규 발매** 입니다. 제목은 브랜드명 + 시즌/상품명으로 정체성을 표현하세요.")
        lines.append("예) '나이키 에어맥스 DN8' / '아크테릭스 25SS 컬렉션'")
    elif content_nature == "프로모션":
        if benefit_type == "Price":
            lines.append("이 소재는 **프로모션(할인)** 입니다. '브랜드 최대 N% 할인' 또는 '[최대 N%] 이벤트명' 패턴을 우선하세요.")
        else:
            lines.append("이 소재는 **프로모션** 입니다. 브랜드명과 행사 규모를 제목에 담으세요.")
    if lines:
        return "\n\n## 소재 성격별 제목 전략\n" + "\n".join(lines)
    return ""


def build_title_prompt(
    brand: str, promotion_content: str, target: str,
    remarks: str = "", collab_pair: str = "",
    content_nature: str = "", benefit_type: str = "",
) -> tuple:
    target_ctx = {"여성": "여성 고객 대상", "남성": "남성 고객 대상"}.get(target, "전체 고객 대상")
    remarks_section = f"\n## 마케터 메모 (우선 반영)\n{remarks.strip()}" if remarks and remarks.strip() else ""
    nature_hint = _build_title_nature_hint(content_nature, benefit_type)

    if collab_pair:
        parts = re.split(r'\s*[Xx×]\s*', collab_pair)
        left, right = parts[0].strip(), parts[-1].strip()
        collab_section = f"""

## ⚠️ 콜라보 소재 — 제목 필수 규칙
이 소재는 **{collab_pair}** 콜라보입니다.
제목에 두 이름 **{left}** 와 **{right}** 를 모두 포함하세요.

✅ 올바른 예:
- "{collab_pair}"
- "{collab_pair} 무신사 에디션"

❌ 절대 금지:
- 한쪽만: "{right} 발매"  ← {left} 누락
- 행동어 혼합: "{collab_pair} 발매"  ← 발매/출시/단독은 본문의 몫"""
    else:
        collab_section = ""

    user = (
        f"{target_ctx} 소재에 대해 앱푸시 제목을 작성하세요.\n\n"
        f"## 소재 정보\n"
        f"- 브랜드: {brand or '정보 없음'}\n"
        f"- 프로모션 내용: {promotion_content or '정보 없음'}"
        f"{remarks_section}{collab_section}{nature_hint}"
    )
    return _TITLE_SYSTEM, user


# ── 본문 프롬프트 ──────────────────────────────────────────────────────────

_CONTENT_SYSTEM = """\
당신은 무신사 앱푸시 메시지 전문가입니다.

제목에 자연스럽게 이어지는 앱푸시 본문을 작성하세요.
제목 = 주어(정체성), 본문 = 서술어(행동/혜택). 두 문장을 합쳐 읽으면 하나의 완성된 문장이 됩니다.

## 작성 규칙
1. 반드시 `(광고) `로 시작
2. 길이: 25~60자 (수신거부 문구 제외)
3. **명사형으로 반드시 끝낼 것** — "...발매", "...혜택", "...에디션", "...할인", "...증정", "...드롭", "...컬렉션", "...출시"
4. 혜택 수치(할인율 %, 쿠폰, 적립금)가 있으면 반드시 포함
5. 혜택이 없으면 브랜드·제품의 차별성을 감성적으로 표현

## 올바른 예시
- 제목: "알리스 x 오정규"  →  본문: "(광고) 무신사 단독 한정 발매"
- 제목: "더마토리 x 톡신 공동개발 블랙세럼"  →  본문: "(광고) 4/27~29 단 3일 선론칭"
- 제목: "에스에스알엘 x 현진 무신사 에디션"  →  본문: "(광고) 최대 31% 할인 + 쿠폰 + 사은품 혜택"
- 제목: "빵처럼 맛있는 쉐이크 발견"  →  본문: "(광고) 테이크핏 브레드밀 단독 출시"

## 잘못된 예시 (이렇게 쓰지 마세요)
- 제목: "알리스 x 오정규"  →  본문: "(광고) 알리스 x 오정규 콜라보, 무신사 단독 발매" ← 쌍 반복
- 제목: "수아레 x 너드킹"  →  본문: "(광고) 수아레 x 너드킹 콜라보, 무신사 단독 발매" ← 쌍 반복

## 절대 금지 (위반 시 재작성)
- 동사형·형용사형 종결: "서둘러요", "해보세요", "놓치지 마세요", "받아보세요", "만나보세요", "느껴보세요", "확인하세요", "경험해보세요", "가세요", "오세요"
- 명령조: "사세요!", "하세요!", "마세요!"
- 감탄사·과장어: "대박", "초특가", "엄청난", "놀라운"
- 모호한 표현: "다양한", "많은", "여러"
- 수신거부 문구 포함 금지 (Python이 별도 추가)

## 출력 형식 (JSON만, 다른 텍스트 없이)
{"message": "생성된 본문 (수신거부 문구 제외)", "confidence": 신뢰도점수}

confidence는 1.0~5.0 사이의 float. 5.0에 가까울수록 품질이 높음."""


def _build_content_strategy_section(content_nature: str, benefit_type: str) -> str:
    hints = []
    if content_nature == "단독선발매":
        hints.append("'무신사 단독', '선발매', '선론칭' 중 적합한 표현을 본문에 반드시 포함하세요.")
    elif content_nature == "신규발매":
        hints.append("'발매', '드롭', '출시', '컬렉션' 중 소재에 맞는 표현을 사용하세요.")
    elif content_nature == "프로모션":
        hints.append("혜택 기간과 조건을 구체적으로 서술하세요.")
    if benefit_type == "Price":
        hints.append("할인율(%) 수치가 있다면 본문에 반드시 포함하세요. (예: '최대 30% 할인')")
    elif benefit_type == "Gift":
        hints.append("사은품·증정 표현을 명시하세요. (예: '키링 증정', '사은품 증정')")
    elif benefit_type == "Edition":
        hints.append("희소성·한정성 표현을 강조하세요. (예: '한정 에디션', '단독 굿즈')")
    if content_nature == "단독선발매" and benefit_type == "Edition":
        hints.append("→ '무신사 단독 한정 발매' 패턴이 가장 효과적입니다.")
    elif content_nature == "콜라보레이션" and benefit_type == "Edition":
        hints.append("→ '무신사 단독 한정 발매' 또는 '단독 에디션 발매' 패턴을 우선하세요.")
    if not hints:
        return ""
    return "\n\n## 소재 유형별 작성 전략\n" + "\n".join(f"- {h}" for h in hints)


def build_content_prompt(
    title: str, brand: str, promotion_content: str,
    content_type: str, target: str,
    title_keywords: list = None,
    collab_pair: str = "",
    remarks: str = "",
    content_nature: str = "",
    benefit_type: str = "",
) -> tuple:
    target_ctx = {"여성": "여성 고객을 대상으로 합니다.", "남성": "남성 고객을 대상으로 합니다."}.get(
        target, "전체 고객을 대상으로 합니다."
    )
    remarks_section = f"\n- 마케터 메모: {remarks.strip()}" if remarks and remarks.strip() else ""

    forbidden_kws_section = ""
    if title_keywords:
        kw_list = ", ".join(f'"{kw}"' for kw in title_keywords)
        forbidden_kws_section = (
            f"\n\n## 본문 절대 사용 금지 단어 (제목에 이미 있음)\n{kw_list}\n"
            "이 단어들은 제목이 이미 전달합니다. 본문에서 반복하면 수신자가 같은 내용을 두 번 읽게 됩니다."
        )

    collab_section = ""
    if collab_pair:
        parts = re.split(r'\s*[Xx×]\s*', collab_pair)
        names = " · ".join(p.strip() for p in parts)
        collab_section = (
            f'\n\n## ⚠️ 콜라보 소재 — 본문 중복 금지\n'
            f'제목이 이미 "{collab_pair}" 콜라보를 표시합니다.\n'
            f'본문에서 **{names} 이름을 절대 반복하지 마세요**.\n'
            f'본문은 발매·혜택·기간·긴급성만 서술합니다.\n\n'
            f'✅ 올바른 예: 제목 "{collab_pair}" → 본문 "(광고) 무신사 단독 한정 발매"\n'
            f'❌ 잘못된 예: 제목 "{collab_pair}" → 본문 "(광고) {collab_pair} 콜라보, 무신사 단독 발매" ← 쌍 반복'
        )

    strategy_section = _build_content_strategy_section(content_nature, benefit_type)

    user = (
        f"{target_ctx}\n\n"
        f"## 소재 정보\n"
        f"- 제목: {title or '정보 없음'}\n"
        f"- 브랜드: {brand or '정보 없음'}\n"
        f"- 프로모션 내용: {promotion_content or '정보 없음'}\n"
        f"- 콘텐츠 유형: {content_type or '정보 없음'}"
        f"{remarks_section}{collab_section}{forbidden_kws_section}{strategy_section}"
    )
    return _CONTENT_SYSTEM, user


# ── 자동 수정 프롬프트 ─────────────────────────────────────────────────────

_FIX_SYSTEM = """\
당신은 무신사 앱푸시 메시지 검수 전문가입니다.

## 수정 규칙
1. `(광고) `로 시작
2. 명사형으로 끝낼 것 — "...발매", "...혜택", "...할인", "...에디션", "...드롭", "...출시"
3. 제목의 핵심 단어를 본문에 반복하지 말 것
4. 길이: 25~60자 (수신거부 제외)

## 출력 형식 (JSON만, 다른 텍스트 없이)
{"message": "수정된 본문 (수신거부 문구 제외)", "confidence": 신뢰도점수}"""


def build_content_fix_prompt(
    title: str, promotion_content: str, target: str,
    original_content: str, violations: list,
    title_keywords: list = None,
) -> tuple:
    target_ctx = {"여성": "여성 고객을 대상으로 합니다.", "남성": "남성 고객을 대상으로 합니다."}.get(
        target, "전체 고객을 대상으로 합니다."
    )

    violation_lines = []
    if "verb_ending_in_contents" in violations:
        violation_lines.append("❌ 동사형 종결 위반: 본문이 '서둘러요', '해보세요', '놓치지 마세요' 등 금지된 동사형으로 끝납니다. 명사형으로 끝나야 합니다.")
    if "title_body_overlap_in_contents" in violations:
        violation_lines.append("❌ 제목-본문 중복: 제목의 핵심 단어가 본문에 그대로 반복됩니다. 제목이 이미 전달한 내용을 본문에서 다시 쓰지 마세요.")

    forbidden_kws_section = ""
    if title_keywords:
        kw_list = ", ".join(f'"{kw}"' for kw in title_keywords)
        forbidden_kws_section = f"\n\n## 본문 절대 사용 금지 단어 (제목에 이미 있음)\n{kw_list}"

    user = (
        f"{target_ctx}\n\n"
        f"아래 본문에서 규칙 위반이 발견됐습니다. 위반 사항만 수정하여 올바른 본문을 다시 작성하세요.\n\n"
        f"## 제목 (수정 불가)\n{title}\n\n"
        f"## 위반이 발견된 기존 본문\n{_strip_unsubscribe(original_content)}\n\n"
        f"## 발견된 위반 사항\n{chr(10).join(violation_lines)}\n\n"
        f"## 프로모션 내용 (참고)\n{promotion_content}"
        f"{forbidden_kws_section}"
    )
    return _FIX_SYSTEM, user


# ── 검토(Red Team) 프롬프트 ────────────────────────────────────────────────

_REVIEW_SYSTEM = """\
당신은 무신사 앱푸시 메시지 품질 검토 전문가입니다.

생성된 앱푸시 메시지를 소재 원본과 비교하며 독립적인 관점에서 평가하세요.
생성 규칙이 아닌 실제 수신자 경험·브랜드 적합성·마케팅 효과 관점에서만 평가합니다.

## 평가 기준
1. **정확성**: 소재 원본의 혜택·브랜드 정보가 왜곡 없이 반영됐는가?
2. **수신자 반응**: 메시지를 받았을 때 클릭 의향이 생기는가?
3. **브랜드 일관성**: 무신사 특유의 감각적·트렌디·젊은 톤앤매너에 맞는가?
4. **차별성**: 흔한 할인 광고와 차별화되는 요소가 있는가?
5. **문제 여부**: 과장 표현, 오해 소지, 부정확한 수치가 있는가?
6. **제목-본문 분리**: 제목의 핵심 단어가 본문에 그대로 반복되지 않는가?

## 출력 형식 (JSON만, 다른 텍스트 없이)
{
  "score": 종합점수,
  "verdict": "pass",
  "notes": "핵심 피드백 한 문장 (문제 없으면 빈 문자열)",
  "issues": ["코드1", "코드2"]
}

score: 1.0~5.0 float. verdict: pass(3.5이상) / warning(2.5~3.4) / fail(2.4이하).
issues는 실제 문제가 있을 때만 기재, 없으면 빈 배열 [].
issues 값은 반드시 아래 코드 중에서만 선택하세요:
- fact_mismatch       — 수치·혜택·브랜드 정보가 원본과 다름
- tone_off            — 톤앤매너가 무신사 감성과 맞지 않음
- brand_inconsistency — 브랜드 표현이 부정확하거나 누락됨
- legal_risk          — 과장·오해 소지·법적 위험 표현
- other               — 위 범주에 해당하지 않는 기타 문제"""


def build_review_prompt(
    title: str, contents: str,
    brand: str, promotion_content: str, target: str,
) -> tuple:
    target_ctx = {"여성": "여성 고객 대상", "남성": "남성 고객 대상"}.get(target, "전체 고객 대상")
    contents_clean = _strip_unsubscribe(contents)

    user = (
        f"{target_ctx} 소재를 평가하세요.\n\n"
        f"## 소재 원본\n"
        f"- 브랜드: {brand or '정보 없음'}\n"
        f"- 프로모션 내용: {promotion_content or '정보 없음'}\n\n"
        f"## 생성된 메시지\n"
        f"- 제목: {title or '(없음)'}\n"
        f"- 본문: {contents_clean or '(없음)'}"
    )
    return _REVIEW_SYSTEM, user


# ── 카테고리 유추 프롬프트 ─────────────────────────────────────────────────

_CATEGORY_RULES = """\
당신은 무신사 앱푸시 소재를 분류하는 전문가입니다.

## 선택 규칙
1. 소재 내용에 가장 부합하는 카테고리 코드를 최대 3개 선택
2. 명확하게 관련 있는 카테고리만 선택 (불확실하면 선택 금지)
3. K-Pop 굿즈·앨범 → 111, E-Sports·스포츠구단 → 112, 캐릭터(디즈니/마블 등) → 113, 만화/애니메이션 → 114
4. 일반 패션 소재(단순 할인행사)는 의류/신발 등 해당 카테고리만 선택
5. 관련 카테고리가 없으면 빈 배열 반환

## 출력 형식 (JSON만, 다른 텍스트 없이)
{"codes": ["코드1", "코드2"]}

codes는 최대 3개. 해당 없으면 빈 배열 []."""


def build_category_infer_prompt(
    event_name: str,
    promotion_content: str,
    main_title: str,
    landing_url: str,
    category_list_str: str,
) -> tuple:
    system = f"{_CATEGORY_RULES}\n\n## 선택 가능한 카테고리 (코드: 카테고리명)\n{category_list_str}"

    user = (
        f"아래 소재 정보를 보고, 해당 소재와 관련된 카테고리 코드를 선택하세요.\n\n"
        f"## 소재 정보\n"
        f"- 이벤트명: {event_name or '정보 없음'}\n"
        f"- 프로모션 내용: {promotion_content or '정보 없음'}\n"
        f"- 제목: {main_title or '정보 없음'}\n"
        f"- 랜딩 URL: {landing_url or '정보 없음'}"
    )
    return system, user
