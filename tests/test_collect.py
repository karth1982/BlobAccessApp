import json
import subprocess
from pathlib import Path

import pytest

from scripts.collect import collect_pr_review, validate_baseline, determine_gate, render_comment_skeleton


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text("*.tmp\n")
    run_git(repo, "add", ".gitignore")
    run_git(repo, "commit", "-m", "init")


def write_file(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def stage_and_commit(repo: Path, message: str) -> str:
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def test_rule_evaluation_and_redaction(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    write_file(repo, "deploy/main.bicep", (
        "resource st 'Microsoft.Storage/storageAccounts@2023-01-01' = {\n"
        "  name: 'sa'\n"
        "  properties: {\n"
        "    publicNetworkAccess: 'Enabled'\n"
        "    minimumTlsVersion: 'TLS1_0'\n"
        "    password: 'super-secret-value'\n"
        "  }\n"
        "}\n"
    ))
    base_sha = stage_and_commit(repo, "baseline")
    write_file(repo, "deploy/main.bicep", (
        "resource st 'Microsoft.Storage/storageAccounts@2023-01-01' = {\n"
        "  name: 'sa2'\n"
        "  properties: {\n"
        "    publicNetworkAccess: 'Enabled'\n"
        "    minimumTlsVersion: 'TLS1_0'\n"
        "    password: 'new-secret'\n"
        "  }\n"
        "}\n"
    ))
    head_sha = stage_and_commit(repo, "change")

    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    (baseline_dir / "config.yml").write_text(
        "scope:\n  paths: [deploy/]\nlimits:\n  max_files: 50\n  max_file_bytes: 500000\n  max_diff_lines_per_file: 400\n"
    )
    (baseline_dir / "rules.yml").write_text(
        "rules:\n"
        "  - id: IAC-SEC-001\n"
        "    title: No hardcoded secrets in templates\n"
        "    description: secrets not allowed\n"
        "    severity: block\n"
        "    type: deterministic\n"
        "    applies_to: ['**/*.bicep']\n"
        "    check: {kind: regex_absent, pattern: '(?i)password', redact: true}\n"
        "    remediation: use key vault\n"
        "  - id: IAC-SEC-002\n"
        "    title: Public network access must not be enabled\n"
        "    description: no public access\n"
        "    severity: warn\n"
        "    type: deterministic\n"
        "    applies_to: ['**/*.bicep']\n"
        "    check: {kind: regex_absent, pattern: 'publicNetworkAccess.*Enabled'}\n"
        "    remediation: set disabled\n"
        "  - id: IAC-SEC-003\n"
        "    title: Minimum TLS version must be 1.2 or higher\n"
        "    description: no older tls\n"
        "    severity: warn\n"
        "    type: deterministic\n"
        "    applies_to: ['**/*.bicep']\n"
        "    check: {kind: regex_absent, pattern: '(?i)minimumTlsVersion.*TLS1_0'}\n"
        "    remediation: set TLS1_2\n"
    )

    result = collect_pr_review(
        checkout_dir=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        mode="advisory",
        baseline_dir=str(baseline_dir),
        baseline_ref="baseref",
        pr_number=13,
        repo_name="demo/repo",
        out_dir=str(tmp_path / "out"),
    )

    assert result["in_scope"] is True
    assert result["summary"]["block_failures"] == 1
    assert any(f["rule_id"] == "IAC-SEC-001" for f in result["findings"])
    assert any("password" in item["evidence"] for item in result["findings"] if item["rule_id"] == "IAC-SEC-001")
    assert result["gate"]["result"] == "pass"
    assert result["gate"]["would_fail_in_enforce"] is True


def test_invalid_baseline_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    write_file(repo, "deploy/app.bicep", "resource example '{}'\n")
    base_sha = stage_and_commit(repo, "baseline")
    write_file(repo, "deploy/app.bicep", "resource example 'updated'\n")
    head_sha = stage_and_commit(repo, "change")

    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    (baseline_dir / "config.yml").write_text("scope:\n  paths: [deploy/]\nlimits:\n  max_files: 50\n  max_file_bytes: 500000\n  max_diff_lines_per_file: 400\n")
    (baseline_dir / "rules.yml").write_text(
        "rules:\n"
        "  - id: invalid\n"
        "    title: bad rule\n"
        "    description: bad rule\n"
        "    severity: block\n"
        "    type: advisory\n"
        "    applies_to: ['**/*.bicep']\n"
        "    remediation: nope\n"
    )

    result = collect_pr_review(
        checkout_dir=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        mode="enforce",
        baseline_dir=str(baseline_dir),
        baseline_ref="baseref",
        pr_number=7,
        repo_name="demo/repo",
        out_dir=str(tmp_path / "out"),
    )

    assert result["baseline_valid"] is False
    assert result["gate"]["result"] == "fail"


def test_gate_truth_table():
    assert determine_gate(mode="advisory", block_failures=1, baseline_valid=True)["result"] == "pass"
    assert determine_gate(mode="enforce", block_failures=1, baseline_valid=True)["result"] == "fail"
    assert determine_gate(mode="enforce", block_failures=0, baseline_valid=False)["result"] == "fail"
    assert determine_gate(mode="advisory", block_failures=0, baseline_valid=False)["result"] == "pass"


def test_skeleton_renders_expected_markers():
    skeleton = render_comment_skeleton(
        result_label="PASS WITH WARNINGS",
        mode="advisory",
        would_fail_in_enforce=True,
        head_sha="abc1234567890",
        baseline_ref="def1234567890",
        scope_paths=["deploy/", "azuredeployment/"],
        files_reviewed=2,
        limits_note="",
        rule_ids=["IAC-SEC-002"],
        findings=[{"severity": "warn", "rule_id": "IAC-SEC-002", "file": "deploy/app.bicep", "line": 4, "message": "test"}],
    )
    assert "<!-- pr-baseline-review:v1 -->" in skeleton
    assert "AGENT_SUMMARY" in skeleton
    assert "AGENT_ADVISORY" in skeleton or "No judgement checks apply" in skeleton


def test_validate_baseline_rejects_unknown_fields():
    config = {"scope": {"paths": ["deploy/"]}, "limits": {"max_files": 10, "max_file_bytes": 1000, "max_diff_lines_per_file": 50}}
    rules = {"rules": [{"id": "R-1", "title": "x", "description": "y", "severity": "warn", "type": "advisory", "applies_to": ["**/*.bicep"], "unexpected": True}]}
    with pytest.raises(ValueError):
        validate_baseline(config, rules)
