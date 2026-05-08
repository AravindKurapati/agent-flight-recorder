import re
import json

_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S+'),
    re.compile(r'sk-[a-zA-Z0-9-]{12,}'),
    re.compile(r'ghp_[a-zA-Z0-9]{20,}'),
    re.compile(r'(?i)bearer\s+[a-zA-Z0-9\-._~+/]+=*'),
    re.compile(r'-----BEGIN [A-Z ]+ KEY-----[\s\S]*?-----END [A-Z ]+ KEY-----', re.DOTALL),
]


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub('[REDACTED]', text)
    return text


def redact_json(obj: dict) -> str:
    return redact(json.dumps(obj))
