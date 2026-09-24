import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"

CHANGES_FILE = OUTPUT_DIR / "policy_changes.json"
V1_FILE = OUTPUT_DIR / "v1_policies.json"
V2_FILE = OUTPUT_DIR / "v2_policies.json"
OUTPUT_FILE = OUTPUT_DIR / "policy_changes_validated.json"


REQUIRED_FIELDS = {
    "policy_area",
    "change_type",
    "v1_provision_ids",
    "v2_provision_ids",
    "v1_rule",
    "v2_rule",
    "v1_evidence",
    "v2_evidence",
    "what_changed",
    "practical_effect",
    "confidence",
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text):
    if text is None:
        return ""
    return " ".join(str(text).lower().split())


def validate_schema(change):
    missing = REQUIRED_FIELDS - set(change.keys())

    if missing:
        return False, f"Missing fields: {sorted(missing)}"

    return True, None


def validate_change(change):
    """
    Conservative deterministic validation.

    This does NOT decide whether the legal interpretation is correct.
    It checks whether the comparison has enough extracted evidence
    to support its classification without relying solely on absence.
    """

    valid_schema, schema_issue = validate_schema(change)

    if not valid_schema:
        return "review_required", schema_issue

    change_type = change["change_type"].lower()

    v1_ids = change["v1_provision_ids"]
    v2_ids = change["v2_provision_ids"]

    v1_rule = normalize(change["v1_rule"])
    v2_rule = normalize(change["v2_rule"])

    v1_evidence = change["v1_evidence"]
    v2_evidence = change["v2_evidence"]

    # Both versions should contain evidence for unchanged/modified.
    if change_type in {"unchanged", "modified"}:
        if not v1_ids or not v2_ids:
            return (
                "review_required",
                "Comparison requires provisions from both V1 and V2.",
            )

        if not v1_evidence or not v2_evidence:
            return (
                "review_required",
                "Missing evidence from one or both policy versions.",
            )

    # Identical normalized rules should not be called modified.
    if change_type == "modified" and v1_rule == v2_rule:
        return (
            "review_required",
            "V1 and V2 normalized rules are identical but classified as modified.",
        )

    # Absence from extracted provisions alone cannot prove added/removed.
    if change_type == "added":
        return (
            "review_required",
            "Appears only in V2 extraction. Full V1 source must be checked "
            "before confirming that the provision was added.",
        )

    if change_type == "removed":
        return (
            "review_required",
            "Appears only in V1 extraction. Full V2 source must be checked "
            "before confirming that the provision was removed.",
        )

    return "validated", None


def main():
    print("Loading comparison results...")

    data = load_json(CHANGES_FILE)

    changes = data["changes"]

    validated_count = 0
    review_count = 0

    for change in changes:
        validation_status, reason = validate_change(change)

        change["validation_status"] = validation_status
        change["validation_reason"] = reason

        if validation_status == "validated":
            validated_count += 1
        else:
            review_count += 1

    output = {
        "source_comparison_file": CHANGES_FILE.name,
        "total_policy_areas": len(changes),
        "validated": validated_count,
        "review_required": review_count,
        "changes": changes,
    }

    OUTPUT_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print("VALIDATION COMPLETE")
    print(f"Policy areas: {len(changes)}")
    print(f"Validated: {validated_count}")
    print(f"Review required: {review_count}")
    print()
    print("Items requiring review:")

    for i, change in enumerate(changes, start=1):
        if change["validation_status"] == "review_required":
            print(
                f"  {i}. {change['policy_area']} "
                f"[{change['change_type'].upper()}]"
            )
            print(f"     Reason: {change['validation_reason']}")

    print()
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()