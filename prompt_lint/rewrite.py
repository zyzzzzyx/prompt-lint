"""把原始提示词重构成模型友好的 RCFT 结构化提示词。

设计原则：
  1. 能提取的信息直接搬过去，不要让用户重写一遍；
  2. 缺失的信息给占位 + 一句引导问题，而不是留空；
  3. 明确列出被删掉的噪声，让用户看到自己说了多少废话。
"""

from __future__ import annotations

import re
from typing import Any

from .analyzer import scan_keywords, split_sentences

PLACEHOLDER = lambda hint: f"[待补充：{hint}]"

ROLE_PAT = re.compile(r"(你是|你是一位|作为|扮演|充当|假设你|请你作为|请以)[^。！？\n]{0,60}")
CONTEXT_PAT = re.compile(
    r"[^。！？\n]*?(受众|读者|用户|对象|面向|背景|场景|目的|目标|用途|行业|公司|"
    r"产品|项目|客户|小白|新手|专家|学生|领导|年龄|水平)[^。！？\n]*"
)
FORMAT_PAT = re.compile(
    r"[^。！？\n]*?(表格|列表|分点|条列|分段|markdown|Markdown|json|JSON|"
    r"yaml|csv|代码块|用表格|分条|编号|项目符号|标题|章节|模板)[^。！？\n]*"
)
LENGTH_PAT = re.compile(r"[^。！？\n]*?\d+\s*(字|词|句|段|条|行|页|个|点|分钟)[^。！？\n]*")
NEG_PAT = re.compile(r"[^。！？\n]*?(不要|别|不能|不可以|禁止|避免|切勿|不许|无需|不用)[^。！？\n]*")
EXAMPLE_PAT = re.compile(r"[^。！？\n]*?(例如|比如|示例|样例|像这样|参考这个|打个比方)[^。！？\n]*")

NOISE_WORDS = [
    "麻烦你", "麻烦", "拜托", "劳烦", "不好意思", "打扰了", "谢谢", "感谢", "辛苦了",
    "就是说", "你知道的", "你懂吧", "嗯", "啊", "哦", "好吧", "先这样", "那个什么",
    "随便", "呗", "对吧", "是吧", "请帮",
]


def _clean(s: str) -> tuple[str, list[str]]:
    """删掉口语噪声，返回 (清洗后文本, 被删词列表)。"""
    removed: list[str] = []
    out = s
    for w in sorted(NOISE_WORDS, key=len, reverse=True):
        if w in out:
            cnt = out.count(w)
            out = out.replace(w, "")
            removed.append(f"「{w}」×{cnt}")
    out = re.sub(r"\s{2,}", " ", out).strip(" ，,。")
    return out, removed


def _first_match(pat: re.Pattern, text: str) -> str:
    m = pat.search(text)
    return m.group(0).strip(" ，,、") if m else ""


def _find_task_sentence(text: str, rules: dict) -> str:
    """挑出最像任务指令的一句：含明确动词、且不是纯背景描述。"""
    verb_det = next((d for d in rules["detectors"] if d["id"] == "missing_verb"), None)
    verbs = verb_det["keywords"] if verb_det else []
    sentences = split_sentences(text)
    scored = []
    for s in sentences:
        vhits = len(scan_keywords(s, verbs))
        if vhits:
            scored.append((vhits + len(s) / 200, s))
    if not scored:
        return ""
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def _issue_by_id(issues: list[Any], iid: str):
    return next((i for i in issues if i.id == iid), None)


def build_rewrite(text: str, rules: dict, issues: list[Any]) -> str:
    tpl = rules["rewrite_template"]
    ask_map = {d["id"]: d.get("ask") for d in rules["detectors"]}

    task_sent, removed = _clean(_find_task_sentence(text, rules))

    # ---- 角色 ----
    role_line = _first_match(ROLE_PAT, text)
    if role_line:
        role = role_line
    else:
        role = f"你是 {PLACEHOLDER('领域 + 身份，例如「有 5 年经验的 B 端产品经理」')}。\n判断标准是 {PLACEHOLDER('它该按什么标准做取舍')}。"

    # ---- 背景 ----
    ctx_line = _first_match(CONTEXT_PAT, text)
    if ctx_line:
        context = ctx_line
    else:
        context = (
            f"受众是 {PLACEHOLDER('谁')}；用途是 {PLACEHOLDER('用在哪个场景')}。\n"
            f"他们已经知道 {PLACEHOLDER('')}，不知道 {PLACEHOLDER('')}。"
        )

    # ---- 输入材料 ----
    mi = _issue_by_id(issues, "missing_input")
    if mi:
        input_block = (
            f"> ⚠️ 原文提到了「{'、'.join(mi.hits[:3])}」，但这里没有附上内容。\n"
            f"> 模型看不到你本地的文件，也不会说「我没看到」——它会直接编一份然后作答。\n\n"
            f"```text\n（在此粘贴原文/数据/代码）\n```"
        )
    elif has_code := ("```" in text):
        blocks = re.findall(r"```[\s\S]*?```", text)
        input_block = "已识别到以下附带材料，请确认内容完整：\n\n" + "\n\n".join(blocks)
    else:
        input_block = f"（无外部材料则留空）\n{PLACEHOLDER('如需模型处理既有文本，粘贴在此')}"

    # ---- 任务 ----
    if task_sent:
        task = task_sent
        vfb = _issue_by_id(issues, "verb_fuzzy")
        if vfb:
            task += f"\n\n> ⚠️ 这句话里的动作仍然模糊。补一句判定标准：" \
                    f"改哪个维度（长度/准确性/语气/结构）？改完怎么算合格？"
    else:
        task = PLACEHOLDER("用祈使句写清唯一主动作，例如「把下面的会议记录整理成 5 条待办，每条含负责人和截止时间」")

    # ---- 格式与长度 ----
    fmt_line = _first_match(FORMAT_PAT, text) or ""
    len_line = _first_match(LENGTH_PAT, text) or ""
    if fmt_line or len_line:
        fmt = " ｜ ".join([x for x in (fmt_line, len_line) if x])
    else:
        fmt = (
            f"形态：{PLACEHOLDER('Markdown 表格 / 编号列表 / JSON / 分段标题，选一个')}\n"
            f"长度：{PLACEHOLDER('数字边界，例如「300 字以内」或「5 条」')}"
        )

    # ---- 约束 ----
    constraints: list[str] = []
    neg = _first_match(NEG_PAT, text)
    if neg:
        constraints.append(
            f"- ⚠️ 原文用了否定式：{neg}\n"
            f"  → 改写成正向表述：必须 {PLACEHOLDER('把「不要 X」翻成「只允许 Y」')}"
        )
    hed = _issue_by_id(issues, "hedging")
    if hed:
        constraints.append(
            f"- ⚠️ 「{'、'.join(hed.hits[:3])}」没有可执行边界"
            f" → 换成数字或明确条件"
        )
    subj = _issue_by_id(issues, "subjective")
    if subj:
        constraints.append(
            f"- ⚠️ 「{'、'.join(subj.hits[:3])}」不可验证"
            f" → 拆成可检查特征，或给一个参考样板"
        )
    contra = _issue_by_id(issues, "contradiction")
    if contra:
        constraints.append(
            f"- ⚠️ 原文同时要求「{contra.hits[0]}」和「{contra.hits[-1]}」，两者冲突"
            f" → 排出优先级，明说放弃哪一个"
        )
    mt = _issue_by_id(issues, "multi_task")
    if mt:
        constraints.append(
            f"- ⚠️ 一段话里塞了多个任务 → 拆成编号列表，一次只交付一件事"
        )
    if not constraints:
        constraints.append(f"- {PLACEHOLDER('必须满足什么？用正向表述')}")

    # ---- 示例 ----
    ex_line = _first_match(EXAMPLE_PAT, text)
    example = ex_line if ex_line else \
        f"（可选但收益最高）{PLACEHOLDER('给一个输入→输出样例，模型的对齐精度会明显提升')}"

    # ---- 组装 ----
    parts = [
        f"# 角色\n{role}",
        f"# 背景与目标\n{context}",
        f"# 输入材料\n{input_block}",
        f"# 任务\n{task}",
        f"# 输出格式与长度\n{fmt}",
        "# 约束\n" + "\n".join(constraints),
        f"# 示例\n{example}",
    ]
    body = "\n\n".join(parts)

    # ---- 待回答问题 ----
    questions: list[str] = []
    for i in issues:
        if i.severity != "high":
            continue
        q = ask_map.get(i.id)
        if not q:
            continue
        hits = "、".join(i.hits[:3]) if i.hits and i.hits[0] != "<全文未出现>" else "（未写明）"
        questions.append(q.replace("{hits}", hits))
    for i in issues:
        if i.severity == "medium" and i.id in ask_map and len(questions) < 6:
            q = ask_map[i.id]
            hits = "、".join(i.hits[:3]) if i.hits and i.hits[0] != "<全文未出现>" else "（未写明）"
            questions.append(q.replace("{hits}", hits))

    foot = []
    if removed:
        foot.append("## 已清理的噪声\n" + "\n".join(f"- {r}" for r in removed))
    if questions:
        foot.append("## 动手前先回答（按优先级）\n" +
                    "\n".join(f"{n}. {q}" for n, q in enumerate(questions[:6], 1)))

    tail = ("\n\n---\n\n" + "\n\n".join(foot)) if foot else ""
    return f"### 重写后的提示词（{tpl['name']}）\n\n{body}{tail}"
