"""PromptLint 命令行入口。

用法：
    python -m prompt_lint.cli "帮我优化一下这个方案"
    python -m prompt_lint.cli -f prompt.txt
    python -m prompt_lint.cli -f prompt.txt --md report.md
    echo "..." | python -m prompt_lint.cli
    python -m prompt_lint.cli -f p.txt --json
"""

from __future__ import annotations

import argparse
import json
import sys

from .analyzer import analyze, load_rules

SEV_TAG = {"high": "[高危]", "medium": "[中等]", "low": "[轻微]"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_text(rep, color: bool = True) -> str:
    L = []
    L.append("=" * 62)
    L.append(f"  PromptLint 诊断结果   得分 {rep.score}/100    评级 {rep.grade}")
    L.append(f"  {rep.verdict}")
    L.append("=" * 62)
    s = rep.stats
    L.append(f"  长度 {s['chars']} 字 / {s['sentences']} 句 / {s['lines']} 段")
    L.append(f"  问题 {s['issue_count']} 处（高危 {s['high']} / 中等 {s['medium']} / 轻微 {s['low']}）")
    if rep.bonuses:
        L.append("  加分：" + "，".join(f"{b['label']} +{b['score']}" for b in rep.bonuses))
    L.append("")

    if not rep.issues:
        L.append("  未检出明显歧义。")
    for i in rep.issues:
        L.append(f"{SEV_TAG[i.severity]} {i.name}   (-{i.penalty} 分)")
        hits = "、".join(f"「{h}」" for h in i.hits[:5])
        if hits:
            L.append(f"     命中：{hits}" + (f" 等 {i.count} 处" if i.count > 5 else ""))
        L.append(f"     模型视角：{i.model_view}")
        L.append(f"     怎么改：{i.suggestion}")
        L.append("")

    L.append("-" * 62)
    L.append(rep.rewrite)
    return "\n".join(L)


def render_markdown(rep) -> str:
    L = ["# PromptLint 诊断报告", ""]
    L.append(f"**得分 {rep.score}/100 ｜ 评级 {rep.grade}** — {rep.verdict}")
    L.append("")
    L.append(f"| 指标 | 值 |\n|---|---|\n"
             f"| 字符数 | {rep.stats['chars']} |\n"
             f"| 句数 | {rep.stats['sentences']} |\n"
             f"| 问题总数 | {rep.stats['issue_count']} |\n"
             f"| 高危 / 中等 / 轻微 | {rep.stats['high']} / {rep.stats['medium']} / {rep.stats['low']} |")
    L.append("")
    L.append("## 问题清单")
    L.append("")
    L.append("| 级别 | 问题 | 命中片段 | 扣分 |")
    L.append("|---|---|---|---|")
    for i in rep.issues:
        hits = "、".join(f"`{h}`" for h in i.hits[:4]) or "—"
        L.append(f"| {i.severity} | {i.name} | {hits} | -{i.penalty} |")
    L.append("")
    for i in rep.issues:
        L.append(f"### {SEV_TAG[i.severity]} {i.name}")
        L.append("")
        L.append(f"- **模型为什么会误解**：{i.model_view}")
        L.append(f"- **怎么改**：{i.suggestion}")
        L.append("")
    L.append("---")
    L.append("")
    L.append(rep.rewrite)
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prompt-lint", description="提示词歧义诊断（模型视角）")
    ap.add_argument("text", nargs="?", help="待检测的提示词文本")
    ap.add_argument("-f", "--file", help="从文件读取提示词")
    ap.add_argument("--rules", help="自定义规则表路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--md", metavar="OUT", help="输出 Markdown 报告到文件")
    ap.add_argument("--rewrite-only", action="store_true", help="只输出重写后的提示词")
    args = ap.parse_args(argv)

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        ap.error("请提供提示词文本（位置参数、-f 文件，或管道输入）")

    rules = load_rules(None if not args.rules else __import__("pathlib").Path(args.rules))
    rep = analyze(text, rules)

    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    elif args.rewrite_only:
        print(rep.rewrite)
    else:
        print(render_text(rep))

    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(render_markdown(rep))
        print(f"\n[已写出 Markdown 报告] {args.md}", file=sys.stderr)

    return 0 if rep.score >= 75 else 1


if __name__ == "__main__":
    raise SystemExit(main())
