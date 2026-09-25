#!/usr/bin/env python3
"""Build small, normalized CSVs for the news fact-check UI."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any, Iterable


NEWS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS = NEWS_DIR / "output" / "fact_check" / "results.json"
DEFAULT_OUTPUT_DIR = NEWS_DIR / "output" / "fact_check"
MAX_FEATURED_CLAIMS = 6
MAX_UI_ARTICLES = 20
POLICY_CLAIM_TYPES = {"policy", "government_position", "interpretation"}

ARTICLE_FIELDS = [
    "article_id",
    "title",
    "article_url",
    "publisher_domain",
    "seen_at",
    "publisher_country",
    "policy_stage",
    "grounding_score",
    "analysis_status",
    "policy_claims_total",
    "policy_claims_scored",
    "supported_claims",
    "partially_supported_claims",
    "contradicted_claims",
    "unresolved_claims",
    "claims_needing_review",
]

CLAIM_FIELDS = [
    "article_id",
    "claim_id",
    "claim_rank",
    "claim_text",
    "claim_category",
    "speaker",
    "organization",
    "verdict",
    "verdict_explanation",
    "needs_review",
]

EVIDENCE_FIELDS = [
    "claim_id",
    "evidence_rank",
    "citation",
    "evidence_type",
    "source_url",
    "evidence_excerpt",
]

VIEWPOINT_FIELDS = [
    "article_id",
    "viewpoint_id",
    "speaker",
    "organization",
    "stance",
    "affected_provision",
    "reason",
    "source_quote",
]

POLICY_STAGE_NAMES = {
    "nprm_published_docket": "proposal_docket",
    "nprm_published": "proposed_rule",
    "comment_deadline": "comment_deadline",
    "final_rule": "final_rule",
    "injunction": "injunction",
}

VERDICT_NAMES = {
    "supported": "supported",
    "partially_supported": "partially_supported",
    "contradicted": "contradicted",
    "not_verifiable": "unresolved",
}

SPECIAL_ANALYSIS_STATUSES = ("unavailable", "not_scored", "no_policy_claims")


def normalize_seen_at(value: Any) -> Any:
    """Convert the source timestamp to ISO 8601 when it uses GDELT format."""
    if not isinstance(value, str):
        return value
    match = re.fullmatch(
        r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", value
    )
    if not match:
        return value
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"


def verdict_count(claims: Iterable[dict[str, Any]], verdict: str) -> int:
    return sum(
        claim.get("verification", {}).get("verdict") == verdict for claim in claims
    )


def analysis_status(article: dict[str, Any]) -> str:
    """Return a small UI state instead of exposing pipeline status details."""
    if article.get("extraction_status") != "complete":
        return "unavailable"
    fact_check = article.get("fact_check") or {}
    if fact_check.get("grounding_score") is not None:
        return "scored"
    if not fact_check.get("checkable_claims"):
        return "no_policy_claims"
    return "not_scored"


def article_ui_row(article: dict[str, Any]) -> dict[str, Any]:
    claims = article.get("claims", [])
    fact_check = article.get("fact_check") or {}
    return {
        "article_id": article.get("article_id"),
        "title": article.get("title"),
        "article_url": article.get("url"),
        "publisher_domain": article.get("domain"),
        "seen_at": normalize_seen_at(article.get("seendate")),
        "publisher_country": article.get("source_country"),
        "policy_stage": POLICY_STAGE_NAMES.get(
            article.get("window"), article.get("window")
        ),
        "grounding_score": fact_check.get("grounding_score"),
        "analysis_status": analysis_status(article),
        "policy_claims_total": fact_check.get("checkable_claims", 0),
        "policy_claims_scored": fact_check.get("checked_claims", 0),
        "supported_claims": verdict_count(claims, "supported"),
        "partially_supported_claims": verdict_count(
            claims, "partially_supported"
        ),
        "contradicted_claims": verdict_count(claims, "contradicted"),
        "unresolved_claims": verdict_count(claims, "not_verifiable"),
        "claims_needing_review": sum(
            bool(claim.get("verification", {}).get("review_required"))
            for claim in claims
        ),
    }


def article_sort_key(article: dict[str, Any]) -> tuple[str, str]:
    return (
        str(normalize_seen_at(article.get("seendate")) or ""),
        str(article.get("article_id") or ""),
    )


def select_special_articles(
    articles: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep distinct unscored UI states and spread them across the timeline."""
    if limit <= 0:
        return []
    ordered = sorted(articles, key=article_sort_key)
    positions = {str(article["article_id"]): index for index, article in enumerate(ordered)}
    selected: list[dict[str, Any]] = []
    for status in SPECIAL_ANALYSIS_STATUSES:
        candidates = [article for article in ordered if analysis_status(article) == status]
        if not candidates or len(selected) == limit:
            continue
        if not selected:
            chosen = candidates[0]
        else:
            chosen = min(
                candidates,
                key=lambda article: (
                    -min(
                        abs(
                            positions[str(article["article_id"])]
                            - positions[str(existing["article_id"])]
                        )
                        for existing in selected
                    ),
                    article_sort_key(article),
                ),
            )
        selected.append(chosen)
    return selected


def select_scored_articles(
    articles: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Use deterministic farthest-point sampling across time rank and score."""
    if limit <= 0:
        return []
    if len(articles) <= limit:
        return list(articles)

    ordered = sorted(articles, key=article_sort_key)
    time_scale = max(1, len(ordered) - 1)
    scores = [float((article.get("fact_check") or {})["grounding_score"]) for article in ordered]
    score_min = min(scores)
    score_scale = max(1.0, max(scores) - score_min)
    points = {
        str(article["article_id"]): (
            index / time_scale,
            (score - score_min) / score_scale,
        )
        for index, (article, score) in enumerate(zip(ordered, scores))
    }

    def distance_squared(article: dict[str, Any], target: tuple[float, float]) -> float:
        point = points[str(article["article_id"])]
        return (point[0] - target[0]) ** 2 + (point[1] - target[1]) ** 2

    selected: list[dict[str, Any]] = []
    corners = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    for corner in corners:
        if len(selected) == limit:
            break
        candidates = [article for article in ordered if article not in selected]
        selected.append(
            min(
                candidates,
                key=lambda article: (
                    distance_squared(article, corner),
                    article_sort_key(article),
                ),
            )
        )

    while len(selected) < limit:
        candidates = [article for article in ordered if article not in selected]

        def nearest_selected_distance(article: dict[str, Any]) -> float:
            point = points[str(article["article_id"])]
            return min(
                (point[0] - points[str(existing["article_id"])][0]) ** 2
                + (point[1] - points[str(existing["article_id"])][1]) ** 2
                for existing in selected
            )

        selected.append(
            min(
                candidates,
                key=lambda article: (
                    -nearest_selected_distance(article),
                    article_sort_key(article),
                ),
            )
        )
    return selected


def select_ui_articles(
    articles: list[dict[str, Any]], max_articles: int = MAX_UI_ARTICLES
) -> list[dict[str, Any]]:
    """Select a reproducible demo sample across time, score, and empty states."""
    if max_articles < 1:
        raise ValueError("max_articles must be a positive integer")
    if len(articles) <= max_articles:
        return list(articles)

    scored = [
        article
        for article in articles
        if (article.get("fact_check") or {}).get("grounding_score") is not None
    ]
    unscored = [article for article in articles if article not in scored]
    special = select_special_articles(unscored, min(3, max_articles))
    selected = special + select_scored_articles(scored, max_articles - len(special))
    selected_ids = {str(article["article_id"]) for article in selected}
    return [
        article for article in articles if str(article["article_id"]) in selected_ids
    ]


def claim_salience(article: dict[str, Any], claim: dict[str, Any]) -> int:
    """Match the scoring model's simple headline/lead salience heuristic."""
    title_words = set(re.findall(r"[a-z0-9]+", article.get("title", "").lower()))
    claim_words = set(re.findall(r"[a-z0-9]+", claim.get("claim_text", "").lower()))
    overlap_count = len(title_words & claim_words)
    overlap = overlap_count / max(1, min(len(title_words), len(claim_words)))
    if overlap_count >= 2 and overlap >= 0.6:
        return 3
    sentence_ids = claim.get("sentence_ids") or []
    if sentence_ids and min(sentence_ids) <= 3:
        return 2
    return 1


def claim_priority(article: dict[str, Any], claim: dict[str, Any], index: int) -> tuple:
    """Rank consequential verdicts first, then headline/lead salience."""
    verification = claim.get("verification") or {}
    verdict = verification.get("verdict")
    risk_order = {
        "contradicted": 0,
        "partially_supported": 1,
        "not_verifiable": 2,
        "supported": 4,
    }.get(verdict, 5)
    if verification.get("review_required") and risk_order > 3:
        risk_order = 3
    return (risk_order, -claim_salience(article, claim), index)


def featured_claims(article: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Select up to six distinct policy-related claims for the detail view."""
    candidates = [
        (index, claim)
        for index, claim in enumerate(article.get("claims", []), start=1)
        if claim.get("claim_type") in POLICY_CLAIM_TYPES
    ]
    candidates.sort(key=lambda item: claim_priority(article, item[1], item[0]))

    selected = []
    seen_text = set()
    for index, claim in candidates:
        normalized_text = " ".join(claim.get("claim_text", "").lower().split())
        if not normalized_text or normalized_text in seen_text:
            continue
        seen_text.add(normalized_text)
        selected.append((index, claim))
        if len(selected) == MAX_FEATURED_CLAIMS:
            break
    return selected


def claim_id(article_id: str, original_index: int) -> str:
    return f"{article_id}:C{original_index:02d}"


def claim_ui_rows(
    article: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    article_id = str(article["article_id"])
    claims = []
    evidence_rows = []
    for rank, (original_index, claim) in enumerate(featured_claims(article), start=1):
        identifier = claim_id(article_id, original_index)
        verification = claim.get("verification") or {}
        claims.append(
            {
                "article_id": article_id,
                "claim_id": identifier,
                "claim_rank": rank,
                "claim_text": claim.get("claim_text"),
                "claim_category": claim.get("claim_type"),
                "speaker": claim.get("speaker"),
                "organization": claim.get("organization"),
                "verdict": VERDICT_NAMES.get(
                    verification.get("verdict"), verification.get("verdict")
                ),
                "verdict_explanation": verification.get("reason"),
                "needs_review": bool(verification.get("review_required")),
            }
        )
        for evidence_rank, evidence in enumerate(
            verification.get("evidence") or [], start=1
        ):
            evidence_rows.append(
                {
                    "claim_id": identifier,
                    "evidence_rank": evidence_rank,
                    "citation": evidence.get("citation"),
                    "evidence_type": evidence.get("source_type"),
                    "source_url": evidence.get("source_url"),
                    "evidence_excerpt": evidence.get("text"),
                }
            )
    return claims, evidence_rows


def viewpoint_ui_rows(article: dict[str, Any]) -> list[dict[str, Any]]:
    article_id = str(article["article_id"])
    return [
        {
            "article_id": article_id,
            "viewpoint_id": f"{article_id}:V{index:02d}",
            "speaker": viewpoint.get("speaker"),
            "organization": viewpoint.get("organization"),
            "stance": viewpoint.get("stance"),
            "affected_provision": viewpoint.get("target_provision"),
            "reason": viewpoint.get("reason"),
            "source_quote": viewpoint.get("source_quote"),
        }
        for index, viewpoint in enumerate(article.get("viewpoints", []), start=1)
    ]


def build_ui_rows(
    articles: list[dict[str, Any]], max_articles: int = MAX_UI_ARTICLES
) -> dict[str, list[dict[str, Any]]]:
    article_rows = []
    claim_rows = []
    evidence_rows = []
    viewpoint_rows = []
    for article in select_ui_articles(articles, max_articles):
        article_rows.append(article_ui_row(article))
        article_claims, article_evidence = claim_ui_rows(article)
        claim_rows.extend(article_claims)
        evidence_rows.extend(article_evidence)
        viewpoint_rows.extend(viewpoint_ui_rows(article))
    return {
        "articles_ui.csv": article_rows,
        "claims_ui.csv": claim_rows,
        "claim_evidence_ui.csv": evidence_rows,
        "viewpoints_ui.csv": viewpoint_rows,
    }


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=fields, extrasaction="raise", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def write_ui_exports(
    output_dir: Path,
    articles: list[dict[str, Any]],
    max_articles: int = MAX_UI_ARTICLES,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_by_file = build_ui_rows(articles, max_articles)
    field_map = {
        "articles_ui.csv": ARTICLE_FIELDS,
        "claims_ui.csv": CLAIM_FIELDS,
        "claim_evidence_ui.csv": EVIDENCE_FIELDS,
        "viewpoints_ui.csv": VIEWPOINT_FIELDS,
    }
    for filename, rows in rows_by_file.items():
        write_csv(output_dir / filename, field_map[filename], rows)
    return {filename: len(rows) for filename, rows in rows_by_file.items()}


def csv_value(value: Any) -> str:
    return "" if value is None else str(value)


def validate_ui_exports(
    output_dir: Path,
    articles: list[dict[str, Any]],
    max_articles: int = MAX_UI_ARTICLES,
) -> dict[str, int]:
    expected_by_file = build_ui_rows(articles, max_articles)
    field_map = {
        "articles_ui.csv": ARTICLE_FIELDS,
        "claims_ui.csv": CLAIM_FIELDS,
        "claim_evidence_ui.csv": EVIDENCE_FIELDS,
        "viewpoints_ui.csv": VIEWPOINT_FIELDS,
    }
    actual_by_file: dict[str, list[dict[str, str]]] = {}
    for filename, expected_rows in expected_by_file.items():
        path = output_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"UI export does not exist: {path}")
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != field_map[filename]:
                raise ValueError(f"{filename} columns do not match the UI contract")
            actual_rows = list(reader)
        normalized_expected = [
            {field: csv_value(row.get(field)) for field in field_map[filename]}
            for row in expected_rows
        ]
        if actual_rows != normalized_expected:
            raise ValueError(f"{filename} does not match results JSON")
        actual_by_file[filename] = actual_rows

    article_rows = actual_by_file["articles_ui.csv"]
    claim_rows = actual_by_file["claims_ui.csv"]
    evidence_rows = actual_by_file["claim_evidence_ui.csv"]
    viewpoint_rows = actual_by_file["viewpoints_ui.csv"]
    article_ids = {row["article_id"] for row in article_rows}
    if len(article_ids) != len(article_rows):
        raise ValueError("articles_ui.csv contains blank or duplicate article IDs")
    if len(article_rows) > max_articles:
        raise ValueError("articles_ui.csv exceeds the article limit")

    claim_ids = {row["claim_id"] for row in claim_rows}
    if len(claim_ids) != len(claim_rows):
        raise ValueError("claims_ui.csv contains blank or duplicate claim IDs")
    claims_per_article: dict[str, int] = defaultdict(int)
    for row in claim_rows:
        if row["article_id"] not in article_ids:
            raise ValueError("claims_ui.csv contains an unknown article ID")
        claims_per_article[row["article_id"]] += 1
    if any(count > MAX_FEATURED_CLAIMS for count in claims_per_article.values()):
        raise ValueError("claims_ui.csv exceeds the featured-claim limit")

    if any(row["claim_id"] not in claim_ids for row in evidence_rows):
        raise ValueError("claim_evidence_ui.csv contains an unknown claim ID")
    if any(row["article_id"] not in article_ids for row in viewpoint_rows):
        raise ValueError("viewpoints_ui.csv contains an unknown article ID")

    return {
        "articles": len(article_rows),
        "claims": len(claim_rows),
        "evidence": len(evidence_rows),
        "viewpoints": len(viewpoint_rows),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-articles", type=int, default=MAX_UI_ARTICLES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
        articles = results["articles"]
        write_ui_exports(args.output_dir, articles, args.max_articles)
        counts = validate_ui_exports(args.output_dir, articles, args.max_articles)
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}")
        return 1
    print(
        f"Wrote {counts['articles']} articles, {counts['claims']} featured claims, "
        f"{counts['evidence']} evidence rows, and {counts['viewpoints']} viewpoints "
        f"to {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
