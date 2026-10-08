"""生成前提问协议（Clarify-before-Generate）。

默认的交互是单向的：用户一次性把话说完，模型立刻开始生成。
缺的信息模型不会追问，它只会用训练分布里的平均值补上——
歧义正是在这一步变成错误产出的。

本协议把「先确认再产出」写成提示词里的一段固定指令，
让模型主动暴露它的不确定点，把歧义消灭在生成之前。

模板内容全部来自 rules.json，Python 与浏览器端共用。
"""

from __future__ import annotations

from typing import Any

MISSING_MARK = "<全文未出现>"


def derive_questions(issues: list[Any], rules: dict, max_n: int) -> list[str]:
    """把检测出的歧义点翻译成模型该问的问题。

    高危优先，其次中等；同一条规则只问一次；总数不超过 max_n。
    """
    ask_map = {d["id"]: d.get("ask") for d in rules.get("detectors", [])}
    out: list[str] = []
    seen: set[str] = set()
    for sev in ("high", "medium"):
        for i in issues:
            if len(out) >= max_n:
                return out
            if i.severity != sev or i.id in seen:
                continue
            q = ask_map.get(i.id)
            if not q:
                continue
            seen.add(i.id)
            hits = "、".join(i.hits[:3]) if i.hits and i.hits[0] != MISSING_MARK else "（未写明）"
            out.append(q.replace("{hits}", hits))
    return out


def list_levels(rules: dict) -> list[dict]:
    """供 UI / CLI 列出可选档位：(key, label, desc)。"""
    cp = rules.get("clarify_protocol") or {}
    out = []
    for key, lv in (cp.get("levels") or {}).items():
        out.append({"key": key, "label": lv.get("label", key), "desc": lv.get("desc", "")})
    return out


def default_level(rules: dict) -> str:
    return (rules.get("clarify_protocol") or {}).get("default_level", "standard")


def build_clarify_block(rules: dict, issues: list[Any], level: str | None = None) -> str:
    """生成协议正文。level 为 off / 未知档位时返回空串。"""
    cp = rules.get("clarify_protocol")
    if not cp:
        return ""
    level = level or cp.get("default_level", "standard")
    lv = (cp.get("levels") or {}).get(level)
    if not lv or not lv.get("steps"):
        return ""

    max_n = int(lv.get("max_questions", 3))
    qs = derive_questions(issues, rules, max_n)
    if qs:
        qtext = "\n".join(f"{n}. {q}" for n, q in enumerate(qs, 1))
    else:
        qtext = cp.get("no_question_fallback", "")

    body = "\n\n".join(
        s.replace("{max}", str(max_n)).replace("{questions}", qtext)
        for s in lv["steps"]
    )
    return f"{cp.get('standalone_header', '# 生成前提问协议')}（{lv['label']}）\n\n{body}"


def should_recommend(score: int, bonuses: list[dict], rules: dict) -> bool:
    """低分且原提示词里没有主动要求模型提问 → 建议开启协议。"""
    cp = rules.get("clarify_protocol") or {}
    threshold = int((cp.get("recommend_when") or {}).get("score_below", 88))
    already = any(b.get("key") == "has_clarify_request" for b in bonuses)
    return score < threshold and not already
