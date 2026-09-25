import csv
import json
import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import flatten_results_csv as flattener  # noqa: E402


def source_row(*, claims: list[dict], viewpoints: list[dict]) -> dict[str, str]:
    return {
        "article_id": "article-1",
        "title": "Example article",
        "source_urls_json": json.dumps(["https://example.test/article"]),
        "source_article_ids_json": json.dumps(["article-1"]),
        "claims_json": json.dumps(claims),
        "viewpoints_json": json.dumps(viewpoints),
        "extraction_warnings_json": "[]",
        "extraction_error_json": "null",
        "verification_warnings_json": "[]",
    }


def test_flatten_results_explodes_evidence_and_viewpoints(tmp_path: Path):
    claim = {
        "claim_text": "The rule changes the admission period.",
        "claim_type": "policy",
        "speaker": "DHS",
        "organization": "Department of Homeland Security",
        "source_quote": "The rule changes the admission period.",
        "start_char": 10,
        "end_char": 49,
        "sentence_ids": [1],
        "verification": {
            "status": "complete",
            "verdict": "supported",
            "reason": "Two sections support the claim.",
            "documents_searched": ["proposed"],
            "evidence_as_of": "2026-09-24",
            "review_required": False,
            "key_fact_ids": ["KF-1"],
            "retrieved_evidence_ids": ["E-1", "E-2"],
            "evidence": [
                {
                    "evidence_id": "E-1",
                    "doc": "proposed",
                    "citation": "90 FR 1",
                    "source_type": "regulatory_text",
                    "source_url": None,
                    "text": "First supporting section.",
                },
                {
                    "evidence_id": "E-2",
                    "doc": "proposed",
                    "citation": "90 FR 2",
                    "source_type": "agency_explanation",
                    "source_url": None,
                    "text": "Second supporting section.",
                },
            ],
        },
    }
    viewpoint = {
        "speaker": "Example advocate",
        "organization": "Example group",
        "stance": "oppose",
        "target_provision": "fixed admission periods",
        "reason": "more paperwork",
        "source_quote": "The rule creates more paperwork.",
        "start_char": 50,
        "end_char": 82,
        "sentence_ids": [2],
    }
    input_path = tmp_path / "results.csv"
    output_path = tmp_path / "results_flattened.csv"
    row = source_row(claims=[claim], viewpoints=[viewpoint])
    with input_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    counts = flattener.flatten_results(input_path, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert counts == {
        "articles": 1,
        "rows": 3,
        "article": 0,
        "claim": 2,
        "viewpoint": 1,
    }
    assert [row["evidence_id"] for row in rows[:2]] == ["E-1", "E-2"]
    assert rows[2]["record_type"] == "viewpoint"
    assert rows[2]["viewpoint_stance"] == "oppose"
    assert not any(field.endswith("_json") for field in rows[0])
    assert len({row["flat_row_id"] for row in rows}) == 3


def test_flatten_results_keeps_an_empty_article(tmp_path: Path):
    input_path = tmp_path / "results.csv"
    output_path = tmp_path / "results_flattened.csv"
    row = source_row(claims=[], viewpoints=[])
    with input_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    counts = flattener.flatten_results(input_path, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        output_row = next(csv.DictReader(handle))
    assert counts["article"] == 1
    assert output_row["record_type"] == "article"
    assert output_row["flat_row_id"] == "article-1:article:1"
