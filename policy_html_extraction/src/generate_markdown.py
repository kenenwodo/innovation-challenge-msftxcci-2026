import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"


def format_list(items):
    if not items:
        return "- None specified"

    return "\n".join(f"- {item}" for item in items)


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python src/generate_markdown.py v1")
        print("  python src/generate_markdown.py v2")
        sys.exit(1)

    version = sys.argv[1].lower()

    if version not in {"v1", "v2"}:
        print("Error: version must be 'v1' or 'v2'.")
        sys.exit(1)

    input_file = OUTPUT_DIR / f"{version}_policies.json"
    output_file = OUTPUT_DIR / f"{version}_policies.md"

    if not input_file.exists():
        raise FileNotFoundError(
            f"Could not find {input_file}"
        )

    data = json.loads(
        input_file.read_text(encoding="utf-8")
    )

    provisions = data["provisions"]

    lines = [
        f"# {version.upper()} Regulatory Provisions",
        "",
        f"**Document Number:** {data.get('document_number', 'Unknown')}",
        f"**Source:** {data.get('source_type', 'Unknown')}",
        f"**Source Section:** {data.get('source_section', 'Unknown')}",
        f"**Total Provisions:** {data.get('provision_count', len(provisions))}",
        (
            f"**Evidence Automatically Verified:** "
            f"{data.get('evidence_verified_count', 0)}/{len(provisions)}"
        ),
        "",
        (
            "> **Note:** Evidence verification confirms whether the "
            "AI-generated evidence quotation can be matched to the "
            "official source after whitespace normalization. A failed "
            "verification is flagged for human review and does not "
            "automatically mean the underlying provision is unsupported."
        ),
        "",
        "---",
        "",
    ]

    for i, provision in enumerate(provisions, start=1):
        verified = provision.get("evidence_verified", False)

        lines.extend([
            f"## {i}. {provision.get('policy_name', 'Unnamed Provision')}",
            "",
            "### Affected Groups",
            format_list(provision.get("affected_groups", [])),
            "",
            "### Current / Baseline Rule",
            provision.get("current_rule") or "Not explicitly stated.",
            "",
            "### Regulatory Provision",
            provision.get("proposed_rule", ""),
            "",
            "### Plain-English Explanation",
            provision.get("plain_english", ""),
            "",
            "### Requirements",
            format_list(provision.get("requirements", [])),
            "",
            "### Restrictions",
            format_list(provision.get("restrictions", [])),
            "",
            "### Exceptions",
            format_list(provision.get("exceptions", [])),
            "",
            "### Time Limits or Durations",
            format_list(provision.get("time_limits_or_durations", [])),
            "",
            "### CFR References",
            format_list(provision.get("cfr_references", [])),
            "",
            "### Source Section",
            provision.get("source_section") or "Not specified.",
            "",
            "### Evidence",
            f"> {provision.get('evidence_quote', '')}",
            "",
            (
                "**Evidence Verification:** VERIFIED"
                if verified
                else "**Evidence Verification:** REQUIRES HUMAN REVIEW"
            ),
            "",
            "---",
            "",
        ])

    output_file.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print(f"Markdown created: {output_file}")
    print(f"Provisions written: {len(provisions)}")
    print(
        "Evidence verified: "
        f"{data.get('evidence_verified_count', 0)}/{len(provisions)}"
    )


if __name__ == "__main__":
    main()
