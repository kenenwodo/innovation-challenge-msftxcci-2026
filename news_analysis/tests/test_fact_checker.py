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


def test_whitespace_only_quote_difference_uses_exact_source_slice():
    text = "The policy may affect higher education\ninstitutions nationwide."
    sentences = fact_checker.split_sentences(text)
    raw = {
        "claims": [
            {
                "claim_text": "The policy may affect higher education institutions.",
                "claim_type": "prediction",
                "speaker": None,
                "organization": None,
                "source_quote": (
                    "The policy may affect higher education institutions nationwide."
                ),
            }
        ],
        "viewpoints": [],
    }

    cleaned, warnings = fact_checker.validate_extraction(raw, text, sentences)

    claim = cleaned["claims"][0]
    assert claim["source_quote"] == text
    assert text[claim["start_char"] : claim["end_char"]] == text
    assert warnings == []


def test_claim_routing_prefers_explicit_wording_and_uses_court_sources():
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
    reasoning = verify_claims.route_claim(
        {
            "claim_text": "The court found that DHS was likely to have acted arbitrarily.",
            "source_quote": "",
        },
        article,
    )
    mixed = verify_claims.route_claim(
        {
            "claim_text": "A judge blocked the final rule's four-year cap.",
            "source_quote": "",
        },
        article,
    )
    lawsuit = verify_claims.route_claim(
        {"claim_text": "Several organizations filed a lawsuit.", "source_quote": ""},
        article,
    )

    assert proposed["documents"] == ["proposed"]
    assert proposed["review_required"] is False
    assert court["documents"] == ["court_order"]
    assert court["review_required"] is False
    assert reasoning["documents"] == ["court_memo"]
    assert reasoning["review_required"] is False
    assert mixed["documents"] == ["court_order", "final"]
    assert mixed["review_required"] is True
    assert lawsuit["documents"] == []
    assert lawsuit["review_required"] is True


def test_policy_retrieval_covers_demo_edge_cases():
    from evidence_index import EvidenceIndex

    index = EvidenceIndex.load()

    lifetime = index.search_facts(
        "Students face a hard lifetime cap of four years and cannot extend their stay",
        k=6,
    )
    assert {"KF-ADMIT-01", "KF-EOS-01"} <= {
        fact["fact_id"] for fact in lifetime
    }

    grace = index.search_facts(
        "Students get 30 days to leave, but transitioning students keep 60 days",
        k=5,
    )
    assert {"KF-GRACE-01", "KF-TRANS-01"} <= {
        fact["fact_id"] for fact in grace
    }

    opt = index.search(
        "post-completion practical training requires additional admission time",
        docs=["final"],
        k=8,
    )
    assert any(hit["evidence_id"] == "final:8 CFR 214.2/p65" for hit in opt)


def test_court_retrieval_is_citable_and_time_aware():
    from evidence_index import EvidenceIndex

    index = EvidenceIndex.load()
    before_ruling = index.search(
        "the effective date was postponed",
        docs=["court_order"],
        tiers=["J"],
        as_of="2026-09-10",
    )
    after_ruling = index.search(
        "the effective date was postponed and implementation was enjoined",
        docs=["court_order"],
        tiers=["J"],
        as_of="2026-09-20",
        k=2,
    )

    assert before_ruling == []
    assert after_ruling[0]["evidence_id"] == "court_order:dkt51:p2"
    assert "Dkt. 51 at 2" in after_ruling[0]["court_citation"]


def test_court_claim_is_verified_against_the_order(tmp_path: Path):
    from evidence_index import EvidenceIndex

    def fake_model_call(_system, prompt, _schema, *_args, **_kwargs):
        payload = json.loads(prompt.split("\n", 1)[1])
        claim = payload["claims"][0]
        evidence_id = next(
            passage["evidence_id"]
            for passage in claim["primary_passages"]
            if passage["evidence_id"] == "court_order:dkt51:p2"
        )
        return {
            "results": [
                {
                    "claim_id": claim["claim_id"],
                    "verdict": "supported",
                    "evidence_ids": [evidence_id],
                    "reason": "The operative order postponed the effective date.",
                }
            ]
        }

    article = {
        "title": "Judge postpones DHS rule",
        "window": "injunction",
        "seendate": "20260920T084500Z",
        "claims": [
            {
                "claim_text": "A judge postponed the final rule's effective date.",
                "claim_type": "policy",
                "source_quote": "A judge postponed the final rule's effective date.",
                "sentence_ids": [1],
            }
        ],
    }

    verify_claims.verify_article(
        article,
        EvidenceIndex.load(),
        tmp_path,
        fake_model_call,
    )

    verification = article["claims"][0]["verification"]
    assert verification["status"] == "complete"
    assert verification["verdict"] == "supported"
    assert verification["documents_searched"] == ["court_order"]
    assert verification["evidence_as_of"] == "2026-09-20"
    assert verification["review_required"] is False
    assert "Dkt. 51 at 2" in verification["evidence"][0]["citation"]


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
    assert item["claim"]["verification"]["review_required"] is True

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


def test_run_writes_json_csv_and_report(tmp_path: Path):
    source = tmp_path / "articles.csv"
    output = tmp_path / "output"
    write_csv(source, sample_rows())
    results = fact_checker.run(source, output)

    assert {path.name for path in output.iterdir()} == {
        "articles_ui.csv",
        "claim_evidence_ui.csv",
        "claims_ui.csv",
        "results.json",
        "results.csv",
        "report.md",
        "viewpoints_ui.csv",
    }
    assert results["summary"] == {
        "article_count": 2,
        "with_text": 1,
        "missing_text": 1,
        "reposts": 1,
        "canonical_count": 2,
        "duplicates_collapsed": 0,
        "stories_extracted": 0,
        "stories_content_filtered": 0,
        "stories_model_errors": 0,
        "claims_extracted": 0,
        "viewpoints_extracted": 0,
        "items_dropped": 0,
        "claims_checked": 0,
        "claims_verification_filtered": 0,
        "claims_verification_errors": 0,
        "claims_supported": 0,
        "claims_partially_supported": 0,
        "claims_contradicted": 0,
        "claims_not_verifiable": 0,
        "claims_review_required": 0,
        "articles_scored": 0,
    }
    saved = json.loads((output / "results.json").read_text(encoding="utf-8"))
    assert "article_text" not in saved["articles"][0]
    assert saved["articles"][0]["claims"] == []
    assert any(
        event["event"] == "effective_date_postponed"
        for event in saved["policy_timeline"]
    )
    with (output / "results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(saved["articles"]) == 2
    assert {row["article_id"] for row in rows} == {
        article["article_id"] for article in saved["articles"]
    }
    assert all(row["grounding_score"] == "" for row in rows)
    assert all(json.loads(row["claims_json"]) == [] for row in rows)
    assert fact_checker.validate_result_artifacts(output) == {
        "article_rows": 2,
        "columns": len(fact_checker.UI_CSV_FIELDS),
        "claims": 0,
        "viewpoints": 0,
        "citations": 0,
        "blank_grounding_scores": 2,
    }


def test_ui_csv_preserves_nested_analysis_and_special_characters(tmp_path: Path):
    article = {
        "article_id": "article-1",
        "title": 'A title, with "quotes"\nand café',
        "url": "https://example.test/article-1",
        "domain": "example.test",
        "seendate": "20260914T120000Z",
        "window": "injunction",
        "source_country": "United States",
        "is_repost": False,
        "has_text": True,
        "text_chars": 123,
        "article_text": "FULL ARTICLE TEXT MUST NOT APPEAR IN THE EXPORT",
        "cluster_size": 2,
        "source_urls": [
            "https://example.test/article-1",
            "https://example.test/repost",
        ],
        "source_article_ids": ["article-1", "article-2"],
        "extraction_status": "complete",
        "claims": [
            {
                "claim_text": "The court postponed the rule.",
                "claim_type": "policy",
                "source_quote": "The court postponed the rule, effective immediately.\n",
                "start_char": 10,
                "end_char": 64,
                "sentence_ids": [1],
                "verification": {
                    "status": "complete",
                    "verdict": "supported",
                    "reason": "The order postponed the effective date.",
                    "review_required": False,
                    "retrieved_evidence_ids": ["court_order:dkt51:p2"],
                    "evidence": [
                        {
                            "evidence_id": "court_order:dkt51:p2",
                            "citation": "Dkt. 51, p. 2",
                            "text": "The effective date is postponed.",
                        }
                    ],
                },
            },
            {
                "claim_text": "A later appeal was filed.",
                "claim_type": "outside_fact",
                "source_quote": "A later appeal was filed.",
                "start_char": 65,
                "end_char": 90,
                "sentence_ids": [2],
                "verification": {
                    "status": "outside_current_evidence",
                    "verdict": "not_verifiable",
                    "reason": "Later proceedings are outside the indexed evidence.",
                    "review_required": True,
                    "retrieved_evidence_ids": [],
                    "evidence": [],
                },
            },
        ],
        "viewpoints": [
            {
                "speaker": "A student",
                "organization": None,
                "stance": "oppose",
                "target_provision": "fixed admission periods",
                "reason": "It may interrupt studies.",
                "source_quote": 'A student said, "This may interrupt studies."',
                "start_char": 91,
                "end_char": 122,
                "sentence_ids": [3],
            }
        ],
        "extraction_warnings": ["warning, with comma"],
        "extraction_error": None,
        "verification_warnings": [],
        "fact_check": {
            "grounding_score": 100.0,
            "checkable_claims": 1,
            "checked_claims": 1,
            "coverage_percent": 100.0,
            "confidence": "Medium",
            "confidence_note": "Demo confidence note.",
        },
    }
    output = tmp_path / "results.csv"

    fact_checker.write_articles_csv(output, [article])

    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert reader.fieldnames == fact_checker.UI_CSV_FIELDS
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == article["title"]
    assert row["grounding_score"] == "100.0"
    assert row["claim_count"] == "2"
    assert row["viewpoint_count"] == "1"
    assert row["supported_count"] == "1"
    assert row["not_verifiable_count"] == "1"
    assert row["review_required_count"] == "1"
    assert json.loads(row["source_urls_json"]) == article["source_urls"]
    assert json.loads(row["claims_json"]) == article["claims"]
    assert json.loads(row["viewpoints_json"]) == article["viewpoints"]
    assert json.loads(row["extraction_warnings_json"]) == article["extraction_warnings"]
    assert json.loads(row["extraction_error_json"]) is None
    assert "article_text" not in row
    assert "FULL ARTICLE TEXT MUST NOT APPEAR" not in output.read_text(encoding="utf-8")


def test_export_existing_results_requires_no_model_rerun(tmp_path: Path):
    source = tmp_path / "articles.csv"
    output = tmp_path / "output"
    write_csv(source, sample_rows())
    original = fact_checker.run(source, output)
    for field in (
        "claims_partially_supported",
        "claims_not_verifiable",
        "claims_review_required",
    ):
        original["summary"].pop(field)
    fact_checker.write_json(output / "results.json", original)
    (output / "results.csv").unlink()
    (output / "report.md").unlink()

    exported = fact_checker.export_existing_results(output)

    assert exported["summary"]["claims_partially_supported"] == 0
    assert exported["summary"]["claims_not_verifiable"] == 0
    assert exported["summary"]["claims_review_required"] == 0
    assert (output / "results.csv").is_file()
    assert (output / "articles_ui.csv").is_file()
    assert (output / "claims_ui.csv").is_file()
    assert (output / "claim_evidence_ui.csv").is_file()
    assert (output / "viewpoints_ui.csv").is_file()
    assert "UI CSV article rows: 2" in (output / "report.md").read_text(
        encoding="utf-8"
    )


def test_ui_export_validation_detects_json_csv_drift(tmp_path: Path):
    source = tmp_path / "articles.csv"
    output = tmp_path / "output"
    write_csv(source, sample_rows())
    results = fact_checker.run(source, output)
    results["articles"][0]["title"] = "A title that is not in the CSV"
    fact_checker.write_json(output / "results.json", results)

    with pytest.raises(ValueError, match="title does not match results JSON"):
        fact_checker.validate_result_artifacts(output)


class FakeResponses:
    def __init__(self):
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
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
    assert all(
        request["max_output_tokens"] == fact_checker.DEFAULT_MAX_OUTPUT_TOKENS
        for request in fake.responses.requests
    )
    assert all(
        request["timeout"] == fact_checker.DEFAULT_REQUEST_TIMEOUT_SECONDS
        for request in fake.responses.requests
    )


class AlwaysInvalidResponses:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            id=f"response-{self.calls}",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text='{"claims":[{"claim_text":"cut off',
        )


def test_model_helper_returns_diagnostic_after_two_invalid_responses(tmp_path: Path):
    responses = AlwaysInvalidResponses()
    fake = SimpleNamespace(responses=responses)
    result = fact_checker.call_model_json(
        "system", "article", {"type": "object"}, tmp_path / "cache", client=fake
    )

    assert responses.calls == 2
    details = result[fact_checker.MODEL_ERROR_RESULT_KEY]
    assert details["type"] == "invalid_json"
    assert details["response_id"] == "response-2"
    assert details["response_status"] == "incomplete"
    assert details["incomplete_reason"] == "max_output_tokens"
    assert "Unterminated string" in details["message"]
    assert not (tmp_path / "cache").exists()


class APITimeoutError(Exception):
    request_id = "timeout-test"


class TimeoutResponses:
    def create(self, **_kwargs):
        raise APITimeoutError()


def test_model_helper_returns_diagnostic_after_timeout(tmp_path: Path):
    fake = SimpleNamespace(responses=TimeoutResponses())
    result = fact_checker.call_model_json(
        "system", "article", {"type": "object"}, tmp_path / "cache", client=fake
    )

    details = result[fact_checker.MODEL_ERROR_RESULT_KEY]
    assert details == {
        "type": "request_timeout",
        "message": "Azure OpenAI did not respond within 180 seconds",
        "request_id": "timeout-test",
    }


class FakeContentFilterError(Exception):
    code = "content_filter"
    body = {
        "error": {
            "message": "The response was filtered due to the prompt triggering policy.",
            "param": "prompt",
            "code": "content_filter",
            "content_filters": [
                {
                    "content_filter_results": {
                        "hate": {"filtered": True, "severity": "medium"},
                        "violence": {"filtered": False, "severity": "safe"},
                    }
                }
            ],
            "innererror": {"code": "ContentFiltered"},
            "request_id": "request-test",
        }
    }


class FilterFirstResponses:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise FakeContentFilterError()
        return SimpleNamespace(output_text='{"claims":[],"viewpoints":[]}')


def test_run_records_content_filter_and_continues(tmp_path: Path, capsys):
    source = tmp_path / "articles.csv"
    output = tmp_path / "output"
    rows = sample_rows()
    rows[1] = {
        **rows[1],
        "title": "Second story",
        "url": "https://example.test/second",
        "is_repost": "False",
        "article_text": "A second policy article.",
    }
    write_csv(source, rows)
    client = SimpleNamespace(responses=FilterFirstResponses())

    results = fact_checker.run(source, output, extract=True, client=client)

    assert results["summary"]["stories_content_filtered"] == 1
    assert results["summary"]["stories_extracted"] == 1
    assert [article["extraction_status"] for article in results["articles"]] == [
        "content_filtered",
        "complete",
    ]
    blocked = results["articles"][0]["extraction_error"]
    assert blocked["filtered_categories"] == {"hate": "medium"}
    assert blocked["request_id"] == "request-test"
    assert "left unmodified and skipped" in capsys.readouterr().out
    assert len(list((output / "cache").glob("*.json"))) == 1


def test_verification_content_filter_is_marked_for_review(tmp_path: Path):
    class FakeIndex:
        def search_facts(self, *_args, **_kwargs):
            return []

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
            }
        ],
    }

    def filtered_model_call(*_args, **_kwargs):
        return {
            fact_checker.CONTENT_FILTER_RESULT_KEY: {
                "filtered_categories": {"hate": "medium"},
                "request_id": "verify-test",
            }
        }

    verify_claims.verify_article(article, FakeIndex(), tmp_path, filtered_model_call)

    verification = article["claims"][0]["verification"]
    assert verification["status"] == "content_filtered"
    assert verification["verdict"] == "not_verifiable"
    assert verification["review_required"] is True
    assert verification["retrieved_evidence_ids"] == ["final:E1"]
    assert article["fact_check"]["grounding_score"] is None
    assert article["verification_warnings"] == [
        "content filter blocked 1 claim(s) (hate=medium); marked for review"
    ]
