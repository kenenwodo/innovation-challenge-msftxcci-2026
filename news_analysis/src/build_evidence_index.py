"""
Step 0 of the news fact-checker: build the evidence index.

Parses both versions of the rule (proposed 2025-16554 and final 2026-14439)
into paragraphs, tags each paragraph with an evidence tier and a voice,
embeds them for semantic search, and verifies the hand-written key-facts
sheet against the parsed text.

Run from the repo root:
  .venv/bin/python news_analysis/src/build_evidence_index.py
  .venv/bin/python news_analysis/src/build_evidence_index.py --no-embeddings

Outputs (news_analysis/output/evidence/):
  proposed_rule_sections.jsonl/.md   parsed section tree of each version
  final_rule_sections.jsonl/.md
  evidence_paragraphs.jsonl          one record per citable paragraph, both versions
  embeddings.npy, embeddings_meta.json
  key_facts_verified.json            key facts with the paragraph each quote was found in
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NEWS_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = NEWS_DIR.parent
sys.path.insert(0, str(REPO_DIR / "policy_analysis" / "src"))

import extract_html  # noqa: E402  (policy_analysis/src/extract_html.py)


# --------------------------------------------------
# 1. CONFIGURATION
# --------------------------------------------------

EVIDENCE_DIR = NEWS_DIR / "output" / "evidence"
KEY_FACTS_PATH = NEWS_DIR / "key_facts.json"
RULE_VERSIONS_PATH = REPO_DIR / "data" / "ICEB-2025-0001-21962" / "rule_versions.json"

HTML_PATHS = {
    "proposed": REPO_DIR / "data" / "ICEB-2025-0001-0001" / "document" / "content.html",
    "final": REPO_DIR / "data" / "ICEB-2025-0001-21962" / "document" / "content.html",
}

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# How much weight a paragraph carries as evidence for a claim about the rule.
TIERS = {
    "A": "binding text: the CFR amendments themselves",
    "B": "agency statement: DHS's description of the rule, its dates, rationale and process",
    "C": "agency estimate: DHS's cost-benefit and regulatory flexibility analysis",
    "D": "comment record: what commenters said and how DHS responded",
    "X": "reference only: headers, contacts, acronym lists, signature",
}

TIER_BY_SOURCE_TYPE = {
    "regulatory_text": "A",
    "amendatory_instruction": "A",
    "agency_explanation": "B",
    "procedural": "B",          # e.g. the Congressional Review Act statement
    "front_matter": "B",        # SUMMARY, DATES, ACTION
    "cost_analysis": "C",
    "comment_response": "D",
}

# Sections that are not evidence for anything a news article would claim
REFERENCE_ONLY = re.compile(
    r"^(FRONT|FRONT\.(ADDRESSES|FOR_FURTHER_INFORMATION_CONTACT|SUPPLEMENTARY_INFORMATION)"
    r"|LIST_OF_SUBJECTS.*|REGULATORY_AMENDMENTS|SIGNATURE)$"
)

COMMENTS_RE = re.compile(r"^Comments?:")
RESPONSE_RE = re.compile(r"^Responses?:")


# --------------------------------------------------
# 2. PARSE BOTH VERSIONS
# --------------------------------------------------

def load_rule_versions():
    versions = json.loads(RULE_VERSIONS_PATH.read_text(encoding="utf-8"))
    return {
        name: {
            "fr_doc": versions[name]["frDocNum"],
            "regulations_gov_id": versions[name]["id"],
            "published": versions[name]["postedDate"][:10],
            "effective": (versions[name].get("effectiveDate") or "")[:10] or None,
        }
        for name in ("proposed", "final")
    }


def parse_versions():
    """Parse each version and save its section tree. Returns {doc: (sections, volume)}."""
    parsed = {}
    for doc, html_path in HTML_PATHS.items():
        document = {**extract_html.DOCUMENTS[doc], "output_stem": f"{doc}_rule_sections"}
        sections, volume, parser = extract_html.parse_rule(html_path, document)
        extract_html.save_outputs(sections, volume, document, EVIDENCE_DIR)

        print(f"\n{document['title']} ({html_path.relative_to(REPO_DIR)})")
        extract_html.print_report(sections, parser)
        parsed[doc] = (sections, volume)
    return parsed


# --------------------------------------------------
# 3. FLATTEN INTO EVIDENCE RECORDS
# --------------------------------------------------

def tier_for(section):
    if REFERENCE_ONLY.match(section["section_id"]) or section["heading"].startswith("Acronyms"):
        return "X"
    return TIER_BY_SOURCE_TYPE.get(section["source_type"], "B")


def fr_cite(volume, start, end):
    if not start:
        return None
    return f"{volume} FR {start}" if start == end else f"{volume} FR {start}–{end}"


def section_records(section, doc, meta, volume):
    """Yield the evidence records for one section's paragraphs and footnotes."""
    tier = tier_for(section)
    base = {
        "doc": doc,
        "fr_doc": meta["fr_doc"],
        "section_id": section["section_id"],
        "section_path": section["path"],
        "heading": section["heading"],
        "tier": tier,
        "source_type": section["source_type"],
    }

    # In the comment record, "Comments:" opens a block of commenter views and
    # "Response:" switches to DHS. Both halves share a comment_block id.
    voice = "regulation" if tier == "A" else "agency"
    block_number, block = 0, None

    for paragraph in section["paragraphs"]:
        text = paragraph["text"]
        if paragraph["kind"] == "omitted" or not text.strip():
            continue

        if tier == "D":
            if COMMENTS_RE.match(text):
                block_number += 1
                block = f"{doc}:{section['section_id']}#c{block_number}"
                voice = "commenters"
            elif RESPONSE_RE.match(text):
                voice = "agency"

        yield {
            "evidence_id": f"{doc}:{paragraph['para_id']}",
            **base,
            "para_id": paragraph["para_id"],
            "kind": paragraph["kind"],
            "voice": voice,
            "comment_block": block if tier == "D" else None,
            "cfr_citation": paragraph["cfr_citation"],
            "fr_page_start": paragraph["fr_page_start"],
            "fr_page_end": paragraph["fr_page_end"],
            "fr_cite": fr_cite(volume, paragraph["fr_page_start"], paragraph["fr_page_end"]),
            "text": text,
        }

    for footnote in section["footnotes"]:
        yield {
            "evidence_id": f"{doc}:{section['section_id']}/fn{footnote['number']}",
            **base,
            "para_id": f"{section['section_id']}/fn{footnote['number']}",
            "kind": "footnote",
            "voice": "agency",
            "comment_block": None,
            "cfr_citation": None,
            "fr_page_start": footnote["fr_page"],
            "fr_page_end": footnote["fr_page"],
            "fr_cite": fr_cite(volume, footnote["fr_page"], footnote["fr_page"]),
            "text": footnote["text"],
        }


def build_records(parsed, versions):
    records = []
    for doc, (sections, volume) in parsed.items():
        for section in sections:
            records.extend(section_records(section, doc, versions[doc], volume))
    return records


def search_text(record):
    """What gets embedded and keyword-indexed: the text plus where it sits."""
    context = record["cfr_citation"] or record["heading"]
    return f"{context}: {record['text']}"


# --------------------------------------------------
# 4. EMBEDDINGS
# --------------------------------------------------

def build_embeddings(records):
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print(f"\nEmbedding {len(records):,} paragraphs with {EMBEDDING_MODEL} ...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    vectors = model.encode(
        [search_text(r) for r in records],
        batch_size=32, normalize_embeddings=True, show_progress_bar=True,
    )
    np.save(EVIDENCE_DIR / "embeddings.npy", vectors.astype(np.float32))

    meta = {
        "model": EMBEDDING_MODEL,
        "dimensions": int(vectors.shape[1]),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evidence_ids": [r["evidence_id"] for r in records],
    }
    (EVIDENCE_DIR / "embeddings_meta.json").write_text(json.dumps(meta), encoding="utf-8")


# --------------------------------------------------
# 5. VERIFY THE KEY FACTS
# --------------------------------------------------

def normalize(text):
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_key_facts(records):
    """
    Find every key-fact quote in the parsed text. Quotes are stored without
    paragraph IDs so the sheet survives parser changes; the paragraph is
    looked up here, preferring the strongest tier.
    """
    sheet = json.loads(KEY_FACTS_PATH.read_text(encoding="utf-8"))
    by_doc = {}
    for record in records:
        by_doc.setdefault(record["doc"], []).append((normalize(record["text"]), record))

    failures = 0
    for fact in sheet["facts"]:
        for evidence in fact["evidence"]:
            quote = normalize(evidence["quote"])
            found = [r for text, r in by_doc[evidence["doc"]] if quote in text]
            found.sort(key=lambda r: r["tier"])

            evidence["found"] = bool(found)
            evidence["matches"] = [
                {key: r[key] for key in ("evidence_id", "tier", "voice", "cfr_citation", "fr_cite")}
                for r in found
            ]
            if not found:
                failures += 1
                print(f"WARNING: {fact['fact_id']}: quote not found in {evidence['doc']} rule: "
                      f"{evidence['quote'][:80]!r}")
            elif found[0]["tier"] not in fact.get("allowed_tiers", "AB"):
                failures += 1
                print(f"WARNING: {fact['fact_id']}: best match is tier {found[0]['tier']}, "
                      f"expected {fact.get('allowed_tiers', 'AB')}")

        fact["verified"] = all(e["found"] for e in fact["evidence"])

    sheet["verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = EVIDENCE_DIR / "key_facts_verified.json"
    path.write_text(json.dumps(sheet, indent=2, ensure_ascii=False), encoding="utf-8")

    verified = sum(f["verified"] for f in sheet["facts"])
    print(f"\nKey facts: {verified}/{len(sheet['facts'])} verified, {failures} problems")
    return failures


# --------------------------------------------------
# 6. MAIN PROGRAM
# --------------------------------------------------

def print_summary(records):
    print(f"\nEvidence records: {len(records):,}")
    for doc in HTML_PATHS:
        counts = {}
        for record in records:
            if record["doc"] == doc:
                counts[record["tier"]] = counts.get(record["tier"], 0) + 1
        print(f"  {doc:<9}" + "  ".join(f"{tier}={counts.get(tier, 0)}" for tier in TIERS))

    voices = {}
    for record in records:
        if record["tier"] == "D":
            voices[record["voice"]] = voices.get(record["voice"], 0) + 1
    blocks = {r["comment_block"] for r in records if r["comment_block"]}
    print(f"  comment record: {len(blocks)} comment/response blocks, "
          + ", ".join(f"{k} {v}" for k, v in sorted(voices.items())))


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    arg_parser.add_argument("--no-embeddings", action="store_true",
                            help="skip the embedding step (keyword search still works)")
    args = arg_parser.parse_args()

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    versions = load_rule_versions()
    parsed = parse_versions()
    records = build_records(parsed, versions)

    with (EVIDENCE_DIR / "evidence_paragraphs.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    (EVIDENCE_DIR / "rule_versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    print_summary(records)

    if not args.no_embeddings:
        build_embeddings(records)

    failures = verify_key_facts(records)
    print(f"\nSaved to {EVIDENCE_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
