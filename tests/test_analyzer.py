"""PromptLint 引擎测试。"""

import pytest

from prompt_lint import analyze, load_rules
from prompt_lint.analyzer import scan_keywords, has_material

RULES = load_rules()

BAD = "麻烦你帮我把这篇文章优化一下，写得好看点、高级一点，另外顺便帮我起个标题，尽量简洁全面，不要太多专业术语，谢谢！"

GOOD = """你是一位有 8 年经验的 B 端 SaaS 产品经理。

## 背景
受众是公司 CTO，用途是季度汇报；他们已了解产品现状，不了解竞品动态。

## 任务
对比 A、B、C 三家竞品的定价策略，列出差异点。

## 输出
用 Markdown 表格，列为 竞品 / 入门价 / 企业价 / 差异点，不超过 8 行。

## 标准
每条差异必须有官网来源支撑，找不到来源就写「未公开」。
"""


def ids(rep):
    return {i.id for i in rep.issues}


# ---------------- 扫描原语 ----------------

def test_scan_keywords_dedup_overlap():
    hits = scan_keywords("这个东西", ["这个", "这个东西"])
    assert [h[2] for h in hits] == ["这个东西"], "长词优先，重叠只算一次"


def test_scan_keywords_no_cross_count():
    hits = scan_keywords("这个和那个", ["这个", "那个"])
    assert len(hits) == 2


def test_has_material():
    assert has_material("看这个 ```print(1)```")
    assert has_material("参考 https://example.com")
    assert not has_material("帮我看看这篇文章")


# ---------------- 各检测器 ----------------

def test_filler_and_politeness():
    assert "filler" in ids(analyze(BAD, RULES))
    assert "politeness" in ids(analyze(BAD, RULES))


def test_vague_pronoun_boosted_when_early():
    rep = analyze("把这个改一下", RULES)
    issue = next(i for i in rep.issues if i.id == "vague_pronoun")
    assert issue.severity == "high", "指代出现在开头，应升级为高危"


def test_vague_pronoun_not_boosted_when_late():
    text = "这是一段很长的背景说明，用于占位以满足比例判断的阈值要求，确保位置靠后。" + "再把这个改一下"
    rep = analyze(text, RULES)
    issue = next((i for i in rep.issues if i.id == "vague_pronoun"), None)
    if issue:
        assert issue.severity == "medium"


def test_missing_verb():
    assert "missing_verb" in ids(analyze("关于人工智能的一些思考", RULES))


def test_verb_present_not_flagged():
    assert "missing_verb" not in ids(analyze("帮我总结这份报告", RULES))


def test_missing_input_flagged():
    assert "missing_input" in ids(analyze("帮我把这篇文章翻译成英文", RULES))


def test_missing_input_skipped_when_material_present():
    text = "帮我把下面这篇文章翻译成英文\n```\n原文内容在这里，足够长以被视为材料。\n```"
    assert "missing_input" not in ids(analyze(text, RULES))


def test_conflict_detected():
    assert "contradiction" in ids(analyze("请详细但简短地介绍一下量子计算", RULES))


def test_negation_detected():
    assert "negation" in ids(analyze("写一段介绍，不要用专业术语", RULES))


def test_subjective_detected():
    assert "subjective" in ids(analyze("让它看起来更有高级感", RULES))


def test_multitask_uses_excess_counting():
    one = analyze("总结一下，另外提炼金句，还有翻译", RULES)
    many = analyze("总结一下，另外提炼金句，还有翻译，此外检查错别字，顺便配图，再者标出重点", RULES)
    p1 = next(i for i in one.issues if i.id == "multi_task").penalty
    p2 = next(i for i in many.issues if i.id == "multi_task").penalty
    assert p2 > p1, "任务越多扣分越多，但不按总命中数线性叠加"


def test_absence_detectors_pass_on_good_prompt():
    rep = analyze(GOOD, RULES)
    for d in ("missing_verb", "missing_context", "missing_format", "missing_role"):
        assert d not in ids(rep), f"{d} 不应在规范提示词上触发"


# ---------------- 评分 ----------------

def test_score_bounded():
    for t in ["", "嗯", BAD, GOOD, "写" * 500]:
        rep = analyze(t, RULES)
        assert 0 <= rep.score <= 100


def test_grade_monotonic():
    assert analyze(BAD, RULES).score < analyze(GOOD, RULES).score


def test_good_prompt_scores_high():
    rep = analyze(GOOD, RULES)
    assert rep.score >= 88, f"规范提示词应达 A 级，实得 {rep.score}（问题：{[i.id for i in rep.issues]}）"


def test_bad_prompt_scores_low():
    assert analyze(BAD, RULES).score < 45


def test_empty_input():
    rep = analyze("", RULES)
    assert rep.score == 0 and rep.issues == []


def test_bonus_applied():
    rep = analyze(GOOD, RULES)
    assert any(b["key"] == "has_success_criteria" for b in rep.bonuses)


# ---------------- 重写 ----------------

def test_rewrite_has_all_sections():
    rw = analyze(BAD, RULES).rewrite
    for h in ("# 角色", "# 背景与目标", "# 输入材料", "# 任务", "# 输出格式与长度", "# 约束"):
        assert h in rw, f"重写结果缺少 {h}"


def test_rewrite_keeps_original_task():
    assert "起个标题" in analyze(BAD, RULES).rewrite


def test_rewrite_asks_questions():
    assert "动手前先回答" in analyze(BAD, RULES).rewrite


def test_rewrite_reuses_existing_role():
    rw = analyze("你是一位资深律师。帮我看看这段合同条款有没有坑。", RULES).rewrite
    assert "资深律师" in rw
    assert "[待补充：领域" not in rw


def test_rewrite_lists_removed_noise():
    assert "已清理的噪声" in analyze(BAD, RULES).rewrite


def test_no_hallucinated_material_section_when_code_provided():
    text = "帮我重构这段代码\n```python\ndef f(): pass\n```"
    rw = analyze(text, RULES).rewrite
    assert "已识别到以下附带材料" in rw


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
