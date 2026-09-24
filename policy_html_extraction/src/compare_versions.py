import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"

V1_FILE = OUTPUT_DIR / "v1_policies.json"
V2_FILE = OUTPUT_DIR / "v2_policies.json"
OUTPUT_FILE = OUTPUT_DIR / "policy_changes.json"

load_dotenv(PROJECT_DIR / ".env")

client = OpenAI(
    base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

MODEL = os.environ["AZURE_OPENAI_MODEL"]


def load_provisions(path: Path):
    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    return data["provisions"]


def extract_json_array(model_output: str):
    cleaned = model_output.strip()
    cleaned = cleaned.replace(
        "```json", ""
    ).replace(
        "```", ""
    ).strip()

    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1:
        raise ValueError(
            "Model response did not contain a JSON array."
        )

    return json.loads(
        cleaned[start:end + 1]
    )


def prepare_provisions(provisions):
    """
    Send only fields useful for version comparison.
    """

    prepared = []

    for index, provision in enumerate(
        provisions,
        start=1,
    ):
        prepared.append({
            "id": index,
            "policy_name": provision.get(
                "policy_name"
            ),
            "affected_groups": provision.get(
                "affected_groups", []
            ),
            "rule": provision.get(
                "proposed_rule"
            ),
            "requirements": provision.get(
                "requirements", []
            ),
            "restrictions": provision.get(
                "restrictions", []
            ),
            "exceptions": provision.get(
                "exceptions", []
            ),
            "time_limits_or_durations": provision.get(
                "time_limits_or_durations", []
            ),
            "cfr_references": provision.get(
                "cfr_references", []
            ),
            "evidence_quote": provision.get(
                "evidence_quote"
            ),
            "evidence_verified": provision.get(
                "evidence_verified", False
            ),
        })

    return prepared


def compare_versions(v1, v2):
    instructions = """
You are comparing two versions of an official U.S. regulatory rule.

V1 contains provisions extracted from the proposed rule.
V2 contains provisions extracted from the final rule.

Use ONLY the V1 and V2 data supplied in the input.

Do not rely on outside knowledge.
Do not provide legal advice.
Do not invent policy changes.

Your job is to semantically match provisions describing the same
underlying regulatory issue.

Do NOT match provisions merely because they have similar wording.
Use the substantive rule, affected groups, CFR references,
requirements, restrictions, exceptions, and time limits.

The ordering of V1 and V2 provisions is NOT meaningful.

One provision in one version may correspond to more than one provision
in the other version if the rule became more or less granular.

IMPORTANT COMPARISON RULES:

Differences caused only by extraction granularity are NOT substantive
regulatory changes.

Do not classify a policy area as "modified" merely because:
- multiple V1 provisions were consolidated into one V2 provision,
- one V1 provision was split into multiple V2 provisions,
- provision names changed,
- wording was reorganized,
- the final rule provides more explanation,
- the extraction grouped concepts differently.

Classify a policy area as "modified" ONLY when the supplied V1 and V2
content demonstrates a substantive change in at least one of:
- requirement,
- restriction,
- exception,
- eligibility,
- affected group,
- duration or time limit,
- required procedure,
- authorization,
- legal or practical consequence.

Prefer the narrowest defensible semantic match.

Do not combine unrelated regulatory concepts into one comparison merely
because they appear in the same CFR section or broad topic.

For example, admission periods, extension-of-stay procedures,
educational-objective changes, form-reference updates, and transition
rules should remain separate policy areas when the supplied provisions
describe separate regulatory requirements.

A provision appearing only in one extracted dataset does NOT by itself
prove that the underlying rule was added or removed. Before classifying
something as "added" or "removed", check all supplied provisions in the
other version for a substantively equivalent rule.

When the evidence does not clearly establish a substantive change,
prefer "unchanged" with medium or low confidence rather than inventing
a modification.

Classify each policy area as exactly one of:

"unchanged"
"modified"
"removed"
"added"

Definitions:

unchanged:
The substantive rule is materially the same in V1 and V2. Minor
wording, formatting, terminology, or technical clarification does not
by itself make a provision modified.

modified:
The same underlying policy area exists in both versions, but a
substantive requirement, restriction, exception, affected group,
duration, procedure, or practical consequence changed.

removed:
A substantive V1 provision has no corresponding V2 provision in the
supplied data.

added:
A substantive V2 provision has no corresponding V1 provision in the
supplied data.

Be conservative. If the supplied evidence does not clearly establish
a substantive difference, do not invent one.

For "what_changed":
- For unchanged provisions, briefly state that no material change was
  identified from the supplied provisions.
- For modified provisions, state exactly what changed from V1 to V2.
- For removed provisions, state what V1 contained that is not present
  in the supplied V2 provisions.
- For added provisions, state what appears in V2 that is not present
  in the supplied V1 provisions.

For "practical_effect":
Describe only an effect that follows directly from the supplied V1 and
V2 provisions. If a practical effect cannot be established from the
supplied material, use null.

Confidence must be one of:
"high"
"medium"
"low"

Return ONLY a valid JSON array.

Each object must contain exactly:

{
  "policy_area": "descriptive policy area",
  "change_type": "unchanged | modified | removed | added",
  "v1_provision_ids": [1],
  "v2_provision_ids": [1],
  "v1_rule": "V1 rule or null",
  "v2_rule": "V2 rule or null",
  "v1_evidence": ["exact evidence quote from supplied V1 data"],
  "v2_evidence": ["exact evidence quote from supplied V2 data"],
  "what_changed": "concise comparison",
  "practical_effect": "concise effect or null",
  "confidence": "high | medium | low"
}

For an added provision:
v1_provision_ids must be []
v1_rule must be null
v1_evidence must be []

For a removed provision:
v2_provision_ids must be []
v2_rule must be null
v2_evidence must be []

Every substantive V1 provision and every substantive V2 provision
should be accounted for somewhere in the result.

Do not duplicate the same policy area unnecessarily.
"""

    input_data = {
        "v1": prepare_provisions(v1),
        "v2": prepare_provisions(v2),
    }

    response = client.responses.create(
        model=MODEL,
        instructions=instructions,
        input=json.dumps(
            input_data,
            ensure_ascii=False,
        ),
    )

    return extract_json_array(
        response.output_text
    )


def validate_comparison(
    comparisons,
    v1_count,
    v2_count,
):
    """
    Check that provision IDs returned by the model are valid and
    calculate coverage of both source versions.
    """

    valid_v1_ids = set(
        range(1, v1_count + 1)
    )
    valid_v2_ids = set(
        range(1, v2_count + 1)
    )

    used_v1_ids = set()
    used_v2_ids = set()

    invalid_v1_ids = set()
    invalid_v2_ids = set()

    for item in comparisons:
        for provision_id in item.get(
            "v1_provision_ids", []
        ):
            if provision_id in valid_v1_ids:
                used_v1_ids.add(provision_id)
            else:
                invalid_v1_ids.add(provision_id)

        for provision_id in item.get(
            "v2_provision_ids", []
        ):
            if provision_id in valid_v2_ids:
                used_v2_ids.add(provision_id)
            else:
                invalid_v2_ids.add(provision_id)

    missing_v1_ids = sorted(
        valid_v1_ids - used_v1_ids
    )

    missing_v2_ids = sorted(
        valid_v2_ids - used_v2_ids
    )

    return {
        "v1_total_provisions": v1_count,
        "v2_total_provisions": v2_count,
        "v1_covered_provisions": len(
            used_v1_ids
        ),
        "v2_covered_provisions": len(
            used_v2_ids
        ),
        "missing_v1_provision_ids": (
            missing_v1_ids
        ),
        "missing_v2_provision_ids": (
            missing_v2_ids
        ),
        "invalid_v1_provision_ids": sorted(
            invalid_v1_ids
        ),
        "invalid_v2_provision_ids": sorted(
            invalid_v2_ids
        ),
    }


def main():
    print("Loading V1 and V2 provisions...")

    v1 = load_provisions(V1_FILE)
    v2 = load_provisions(V2_FILE)

    print(f"V1 provisions: {len(v1)}")
    print(f"V2 provisions: {len(v2)}")

    print()
    print(
        f"Comparing policy versions with {MODEL}..."
    )

    comparisons = compare_versions(
        v1,
        v2,
    )

    validation = validate_comparison(
        comparisons,
        len(v1),
        len(v2),
    )
    # Reconcile provisions missed by the first comparison pass
    missing_v1 = validation["missing_v1_provision_ids"]
    missing_v2 = validation["missing_v2_provision_ids"]

    if missing_v1 or missing_v2:
        print()
        print("Coverage gaps detected.")
        print(f"Missing V1 IDs: {missing_v1}")
        print(f"Missing V2 IDs: {missing_v2}")
        print("Running reconciliation pass...")

        v1_prepared = prepare_provisions(v1)
        v2_prepared = prepare_provisions(v2)

        reconciliation_input = {
            "existing_comparisons": [
                {
                    "comparison_index": i,
                    **comparison,
                }
                for i, comparison in enumerate(comparisons, start=1)
            ],
            "missing_v1_provisions": [
                v1_prepared[i - 1]
                for i in missing_v1
            ],
            "missing_v2_provisions": [
                v2_prepared[i - 1]
                for i in missing_v2
            ],
            "all_v1_provisions": v1_prepared,
            "all_v2_provisions": v2_prepared,
        }

        reconciliation_prompt = """
You are performing a reconciliation pass on a regulatory
version comparison.

The first semantic comparison failed to account for some
source provisions.

Use ONLY the supplied V1 and V2 data.

For each missing provision, determine whether:

1. It belongs to an existing comparison representing the
   same underlying regulatory issue; or

2. It requires a new comparison entry.

Do not invent provisions or policy changes.

If a missing provision belongs to an existing comparison,
return the comparison_index and the provision ID that
should be added.

If it requires a new comparison, classify it as:
unchanged, modified, removed, or added.

Return ONLY valid JSON in this structure:

{
  "existing_comparison_updates": [
    {
      "comparison_index": 1,
      "add_v1_provision_ids": [],
      "add_v2_provision_ids": []
    }
  ],
  "new_comparisons": [
  {
    "policy_area": "descriptive policy area",
    "change_type": "unchanged | modified | removed | added",
    "v1_provision_ids": [],
    "v2_provision_ids": [],
    "v1_rule": "V1 rule or null",
    "v2_rule": "V2 rule or null",
    "v1_evidence": [],
    "v2_evidence": [],
    "what_changed": "concise evidence-grounded explanation",
    "practical_effect": "concise effect or null",
    "confidence": "high | medium | low"
  }
]
Every object in new_comparisons MUST contain ALL fields shown above.

Do not return partial comparison objects.

Differences caused only by extraction grouping, consolidation,
splitting, wording, or organization are NOT substantive changes.

Classify "modified" only when the supplied evidence demonstrates
a substantive change in a requirement, restriction, exception,
eligibility, affected group, duration, procedure, authorization,
or consequence.

If the substantive rule is the same, classify it as "unchanged"
even if V1 and V2 were extracted at different levels of granularity.
}

Every missing V1 AND every missing V2 provision must be accounted for.

You MUST explicitly account for every ID appearing in
missing_v1_provisions and missing_v2_provisions.

For each missing provision you must do exactly one of:

1. Add its ID to an existing comparison representing the same
   substantive regulatory issue; OR

2. Create a new comparison containing that ID.

Do not omit a missing provision merely because it is related to another
policy area.

Before returning your JSON, verify that every missing V1 ID and every
missing V2 ID appears exactly once in either:
- existing_comparison_updates, or
- new_comparisons.

A V2 provision with no substantively equivalent V1 provision may be
classified as "added".

A V1 provision with no substantively equivalent V2 provision may be
classified as "removed".

Return ONLY the required JSON object.
"""

        response = client.responses.create(
            model=MODEL,
            instructions=reconciliation_prompt,
            input=json.dumps(
                reconciliation_input,
                ensure_ascii=False,
            ),
        )

        cleaned = response.output_text.strip()
        cleaned = cleaned.replace(
            "```json", ""
        ).replace(
            "```", ""
        ).strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start == -1 or end == -1:
            raise ValueError(
                "Reconciliation response did not contain JSON."
            )

        reconciliation = json.loads(
            cleaned[start:end + 1]
        )

        for update in reconciliation.get(
            "existing_comparison_updates", []
        ):
            index = update["comparison_index"] - 1

            if not 0 <= index < len(comparisons):
                raise ValueError(
                    f"Invalid comparison index: {index + 1}"
                )

            comparison = comparisons[index]

            comparison["v1_provision_ids"] = sorted(
                set(comparison.get("v1_provision_ids", []))
                | set(update.get("add_v1_provision_ids", []))
            )

            comparison["v2_provision_ids"] = sorted(
                set(comparison.get("v2_provision_ids", []))
                | set(update.get("add_v2_provision_ids", []))
            )

        comparisons.extend(
            reconciliation.get("new_comparisons", [])
        )
        required_fields = {
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

        for i, comparison in enumerate(comparisons, start=1):
            missing_fields = required_fields - set(comparison.keys())

            if missing_fields:
                raise RuntimeError(
                    f"Comparison #{i} is incomplete. "
                    f"Missing fields: {sorted(missing_fields)}"
                )
        

        # Validate again after reconciliation
        validation = validate_comparison(
            comparisons,
            len(v1),
            len(v2),
        )
        remaining_v1 = validation["missing_v1_provision_ids"]
        remaining_v2 = validation["missing_v2_provision_ids"]

        if remaining_v1 or remaining_v2:
            raise RuntimeError(
                "Reconciliation did not achieve complete coverage. "
                f"Missing V1 IDs: {remaining_v1}; "
                f"Missing V2 IDs: {remaining_v2}"
            )
        print("Reconciliation complete.")
        print(
            f"V1 coverage: "
            f"{validation['v1_covered_provisions']}/{len(v1)}"
        )
        print(
            f"V2 coverage: "
            f"{validation['v2_covered_provisions']}/{len(v2)}"
        )
    change_counts = {
        "unchanged": 0,
        "modified": 0,
        "removed": 0,
        "added": 0,
    }

    for item in comparisons:
        change_type = item.get(
            "change_type"
        )

        if change_type in change_counts:
            change_counts[change_type] += 1

    result = {
        "comparison": "V1 proposed rule → V2 final rule",
        "v1_document": "2025-16554",
        "v2_document": "2026-14439",
        "comparison_count": len(comparisons),
        "change_counts": change_counts,
        "coverage_validation": validation,
        "changes": comparisons,
    }

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("VERSION COMPARISON COMPLETE")
    print(
        f"Policy areas compared: "
        f"{len(comparisons)}"
    )
    print(
        f"Unchanged: "
        f"{change_counts['unchanged']}"
    )
    print(
        f"Modified: "
        f"{change_counts['modified']}"
    )
    print(
        f"Removed: "
        f"{change_counts['removed']}"
    )
    print(
        f"Added: "
        f"{change_counts['added']}"
    )

    print()
    print("COVERAGE CHECK")
    print(
        f"V1 covered: "
        f"{validation['v1_covered_provisions']}"
        f"/{validation['v1_total_provisions']}"
    )
    print(
        f"V2 covered: "
        f"{validation['v2_covered_provisions']}"
        f"/{validation['v2_total_provisions']}"
    )
    print(
        "Missing V1 IDs:",
        validation[
            "missing_v1_provision_ids"
        ],
    )
    print(
        "Missing V2 IDs:",
        validation[
            "missing_v2_provision_ids"
        ],
    )

    print()
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
