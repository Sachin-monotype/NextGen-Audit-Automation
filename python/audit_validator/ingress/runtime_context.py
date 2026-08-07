"""Resolve dynamic Ingress payload + HTTP header context before each POST.

Actor ids come from the same Bearer JWT as UI/GraphQL. Host OS / CPU are read from
the runner machine. Device ids resolve in order:

1. ``INGRESS_MACHINE_ID`` / ``INGRESS_UNIQUE_ID`` in ``.env`` (override for any machine)
2. ``INGRESS_DEVICE_FILE`` or ``ingress-device.local.json`` at repo root
3. Monotype desktop app preferences on macOS (``InstallationId``)
4. Bundled ``data/ingress_device.defaults.json`` (audit preprod reference machine)
"""

from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..models import JsonDict

log = logging.getLogger(__name__)

_MACOS_MONOTYPE_PREFS = (
    "com.monotype.nextgen",
    "com.monotype.unified",
    "com.monotype.fonts",
)
_PKG_DEFAULTS = Path(__file__).resolve().parent.parent / "data" / "ingress_device.defaults.json"


@dataclass(frozen=True)
class IngressRuntimeContext:
    """Values injected into ingress audit envelopes and Ingress API headers."""

    gcid: str = ""
    profile_id: str = ""
    email: str = ""
    org_id: str = ""
    machine_id: str = ""
    unique_id: str = ""
    os_name: str = "mac"
    os_version: str = ""
    cpu_arch: str = ""
    app_version: str = "1.0.0.0"
    os_platform: str = "MAC"
    actor_user_agent: str = "MonotypeNextGen/1.0.0.0"
    device_source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_first(*keys: str) -> str:
    for key in keys:
        val = (os.getenv(key) or "").strip()
        if val:
            return val
    return ""


def _macos_defaults(domain: str, key: str) -> str:
    try:
        out = subprocess.check_output(
            ["defaults", "read", domain, key],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.strip().strip('"').strip()
    except Exception:
        return ""


def _macos_product_version() -> str:
    try:
        return subprocess.check_output(["sw_vers", "-productVersion"], text=True, timeout=5).strip()
    except Exception:
        return platform.mac_ver()[0]


def _normalize_cpu_arch(raw: str) -> str:
    val = (raw or platform.machine() or "").strip().lower()
    if val in {"aarch64", "arm64"}:
        return "arm64"
    if val in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    return val or "arm64"


def _normalize_os_name(system: str) -> tuple[str, str]:
    key = (system or platform.system() or "").lower()
    if key == "darwin":
        return "mac", "MAC"
    if key == "windows":
        return "win", "WIN"
    if key == "linux":
        return "linux", "LINUX"
    return key or "mac", "MAC"


def _load_json_device_meta(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read device file %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None and not str(k).startswith("_")}


def _device_file_candidates(*, include_repo_defaults: bool = False) -> list[Path]:
    paths: list[Path] = []
    explicit = (os.getenv("INGRESS_DEVICE_FILE") or "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    else:
        try:
            from ..project_root import find_project_root

            paths.append(find_project_root() / "ingress-device.local.json")
        except Exception:
            pass
    if include_repo_defaults:
        paths.append(_PKG_DEFAULTS)
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _load_device_file() -> dict[str, str]:
    for path in _device_file_candidates(include_repo_defaults=False):
        meta = _load_json_device_meta(path)
        if meta:
            return meta
    return {}


def _discover_monotype_device_ids() -> tuple[str, str, str]:
    machine_id = _env_first("INGRESS_MACHINE_ID")
    unique_id = _env_first("INGRESS_UNIQUE_ID")
    source = "env" if machine_id or unique_id else ""

    file_meta = _load_device_file()
    if not machine_id:
        machine_id = str(
            file_meta.get("machineId")
            or file_meta.get("deviceId")
            or file_meta.get("DeviceId")
            or ""
        ).strip()
        if machine_id:
            source = source or "device_file"
    if not unique_id:
        unique_id = str(
            file_meta.get("uniqueId")
            or file_meta.get("installationId")
            or file_meta.get("InstallationId")
            or ""
        ).strip()
        if unique_id and not source:
            source = "device_file"

    if sys.platform == "darwin":
        for domain in _MACOS_MONOTYPE_PREFS:
            if not unique_id:
                val = _macos_defaults(domain, "InstallationId")
                if val:
                    unique_id = val
                    source = source or f"macos:{domain}:InstallationId"
            for key in ("DeviceId", "MachineId", "deviceId", "machineId"):
                if not machine_id:
                    val = _macos_defaults(domain, key)
                    if val:
                        machine_id = val
                        source = source or f"macos:{domain}:{key}"

    if not machine_id or not unique_id:
        repo = _load_json_device_meta(_PKG_DEFAULTS)
        if not machine_id:
            machine_id = str(repo.get("machineId") or repo.get("deviceId") or "").strip()
            if machine_id:
                source = source or "repo_defaults"
        if not unique_id:
            unique_id = str(repo.get("uniqueId") or repo.get("installationId") or "").strip()
            if unique_id and not source:
                source = source or "repo_defaults"

    return machine_id, unique_id, source


def _discover_app_version() -> str:
    explicit = _env_first("INGRESS_APP_VERSION")
    if explicit:
        return explicit
    for path in _device_file_candidates(include_repo_defaults=False):
        meta = _load_json_device_meta(path)
        version = str(meta.get("appVersion") or meta.get("platformVersion") or "").strip()
        if version:
            return version
    repo = _load_json_device_meta(_PKG_DEFAULTS)
    version = str(repo.get("appVersion") or "").strip()
    if version:
        return version
    for app_name in ("Monotype NextGen.app", "Monotype Connect.app"):
        plist_path = Path("/Applications") / app_name / "Contents" / "Info.plist"
        if not plist_path.is_file():
            continue
        try:
            data = plistlib.loads(plist_path.read_bytes())
        except Exception:
            continue
        version = str(
            data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or ""
        ).strip()
        if version:
            return version
    return "1.0.0.0"


def _resolve_identity() -> tuple[str, str, str, str]:
    from ..auth import jwt_identity, resolve_our_profile_id

    ident = jwt_identity()
    gcid = (ident.get("gcid") or "").strip() or _env_first(
        "INGRESS_DEFAULT_GCID",
        "CRON_DEFAULT_GCID",
        "GRAPHQL_CONTEXT_CUSTOMER_ID",
        "GLOBAL_CUSTOMER_ID",
        "NEXTGEN_UI_GCID",
        "OAUTH_GCID",
    )
    profile_id = (resolve_our_profile_id() or "").strip() or _env_first(
        "INGRESS_DEFAULT_USER_ID",
        "NOTIFICATION_CLEANUP_USER_ID",
        "OAUTH_PROFILE_ID",
        "AUDIT_PROFILE_ID",
    )
    email = str(ident.get("email") or "").strip()
    org_id = str(ident.get("org_id") or "").strip()
    return gcid, profile_id, email, org_id


@lru_cache(maxsize=1)
def resolve_ingress_runtime_context() -> IngressRuntimeContext:
    gcid, profile_id, email, org_id = _resolve_identity()
    machine_id, unique_id, device_source = _discover_monotype_device_ids()

    os_name, os_platform = _normalize_os_name(platform.system())
    if override := _env_first("INGRESS_OS_NAME"):
        os_name = override
    os_version = _env_first("INGRESS_OS_VERSION")
    if not os_version:
        os_version = _macos_product_version() if sys.platform == "darwin" else platform.version()
    cpu_arch = _normalize_cpu_arch(_env_first("INGRESS_CPU_ARCH"))
    app_version = _discover_app_version()
    if override_platform := _env_first("INGRESS_OS_PLATFORM"):
        os_platform = override_platform.upper()

    return IngressRuntimeContext(
        gcid=gcid,
        profile_id=profile_id,
        email=email,
        org_id=org_id,
        machine_id=machine_id,
        unique_id=unique_id,
        os_name=os_name,
        os_version=os_version,
        cpu_arch=cpu_arch,
        app_version=app_version,
        os_platform=os_platform,
        actor_user_agent=f"MonotypeNextGen/{app_version}",
        device_source=device_source,
    )


def clear_ingress_runtime_context_cache() -> None:
    resolve_ingress_runtime_context.cache_clear()


def apply_ingress_runtime_context(
    payload: JsonDict,
    ctx: IngressRuntimeContext | None = None,
) -> IngressRuntimeContext:
    from ..cron.payloads import _apply_runtime_overrides

    ctx = ctx or resolve_ingress_runtime_context()
    source = payload.get("source")
    if isinstance(source, dict):
        source["osName"] = ctx.os_name
        source["osVersion"] = ctx.os_version
        source["cpuArch"] = ctx.cpu_arch
        source["platformVersion"] = ctx.app_version
        source["actorUserAgent"] = ctx.actor_user_agent

    actor = payload.setdefault("actor", {})
    machine_id = ""
    if isinstance(actor, dict):
        if ctx.gcid:
            actor["globalCustomerId"] = ctx.gcid
        if ctx.profile_id:
            actor["globalUserId"] = ctx.profile_id
        gcid = str(actor.get("globalCustomerId") or "").strip()
        guid = str(actor.get("globalUserId") or "").strip()
        # Identities present → always authenticated (never leave anonymous).
        if gcid and guid:
            actor["authenticationState"] = "authenticated"
        else:
            actor.setdefault("authenticationState", "authenticated")
        if ctx.machine_id:
            actor["machineId"] = ctx.machine_id
        else:
            actor.pop("machineId", None)
        if ctx.unique_id:
            actor["uniqueId"] = ctx.unique_id
        else:
            actor.pop("uniqueId", None)
        machine_id = str(ctx.machine_id or "").strip()

    if ctx.gcid or ctx.profile_id:
        _apply_runtime_overrides(
            payload,
            gcid=ctx.gcid or None,
            user_id=None,
            profile_id=ctx.profile_id or None,
            now_iso=str(payload.get("occurredAt") or ""),
        )

    subject = payload.get("subject")
    if isinstance(subject, dict):
        src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        operation = str(src.get("operation") or "")
        if subject.get("type") == "machine":
            if machine_id:
                subject["id"] = [machine_id]
            else:
                subject.pop("id", None)
        if operation == "userSwitchWorkspaceApp":
            if ctx.profile_id:
                subject["targetWorkspaceId"] = ctx.profile_id
            if ctx.gcid:
                subject["sourceWorkspaceId"] = ctx.gcid

    return ctx
