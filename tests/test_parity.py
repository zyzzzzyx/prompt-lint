"""Python 引擎 与 浏览器前端 的一致性校验。

两端共用同一份 rules.json，必须给出完全相同的分数与问题清单。
用 node 在沙箱里加载 web/app.js（注入最小 DOM stub），跑同一批样例后比对。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import pytest
from prompt_lint import analyze, load_rules

RULES = load_rules()

CASES = {
    "bad": "麻烦你帮我把这篇文章优化一下，写得好看点、高级一点，另外顺便帮我起个标题，尽量简洁全面，不要太多专业术语，谢谢！",
    "mid": "请帮我把下面的会议记录整理成待办事项，每条包含负责人和截止时间，用表格输出，5 条以内。",
    "good": (
        "你是一位有 8 年经验的 B 端 SaaS 产品经理。\n\n## 背景\n"
        "受众是公司 CTO，用途是季度汇报；他们已了解产品现状，不了解竞品动态。\n\n## 任务\n"
        "对比 A、B、C 三家竞品的定价策略，列出差异点。\n\n## 输出\n"
        "用 Markdown 表格，列为 竞品 / 入门价 / 企业价 / 差异点，不超过 8 行。\n\n## 标准\n"
        "每条差异必须有官网来源支撑，找不到来源就写「未公开」。"
    ),
    "multi": "帮我总结一下这个文档，另外顺便提炼出 3 个金句，还有帮我翻译成英文，对了再看看有没有错别字。",
    "negation": "给我写一段产品介绍，不要用专业术语，别太长，不要那种营销腔，别提竞品。",
    "conflict": "请详细但简短地介绍一下量子计算，要全面又精简。",
}


def _run_js() -> dict | None:
    if not shutil.which("node"):
        pytest.skip("未安装 node，跳过双端一致性校验")
    script = ROOT / "tests" / "parity_runner.js"
    cases_file = ROOT / ".tmp" / "parity_cases.json"
    out_file = ROOT / ".tmp" / "parity_js.json"
    cases_file.parent.mkdir(exist_ok=True)
    cases_file.write_text(json.dumps(CASES, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run(["node", str(script)], cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        pytest.fail(f"JS 端执行失败：{res.stderr[:800]}")
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_python_js_parity():
    js = _run_js()
    for key, text in CASES.items():
        rep = analyze(text, RULES)
        py_ids = [f"{i.id}:{i.penalty}" for i in rep.issues]
        assert js[key]["score"] == rep.score, f"[{key}] 分数不一致 py={rep.score} js={js[key]['score']}"
        assert js[key]["ids"] == py_ids, f"[{key}] 问题清单不一致\n py={py_ids}\n js={js[key]['ids']}"


def test_rules_json_in_sync():
    """web/rules.json 必须是 prompt_lint/rules.json 的副本。"""
    a = (ROOT / "prompt_lint" / "rules.json").read_text(encoding="utf-8")
    b = (ROOT / "web" / "rules.json").read_text(encoding="utf-8")
    assert a == b, "规则表不同步，请运行：cp prompt_lint/rules.json web/rules.json"
