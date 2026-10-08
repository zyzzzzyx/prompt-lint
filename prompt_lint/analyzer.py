"""PromptLint 分析引擎。

站在模型视角，检测提示词中会造成歧义 / 偏差 / 误解的片段。
规则全部来自 rules.json，与浏览器前端共用同一份规则表。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).with_name("rules.json")

SENTENCE_SPLIT = re.compile(r"[。！？；;\n]+|(?<=[a-zA-Z])\.(?=\s)")


@dataclass
class Issue:
    id: str
    name: str
    dimension: str
    severity: str
    penalty: int
    count: int
    hits: list[str]
    model_view: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    score: int
    grade: str
    verdict: str
    issues: list[Issue]
    bonuses: list[dict[str, Any]]
    stats: dict[str, Any]
    rewrite: str
    raw_length: int
    clarify: str = ""            # 生成前提问协议正文（可单独复制）
    clarify_level: str = "off"   # off / light / standard / strict
    clarify_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade,
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "bonuses": self.bonuses,
            "stats": self.stats,
            "rewrite": self.rewrite,
            "raw_length": self.raw_length,
            "clarify": self.clarify,
            "clarify_level": self.clarify_level,
            "clarify_recommended": self.clarify_recommended,
        }


def load_rules(path: Path | None = None) -> dict[str, Any]:
    p = path or RULES_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 命中扫描
# --------------------------------------------------------------------------

def scan_keywords(text: str, keywords: list[str]) -> list[tuple[int, int, str]]:
    """扫描关键词，返回去重后的 (start, end, word) 列表。

    中文没有词边界，长词优先匹配，并用占用数组消除重叠
    （例如 "这个" 与 "这个东西" 只算一次命中）。
    """
    occupied = [False] * len(text)
    hits: list[tuple[int, int, str]] = []
    for kw in sorted(keywords, key=len, reverse=True):
        if not kw:
            continue
        start = 0
        while True:
            idx = text.find(kw, start)
            if idx == -1:
                break
            end = idx + len(kw)
            if not any(occupied[idx:end]):
                for i in range(idx, end):
                    occupied[i] = True
                hits.append((idx, end, kw))
            start = idx + 1
    hits.sort()
    return hits


def scan_patterns(text: str, patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.MULTILINE):
            frag = m.group(0).strip()
            if frag:
                out.append(frag)
    return out


def has_material(text: str) -> bool:
    """判断提示词里是否真的附带了材料（代码块 / 链接 / 较长引号块）。"""
    if "```" in text:
        return True
    if re.search(r"https?://", text):
        return True
    for m in re.finditer(r"[\"'“‘「『]([^\"'”’」』]{20,})[\"'”’」』]", text):
        if m.group(1).strip():
            return True
    return False


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s and s.strip()]


# --------------------------------------------------------------------------
# 检测器
# --------------------------------------------------------------------------

def _penalty_for(rules: dict, severity: str, count: int, max_penalty: int) -> int:
    weight = rules["scoring"]["severity_weight"][severity]
    cap = rules["scoring"]["hit_cap"]
    return min(weight * min(count, cap), max_penalty)


def _run_keyword(det: dict, text: str) -> tuple[list[tuple[int, int, str]], str]:
    hits = scan_keywords(text, det.get("keywords", []))
    sev = det["severity"]
    if det.get("boost_if_early") and hits:
        ratio = det.get("boost_ratio", 1 / 3)
        if hits[0][0] < len(text) * ratio:
            sev = det.get("boost_severity", sev)
    return hits, sev


def analyze(text: str, rules: dict[str, Any] | None = None,
            clarify_level: str | None = None) -> Report:
    rules = rules or load_rules()
    text = (text or "").strip()
    issues: list[Issue] = []
    bonuses: list[dict[str, Any]] = []

    if not text:
        return Report(
            score=0, grade="F", verdict="提示词为空",
            issues=[], bonuses=[], stats={}, rewrite="", raw_length=0,
        )

    sentences = split_sentences(text)
    material = has_material(text)

    for det in rules["detectors"]:
        kind = det["kind"]
        hits: list[tuple[int, int, str]] = []
        frags: list[str] = []
        sev = det["severity"]

        if det.get("skip_if_material_present") and material:
            continue

        if kind == "keyword":
            hits, sev = _run_keyword(det, text)

        elif kind == "regex":
            frags = scan_patterns(text, det.get("patterns", []))
            if frags:
                hits = [(0, len(f), f) for f in frags]

        elif kind == "absence":
            present = scan_keywords(text, det.get("keywords", []))
            extra = det.get("regex_positive")
            if not present and extra and re.search(extra, text):
                present = [(0, 0, "regex")]
            if not present:
                hits = [(0, 0, "<全文未出现>")]

        elif kind == "counter":
            found = scan_keywords(text, det.get("keywords", []))
            if len(found) >= det.get("threshold", 2):
                hits = found

        elif kind == "conflict":
            for a, b in det.get("pairs", []):
                ha = scan_keywords(text, [a])
                hb = scan_keywords(text, [b])
                if ha and hb:
                    hits.append((ha[0][0], ha[0][1], a))
                    hits.append((hb[0][0], hb[0][1], b))

        elif kind == "structure":
            lines = [l for l in text.splitlines() if l.strip()]
            if len(lines) <= 1 and len(text) >= det.get("min_length", 100):
                hits = [(0, min(len(text), 40), text[:40] + ("…" if len(text) > 40 else ""))]

        if not hits:
            continue

        count = max(1, len(hits))
        # counter 型：只按"超出阈值的部分"计分，避免长文天然被重罚
        if det.get("count_mode") == "excess":
            count = max(1, len(hits) - det.get("threshold", 2) + 1)
        penalty = _penalty_for(rules, sev, count, det.get("max_penalty", 99))
        shown = []
        for _, _, w in hits[:6]:
            if w not in shown:
                shown.append(w)

        issues.append(Issue(
            id=det["id"],
            name=det["name"],
            dimension=det["dimension"],
            severity=sev,
            penalty=penalty,
            count=count,
            hits=shown,
            model_view=det["model_view"],
            suggestion=det["suggestion"],
        ))

    # 加分项
    for key, sig in rules["bonus_signals"].items():
        if scan_keywords(text, sig["keywords"]):
            bonuses.append({"key": key, "label": sig["label"],
                            "score": rules["scoring"]["bonus"][key]})

    score = rules["scoring"]["base"]
    score -= sum(i.penalty for i in issues)
    score += sum(b["score"] for b in bonuses)
    score = max(0, min(100, score))

    grade_info = next(g for g in rules["scoring"]["grades"] if score >= g["min"])

    issues.sort(key=lambda i: (-{"high": 3, "medium": 2, "low": 1}[i.severity], -i.penalty))

    stats = {
        "chars": len(text),
        "sentences": len(sentences),
        "lines": len([l for l in text.splitlines() if l.strip()]),
        "issue_count": len(issues),
        "high": sum(1 for i in issues if i.severity == "high"),
        "medium": sum(1 for i in issues if i.severity == "medium"),
        "low": sum(1 for i in issues if i.severity == "low"),
    }

    from .clarify import build_clarify_block, default_level, should_recommend
    from .rewrite import build_rewrite  # 局部导入，便于单独使用本模块

    level = clarify_level or default_level(rules)

    return Report(
        score=score,
        grade=grade_info["label"],
        verdict=grade_info["verdict"],
        issues=issues,
        bonuses=bonuses,
        stats=stats,
        rewrite=build_rewrite(text, rules, issues, level),
        raw_length=len(text),
        clarify=build_clarify_block(rules, issues, level),
        clarify_level=level,
        clarify_recommended=should_recommend(score, bonuses, rules),
    )
