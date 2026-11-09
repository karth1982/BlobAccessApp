from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from scripts import checks

VALID_SEVERITIES = {"block", "warn", "info"}
VALID_TYPES = {"deterministic", "advisory"}
VALID_DETERMINISTIC_KINDS = {"regex_absent", "regex_required", "custom"}


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=check,
    )
    return result.stdout.strip()


def load_yaml_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return loaded


def validate_baseline(config: Dict[str, Any], rules_payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    allowed_config_top = {"scope", "limits"}
    allowed_scope_keys = {"paths"}
    allowed_limit_keys = {"max_files", "max_file_bytes", "max_diff_lines_per_file"}
    allowed_rule_keys = {
        "id",
        "title",
        "description",
        "severity",
        "type",
        "applies_to",
        "check",
        "remediation",
        "guidance",
        "name",
    }

    if not isinstance(config, dict):
        return ["config.yml must be a mapping"]

    unknown_config_keys = set(config.keys()) - allowed_config_top
    if unknown_config_keys:
        errors.append(f"Unknown config keys: {sorted(unknown_config_keys)}")

    scope = config.get("scope", {})
    if not isinstance(scope, dict):
        errors.append("scope must be a mapping")
    else:
        unknown_scope = set(scope.keys()) - allowed_scope_keys
        if unknown_scope:
            errors.append(f"Unknown scope keys: {sorted(unknown_scope)}")
        paths = scope.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
            errors.append("scope.paths must be a list of strings")

    limits = config.get("limits", {})
    if not isinstance(limits, dict):
        errors.append("limits must be a mapping")
    else:
        unknown_limits = set(limits.keys()) - allowed_limit_keys
        if unknown_limits:
            errors.append(f"Unknown limits keys: {sorted(unknown_limits)}")

    rules = rules_payload.get("rules", [])
    if not isinstance(rules, list):
        return errors + ["rules.yml must contain a 'rules' list"]

    seen_ids = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"Rule #{index + 1} is not a mapping")
            continue
        unknown_rule_keys = set(rule.keys()) - allowed_rule_keys
        if unknown_rule_keys:
            errors.append(f"Rule '{rule.get('id', '#%s' % (index + 1))}' has unknown keys: {sorted(unknown_rule_keys)}")
        missing = [k for k in ["id", "title", "description", "severity", "type", "applies_to"] if k not in rule]
        if missing:
            errors.append(f"Rule #{index + 1} is missing required fields: {missing}")
            continue
        rule_id = str(rule["id"])
        if rule_id in seen_ids:
            errors.append(f"Duplicate rule id: {rule_id}")
        seen_ids.add(rule_id)
        if rule["severity"] not in VALID_SEVERITIES:
            errors.append(f"Rule '{rule_id}' has invalid severity '{rule['severity']}'")
        if rule["type"] not in VALID_TYPES:
            errors.append(f"Rule '{rule_id}' has invalid type '{rule['type']}'")
        if rule["type"] == "advisory" and rule["severity"] == "block":
            errors.append(f"Rule '{rule_id}' cannot be advisory with severity block")
        if not isinstance(rule["applies_to"], list):
            errors.append(f"Rule '{rule_id}' applies_to must be a list")
        check = rule.get("check")
        if rule["type"] == "deterministic":
            if not isinstance(check, dict):
                errors.append(f"Rule '{rule_id}' is deterministic but missing check object")
            else:
                kind = check.get("kind")
                if kind not in VALID_DETERMINISTIC_KINDS:
                    errors.append(f"Rule '{rule_id}' uses unsupported check kind '{kind}'")
                if kind in {"regex_absent", "regex_required"} and not isinstance(check.get("pattern"), str):
                    errors.append(f"Rule '{rule_id}' regex check is missing a valid pattern")
                if kind == "regex_absent" or kind == "regex_required":
                    try:
                        re.compile(check["pattern"])  # type: ignore[index]
                    except re.error as exc:
                        errors.append(f"Rule '{rule_id}' has invalid regex: {exc}")
                if kind == "custom":
                    name = check.get("name") or check.get("function")
                    if not isinstance(name, str):
                        errors.append(f"Rule '{rule_id}' custom check needs a name")
                    elif not hasattr(checks, name):
                        errors.append(f"Rule '{rule_id}' custom check '{name}' does not exist in scripts/checks.py")

    if errors:
        raise ValueError("; ".join(errors))
    return errors


def load_baseline(baseline_dir: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    config_path = Path(baseline_dir) / "config.yml"
    rules_path = Path(baseline_dir) / "rules.yml"
    config_root = load_yaml_file(config_path)
    rules_root = load_yaml_file(rules_path)
    try:
        baseline_errors = validate_baseline(config_root, rules_root)
    except ValueError as exc:
        baseline_errors = [str(exc)]
    if baseline_errors:
        return config_root, rules_root.get("rules", []), baseline_errors
    return config_root, rules_root.get("rules", []), []


def _match_glob(path: str, pattern: str) -> bool:
    return bool(__import__("fnmatch").fnmatchcase(path, pattern))


def _rule_applies(file_path: str, applies_to: List[str]) -> bool:
    if not applies_to:
        return False
    return any(_match_glob(file_path, item) for item in applies_to)


def _git_diff_name_status(repo: Path, base_sha: str, head_sha: str) -> List[Tuple[str, str, str]]:
    text = run_git(repo, "diff", "--name-status", "-M", f"{base_sha}...{head_sha}")
    rows: List[Tuple[str, str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0]
            old_path = parts[1]
            new_path = parts[2] if len(parts) > 2 else old_path
            rows.append((status, old_path, new_path))
    return rows


def _git_unified_diff(repo: Path, base_sha: str, head_sha: str) -> str:
    return run_git(repo, "diff", "--unified=0", "--no-color", f"{base_sha}...{head_sha}", check=False)


def _parse_added_lines(diff_text: str) -> Dict[str, List[Tuple[int, str]]]:
    result: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    current_path: str | None = None
    current_line = 1
    started_hunk = False

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_path = raw_line.split(" b/", 1)[1] if " b/" in raw_line else None
            current_line = 1
            started_hunk = False
            continue
        if raw_line.startswith("+++ ") or raw_line.startswith("--- "):
            if raw_line.startswith("+++ ") and raw_line != "+++ /dev/null":
                current_path = raw_line[6:]
            continue
        if raw_line.startswith("@@"):
            match = re.search(r"\+(\d+)", raw_line)
            current_line = int(match.group(1)) if match else 1
            started_hunk = True
            continue
        if not started_hunk or current_path is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            result[current_path].append((current_line, raw_line[1:]))
            current_line += 1
        elif raw_line.startswith(" "):
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
    return result


def _read_file_text(repo: Path, relative_path: str) -> str:
    file_path = repo / relative_path
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8", errors="replace")


def _redacted_evidence(pattern: str, line_text: str) -> str:
    match = re.search(pattern, line_text)
    if match:
        token = match.group(0)
        identifier = token.split(":", 1)[0].split("=", 1)[0].strip()
        return f"{identifier} [redacted]"
    return "matched [redacted]"


def determine_gate(mode: str, block_failures: int, baseline_valid: bool) -> Dict[str, Any]:
    mode = (mode or "advisory").lower()
    if mode == "advisory":
        if not baseline_valid:
            return {"result": "pass", "reason": "baseline invalid; advisory mode ignores deterministic gate", "would_fail_in_enforce": True}
        if block_failures:
            return {"result": "pass", "reason": "advisory mode", "would_fail_in_enforce": True}
        return {"result": "pass", "reason": "advisory mode", "would_fail_in_enforce": False}

    if not baseline_valid or block_failures:
        return {"result": "fail", "reason": "enforce mode blocked by baseline error or block findings", "would_fail_in_enforce": False}
    return {"result": "pass", "reason": "enforce mode", "would_fail_in_enforce": False}


def render_comment_skeleton(
    result_label: str,
    mode: str,
    would_fail_in_enforce: bool,
    head_sha: str,
    baseline_ref: str,
    scope_paths: List[str],
    files_reviewed: int,
    limits_note: str,
    rule_ids: List[str],
    findings: List[Dict[str, Any]],
) -> str:
    would_note = "  |  **Would fail in enforce:** yes" if would_fail_in_enforce and mode == "advisory" else ""
    summary_placeholder = "<!-- AGENT_SUMMARY -->"
    advisory_placeholder = "<!-- AGENT_ADVISORY -->"
    if not any(f.get("status") == "needs_review" for f in findings):
        advisory_placeholder = "No judgement checks apply to this change."

    deterministic_rows = []
    for item in sorted(findings, key=lambda f: (0 if f.get("severity") == "block" else 1 if f.get("severity") == "warn" else 2, f.get("rule_id", ""))):
        if item.get("status") == "fail":
            location = f"{item.get('file')}:{item.get('line', '?')}"
            deterministic_rows.append(f"| {item.get('severity','info')} | {item.get('rule_id','')} | {location} | {item.get('message','')} | {item.get('remediation','')} |")
    if deterministic_rows:
        table = "\n".join([
            "| Severity | Rule | File:line | Issue | Suggested fix |",
            "| --- | --- | --- | --- | --- |",
            *deterministic_rows,
        ])
    else:
        table = "No deterministic findings."

    passed_or_not = ", ".join(rule_ids) if rule_ids else "None"
    head_short = head_sha[:7]
    baseline_short = baseline_ref[:7]
    return f"""<!-- pr-baseline-review:v1 -->
## Platform baseline review

**Result:** {result_label}  |  **Mode:** {mode}{would_note}
Commit {head_short} | Baseline {baseline_short} | Scope: {scope_paths} | Files reviewed: {files_reviewed}
{limits_note}

### Summary
{summary_placeholder}

### Findings that need attention
{table}

### Judgement checks
{advisory_placeholder}

<details><summary>Rules passed or not applicable ({len(rule_ids)})</summary>
{passed_or_not}
</details>

<sub>Deterministic findings come from a script run against the platform baseline.
The summary and judgement checks are written by an AI assistant and may be wrong.
This review does not approve the PR; a human reviewer does.</sub>
"""


def collect_pr_review(
    checkout_dir: Path | str,
    base_sha: str,
    head_sha: str,
    mode: str,
    baseline_dir: str,
    baseline_ref: str,
    pr_number: int,
    repo_name: str,
    out_dir: Path | str,
) -> Dict[str, Any]:
    repo = Path(checkout_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Any] = {
        "schema_version": 1,
        "repo": repo_name,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "baseline_ref": baseline_ref,
        "mode": (mode or "advisory").lower(),
        "in_scope": False,
        "skipped_reason": None,
        "baseline_valid": True,
        "baseline_errors": [],
        "scope_paths": [],
        "limits_hit": [],
        "changed_files": [],
        "findings": [],
        "summary": {
            "block_failures": 0,
            "warn_failures": 0,
            "info_failures": 0,
            "needs_review": 0,
            "rules_passed": 0,
            "rules_not_applicable": 0,
        },
        "gate": {"result": "pass", "reason": "advisory mode", "would_fail_in_enforce": False},
        "result_label": "NOT IN SCOPE",
    }

    config, rules, baseline_errors = load_baseline(baseline_dir)
    result["baseline_valid"] = not baseline_errors
    result["baseline_errors"] = baseline_errors
    if baseline_errors:
        gate = determine_gate(mode, 0, False)
        result["gate"] = gate
        result["result_label"] = "BASELINE ERROR"
        result["scope_paths"] = config.get("scope", {}).get("paths", [])
        render_skeleton = render_comment_skeleton(
            result_label=result["result_label"],
            mode=result["mode"],
            would_fail_in_enforce=gate["would_fail_in_enforce"],
            head_sha=head_sha,
            baseline_ref=baseline_ref,
            scope_paths=result["scope_paths"],
            files_reviewed=0,
            limits_note="",
            rule_ids=[],
            findings=[],
        )
        (out_path / "comment-skeleton.md").write_text(render_skeleton, encoding="utf-8")
        (out_path / "findings.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    scope_paths = config.get("scope", {}).get("paths", [])
    result["scope_paths"] = scope_paths
    limits = config.get("limits", {})

    name_status = _git_diff_name_status(repo, base_sha, head_sha)
    changed = []
    for status, old_path, new_path in name_status:
        p = new_path if status != "D" else old_path
        in_scope = any(p.startswith(scope_prefix) for scope_prefix in scope_paths)
        changed.append({"path": p, "status": status, "in_scope": in_scope, "binary": False})
        if status == "D":
            continue
    result["changed_files"] = changed
    if not any(item["in_scope"] for item in changed):
        result["in_scope"] = False
        result["skipped_reason"] = "no in-scope changes"
        result["gate"] = {"result": "pass", "reason": "no in-scope changes", "would_fail_in_enforce": False}
        result["result_label"] = "NOT IN SCOPE"
        (out_path / "findings.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out_path / "comment-skeleton.md").write_text("<!-- pr-baseline-review:v1 -->\n## Platform baseline review\n\n**Result:** NOT IN SCOPE\n", encoding="utf-8")
        return result

    diff_text = _git_unified_diff(repo, base_sha, head_sha)
    added_by_file = _parse_added_lines(diff_text)
    in_scope_files = [item["path"] for item in changed if item["in_scope"]]
    file_limit = int(limits.get("max_files", 200))
    if len(in_scope_files) > file_limit:
        result["limits_hit"].append(f"max_files exceeded ({len(in_scope_files)} > {file_limit})")
        in_scope_files = in_scope_files[:file_limit]

    applicable_rule_ids: List[str] = []
    findings: List[Dict[str, Any]] = []
    rules_passed = 0
    rules_not_applicable = 0
    for rule in rules:
        matching_files = [path for path in in_scope_files if _rule_applies(path, rule.get("applies_to", []))]
        if not matching_files:
            rules_not_applicable += 1
            continue
        applicable_rule_ids.append(rule["id"])
        if rule["type"] == "advisory":
            findings.append({
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "type": "advisory",
                "status": "needs_review",
                "files": sorted(set(matching_files)),
                "guidance": rule.get("guidance") or rule.get("description") or "Review this change manually.",
            })
            result["summary"]["needs_review"] += 1
            rules_passed += 1
            continue

        check = rule.get("check", {})
        kind = check.get("kind")
        matched_findings = []
        if kind == "regex_absent":
            pattern = re.compile(check["pattern"])
            for file_path in sorted(set(matching_files)):
                for line_number, line_text in added_by_file.get(file_path, []):
                    if pattern.search(line_text):
                        evidence = _redacted_evidence(check["pattern"], line_text) if check.get("redact") else line_text.strip()
                        matched_findings.append({
                            "rule_id": rule["id"],
                            "title": rule["title"],
                            "severity": rule["severity"],
                            "type": "deterministic",
                            "status": "fail",
                            "file": file_path,
                            "line": line_number,
                            "evidence": evidence,
                            "message": rule["description"],
                            "remediation": rule.get("remediation", "Review the change"),
                        })
        elif kind == "regex_required":
            pattern = re.compile(check["pattern"])
            for file_path in sorted(set(matching_files)):
                full_text = _read_file_text(repo, file_path)
                if not pattern.search(full_text):
                    matched_findings.append({
                        "rule_id": rule["id"],
                        "title": rule["title"],
                        "severity": rule["severity"],
                        "type": "deterministic",
                        "status": "fail",
                        "file": file_path,
                        "line": 1,
                        "evidence": "pattern missing",
                        "message": rule["description"],
                        "remediation": rule.get("remediation", "Review the change"),
                    })
        elif kind == "custom":
            custom_name = check.get("name") or check.get("function")
            callable_rule = getattr(checks, custom_name)
            for file_path in sorted(set(matching_files)):
                full_text = _read_file_text(repo, file_path)
                added_lines = {file_path: [text for _, text in added_by_file.get(file_path, [])]}
                custom_findings = callable_rule(file_path, full_text, added_lines)
                for finding in custom_findings:
                    matched_findings.append({
                        "rule_id": rule["id"],
                        "title": rule["title"],
                        "severity": rule["severity"],
                        "type": "deterministic",
                        "status": "fail",
                        **finding,
                    })

        if not matched_findings:
            rules_passed += 1
        else:
            findings.extend(matched_findings)
            for item in matched_findings:
                if item["severity"] == "block":
                    result["summary"]["block_failures"] += 1
                elif item["severity"] == "warn":
                    result["summary"]["warn_failures"] += 1
                elif item["severity"] == "info":
                    result["summary"]["info_failures"] += 1

    result["summary"]["rules_passed"] = rules_passed
    result["summary"]["rules_not_applicable"] = rules_not_applicable
    result["findings"] = findings
    result["in_scope"] = True
    result["gate"] = determine_gate(result["mode"], result["summary"]["block_failures"], True)
    if result["summary"]["block_failures"] or result["summary"]["warn_failures"] or result["summary"]["info_failures"]:
        result["result_label"] = "PASS WITH WARNINGS" if result["gate"]["result"] == "pass" else "BLOCKING ISSUES"
    else:
        result["result_label"] = "PASS" if result["gate"]["result"] == "pass" else "BLOCKING ISSUES"
    if result["gate"]["would_fail_in_enforce"] and result["mode"] == "advisory":
        result["result_label"] = "BLOCKING ISSUES (ADVISORY MODE)"

    skeleton = render_comment_skeleton(
        result_label=result["result_label"],
        mode=result["mode"],
        would_fail_in_enforce=result["gate"]["would_fail_in_enforce"],
        head_sha=head_sha,
        baseline_ref=baseline_ref,
        scope_paths=scope_paths,
        files_reviewed=len(in_scope_files),
        limits_note="",
        rule_ids=applicable_rule_ids,
        findings=result["findings"],
    )
    with (out_path / "comment-skeleton.md").open("w", encoding="utf-8") as handle:
        handle.write(skeleton)
    with (out_path / "findings.json").open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    if out_path.joinpath("diffs").exists():
        pass
    diffs_dir = out_path / "diffs"
    diffs_dir.mkdir(exist_ok=True)
    for index, file_path in enumerate(in_scope_files, start=1):
        filepath = diffs_dir / f"{index}.diff"
        file_diff = run_git(repo, "diff", "--unified=0", "--no-color", f"{base_sha}...{head_sha}", "--", file_path, check=False)
        filepath.write_text(file_diff, encoding="utf-8")

    return result


def main() -> int:
    repo = Path(os.environ.get("PR_CHECKOUT_DIR", ".")).resolve()
    base_sha = os.environ.get("BASE_SHA", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    mode = (os.environ.get("PR_REVIEW_MODE") or "advisory").lower()
    baseline_dir = os.environ.get("BASELINE_DIR", "baselines")
    baseline_ref = os.environ.get("BASELINE_REF", "")
    pr_number = int(os.environ.get("PR_NUMBER", "0"))
    repo_name = os.environ.get("GITHUB_REPOSITORY", "local/repo")
    out_dir = os.environ.get("OUT_DIR", "pr-review")
    if not base_sha or not head_sha:
        print("Missing BASE_SHA or HEAD_SHA")
        return 2
    result = collect_pr_review(repo, base_sha, head_sha, mode, baseline_dir, baseline_ref, pr_number, repo_name, out_dir)
    print(json.dumps({"result": result["gate"]["result"], "mode": mode}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
