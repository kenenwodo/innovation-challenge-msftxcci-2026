import os
import json
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"
# --------------------------------------------------
# 1. CONFIGURATION
# --------------------------------------------------

load_dotenv()

client = OpenAI(
    base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

MODEL = os.environ["AZURE_OPENAI_MODEL"]


# --------------------------------------------------
# 2. LOAD ALL CANDIDATE POLICIES
# --------------------------------------------------

main_policies = json.loads(
    (OUTPUT_DIR / "proposed_policies.json").read_text(encoding="utf-8")
)

chunk1_policies = json.loads(
    (OUTPUT_DIR / "chunk1_policies.json").read_text(encoding="utf-8")
)

all_policies = chunk1_policies + main_policies

print(f"Loaded {len(all_policies)} candidate policies.")


# --------------------------------------------------
# 3. CONSOLIDATE + QUALITY CONTROL
# --------------------------------------------------

PROMPT = """
You are performing final quality control on policy provisions extracted
from ONE official U.S. federal proposed rule.

The input contains candidate policy changes extracted from different
pages of the same document.

IMPORTANT:
The extraction contains repetition because the Federal Register document
discusses the same proposal in summaries, explanations, analyses, and
proposed regulatory text.

Your job is NOT merely to shorten the list.

Your job is to produce a clean, evidence-grounded set of DISTINCT
substantive proposed regulatory changes.


STEP 1 — IDENTIFY TRUE DUPLICATES

Merge candidates when they describe the same underlying regulatory
change.

For example, multiple candidates describing replacement of Duration of
Status with fixed admission periods should not remain separate merely
because they came from different pages.

However, DO NOT merge separate substantive requirements merely because
they affect the same population.


STEP 2 — PRESERVE DISTINCT SUB-RULES WHEN IMPORTANT

Keep a rule separate when it creates a meaningfully different:

- requirement
- prohibition
- eligibility condition
- time limit
- procedure
- exception
- employment rule
- transfer rule
- educational-objective rule
- extension-of-stay rule
- departure/grace-period rule
- reporting requirement
- transition rule

Do not over-consolidate.


STEP 3 — EVIDENCE-FIRST QUALITY CONTROL

Every factual detail in the final policy must be supported by at least
one evidence quote contained in the candidate data.

This applies especially to:

- numbers
- durations
- deadlines
- admission periods
- eligibility requirements
- restrictions
- exceptions
- affected groups
- CFR references

If a candidate contains a factual detail that is NOT supported by its
evidence quote, REMOVE that detail.

Do not preserve unsupported information simply because it appeared in
a candidate field.


STEP 4 — RESOLVE CONTRADICTIONS

Candidate extractions may conflict.

If two candidates contain conflicting numbers, requirements, or
descriptions:

1. Compare their evidence quotes.
2. Keep only the version directly supported by the strongest and most
   specific quoted regulatory evidence.
3. If the evidence does not resolve the conflict, omit the disputed
   detail rather than guessing.

Never combine conflicting numbers into one policy.


STEP 5 — DO NOT INVENT

Use ONLY the supplied candidate data and evidence quotes.

Do not use outside knowledge.

Do not infer a restriction, exception, deadline, or requirement that
is not supported by the supplied evidence.


STEP 6 — WRITE PRECISE PLAIN ENGLISH

The plain-English explanation must preserve the actual scope of the
proposal.

For example, do not turn a specific restriction involving enrollment
at the same or lower educational level into a broader statement unless
the evidence actually supports that broader statement.


STEP 7 — SOURCE TRACEABILITY

Preserve the page numbers associated with the evidence retained for
each final policy.

Keep only useful evidence quotes.

Prefer specific regulatory text over vague explanatory discussion when
both support the same proposition.


OUTPUT

Return ONLY a valid JSON array.

Each object must have exactly this structure:

{
  "policy_name": "short specific name",
  "affected_groups": [],
  "current_policy": null,
  "proposed_change": "",
  "plain_english": "",
  "allowed": [],
  "restricted_or_prohibited": [],
  "exceptions": [],
  "time_limits_or_durations": [],
  "cfr_references": [],
  "source_pages": [],
  "evidence_quotes": []
}

QUALITY STANDARD:

A human government analyst should be able to read each item and know:

- exactly what DHS proposes to change
- who is affected
- what becomes allowed or restricted
- what important time limits apply
- what exceptions apply
- where the supporting evidence came from

Accuracy and evidence support are more important than producing a
particular number of policies.
"""


print("Running final consolidation and evidence review...")

response = client.responses.create(
    model=MODEL,
    input=[
        {
            "role": "system",
            "content": PROMPT
        },
        {
            "role": "user",
            "content": json.dumps(all_policies, ensure_ascii=False)
        }
    ],
)

result = response.output_text.strip()

result = result.replace("```json", "").replace("```", "").strip()

start = result.find("[")
end = result.rfind("]")

if start == -1 or end == -1:
    raise ValueError("Model did not return a JSON array.")

result = result[start:end + 1]

final_policies = json.loads(result)


# --------------------------------------------------
# 4. BASIC VALIDATION
# --------------------------------------------------

required_fields = [
    "policy_name",
    "affected_groups",
    "current_policy",
    "proposed_change",
    "plain_english",
    "allowed",
    "restricted_or_prohibited",
    "exceptions",
    "time_limits_or_durations",
    "cfr_references",
    "source_pages",
    "evidence_quotes",
]

for number, policy in enumerate(final_policies, start=1):

    missing = [
        field
        for field in required_fields
        if field not in policy
    ]

    if missing:
        print(
            f"WARNING: Policy {number} is missing fields: "
            f"{', '.join(missing)}"
        )

    if not policy.get("evidence_quotes"):
        print(
            f"WARNING: Policy {number} has no evidence: "
            f"{policy.get('policy_name')}"
        )


# --------------------------------------------------
# 5. SAVE FINAL JSON
# --------------------------------------------------

json_output = OUTPUT_DIR / "proposed_policies_final.json"

json_output.write_text(
    json.dumps(final_policies, indent=2, ensure_ascii=False),
    encoding="utf-8"
)


# --------------------------------------------------
# 6. CREATE HUMAN-READABLE MARKDOWN
# --------------------------------------------------

lines = [
    "# Proposed Policy Changes",
    "",
    "AI-assisted analysis of the proposed Federal Register rule.",
    "",
    "> **Important:** Policy descriptions are AI interpretations of "
    "the cited source evidence. The official Federal Register text "
    "remains authoritative.",
    "",
]

for i, policy in enumerate(final_policies, start=1):

    lines.extend([
        f"## {i}. {policy.get('policy_name', 'Unnamed Policy')}",
        "",
        "**Affected groups:** "
        + (
            ", ".join(policy.get("affected_groups", []))
            or "Not stated"
        ),
        "",
        "**Current policy:** "
        + str(policy.get("current_policy") or "Not stated"),
        "",
        "**Proposed change:**",
        "",
        policy.get("proposed_change", ""),
        "",
        "**Plain English:**",
        "",
        policy.get("plain_english", ""),
        "",
        "**Allowed:** "
        + (
            ", ".join(policy.get("allowed", []))
            or "Not stated"
        ),
        "",
        "**Restricted or prohibited:** "
        + (
            ", ".join(
                policy.get("restricted_or_prohibited", [])
            )
            or "Not stated"
        ),
        "",
        "**Exceptions:** "
        + (
            ", ".join(policy.get("exceptions", []))
            or "Not stated"
        ),
        "",
        "**Time limits / durations:** "
        + (
            ", ".join(
                policy.get("time_limits_or_durations", [])
            )
            or "Not stated"
        ),
        "",
        "**CFR references:** "
        + (
            ", ".join(policy.get("cfr_references", []))
            or "Not stated"
        ),
        "",
        "**Source pages:** "
        + (
            ", ".join(
                str(page)
                for page in policy.get("source_pages", [])
            )
            or "Not stated"
        ),
        "",
        "**Evidence:**",
        "",
    ])

    quotes = policy.get("evidence_quotes", [])

    if quotes:
        for quote in quotes:
            lines.append(f"> {quote}")
            lines.append("")
    else:
        lines.append("> No supporting quote retained.")
        lines.append("")

    lines.extend(["---", ""])


markdown_output = OUTPUT_DIR / "proposed_policies_final.md"

markdown_output.write_text(
    "\n".join(lines),
    encoding="utf-8"
)


# --------------------------------------------------
# 7. FINISH
# --------------------------------------------------

print()
print("FINAL QUALITY CONTROL COMPLETE")
print(f"Candidate policies reviewed: {len(all_policies)}")
print(f"Final distinct policies: {len(final_policies)}")
print()
print("Saved:")
print(f"  {json_output}")
print(f"  {markdown_output}")