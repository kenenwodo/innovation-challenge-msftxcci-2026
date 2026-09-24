import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"

INPUT_FILE = OUTPUT_DIR / "policy_changes_validated.json"
V1_FILE = OUTPUT_DIR / "v1_policies.json"
V2_FILE = OUTPUT_DIR / "v2_policies.json"

OUTPUT_FILE = OUTPUT_DIR / "policy_changes_validated.md"


def format_list(items):
    if not items:
        return "None"

    if not isinstance(items, list):
        items = [items]

    return "\n".join(f"- {item}" for item in items)


def load_provisions(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return data.get("provisions", [])

    return data


def main():
    # --------------------------------------------------
    # LOAD DATA
    # --------------------------------------------------

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        comparisons = data.get("changes", [])
    else:
        comparisons = data

    v1_provisions = load_provisions(V1_FILE)
    v2_provisions = load_provisions(V2_FILE)

    # --------------------------------------------------
    # EVIDENCE VERIFICATION COUNTS
    # --------------------------------------------------

    v1_manual_review = [
        provision
        for provision in v1_provisions
        if not provision.get("evidence_verified", False)
    ]

    v2_manual_review = [
        provision
        for provision in v2_provisions
        if not provision.get("evidence_verified", False)
    ]

    # --------------------------------------------------
    # COMPARISON VALIDATION COUNTS
    # --------------------------------------------------

    validated = sum(
        1
        for item in comparisons
        if item.get("validation_status") == "validated"
    )

    review_required = sum(
        1
        for item in comparisons
        if item.get("validation_status") == "review_required"
    )

    # --------------------------------------------------
    # CHANGE TYPE COUNTS
    # --------------------------------------------------

    change_counts = {}

    for item in comparisons:
        change_type = item.get("change_type", "unknown").lower()

        change_counts[change_type] = (
            change_counts.get(change_type, 0) + 1
        )

    # --------------------------------------------------
    # BUILD MARKDOWN
    # --------------------------------------------------

    lines = []

    # Title
    lines.append("# Policy Version Comparison")
    lines.append("")
    lines.append(
        "AI-assisted extraction and semantic comparison of regulatory provisions from two official policy versions."
    )
    lines.append("")

    # --------------------------------------------------
    # EXTRACTION SUMMARY
    # --------------------------------------------------

    lines.append("## Extraction Summary")
    lines.append("")

    # V1
    lines.append("### V1 — Proposed Rule")
    lines.append("")

    lines.append(
        f"- Provisions extracted: {len(v1_provisions)}"
    )

    lines.append(
        f"- Exact evidence matches verified: "
        f"{len(v1_provisions) - len(v1_manual_review)}/"
        f"{len(v1_provisions)}"
    )

    lines.append(
        f"- Manual evidence verification required: "
        f"{len(v1_manual_review)}/{len(v1_provisions)}"
    )

    lines.append("")

    if v1_manual_review:
        lines.append(
            "#### V1 Provisions Requiring Manual Evidence Verification"
        )
        lines.append("")

        for provision in v1_manual_review:
            lines.append(
                f"- {provision.get('policy_name', 'Unnamed provision')}"
            )

        lines.append("")

    # V2
    lines.append("### V2 — Final Rule")
    lines.append("")

    lines.append(
        f"- Provisions extracted: {len(v2_provisions)}"
    )

    lines.append(
        f"- Exact evidence matches verified: "
        f"{len(v2_provisions) - len(v2_manual_review)}/"
        f"{len(v2_provisions)}"
    )

    lines.append(
        f"- Manual evidence verification required: "
        f"{len(v2_manual_review)}/{len(v2_provisions)}"
    )

    lines.append("")

    if v2_manual_review:
        lines.append(
            "#### V2 Provisions Requiring Manual Evidence Verification"
        )
        lines.append("")

        for provision in v2_manual_review:
            lines.append(
                f"- {provision.get('policy_name', 'Unnamed provision')}"
            )

        lines.append("")

    # Evidence verification explanation
    lines.append(
        "> **Evidence verification note:** Manual verification required does not mean that a provision is incorrect or unsupported. It means that the generated evidence quote could not be matched as one exact, contiguous passage in the extracted official source text. This can occur when nearby passages are combined or source-text spacing differs. These items are retained and flagged for human verification against the official source."
    )

    lines.append("")

    # --------------------------------------------------
    # VERSION COMPARISON SUMMARY
    # --------------------------------------------------

    lines.append("## Version Comparison Summary")
    lines.append("")

    lines.append(
        f"- Policy areas compared: {len(comparisons)}"
    )

    lines.append(
        f"- Passed automated validation checks: "
        f"{validated}/{len(comparisons)}"
    )

    lines.append(
        f"- Analyst review required: "
        f"{review_required}/{len(comparisons)}"
    )

    for change_type in [
        "unchanged",
        "modified",
        "added",
        "removed",
    ]:
        if change_type in change_counts:
            label = change_type.capitalize()

            if change_type in {"added", "removed"}:
                label += " candidates"

            lines.append(
                f"- {label}: {change_counts[change_type]}"
            )

    lines.append("")

    # Comparison validation explanation
    lines.append(
        "> **Comparison validation note:** Analyst review required does not mean that a comparison is wrong. It means the automated pipeline cannot confirm the classification without human review. In particular, absence from an extracted provision set is not treated as proof that a provision was absent from the underlying official document."
    )

    lines.append("")

    # --------------------------------------------------
    # DETAILED COMPARISONS
    # --------------------------------------------------

    lines.append("## Detailed Comparisons")
    lines.append("")

    for index, item in enumerate(comparisons, start=1):
        policy_area = item.get(
            "policy_area",
            item.get(
                "policy_name",
                f"Policy Area {index}",
            ),
        )

        change_type = item.get(
            "change_type",
            "Unknown",
        )

        confidence = item.get(
            "confidence",
            "Not specified",
        )

        validation_status = item.get(
            "validation_status",
            "Not specified",
        )

        lines.append("---")
        lines.append("")

        lines.append(
            f"### {index}. {policy_area}"
        )

        lines.append("")

        lines.append(
            f"**Change Type:** {change_type.upper()}"
        )

        lines.append("")

        lines.append(
            f"**Confidence:** {confidence}"
        )

        lines.append("")

        lines.append(
            f"**Validation Status:** "
            f"{validation_status.replace('_', ' ').title()}"
        )

        lines.append("")

        # Validation note
        if item.get("validation_reason"):
            lines.append("#### Validation Note")
            lines.append("")

            lines.append(
                str(item["validation_reason"])
            )

            lines.append("")

        # V1 IDs
        lines.append("#### V1 Provision IDs")
        lines.append("")

        lines.append(
            format_list(
                item.get("v1_provision_ids", [])
            )
        )

        lines.append("")

        # V2 IDs
        lines.append("#### V2 Provision IDs")
        lines.append("")

        lines.append(
            format_list(
                item.get("v2_provision_ids", [])
            )
        )

        lines.append("")

        # V1 Rule
        lines.append("#### V1 Rule")
        lines.append("")

        lines.append(
            item.get("v1_rule")
            or "No corresponding V1 rule identified."
        )

        lines.append("")

        # V2 Rule
        lines.append("#### V2 Rule")
        lines.append("")

        lines.append(
            item.get("v2_rule")
            or "No corresponding V2 rule identified."
        )

        lines.append("")

        # What changed
        lines.append("#### What Changed")
        lines.append("")

        lines.append(
            item.get("what_changed")
            or "No substantive change identified."
        )

        lines.append("")

        # Practical effect
        lines.append("#### Practical Effect")
        lines.append("")

        lines.append(
            item.get("practical_effect")
            or "Not specified."
        )

        lines.append("")

        # V1 evidence
        lines.append("#### V1 Evidence")
        lines.append("")

        lines.append(
            format_list(
                item.get("v1_evidence", [])
            )
        )

        lines.append("")

        # V2 evidence
        lines.append("#### V2 Evidence")
        lines.append("")

        lines.append(
            format_list(
                item.get("v2_evidence", [])
            )
        )

        lines.append("")

    # --------------------------------------------------
    # SAVE MARKDOWN
    # --------------------------------------------------

    OUTPUT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    # --------------------------------------------------
    # TERMINAL SUMMARY
    # --------------------------------------------------

    print("Markdown created:", OUTPUT_FILE)

    print(
        "V1 provisions:",
        len(v1_provisions),
    )

    print(
        "V1 evidence verified:",
        f"{len(v1_provisions) - len(v1_manual_review)}/"
        f"{len(v1_provisions)}",
    )

    print(
        "V2 provisions:",
        len(v2_provisions),
    )

    print(
        "V2 evidence verified:",
        f"{len(v2_provisions) - len(v2_manual_review)}/"
        f"{len(v2_provisions)}",
    )

    print(
        "Policy areas compared:",
        len(comparisons),
    )

    print(
        "Passed automated validation checks:",
        validated,
    )

    print(
        "Analyst review required:",
        review_required,
    )


if __name__ == "__main__":
    main()