"""
Check Monotype team changes in mt-audit-log-resolver-service and mtconnect-api.

Clones/updates repos, filters commits by team_github_users, compares routing maps
and subject extractors against this automation project.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..operation_registry import tracked_operations


def _project_root() -> Path:
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (
            (parent / "python" / "audit_validator" / "__init__.py").is_file()
            and (parent / "config" / "team_github_users.json").is_file()
        ):
            return parent
    raise FileNotFoundError("Project root not found")


def _load_team_config() -> dict:
    return json.loads((_project_root() / "config" / "team_github_users.json").read_text())


def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.check_output(cmd, cwd=str(cwd) if cwd else None, text=True, stderr=subprocess.STDOUT)


def _ensure_repo(name: str, repo_slug: str, branch: str, cache_dir: Path) -> Path:
    path = cache_dir / name
    cache_dir.mkdir(parents=True, exist_ok=True)
    marker = path / "src" if name == "mtconnect-api" else path / "config"
    if not (path / ".git").is_dir() or not marker.exists():
        if path.exists():
            import shutil

            shutil.rmtree(path)
        _run(["gh", "repo", "clone", repo_slug, str(path), "--", "--depth=200", f"--branch={branch}"])
        return path
    _run(["git", "-C", str(path), "fetch", "origin", branch, "--depth=200"])
    _run(["git", "-C", str(path), "checkout", branch])
    try:
        _run(["git", "-C", str(path), "pull", "--ff-only", "origin", branch])
    except subprocess.CalledProcessError:
        pass
    return path


def _team_match(author: str, email: str, team: set[str]) -> bool:
    author_norm = author.lower().replace("-", "").replace("_", "")
    email_user = email.split("@")[0].lower().replace("-", "").replace("_", "")
    for t in team:
        t_norm = t.lower().replace("-", "").replace("_", "")
        if t_norm in author_norm or t_norm in email_user or author == t:
            return True
    return False


def _team_commits(repo_path: Path, team: set[str], limit: int = 300) -> list[dict]:
    log = _run(["git", "-C", str(repo_path), "log", f"-{limit}", "--format=%H|%an|%ae|%s|%ci"])
    commits: list[dict] = []
    for line in log.strip().split("\n"):
        if not line.strip():
            continue
        sha, author, email, subject, date = line.split("|", 4)
        if _team_match(author, email, team):
            commits.append(
                {
                    "sha": sha[:7],
                    "author": author,
                    "date": date[:10],
                    "subject": subject,
                }
            )
    return commits


def _extract_connect_api_ops(connect_path: Path) -> set[str]:
    base = connect_path / "src" / "plugins" / "operationEvents" / "subjectExtractor"
    ops: set[str] = set()
    if not base.is_dir():
        return ops
    pattern = re.compile(r"^\s{2}(\w+):\s*(?:\(|async\s*\()", re.M)
    for f in base.glob("*.ts"):
        if f.name in ("index.ts", "utils.ts"):
            continue
        ops.update(pattern.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return ops


def _load_routing_map(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class SyncReport:
    resolver_commits: list[dict] = field(default_factory=list)
    connect_commits: list[dict] = field(default_factory=list)
    resolver_ops: dict[str, str] = field(default_factory=dict)
    local_ops: dict[str, str] = field(default_factory=dict)
    connect_extractor_ops: set[str] = field(default_factory=set)
    new_in_resolver: dict[str, str] = field(default_factory=dict)
    removed_from_resolver: dict[str, str] = field(default_factory=dict)
    resolver_not_in_automation: list[str] = field(default_factory=list)
    connect_not_in_resolver: list[str] = field(default_factory=list)
    map_updated: bool = False


def sync_team_repos(*, apply_routing_map: bool = False) -> SyncReport:
    cfg = _load_team_config()
    team = set(cfg["team_github_users"]) | {"Sachin-monotype"}
    root = _project_root()
    cache = root / ".cache" / "upstream-repos"

    resolver_path = _ensure_repo(
        "mt-audit-log-resolver-service",
        cfg["repos"]["resolver"],
        cfg["default_branch"]["resolver"],
        cache,
    )
    connect_path = _ensure_repo(
        "mtconnect-api",
        cfg["repos"]["connect_api"],
        cfg["default_branch"]["connect_api"],
        cache,
    )

    report = SyncReport(
        resolver_commits=_team_commits(resolver_path, team),
        connect_commits=_team_commits(connect_path, team),
    )

    upstream_map_path = resolver_path / "config" / "outbound-routing-map.json"
    local_map_path = root / "python" / "audit_validator" / "data" / "outbound-routing-map.json"

    if upstream_map_path.is_file():
        report.resolver_ops = _load_routing_map(upstream_map_path)
    if local_map_path.is_file():
        report.local_ops = _load_routing_map(local_map_path)

    report.connect_extractor_ops = _extract_connect_api_ops(connect_path)

    up_keys = set(report.resolver_ops)
    local_keys = set(report.local_ops)
    report.new_in_resolver = {k: report.resolver_ops[k] for k in sorted(up_keys - local_keys)}
    report.removed_from_resolver = {k: report.local_ops[k] for k in sorted(local_keys - up_keys)}

    automation_ops = set(tracked_operations())
    report.resolver_not_in_automation = sorted(up_keys - automation_ops)

    mapped = set(report.resolver_ops.keys())
    report.connect_not_in_resolver = sorted(
        op for op in report.connect_extractor_ops if op not in mapped
    )

    if apply_routing_map and report.resolver_ops and report.new_in_resolver:
        local_map_path.write_text(json.dumps(report.resolver_ops, indent=2) + "\n", encoding="utf-8")
        report.map_updated = True
    elif apply_routing_map and report.resolver_ops and report.resolver_ops != report.local_ops:
        local_map_path.write_text(json.dumps(report.resolver_ops, indent=2) + "\n", encoding="utf-8")
        report.map_updated = True

    return report


def write_sync_report_json(report: SyncReport, path: Path) -> None:
    payload = {
        "resolver_team_commits": len(report.resolver_commits),
        "connect_team_commits": len(report.connect_commits),
        "recent_resolver_commits": report.resolver_commits[:15],
        "recent_connect_commits": report.connect_commits[:15],
        "resolver_operations": len(report.resolver_ops),
        "local_operations": len(report.local_ops),
        "new_in_resolver": report.new_in_resolver,
        "removed_from_resolver": report.removed_from_resolver,
        "resolver_not_in_automation_templates": report.resolver_not_in_automation,
        "connect_extractors_not_in_resolver_map": report.connect_not_in_resolver,
        "routing_map_updated": report.map_updated,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def print_sync_report(report: SyncReport) -> None:
    print("\n" + "=" * 72)
    print("  UPSTREAM TEAM SYNC")
    print("=" * 72)
    print(f"  Resolver team commits (recent): {len(report.resolver_commits)}")
    print(f"  Connect-api team commits:       {len(report.connect_commits)}")
    print(f"  Resolver routing map ops:       {len(report.resolver_ops)}")
    print(f"  Local routing map ops:          {len(report.local_ops)}")
    print(f"  Connect-api extractors:         {len(report.connect_extractor_ops)}")

    if report.new_in_resolver:
        print(f"\n  NEW in resolver ({len(report.new_in_resolver)}):")
        for op, rk in list(report.new_in_resolver.items())[:10]:
            print(f"    + {op} → {rk}")
    else:
        print("\n  Routing map: in sync with resolver develop branch")

    if report.resolver_not_in_automation:
        print(f"\n  Resolver ops missing automation template ({len(report.resolver_not_in_automation)}):")
        for op in report.resolver_not_in_automation[:10]:
            print(f"    ? {op}")

    if report.connect_not_in_resolver:
        print(f"\n  Connect extractors not in resolver map ({len(report.connect_not_in_resolver)}):")
        for op in report.connect_not_in_resolver[:10]:
            print(f"    ? {op}")

    if report.resolver_commits:
        print("\n  Recent resolver team commits:")
        for c in report.resolver_commits[:5]:
            print(f"    {c['date']} {c['sha']} {c['author']}: {c['subject'][:70]}")

    print("=" * 72 + "\n")
