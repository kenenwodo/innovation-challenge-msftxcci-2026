import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import run_fact_checker as fact_checker  # noqa: E402
import verify_claims  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "window",
        "seendate",
        "title",
        "url",
        "domain",
        "sourcecountry",
        "is_repost",
        "article_text",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sample_rows() -> list[dict[str, str]]:
    return [
        {
            "window": "nprm_published",
            "seendate": "20250828T000000Z",
            "title": "Example story",
            "url": "https://example.test/story",
            "domain": "example.test",
            "sourcecountry": "United States",
            "is_repost": "False",
            "article_text": "A policy article.",
        },
        {
            "window": "nprm_published",
            "seendate": "20250828T010000Z",
            "title": "Example repost",
            "url": "https://example.test/repost",
            "domain": "example.test",
            "sourcecountry": "United States",
            "is_repost": "True",
            "article_text": "",
        },
    ]


def test_article_id_is_stable():
    assert fact_checker.article_id("https://example.test/story") == fact_checker.article_id(
        "https://example.test/story"
    )
    assert len(fact_checker.article_id("https://example.test/story")) == 16


def test_load_articles_handles_bom_and_missing_text(tmp_path: Path):
    source = tmp_path / "articles.csv"
    write_csv(source, sample_rows())
    articles = fact_checker.load_articles(source)
    assert len(articles) == 2
    assert articles[0]["has_text"] is True
    assert articles[1]["has_text"] is False
    assert articles[1]["is_repost"] is True


def test_current_corpus_counts():
    articles = fact_checker.load_articles(fact_checker.DEFAULT_INPUT)
    assert len(articles) == 112
    assert sum(article["has_text"] for article in articles) == 96


def test_exact_title_and_near_duplicate_stories_cluster(tmp_path: Path):
    source = tmp_path / "articles.csv"
    common = " ".join(f"shared{i}" for i in range(35))
    rows = [
        {**sample_rows()[0], "title": "Same Story", "url": "https://a.test/1", "article_text": "Short copy."},
        {**sample_rows()[0], "title": "same-story!", "url": "https://b.test/2", "article_text": "A much longer but differently worded copy."},
        {**sample_rows()[0], "title": "Syndicated headline A", "url": "https://c.test/3", "article_text": common},
        {**sample_rows()[0], "title": "Different headline B", "url": "https://d.test/4", "article_text": common + " extra"},
        {**sample_rows()[0], "title": "Unrelated", "url": "https://e.test/5", "article_text": " ".join(f"other{i}" for i in range(35))},
    ]
    write_csv(source, rows)

    clusters = fact_checker.cluster_articles(fact_checker.load_articles(source))

    assert sorted(cluster["cluster_size"] for cluster in clusters) == [1, 2, 2]
    exact_title_cluster = next(
        cluster for cluster in clusters if "https://a.test/1" in cluster["source_urls"]
    )
    assert exact_title_cluster["url"] == "https://b.test/2"
    assert exact_title_cluster["source_urls"] == ["https://a.test/1", "https://b.test/2"]


def test_sentence_offsets_reproduce_the_article_text():
    text = "The rule sets four years. It may raise costs.\n\nA final sentence!"
    sentences = fact_checker.split_sentences(text)

    assert len(sentences) == 3
    assert [sentence["sentence_id"] for sentence in sentences] == [1, 2, 3]
    assert all(
        text[sentence["start"] : sentence["end"]] == sentence["text"]
        for sentence in sentences
    )


def test_validation_keeps_atomic_types_and_drops_untraceable_items():
    text = "The rule sets four years. An adviser said it may raise costs."
    sentences = fact_checker.split_sentences(text)
    first_quote = sentences[0]["text"]
    first_span = {"source_quote": first_quote}
    second_span = {"source_quote": sentences[1]["text"]}
    raw = {
        "claims": [
            {
                "claim_text": "The rule sets a four-year period.",
                "claim_type": "policy",
                "speaker": None,
                "organization": None,
                **first_span,
            },
            {
                "claim_text": "The rule may raise costs.",
                "claim_type": "prediction",
                "speaker": "An adviser",
                "organization": None,
                **second_span,
            },
            {
                "claim_text": "This fabricated quote must be dropped.",
                "claim_type": "policy",
                "speaker": None,
                "organization": None,
                **first_span,
                "source_quote": "Not in the article",
            },
        ],
        "viewpoints": [
            {
                "speaker": "An adviser",
                "organization": None,
                "stance": "oppose",
                "target_provision": "four-year period",
                "reason": "may raise costs",
                **second_span,
            }
        ],
    }

    cleaned, warnings = fact_checker.validate_extraction(raw, text, sentences)

    assert [claim["claim_type"] for claim in cleaned["claims"]] == [
        "policy",
        "prediction",
    ]
    assert len(cleaned["viewpoints"]) == 1
    assert cleaned["claims"][0]["sentence_ids"] == [1]
    assert text[
        cleaned["claims"][0]["start_char"] : cleaned["claims"][0]["end_char"]
    ] == first_quote
    assert warnings == ["claim 3: source quote was not found exactly in the article"]


def test_repeated_quote_uses_first_exact_occurrence():
    text = "The policy changes. The policy changes."
    sentences = fact_checker.split_sentences(text)
    raw = {
        "claims": [
            {
                "claim_text": "The policy changes.",
                "claim_type": "policy",
                "speaker": None,
                "organization": None,
                "source_quote": "The policy changes.",
            }
        ],
        "viewpoints": [],
    }

    cleaned, warnings = fact_checker.validate_extraction(raw, text, sentences)

    assert cleaned["claims"][0]["start_char"] == 0
    assert cleaned["claims"][0]["sentence_ids"] == [1]
    assert warnings == []


def test_claim_routing_prefers_explicit_wording_and_flags_court_status():
    article = {"window": "final_rule"}
    proposed = verify_claims.route_claim(
        {
            "claim_text": "The proposed regulation would set a four-year maximum.",
            "source_quote": "",
        },
        article,
    )
    court = verify_claims.route_claim(
        {"claim_text": "A judge blocked the rule.", "source_quote": ""}, article
    )

    assert proposed["documents"] == ["proposed"]
    assert proposed["review_required"] is False
    assert court["documents"] == []
    assert court["review_required"] is True


def test_policy_retrieval_covers_demo_edge_cases():
    from evidence_index import EvidenceIndex

    index = EvidenceIndex.load(use_embeddings=False)

    lifetime = index.search_facts(
        "Students face a hard lifetime cap of four years and cannot extend their stay",
        k=6,
        mode="bm25",
    )
    assert {"KF-ADMIT-01", "KF-EOS-01"} <= {
        fact["fact_id"] for fact in lifetime
    }

    grace = index.search_facts(
        "Students get 30 days to leave, but transitioning students keep 60 days",
        k=5,
        mode="bm25",
    )
    assert {"KF-GRACE-01", "KF-TRANS-01"} <= {
        fact["fact_id"] for fact in grace
    }

    opt = index.search(
        "post-completion practical training requires additional admission time",
        docs=["final"],
        k=8,
        mode="bm25",
    )
    assert any(hit["evidence_id"] == "final:8 CFR 214.2/p65" for hit in opt)


def test_verifier_removes_unretrieved_evidence_ids(tmp_path: Path):
    class FakeIndex:
        def search_facts(self, *_args, **_kwargs):
            return [
                {
                    "fact_id": "KF-ADMIT-01",
                    "statement": "Admission is limited to the program length, up to 4 years.",
                    "change_from_proposed": None,
                    "common_misreadings": [],
                }
            ]

        def search(self, *_args, **_kwargs):
            return [
                {
                    "evidence_id": "final:E1",
                    "doc": "final",
                    "heading": "Period of stay",
                    "cfr_citation": "8 CFR 214.2(f)(5)",
                    "fr_cite": None,
                    "text": "The admission period may not exceed 4 years.",
                }
            ]

    def fake_model_call(*_args, **_kwargs):
        return {
            "results": [
                {
                    "claim_id": "C1",
                    "verdict": "supported",
                    "evidence_ids": ["final:E1", "invented:E9"],
                    "reason": "The retrieved rule text states the maximum.",
                }
            ]
        }

    article = {
        "title": "Final rule sets four-year maximum",
        "window": "final_rule",
        "seendate": "20260717T000000Z",
        "claims": [
            {
                "claim_text": "The final rule sets a 4-year maximum.",
                "claim_type": "policy",
                "source_quote": "The final rule sets a 4-year maximum.",
                "sentence_ids": [1],
            },
            {
                "claim_text": "The rule is unfair.",
                "claim_type": "opinion",
                "source_quote": "The rule is unfair.",
                "sentence_ids": [2],
            },
        ],
    }

    verify_claims.verify_article(article, FakeIndex(), tmp_path, fake_model_call)

    verification = article["claims"][0]["verification"]
    assert verification["verdict"] == "supported"
    assert verification["retrieved_evidence_ids"] == ["final:E1"]
    assert [item["evidence_id"] for item in verification["evidence"]] == [
        "final:E1"
    ]
    assert article["claims"][1]["verification"]["status"] == "excluded"
    assert article["fact_check"]["grounding_score"] == 100.0


def test_numeric_guard_and_weighted_article_score():
    item = {
        "claim": {"claim_text": "Students receive 30 days.", "sentence_ids": [1]},
        "route": {"documents": ["final"], "review_required": False},
        "retrieval": {
            "key_facts": [],
            "passages": [
                {
                    "evidence_id": "final:E1",
                    "doc": "final",
                    "heading": "Departure",
                    "cfr_citation": None,
                    "fr_cite": "91 FR 1",
                    "text": "Transitioning students receive 60 days.",
                }
            ],
        },
    }
    verify_claims.apply_model_result(
        item,
        {
            "verdict": "supported",
            "evidence_ids": ["final:E1"],
            "reason": "Model said supported.",
        },
    )
    assert item["claim"]["verification"]["verdict"] == "partially_supported"

    article = {
        "title": "Rule sets four year limit",
        "claims": [
            {
                "claim_text": "Rule sets four year limit",
                "claim_type": "policy",
                "sentence_ids": [1],
                "verification": {"verdict": "supported"},
            },
            {
                "claim_text": "Extensions remain available",
                "claim_type": "policy",
                "sentence_ids": [2],
                "verification": {"verdict": "partially_supported"},
            },
            {
                "claim_text": "Another policy statement",
                "claim_type": "policy",
                "sentence_ids": [10],
                "verification": {"verdict": "contradicted"},
            },
        ],
    }
    score = verify_claims.score_article(article)
    assert score["grounding_score"] == 66.7
    assert score["coverage_percent"] == 100.0
    assert score["confidence"] == "High"


def test_limit_must_be_positive(tmp_path: Path):
    source = tmp_path / "articles.csv"
    write_csv(source, sample_rows())
    with pytest.raises(ValueError, match="at least 1"):
        fact_checker.load_articles(source, 0)


def test_run_writes_only_results_and_report(tmp_path: Path):
    source = tmp_path / "articles.csv"
    output = tmp_path / "output"
    write_csv(source, sample_rows())
    results = fact_checker.run(source, output)

    assert {path.name for path in output.iterdir()} == {"results.json", "report.md"}
    assert results["summary"] == {
        "article_count": 2,
        "with_text": 1,
        "missing_text": 1,
        "reposts": 1,
        "canonical_count": 2,
        "duplicates_collapsed": 0,
        "stories_extracted": 0,
        "claims_extracted": 0,
        "viewpoints_extracted": 0,
        "items_dropped": 0,
        "claims_checked": 0,
        "claims_supported": 0,
        "claims_contradicted": 0,
        "articles_scored": 0,
    }
    saved = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert "article_text" not in saved["articles"][0]
    assert saved["articles"][0]["claims"] == []
    assert any(
        event["event"] == "effective_date_postponed"
        for event in saved["policy_timeline"]
    )


class FakeResponses:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        text = "not json" if self.calls == 1 else '{"claims":[]}'
        return SimpleNamespace(output_text=text)


def test_model_helper_retries_once_and_uses_cache(tmp_path: Path):
    fake = SimpleNamespace(responses=FakeResponses())
    schema = {
        "type": "object",
        "properties": {"claims": {"type": "array", "items": {}}},
        "required": ["claims"],
        "additionalProperties": False,
    }
    first = fact_checker.call_model_json(
        "system", "article", schema, tmp_path / "cache", client=fake
    )
    second = fact_checker.call_model_json(
        "system", "article", schema, tmp_path / "cache", client=fake
    )
    assert first == second == {"claims": []}
    assert fake.responses.calls == 2
