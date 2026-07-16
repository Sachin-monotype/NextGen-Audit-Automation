"""Layer 3 — domain template validation (macro families)."""

from __future__ import annotations

import re

from ..models import JsonDict, ValidationResult, ValidationStatus
from ..template_registry import DomainTemplate

BATCH_RESULT_KEYS = frozenset(
    {"actionCounts", "actionType", "batchId", "createdAt", "progressPercent", "status", "updatedAt"}
)

# Unresolved tokens that appear in NextGen notification copy when enrichment is incomplete.
_NOTIFICATION_PLACEHOLDER = re.compile(r":(fontName|familyName|styleName)\b")


def _keys(obj: JsonDict | None) -> frozenset[str]:
    if not isinstance(obj, dict):
        return frozenset()
    return frozenset(obj.keys())


def validate_domain_template(
    enriched: JsonDict,
    template: DomainTemplate,
    result: ValidationResult,
    *,
    skip_on_failure: bool = True,
) -> None:
    from .outcome import is_failure_event

    if skip_on_failure and is_failure_event(enriched):
        if template.outcome == "failure":
            result.add(
                "layer3-domain",
                "template",
                ValidationStatus.PASS,
                f"Applied failure template `{template.id}`",
            )
        return

    subject = enriched.get("subject")
    if not isinstance(subject, dict):
        return

    subject_type = subject.get("type")
    if template.subject_type != "*" and subject_type != template.subject_type:
        result.add(
            "layer3-domain",
            "subject_type",
            ValidationStatus.FAIL,
            f"Expected subject.type `{template.subject_type}`, got `{subject_type}`",
            "subject.type",
        )

    snap = subject.get("enrichedSnapshot")
    if template.requires_subject_enrichment:
        if not isinstance(snap, dict):
            result.add(
                "layer3-domain",
                "subject_snap",
                ValidationStatus.FAIL,
                "Missing subject.enrichedSnapshot",
                "subject.enrichedSnapshot",
            )
            return

        snap_keys = _keys(snap)
        # Bulk font ops often put batchId/styleIds on subject root before snapshot fills in.
        for key in template.subject_snap_keys:
            if key in subject and subject[key] is not None:
                snap_keys = snap_keys | {key}
        missing = template.subject_snap_keys - snap_keys
        if missing:
            result.add(
                "layer3-domain",
                "subject_snap_keys",
                ValidationStatus.FAIL,
                f"Missing subject.enrichedSnapshot keys: {sorted(missing)}",
                "subject.enrichedSnapshot",
            )

        if template.requires_font_details:
            _validate_font_details(snap, result)

        if template.requires_asset_sharing:
            _validate_asset_sharing(snap, result)

    extra_keys = _keys(subject) - {"type", "id", "metadata", "name"}
    if template.subject_extra_keys is not None:
        unexpected = extra_keys - template.subject_extra_keys - {"enrichedSnapshot"}
        if unexpected:
            result.add(
                "layer3-domain",
                "subject_extra",
                ValidationStatus.WARN,
                f"Unexpected subject keys: {sorted(unexpected)}",
                "subject",
            )

    metadata = subject.get("metadata")
    if isinstance(metadata, dict):
        if template.metadata_result_keys is not None:
            result_obj = metadata.get("result")
            if isinstance(result_obj, dict):
                missing = template.metadata_result_keys - _keys(result_obj)
                if missing:
                    result.add(
                        "layer3-domain",
                        "metadata_result",
                        ValidationStatus.WARN,
                        f"Missing metadata.result keys: {sorted(missing)}",
                        "subject.metadata.result",
                    )

        if template.metadata_input_keys is not None:
            input_obj = metadata.get("input")
            if isinstance(input_obj, dict):
                missing = template.metadata_input_keys - _keys(input_obj)
                if missing:
                    result.add(
                        "layer3-domain",
                        "metadata_input",
                        ValidationStatus.WARN,
                        f"Missing metadata.input keys: {sorted(missing)}",
                        "subject.metadata.input",
                    )

        if template.requires_batch_result:
            result_obj = metadata.get("result")
            if isinstance(result_obj, dict):
                missing = BATCH_RESULT_KEYS - _keys(result_obj)
                if missing:
                    result.add(
                        "layer3-domain",
                        "batch_result",
                        ValidationStatus.FAIL,
                        f"Bulk op missing batch result keys: {sorted(missing)}",
                        "subject.metadata.result",
                    )

    if template.requires_actor_enrichment:
        actor = enriched.get("actor")
        if isinstance(actor, dict):
            _validate_actor_enrichment(actor.get("enrichedSnapshot"), result)

    if template.requires_font_details:
        validate_notification_placeholders(enriched, result)

    if result.status == ValidationStatus.PASS:
        result.add(
            "layer3-domain",
            "template",
            ValidationStatus.PASS,
            f"Matched domain template `{template.id}` (macro {template.macro_family})",
        )


def _validate_actor_enrichment(snap: object, result: ValidationResult) -> None:
    if not isinstance(snap, dict):
        return
    user = snap.get("user")
    if isinstance(user, dict):
        profile = user.get("profile")
        if not isinstance(profile, dict) or not profile.get("id"):
            result.add(
                "layer3-domain",
                "actor_profile",
                ValidationStatus.FAIL,
                "actor.enrichedSnapshot.user.profile.id required",
                "actor.enrichedSnapshot.user.profile.id",
            )
        role = user.get("role")
        if not isinstance(role, dict):
            result.add(
                "layer3-domain",
                "actor_role",
                ValidationStatus.WARN,
                "actor.enrichedSnapshot.user.role expected",
                "actor.enrichedSnapshot.user.role",
            )
    customer = snap.get("customer")
    if isinstance(customer, dict) and not customer.get("id"):
        result.add(
            "layer3-domain",
            "actor_customer_id",
            ValidationStatus.FAIL,
            "actor.enrichedSnapshot.customer.id required",
            "actor.enrichedSnapshot.customer.id",
        )


def _validate_font_details(snap: JsonDict, result: ValidationResult) -> None:
    details = snap.get("fontDetails")
    if details is None:
        return
    if not isinstance(details, list) or len(details) == 0:
        result.add(
            "layer3-domain",
            "font_details",
            ValidationStatus.FAIL,
            "fontDetails must be a non-empty array when present",
            "subject.enrichedSnapshot.fontDetails",
        )
        return
    first = details[0]
    if not isinstance(first, dict):
        return
    if "family" not in first:
        result.add(
            "layer3-domain",
            "font_family",
            ValidationStatus.FAIL,
            "fontDetails[0].family required for notification templates",
            "subject.enrichedSnapshot.fontDetails[0].family",
        )
        return
    family = first.get("family")
    if isinstance(family, dict):
        catalog = family.get("catalog")
        family_id = family.get("id")
        name = catalog.get("name_en") if isinstance(catalog, dict) else None
        if not family_id:
            result.add(
                "layer3-domain",
                "font_family_id",
                ValidationStatus.FAIL,
                "fontDetails[0].family.id required for notification templates",
                "subject.enrichedSnapshot.fontDetails[0].family.id",
            )
        if not name or not str(name).strip():
            result.add(
                "layer3-domain",
                "font_family_name",
                ValidationStatus.FAIL,
                "fontDetails[0].family.catalog.name_en required (UI shows :familyName without it)",
                "subject.enrichedSnapshot.fontDetails[0].family.catalog.name_en",
            )
    styles = first.get("styles")
    if isinstance(styles, list) and styles:
        style0 = styles[0]
        if isinstance(style0, dict):
            sc = style0.get("catalog")
            font_name = sc.get("font_name") if isinstance(sc, dict) else None
            if not font_name:
                result.add(
                    "layer3-domain",
                    "font_style_name",
                    ValidationStatus.FAIL,
                    "fontDetails[0].styles[0].catalog.font_name required (UI shows :fontName without it)",
                    "subject.enrichedSnapshot.fontDetails[0].styles[0].catalog.font_name",
                )
    if styles is not None and not isinstance(styles, list):
        result.add(
            "layer3-domain",
            "font_styles",
            ValidationStatus.FAIL,
            "fontDetails[0].styles must be an array",
            "subject.enrichedSnapshot.fontDetails[0].styles",
        )


def _validate_asset_sharing(snap: JsonDict, result: ValidationResult) -> None:
    asset = snap.get("asset")
    if asset is not None and not isinstance(asset, dict):
        result.add(
            "layer3-domain",
            "asset_snap",
            ValidationStatus.FAIL,
            "enrichedSnapshot.asset must be an object",
            "subject.enrichedSnapshot.asset",
        )
    sharing = snap.get("sharingInfo")
    if sharing is not None and not isinstance(sharing, list):
        result.add(
            "layer3-domain",
            "sharing_info",
            ValidationStatus.FAIL,
            "enrichedSnapshot.sharingInfo must be an array",
            "subject.enrichedSnapshot.sharingInfo",
        )


def validate_notification_placeholders(enriched: JsonDict, result: ValidationResult) -> None:
    """Fail when enriched payload still contains unresolved :fontName / :familyName tokens."""
    hits: list[str] = []

    def walk(obj: object, path: str) -> None:
        if isinstance(obj, str):
            if _NOTIFICATION_PLACEHOLDER.search(obj):
                snippet = obj if len(obj) <= 120 else obj[:117] + "..."
                hits.append(f"{path}: {snippet}")
        elif isinstance(obj, dict):
            for key, val in obj.items():
                walk(val, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for idx, val in enumerate(obj):
                walk(val, f"{path}[{idx}]")

    walk(enriched, "")
    if hits:
        result.add(
            "layer3-domain",
            "notification_placeholder",
            ValidationStatus.FAIL,
            "Unresolved notification placeholders "
            + "; ".join(hits[:3])
            + (" …" if len(hits) > 3 else ""),
            hits[0].split(":")[0] if hits else None,
        )
