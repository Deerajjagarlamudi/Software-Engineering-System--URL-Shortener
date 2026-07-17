"""Policy guardrails applied to agent output before it enters workflow state."""

import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

FORBIDDEN_CODE_PATTERNS = [
    re.compile(r"(?i)\bos\.system\s*\("),
    re.compile(r"(?i)subprocess\.(Popen|run|call)\([^)]*shell\s*=\s*True"),
    re.compile(r"(?i)\beval\s*\("),
    re.compile(r"rm\s+-rf\s+/"),
    re.compile(r"(?i)DROP\s+TABLE"),
]


class PolicyViolation(Exception):
    def __init__(self, rule: str, location: str):
        super().__init__(f"policy violation [{rule}] in {location}")
        self.rule = rule
        self.location = location


def check_text(text: str, location: str) -> None:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            raise PolicyViolation("secret-material", location)


def check_patch(files: dict[str, str]) -> None:
    """Scan generated code for secrets and destructive patterns."""
    for path, content in files.items():
        check_text(content, path)
        for pat in FORBIDDEN_CODE_PATTERNS:
            if pat.search(content):
                raise PolicyViolation(f"forbidden-pattern:{pat.pattern[:30]}", path)
