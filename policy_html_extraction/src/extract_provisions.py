import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "output"

load_dotenv(PROJECT_DIR / ".env")

client = OpenAI(
    base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
)

MODEL = os.environ["AZURE_OPENAI_MODEL"]


def extract_document_number(full_text: str) -> str | None:
    """
    Extract the Federal Register document number directly from the
    official source text.
    """
    match = re.search(
        r"\[FR Doc No:\s*([^\]]+)\]",
        full_text,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    return None


def extract_summary_section(full_text: str, version: str) -> str:
    """
    Extract the appropriate authoritative summary section.

    V1:
        B. Summary of the Proposed Regulatory Provisions

    V2:
        C. Summary of Changes
    """

    if version == "v1":
        start_pattern = (
            r"B\.\s+Summary of the Proposed Regulatory Provisions"
        )
        end_pattern = r"\n\s*C\.\s+"
        section_name = "Summary of the Proposed Regulatory Provisions"

    elif version == "v2":
        start_pattern = r"C\.\s+Summary of Changes"
        end_pattern = (
            r"\n\s*D\.\s+Summary of the Costs and Benefits"
        )
        section_name = "Summary of Changes"

    else:
        raise ValueError("Version must be 'v1' or 'v2'.")

    start_match = re.search(
        start_pattern,
        full_text,
        flags=re.IGNORECASE,
    )

    if not start_match:
        raise ValueError(
            f"Could not find '{section_name}' for {version.upper()}."
        )

    start = start_match.start()

    remaining_text = full_text[start_match.end():]

    end_match = re.search(
        end_pattern,
        remaining_text,
        flags=re.IGNORECASE,
    )

    if end_match:
        end = start_match.end() + end_match.start()
        section = full_text[start:end]
    else:
        section = full_text[start:]

    return section.strip()


def extract_json_array(model_output: str):
    """
    Safely locate and parse the JSON array returned by the model.
    """

    cleaned = model_output.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1:
        raise ValueError(
            "Model response did not contain a JSON array."
        )

    return json.loads(cleaned[start:end + 1])


def extract_provisions(source_text: str, version: str):
    """
    Normalize regulatory provisions using the same schema for both
    document versions.
    """

    rule_label = (
        "proposed regulatory change"
        if version == "v1"
        else "final regulatory provision"
    )

    system_prompt = f"""
You are extracting regulatory provisions from an official U.S. Federal
Register document.

Your job is extraction and normalization, not legal advice and not
independent policy analysis.

Use ONLY the source text provided by the user.

This document is {version.upper()}.

Identify each distinct substantive regulatory change described in the
source text.

For the "proposed_rule" field, record the {rule_label} described by the
source. The field name remains "proposed_rule" so V1 and V2 use the
same comparison schema.

Do not invent requirements, exceptions, durations, affected groups,
CFR references, or policy effects.

Do not merge distinct provisions merely because they affect the same
visa category.

Do not create duplicate provisions that describe the same underlying
change.

For every provision, provide an exact evidence quote copied from the
source text. The quote must directly support the extracted rule.

Return ONLY a valid JSON array.

Each object must contain exactly these fields:

{{
  "policy_name": "short descriptive name",
  "affected_groups": ["group 1", "group 2"],
  "current_rule": "current/baseline rule if explicitly stated, otherwise null",
  "proposed_rule": "the regulatory provision",
  "plain_english": "concise explanation in plain English",
  "requirements": ["requirement"],
  "restrictions": ["restriction"],
  "exceptions": ["exception"],
  "time_limits_or_durations": ["duration or deadline"],
  "cfr_references": ["CFR citation"],
  "source_section": "section or subsection name if identifiable",
  "evidence_quote": "exact quotation from supplied source text"
}}

Use [] when a list field is not supported by the source.
Use null for current_rule when the baseline is not explicitly stated.

The evidence_quote must be copied exactly from the supplied text.
"""

    response = client.responses.create(
        model=MODEL,
        instructions=system_prompt,
        input=source_text,
    )

    return extract_json_array(response.output_text)


def normalize_text(text: str) -> str:
    """
    Normalize harmless formatting differences such as Federal Register
    line wrapping while preserving the actual wording.
    """
    text = text.replace("\x00", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def verify_evidence(provisions, source_text):
    """
    Verify that each evidence quote occurs in the official source after
    normalizing whitespace.
    """

    normalized_source = normalize_text(source_text)

    verified = []

    for provision in provisions:
        quote = provision.get("evidence_quote", "")
        normalized_quote = normalize_text(quote)

        provision["evidence_verified"] = bool(
            normalized_quote
            and normalized_quote in normalized_source
        )

        verified.append(provision)

    return verified


def main():
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python src/extract_provisions.py v1")
        print("  python src/extract_provisions.py v2")
        sys.exit(1)

    version = sys.argv[1].lower()

    if version not in {"v1", "v2"}:
        print("Error: version must be 'v1' or 'v2'.")
        sys.exit(1)

    input_file = OUTPUT_DIR / f"{version}_official_text.txt"
    output_file = OUTPUT_DIR / f"{version}_policies.json"

    if not input_file.exists():
        raise FileNotFoundError(
            f"Could not find {version.upper()} official text: "
            f"{input_file}"
        )

    print(f"Reading {version.upper()} official text...")

    full_text = input_file.read_text(
        encoding="utf-8"
    )

    document_number = extract_document_number(full_text)

    summary_section = extract_summary_section(
        full_text,
        version,
    )

    print(
        f"Found {version.upper()} regulatory provisions section: "
        f"{len(summary_section):,} characters."
    )

    print(
        f"Extracting {version.upper()} regulatory provisions "
        f"with {MODEL}..."
    )

    provisions = extract_provisions(
        summary_section,
        version,
    )

    provisions = verify_evidence(
        provisions,
        summary_section,
    )

    verified_count = sum(
        1
        for provision in provisions
        if provision["evidence_verified"]
    )

    result = {
        "version": version.upper(),
        "document_number": document_number,
        "source_type": "official Federal Register HTML",
        "source_section": (
            "Summary of the Proposed Regulatory Provisions"
            if version == "v1"
            else "Summary of Changes"
        ),
        "provision_count": len(provisions),
        "evidence_verified_count": verified_count,
        "provisions": provisions,
    }

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(f"{version.upper()} EXTRACTION COMPLETE")
    print(f"Document number: {document_number}")
    print(f"Provisions extracted: {len(provisions)}")
    print(
        f"Evidence quotes verified: "
        f"{verified_count}/{len(provisions)}"
    )
    print(f"Saved to: {output_file}")


if __name__ == "__main__":
    main()
