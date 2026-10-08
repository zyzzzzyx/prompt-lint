"""生成前提问协议的测试。

重点不是「模板文案长什么样」，而是它是否真的把单向输出改成了双向确认：
  - 有没有让模型先别动手
  - 有没有强制带默认项（否则模型会为了提问而提问，把任务拖死）
  - 问题是不是来自**本次检测出的**歧义，而不是泛泛的「你还有什么想问的」
"""

from __future__ import annotations

from prompt_lint import analyze, load_rules
from prompt_lint.clarify import build_clarify_block, derive_questions, should_recommend

RULES = load_rules()
BAD = "麻烦你帮我把这篇文章优化一下，写得好看点、高级一点，另外顺便帮我起个标题，尽量简洁全面，不要太多专业术语，谢谢！"
GOOD = (
    "你是一位有 8 年经验的 B 端 SaaS 产品经理。\n\n## 背景\n"
    "受众是公司 CTO，用途是季度汇报；他们已了解产品现状，不了解竞品动态。\n\n## 任务\n"
    "对比 A、B、C 三家竞品的定价策略，列出差异点。\n\n## 输出\n"
    "用 Markdown 表格，列为 竞品 / 入门价 / 企业价 / 差异点，不超过 8 行。\n\n## 标准\n"
    "每条差异必须有官网来源支撑，找不到来源就写「未公开」。"
)


def test_default_level_is_standard():
    assert RULES["clarify_protocol"]["default_level"] == "standard"
    assert analyze(BAD).clarify_level == "standard"


def test_off_produces_nothing():
    rep = analyze(BAD, clarify_level="off")
    assert rep.clarify == ""
    assert "生成前提问协议" not in rep.rewrite


def test_standard_is_appended_to_rewrite():
    rep = analyze(BAD, clarify_level="standard")
    assert rep.clarify
    assert rep.rewrite.rstrip().endswith(rep.clarify.rstrip()) is False  # 后面还有人类向的附加信息
    assert rep.clarify in rep.rewrite


def test_standard_forces_confirm_before_generating():
    """协议的核心：把默认动作从「回答」改成「核对」。"""
    block = analyze(BAD, clarify_level="standard").clarify
    assert "先不要产出" in block
    assert "停在这里等我" in block
    assert "复述你的理解" in block
    assert "假设" in block


def test_every_level_must_have_default_option():
    """没有兜底条款的模型会把简单任务也拖成两轮，这是硬要求。"""
    for level in ("light", "standard", "strict"):
        block = analyze(BAD, clarify_level=level).clarify
        assert "默认" in block, f"{level} 档缺少默认项条款"
        assert "不要为了凑数而提问" in block or "不要追问第二轮" in block or "正常产出" in block, \
            f"{level} 档缺少「信息足够就直接开工 / 只问一轮」的兜底"


def test_strict_forbids_fabrication():
    block = analyze(BAD, clarify_level="strict").clarify
    assert "严禁编造" in block
    assert "待确认" in block
    assert "自相矛盾" in block


def test_light_does_not_block_output():
    """light 档的定位是不打断流程，所以它不能出现停等字样。"""
    block = analyze(BAD, clarify_level="light").clarify
    assert "正常产出" in block
    assert "停在这里等我" not in block


def test_questions_come_from_detected_issues():
    """问题必须是本次检出的歧义点，而不是模板里那句万能的「你还有什么想问的」。"""
    rep = analyze(BAD, clarify_level="standard")
    qs = derive_questions(rep.issues, RULES, 3)
    assert qs, "糟糕提示词应当能派生出问题"
    issue_ids = {i.id for i in rep.issues}
    assert "subjective" in issue_ids  # 好看点/高级 —— 不可验证形容词
    assert any("好看点" in q or "高级" in q for q in qs), qs
    for q in qs:
        assert q in rep.clarify


def test_question_count_respects_level_cap():
    for level, cap in (("standard", 3), ("strict", 5)):
        qs = derive_questions(analyze(BAD).issues, RULES, cap)
        assert len(qs) <= cap


def test_clean_prompt_falls_back_gracefully():
    """没有问题可问时，协议要自己让路，不许硬凑。"""
    block = build_clarify_block(RULES, [], "standard")
    assert "未检出必答项" in block
    assert "不要为了凑数而提问" in block


def test_good_prompt_still_gets_the_escape_hatch():
    """即使派生出了问题，也必须保留「信息够了就直接开工」的出口。"""
    block = analyze(GOOD, clarify_level="standard").clarify
    assert "信息已足够，我开始产出" in block
    assert "不要为了凑数而提问" in block


def test_recommendation_logic():
    bad = analyze(BAD, clarify_level="standard")
    assert bad.score < RULES["clarify_protocol"]["recommend_when"]["score_below"]
    assert bad.clarify_recommended is True

    # 提示词里已经主动要求模型提问 → 不再推荐
    asked = analyze(BAD + " 有不确定的地方先问我。")
    assert any(b["key"] == "has_clarify_request" for b in asked.bonuses)
    assert asked.clarify_recommended is False


def test_bonus_signal_detects_clarify_intent():
    for text in ("生成前先问我", "有不确定的地方向我确认", "不确定的地方先问我再开始"):
        rep = analyze(text)
        assert any(b["key"] == "has_clarify_request" for b in rep.bonuses), text


def test_unknown_level_is_safe():
    assert build_clarify_block(RULES, [], "nonexistent") == ""


def test_should_recommend_helpers():
    assert should_recommend(50, [], RULES) is True
    assert should_recommend(100, [], RULES) is False
    assert should_recommend(50, [{"key": "has_clarify_request"}], RULES) is False
