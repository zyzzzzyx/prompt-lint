"""PromptLint —— 站在模型视角诊断提示词歧义。"""

__version__ = "1.1.0"

from .analyzer import analyze, load_rules, Issue, Report  # noqa: F401
