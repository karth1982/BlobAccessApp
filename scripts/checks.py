from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


def no_hardcoded_secrets(file_path: str, full_text: str, added_lines: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for line_number, line in enumerate(full_text.splitlines(), start=1):
        if re.search(r"(?i)(password|secret|accountkey|sharedaccesskey)\s*[:=]\s*'[^'$\{][^']{3,}'", line):
            matches.append({
                "rule_id": "IAC-SEC-001",
                "severity": "block",
                "file": file_path,
                "line": line_number,
                "message": "Hardcoded secret-like value detected.",
                "evidence": re.search(r"(?i)(password|secret|accountkey|sharedaccesskey)", line).group(1) + " [redacted]",
            })
    return matches


def mandatory_tag_check(file_path: str, full_text: str, added_lines: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    if not full_text:
        return []
    missing = []
    if "owner" not in full_text.lower() and "cost" not in full_text.lower():
        missing.append({
            "rule_id": "IAC-STD-002",
            "severity": "warn",
            "file": file_path,
            "line": 1,
            "message": "Mandatory tags are not visible in this file.",
            "evidence": "tags missing",
        })
    return missing
