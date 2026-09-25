import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import export_ui_csvs as ui_exports  # noqa: E402


def make_claim(
    text: str,
    verdict: str,
    *,
    sentence: int,
    review: bool = False,
    evidence: list[dict] | None = None,
) -> dict:
    return {
        "claim_text": text,
        "claim_type": "policy",
        "speaker": None,
        "organization": "DHS",
        "sentence_ids": [sentence],
        "verification": {
            "verdict": verdict,
            "reason": f"Reason for {verdict}.",
            "review_required": review,
            "evidence": evidence or [],
        },
    }


def sample_article() -> dict:
    headline = "Rule limits student admission to four years"
    evidence = {
        "citation": "91 FR 44976",
        "source_type": "regulatory_text",
        "source_url": None,
        "text": "Admission may not exceed four years.",
    }
    claims = [
        make_claim(headline, "supported", sentence=4),
        make_claim("Students must request extensions.", "supported", sentence=2),
        make_claim("The rule took effect immediately.", "contradicted", sentence=8, evidence=[evidence]),
        make_claim("The rule applies to every visa category.", "partially_supported", sentence=9),
        make_claim("The filing fee will double.", "not_verifiable", sentence=10, review=True),
        make_claim("A court challenge may follow.", "supported", sentence=11, review=True),
        make_claim("Another supported detail.", "supported", sentence=12),
        make_claim("Another supported detail.", "supported", sentence=13),
    ]
    return {
        "article_id": "article-1",
        "title": headline,
        "url": "https://example.test/article",
        "domain": "example.test",
        "seendate": "20260717T164500Z",
        "window": "final_rule",
        "source_country": "United States",
        "extraction_status": "complete",
        "claims": claims,
        "viewpoints": [
            {
                "speaker": "Example advocate",
                "organization": "Example group",
                "stance": "oppose",
                "target_provision": "fixed admission periods",
                "reason": "It adds uncertainty.",
                "source_quote": "The change adds uncertainty.",
            }
        ],
        "fact_check": {
            "grounding_score": 72.5,
            "checkable_claims": 8,
            "checked_claims": 7,
        },
    }


def test_normalized_exports_rank_and_join_ui_details(tmp_path: Path):
    article = sample_article()

    counts = ui_exports.write_ui_exports(tmp_path, [article])
    validation = ui_exports.validate_ui_exports(tmp_path, [article])

    assert counts == {
        "articles_ui.csv": 1,
        "claims_ui.csv": 6,
        "claim_evidence_ui.csv": 1,
        "viewpoints_ui.csv": 1,
    }
    assert validation == {
        "articles": 1,
        "claims": 6,
        "evidence": 1,
        "viewpoints": 1,
    }

    with (tmp_path / "articles_ui.csv").open(newline="", encoding="utf-8") as handle:
        article_row = next(csv.DictReader(handle))
    assert article_row["seen_at"] == "2026-07-17T16:45:00Z"
    assert article_row["analysis_status"] == "scored"
    assert article_row["policy_claims_total"] == "8"
    assert article_row["policy_claims_scored"] == "7"
    assert "checked_claims" not in article_row
    assert "checkable_claims" not in article_row

    with (tmp_path / "claims_ui.csv").open(newline="", encoding="utf-8") as handle:
        claims = list(csv.DictReader(handle))
    assert [row["claim_id"] for row in claims] == [
        "article-1:C03",
        "article-1:C04",
        "article-1:C05",
        "article-1:C06",
        "article-1:C01",
        "article-1:C02",
    ]
    assert claims[2]["verdict"] == "unresolved"

    with (tmp_path / "claim_evidence_ui.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        evidence = next(csv.DictReader(handle))
    assert evidence["claim_id"] == "article-1:C03"
    assert evidence["citation"] == "91 FR 44976"

    with (tmp_path / "viewpoints_ui.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        viewpoint = next(csv.DictReader(handle))
    assert viewpoint["viewpoint_id"] == "article-1:V01"
    assert viewpoint["affected_provision"] == "fixed admission periods"


def test_analysis_status_distinguishes_empty_and_unavailable_articles():
    article = sample_article()
    article["fact_check"] = {
        "grounding_score": None,
        "checkable_claims": 0,
        "checked_claims": 0,
    }
    assert ui_exports.analysis_status(article) == "no_policy_claims"

    article["fact_check"]["checkable_claims"] = 2
    assert ui_exports.analysis_status(article) == "not_scored"

    article["extraction_status"] = "content_filtered"
    assert ui_exports.analysis_status(article) == "unavailable"


def test_ui_article_sample_spans_time_scores_and_empty_states():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    articles = []
    for index in range(30):
        seen_at = start + timedelta(days=index * 10)
        articles.append(
            {
                "article_id": f"scored-{index:02d}",
                "title": f"Scored article {index}",
                "seendate": seen_at.strftime("%Y%m%dT%H%M%SZ"),
                "extraction_status": "complete",
                "claims": [],
                "viewpoints": [],
                "fact_check": {
                    "grounding_score": 50 + (index % 11) * 5,
                    "checkable_claims": 1,
                    "checked_claims": 1,
                },
            }
        )
    articles.extend(
        [
            {
                "article_id": "unavailable",
                "title": "Unavailable article",
                "seendate": "20250201T000000Z",
                "extraction_status": "content_filtered",
                "claims": [],
                "viewpoints": [],
                "fact_check": {},
            },
            {
                "article_id": "not-scored",
                "title": "Not scored article",
                "seendate": "20250601T000000Z",
                "extraction_status": "complete",
                "claims": [],
                "viewpoints": [],
                "fact_check": {
                    "grounding_score": None,
                    "checkable_claims": 2,
                    "checked_claims": 0,
                },
            },
            {
                "article_id": "no-policy-claims",
                "title": "No policy claims article",
                "seendate": "20251001T000000Z",
                "extraction_status": "complete",
                "claims": [],
                "viewpoints": [],
                "fact_check": {
                    "grounding_score": None,
                    "checkable_claims": 0,
                    "checked_claims": 0,
                },
            },
        ]
    )

    selected = ui_exports.select_ui_articles(articles)
    selected_again = ui_exports.select_ui_articles(articles)

    assert len(selected) == ui_exports.MAX_UI_ARTICLES == 20
    assert [article["article_id"] for article in selected] == [
        article["article_id"] for article in selected_again
    ]
    selected_ids = {article["article_id"] for article in selected}
    assert {"unavailable", "not-scored", "no-policy-claims"} <= selected_ids

    scored_indexes = sorted(
        int(article["article_id"].split("-")[1])
        for article in selected
        if article["article_id"].startswith("scored-")
    )
    selected_scores = [
        article["fact_check"]["grounding_score"]
        for article in selected
        if article["article_id"].startswith("scored-")
    ]
    assert scored_indexes[0] <= 2
    assert scored_indexes[-1] >= 27
    assert min(selected_scores) <= 55
    assert max(selected_scores) >= 95
