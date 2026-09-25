#!/usr/bin/env python3
"""Convert the fact-check CSV into a readable long-form CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable


NEWS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = NEWS_DIR / "output" / "fact_check" / "results.csv"
DEFAULT_OUTPUT = NEWS_DIR / "output" / "fact_check" / "results_flattened.csv"

ARTICLE_FIELDS = [
    "article_id",
    "title",
    "url",
    "domain",
    "seendate",
    "window",
    "source_country",
    "is_repost",
    "has_text",
    "text_chars",
    "cluster_size",
    "source_urls",
    "source_article_ids",
    "extraction_status",
    "grounding_score",
    "confidence",
    "confidence_note",
    "checkable_claims",
    "checked_claims",
    "coverage_percent",
    "claim_count",
    "viewpoint_count",
    "supported_count",
    "partially_supported_count",
    "contradicted_count",
    "not_verifiable_count",
    "review_required_count",
    "extraction_warnings",
    "verification_warnings",
    "extraction_error_type",
    "extraction_error_provider",
    "extraction_error_code",
    "extraction_error_param",
    "extraction_error_request_id",
    "extraction_error_filtered_categories",
]

DETAIL_FIELDS = [
    "flat_row_id",
    "record_type",
    "record_index",
    "evidence_index",
    "speaker",
    "organization",
    "source_quote",
    "start_char",
    "end_char",
    "sentence_ids",
    "claim_text",
    "claim_type",
    "viewpoint_stance",
    "viewpoint_target_provision",
    "viewpoint_reason",
    "verification_status",
    "verification_verdict",
    "verification_reason",
    "verification_documents_searched",
    "verification_evidence_as_of",
    "verification_review_required",
    "verification_key_fact_ids",
    "verification_retrieved_evidence_ids",
    "evidence_id",
    "evidence_doc",
    "evidence_citation",
    "evidence_source_type",
    "evidence_source_url",
    "evidence_text",
]

FLAT_FIELDS = ARTICLE_FIELDS + DETAIL_FIELDS


def allow_large_csv_fields() -> None:
    """Raise Python's CSV field limit for the nested source cells."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def parse_json_cell(row: dict[str, str], column: str) -> Any:
    """Parse one JSON cell and include the article ID in any error."""
    try:
        return json.loads(row[column])
    except (KeyError, json.JSONDecodeError) as error:
        article_id = row.get("article_id") or "unknown article"
        raise ValueError(f"{article_id} has invalid JSON in {column}: {error}") from error


def readable_value(value: Any) -> str:
    """Render lists and small dictionaries without leaving JSON in a cell."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " | ".join(
            f"{key}={readable_value(item)}" for key, item in value.items()
        )
    if isinstance(value, list):
        return " | ".join(readable_value(item) for item in value)
    return str(value)


def article_values(row: dict[str, str]) -> dict[str, Any]:
    """Return repeated article metadata with article-level JSON made readable."""
    extraction_error = parse_json_cell(row, "extraction_error_json") or {}
    values: dict[str, Any] = {
        field: row.get(field, "")
        for field in ARTICLE_FIELDS
        if field in row
    }
    values.update(
        {
            "source_urls": readable_value(parse_json_cell(row, "source_urls_json")),
            "source_article_ids": readable_value(
                parse_json_cell(row, "source_article_ids_json")
            ),
            "extraction_warnings": readable_value(
                parse_json_cell(row, "extraction_warnings_json")
            ),
            "verification_warnings": readable_value(
                parse_json_cell(row, "verification_warnings_json")
            ),
            "extraction_error_type": extraction_error.get("type", ""),
            "extraction_error_provider": extraction_error.get("provider", ""),
            "extraction_error_code": extraction_error.get("code", ""),
            "extraction_error_param": extraction_error.get("param", ""),
            "extraction_error_request_id": extraction_error.get("request_id", ""),
            "extraction_error_filtered_categories": readable_value(
                extraction_error.get("filtered_categories")
            ),
        }
    )
    return values


def shared_item_values(item: dict[str, Any]) -> dict[str, Any]:
    """Return fields shared by claims and viewpoints."""
    return {
        "speaker": item.get("speaker", ""),
        "organization": item.get("organization", ""),
        "source_quote": item.get("source_quote", ""),
        "start_char": item.get("start_char", ""),
        "end_char": item.get("end_char", ""),
        "sentence_ids": readable_value(item.get("sentence_ids")),
    }


def claim_rows(
    base: dict[str, Any], claim: dict[str, Any], claim_index: int
) -> Iterable[dict[str, Any]]:
    """Yield one row per cited evidence object, or one row for an uncited claim."""
    article_id = str(base["article_id"])
    verification = claim.get("verification") or {}
    evidence_items = verification.get("evidence") or [None]

    claim_values = {
        **shared_item_values(claim),
        "record_type": "claim",
        "record_index": claim_index,
        "claim_text": claim.get("claim_text", ""),
        "claim_type": claim.get("claim_type", ""),
        "verification_status": verification.get("status", ""),
        "verification_verdict": verification.get("verdict", ""),
        "verification_reason": verification.get("reason", ""),
        "verification_documents_searched": readable_value(
            verification.get("documents_searched")
        ),
        "verification_evidence_as_of": verification.get("evidence_as_of", ""),
        "verification_review_required": verification.get("review_required", ""),
        "verification_key_fact_ids": readable_value(
            verification.get("key_fact_ids")
        ),
        "verification_retrieved_evidence_ids": readable_value(
            verification.get("retrieved_evidence_ids")
        ),
    }

    for evidence_index, evidence in enumerate(evidence_items, start=1):
        evidence_values: dict[str, Any] = {}
        flat_row_id = f"{article_id}:claim:{claim_index}"
        if evidence is not None:
            flat_row_id += f":evidence:{evidence_index}"
            evidence_values = {
                "evidence_index": evidence_index,
                "evidence_id": evidence.get("evidence_id", ""),
                "evidence_doc": evidence.get("doc", ""),
                "evidence_citation": evidence.get("citation", ""),
                "evidence_source_type": evidence.get("source_type", ""),
                "evidence_source_url": evidence.get("source_url", ""),
                "evidence_text": evidence.get("text", ""),
            }
        yield {
            **base,
            **claim_values,
            "flat_row_id": flat_row_id,
            **evidence_values,
        }


def viewpoint_row(
    base: dict[str, Any], viewpoint: dict[str, Any], viewpoint_index: int
) -> dict[str, Any]:
    """Return one flat row for an attributed viewpoint."""
    article_id = str(base["article_id"])
    return {
        **base,
        **shared_item_values(viewpoint),
        "flat_row_id": f"{article_id}:viewpoint:{viewpoint_index}",
        "record_type": "viewpoint",
        "record_index": viewpoint_index,
        "viewpoint_stance": viewpoint.get("stance", ""),
        "viewpoint_target_provision": viewpoint.get("target_provision", ""),
        "viewpoint_reason": viewpoint.get("reason", ""),
    }


def flatten_article(row: dict[str, str]) -> Iterable[dict[str, Any]]:
    """Yield all long-form records for one article."""
    base = article_values(row)
    claims = parse_json_cell(row, "claims_json")
    viewpoints = parse_json_cell(row, "viewpoints_json")

    for claim_index, claim in enumerate(claims, start=1):
        yield from claim_rows(base, claim, claim_index)
    for viewpoint_index, viewpoint in enumerate(viewpoints, start=1):
        yield viewpoint_row(base, viewpoint, viewpoint_index)

    if not claims and not viewpoints:
        article_id = str(base["article_id"])
        yield {
            **base,
            "flat_row_id": f"{article_id}:article:1",
            "record_type": "article",
            "record_index": 1,
        }


def flatten_results(input_path: Path, output_path: Path) -> dict[str, int]:
    """Read the UI CSV and write a long-form CSV with no JSON cells."""
    allow_large_csv_fields()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    article_count = 0
    output_count = 0
    record_counts = {"article": 0, "claim": 0, "viewpoint": 0}
    with input_path.open(newline="", encoding="utf-8") as source, output_path.open(
        "w", newline="", encoding="utf-8"
    ) as destination:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(
            destination,
            fieldnames=FLAT_FIELDS,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for article_count, article in enumerate(reader, start=1):
            for flat_row in flatten_article(article):
                writer.writerow(flat_row)
                output_count += 1
                record_counts[str(flat_row["record_type"])] += 1

    return {
        "articles": article_count,
        "rows": output_count,
        **record_counts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        counts = flatten_results(args.input, args.output)
    except (FileNotFoundError, ValueError, csv.Error) as error:
        print(f"ERROR: {error}")
        return 1
    print(
        f"Flattened {counts['articles']} articles into {counts['rows']} rows "
        f"({counts['claim']} claim/evidence, {counts['viewpoint']} viewpoint, "
        f"{counts['article']} article-only): {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
