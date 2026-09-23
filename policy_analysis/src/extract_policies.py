import os
import re
import json
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# --------------------------------------------------
# 1. CONFIGURATION
# --------------------------------------------------

load_dotenv()

client = OpenAI(
    base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

MODEL = os.environ["AZURE_OPENAI_MODEL"]

INPUT_FILE = Path("output/extracted_text.txt")
#JSON_OUTPUT = Path("output/proposed_policies.json")
JSON_OUTPUT = Path("output/chunk1_policies.json")
#MARKDOWN_OUTPUT = Path("output/proposed_policies.md")
MARKDOWN_OUTPUT = Path("output/chunk1_policies.md")

# --------------------------------------------------
# 2. SPLIT EXTRACTED TEXT INTO PDF PAGES
# --------------------------------------------------

def parse_pages(text):
    pattern = r"--- PAGE (\d+) ---"
    parts = re.split(pattern, text)

    pages = []

    # parts:
    # ["", "1", "page 1 text", "2", "page 2 text", ...]
    for i in range(1, len(parts), 2):
        page_number = int(parts[i])
        page_text = parts[i + 1].strip()

        pages.append({
            "page": page_number,
            "text": page_text
        })

    return pages


# --------------------------------------------------
# 3. GROUP PAGES INTO CHUNKS
# --------------------------------------------------

def create_chunks(pages, pages_per_chunk=5):
    chunks = []

    for i in range(0, len(pages), pages_per_chunk):
        group = pages[i:i + pages_per_chunk]

        chunk_text = ""

        for page in group:
            chunk_text += (
                f"\n\n--- PAGE {page['page']} ---\n\n"
                f"{page['text']}"
            )

        chunks.append({
            "start_page": group[0]["page"],
            "end_page": group[-1]["page"],
            "text": chunk_text
        })

    return chunks


# --------------------------------------------------
# 4. POLICY EXTRACTION PROMPT
# --------------------------------------------------

SYSTEM_PROMPT = """
You are analyzing an official U.S. federal proposed rule.

Your job is to identify CONCRETE PROPOSED REGULATORY CHANGES.

Do NOT provide a generic document summary.

A proposed policy change includes things such as:
- a new requirement
- a new restriction
- something newly allowed
- something no longer allowed
- a time limit or duration
- an admission-period rule
- an extension-of-stay procedure
- a transfer rule
- a program or educational-objective rule
- an employment-related rule
- a departure or grace-period rule
- an eligibility requirement
- an exception
- a reporting requirement
- any other substantive regulatory change

These are examples only. Discover the actual changes from the supplied
document rather than assuming these changes exist.

Do NOT extract:
- general background
- historical discussion
- economic analysis
- policy justification by itself
- general commentary
- a proposal that is not actually supported by the supplied text

For EVERY distinct proposed change return a JSON object containing:

{
  "policy_name": "short descriptive name",
  "affected_groups": ["groups affected"],
  "current_policy": "current rule if explicitly stated, otherwise null",
  "proposed_change": "precise description of what DHS proposes",
  "plain_english": "simple explanation",
  "allowed": ["things explicitly allowed"],
  "restricted_or_prohibited": ["things explicitly restricted or prohibited"],
  "exceptions": ["exceptions explicitly stated"],
  "time_limits_or_durations": ["all relevant durations or deadlines"],
  "cfr_references": ["relevant CFR sections"],
  "evidence_quote": "short exact quote from supplied text",
  "source_page": 1
}

IMPORTANT:
- Use ONLY the supplied document text.
- Do not use outside knowledge.
- Do not invent missing details.
- Preserve qualifications and exceptions.
- source_page must correspond to a PAGE marker in the supplied text.
- evidence_quote must be copied exactly from the supplied text.
- Return ONLY a JSON array.
- If there are no substantive proposed changes in the supplied pages,
  return [].
"""


# --------------------------------------------------
# 5. SEND EACH CHUNK TO THE MODEL
# --------------------------------------------------

def extract_from_chunk(chunk):
    response = client.responses.create(
        model=MODEL,
        input=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": chunk["text"]
            }
        ],
    )

    #result = response.output_text.strip()
    
    # Remove markdown fences if the model adds them.
    #result = result.replace("```json", "").replace("```", "").strip()

    #return json.loads(result)
    result = response.output_text.strip()

    result = result.replace("```json", "").replace("```", "").strip()

    # Keep only the JSON array if the model added extra text
    start = result.find("[")
    end = result.rfind("]")

    if start == -1 or end == -1:
        raise ValueError("Model did not return a JSON array")

    result = result[start:end + 1]

    return json.loads(result)

# --------------------------------------------------
# 6. SAVE HUMAN-READABLE MARKDOWN
# --------------------------------------------------

def save_markdown(policies):
    lines = [
        "# Proposed Policy Changes",
        "",
        "> AI-assisted extraction from the proposed Federal Register rule.",
        "> Each item should be reviewed against the cited source evidence.",
        ""
    ]

    for number, policy in enumerate(policies, start=1):
        lines.extend([
            f"## {number}. {policy.get('policy_name', 'Unnamed policy')}",
            "",
            f"**Affected groups:** {', '.join(policy.get('affected_groups', []))}",
            "",
            f"**Current policy:** {policy.get('current_policy') or 'Not stated'}",
            "",
            f"**Proposed change:** {policy.get('proposed_change', '')}",
            "",
            f"**Plain English:** {policy.get('plain_english', '')}",
            "",
            f"**Allowed:** {', '.join(policy.get('allowed', [])) or 'Not stated'}",
            "",
            "**Restricted or prohibited:** "
            + (", ".join(policy.get("restricted_or_prohibited", [])) or "Not stated"),
            "",
            f"**Exceptions:** {', '.join(policy.get('exceptions', [])) or 'Not stated'}",
            "",
            "**Time limits / durations:** "
            + (", ".join(policy.get("time_limits_or_durations", [])) or "Not stated"),
            "",
            f"**CFR references:** {', '.join(policy.get('cfr_references', [])) or 'Not stated'}",
            "",
            f"**Source page:** {policy.get('source_page', 'Unknown')}",
            "",
            "**Evidence:**",
            "",
            f"> {policy.get('evidence_quote', '')}",
            "",
            "---",
            ""
        ])

    MARKDOWN_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------
# 7. MAIN PROGRAM
# --------------------------------------------------

def main():
    print("Loading extracted Federal Register text...")

    text = INPUT_FILE.read_text(encoding="utf-8")

    pages = parse_pages(text)

    print(f"Found {len(pages)} PDF pages.")

    chunks = create_chunks(pages, pages_per_chunk=5)

    print(f"Created {len(chunks)} chunks.")

    all_policies = []

    for index, chunk in enumerate(chunks[:1], start=1):
        print(
            f"Processing chunk {index}/{len(chunks)} "
            f"(pages {chunk['start_page']}-{chunk['end_page']})..."
        )

        try:
            policies = extract_from_chunk(chunk)
            all_policies.extend(policies)

            print(f"  Found {len(policies)} candidate policies.")

        except Exception as error:
            print(f"  ERROR: {error}")

    JSON_OUTPUT.write_text(
        json.dumps(all_policies, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    save_markdown(all_policies)

    print()
    print("Extraction complete.")
    print(f"Total candidate policies: {len(all_policies)}")
    print(f"JSON: {JSON_OUTPUT}")
    print(f"Markdown: {MARKDOWN_OUTPUT}")


if __name__ == "__main__":
    main()