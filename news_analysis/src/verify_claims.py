"""Small, evidence-only verifier used by run_fact_checker.py."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


CHECKABLE_TYPES = {"policy", "government_position", "interpretation"}
VERDICTS = {"supported", "partially_supported", "contradicted", "not_verifiable"}
VERIFY_BATCH_SIZE = 8

# The first three events come from the indexed rule documents. The court event
# is status context only: the court order is not yet part of the evidence index.
POLICY_TIMELINE = [
    {
        "date": "2025-08-28",
        "event": "proposed_rule_published",
        "description": "DHS published proposed rule 2025-16554.",
        "source_scope": "indexed_policy_document",
    },
    {
        "date": "2026-07-17",
        "event": "final_rule_published",
        "description": "DHS published final rule 2026-14439.",
        "source_scope": "indexed_policy_document",
    },
    {
        "date": "2026-09-14",
        "event": "effective_date_postponed",
        "description": (
            "A preliminary injunction postponed the final rule's effective date; "
            "the existing duration-of-status framework remained in effect."
        ),
        "source_scope": "status_context_only_court_order_not_indexed",
        "case": "Presidents' Alliance v. DHS, No. 1:26-cv-13799-FDS",
    },
    {
        "date": "2026-09-15",
        "event": "scheduled_effective_date",
        "description": "The final rule was scheduled to take effect but was postponed.",
        "source_scope": "indexed_date_plus_external_status_context",
    },
]

COURT_WORDS = re.compile(
    r"\b(court|judge|lawsuit|injunction|enjoin|blocked|halted|postponed|stayed)\b",
    re.IGNORECASE,
)
PROPOSAL_WORDS = re.compile(r"\b(proposal|proposed|nprm|draft rule)\b", re.IGNORECASE)
FINAL_WORDS = re.compile(
    r"\b(final rule|finalized|effective date|takes? effect|took effect)\b", re.IGNORECASE
)

VERIFICATION_SYSTEM_PROMPT = """You verify news claims using only the supplied policy evidence.

Verdicts:
- supported: all material parts of the claim are directly supported
- partially_supported: some material parts are supported but another part is missing or overstated
- contradicted: the supplied evidence directly conflicts with a material part
- not_verifiable: the supplied evidence is insufficient

Treat each claim atomically. Pay special attention to qualifiers, dates, numbers,
program length, transition rules, extensions, and whether text is proposed or final.
The key facts are retrieval aids, not citations. Cite only evidence_id values from
the supplied primary passages. Do not use outside knowledge."""

VERIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": sorted(VERDICTS)},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["claim_id", "verdict", "evidence_ids", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def route_claim(claim: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    """Choose proposed/final evidence using claim wording before article metadata."""
    text = f"{claim.get('claim_text', '')} {claim.get('source_quote', '')}"
    if COURT_WORDS.search(text):
        return {
            "documents": [],
            "review_required": True,
            "reason": "Court-status source is not included in the policy evidence index.",
        }

    mentions_proposal = bool(PROPOSAL_WORDS.search(text))
    mentions_final = bool(FINAL_WORDS.search(text))
    if mentions_proposal and not mentions_final:
        return {
            "documents": ["proposed"],
            "review_required": False,
            "reason": "Claim explicitly describes a proposal or NPRM.",
        }
    if mentions_final and not mentions_proposal:
        return {
            "documents": ["final"],
            "review_required": False,
            "reason": "Claim explicitly describes the final rule or effective date.",
        }
    if mentions_proposal and mentions_final:
        return {
            "documents": ["proposed", "final"],
            "review_required": True,
            "reason": "Claim refers to both proposed and final rule stages.",
        }

    window = article.get("window", "")
    if window in {"nprm_published", "nprm_published_docket", "comment_deadline"}:
        return {
            "documents": ["proposed"],
            "review_required": False,
            "reason": "Article belongs to a proposed-rule collection window.",
        }
    if window in {"final_rule", "injunction"}:
        return {
            "documents": ["final"],
            "review_required": False,
            "reason": "Article belongs to a final-rule collection window.",
        }
    return {
        "documents": ["proposed", "final"],
        "review_required": True,
        "reason": "Rule version is ambiguous, so both versions were searched.",
    }


def retrieve_claim(index: Any, claim: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    query = claim["claim_text"]
    documents = route["documents"]
    facts = index.search_facts(query, docs=documents, k=3)
    keyword_hits = index.search(query, docs=documents, tiers=("A", "B"), k=5)

    # Promote one machine-verified primary paragraph per matched key fact, then
    # fill the remaining slots with keyword hits. Key-fact prose is never cited.
    passages = []
    seen_ids = set()
    for fact in facts:
        matches = [
            match
            for evidence in fact.get("evidence", [])
            if evidence["doc"] in documents
            for match in evidence.get("matches", [])
            if match["tier"] in {"A", "B"}
        ]
        if matches:
            passage = index.get(matches[0]["evidence_id"])
            if passage["evidence_id"] not in seen_ids:
                passages.append(passage)
                seen_ids.add(passage["evidence_id"])
    passages.extend(
        passage for passage in keyword_hits if passage["evidence_id"] not in seen_ids
    )
    passages = passages[:5]
    return {
        "key_facts": [
            {
                "fact_id": fact["fact_id"],
                "statement": fact["statement"],
                "change_from_proposed": fact.get("change_from_proposed"),
                "common_misreadings": fact.get("common_misreadings", []),
            }
            for fact in facts
        ],
        "passages": [
            {
                "evidence_id": passage["evidence_id"],
                "doc": passage["doc"],
                "heading": passage["heading"],
                "cfr_citation": passage.get("cfr_citation"),
                "fr_cite": passage.get("fr_cite"),
                "text": passage["text"],
            }
            for passage in passages
        ],
    }


def verification_prompt(article: dict[str, Any], batch: list[dict[str, Any]]) -> str:
    payload = {
        "article": {
            "title": article["title"],
            "published": article.get("seendate"),
            "collection_window": article.get("window"),
        },
        "status_timeline": POLICY_TIMELINE,
        "claims": [
            {
                "claim_id": item["claim_id"],
                "claim_text": item["claim"]["claim_text"],
                "source_quote": item["claim"]["source_quote"],
                "documents_searched": item["route"]["documents"],
                "key_facts": item["retrieval"]["key_facts"],
                "primary_passages": item["retrieval"]["passages"],
            }
            for item in batch
        ],
    }
    return "Verify every claim in this JSON payload:\n" + json.dumps(
        payload, ensure_ascii=False
    )


NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "six": "6",
    "twelve": "12",
    "fifteen": "15",
    "thirty": "30",
    "sixty": "60",
    "ninety": "90",
}


def numeric_tokens(text: str) -> set[str]:
    """Extract consequential numbers while ignoring common visa and form labels."""
    cleaned = text.lower()
    cleaned = re.sub(r"\b(?:f|j|m|i|ds)-\d+\b", " ", cleaned)
    cleaned = re.sub(r"\bform\s+[a-z]+-?\d+\b", " ", cleaned)
    tokens = set(re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", cleaned))
    tokens.update(value for word, value in NUMBER_WORDS.items() if re.search(rf"\b{word}\b", cleaned))
    return {token.replace(",", "") for token in tokens}


def citation_for(passage: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": passage["evidence_id"],
        "doc": passage["doc"],
        "citation": passage.get("cfr_citation") or passage.get("fr_cite") or passage["heading"],
        "text": passage["text"],
    }


def apply_model_result(item: dict[str, Any], result: dict[str, Any] | None) -> None:
    passages = item["retrieval"]["passages"]
    by_id = {passage["evidence_id"]: passage for passage in passages}
    route = item["route"]
    claim = item["claim"]

    if not result:
        verdict, cited_ids = "not_verifiable", []
        reason = "The verifier did not return a result for this claim."
    else:
        verdict = result.get("verdict", "not_verifiable")
        if verdict not in VERDICTS:
            verdict = "not_verifiable"
        requested_ids = result.get("evidence_ids", [])
        cited_ids = list(dict.fromkeys(evidence_id for evidence_id in requested_ids if evidence_id in by_id))
        invalid_ids = [evidence_id for evidence_id in requested_ids if evidence_id not in by_id]
        reason = result.get("reason", "")
        if invalid_ids:
            for evidence_id in invalid_ids:
                reason = reason.replace(evidence_id, "[unretrieved evidence]")
            reason += " Unretrieved evidence IDs were removed."
        if verdict != "not_verifiable" and not cited_ids:
            verdict = "not_verifiable"
            reason += " No valid retrieved citation supported the verdict."

    citations = [citation_for(by_id[evidence_id]) for evidence_id in cited_ids]
    if verdict == "supported" and citations:
        claim_numbers = numeric_tokens(claim["claim_text"])
        evidence_numbers = numeric_tokens(" ".join(citation["text"] for citation in citations))
        missing_numbers = sorted(claim_numbers - evidence_numbers)
        if missing_numbers:
            verdict = "partially_supported"
            reason += f" Number/date values not found in cited evidence: {', '.join(missing_numbers)}."

    claim["verification"] = {
        "status": "complete",
        "verdict": verdict,
        "reason": reason.strip(),
        "documents_searched": route["documents"],
        "review_required": route["review_required"],
        "key_fact_ids": [fact["fact_id"] for fact in item["retrieval"]["key_facts"]],
        "retrieved_evidence_ids": list(by_id),
        "evidence": citations,
    }


def claim_weight(article: dict[str, Any], claim: dict[str, Any]) -> int:
    title_words = set(re.findall(r"[a-z0-9]+", article["title"].lower()))
    claim_words = set(re.findall(r"[a-z0-9]+", claim["claim_text"].lower()))
    overlap = len(title_words & claim_words) / max(1, min(len(title_words), len(claim_words)))
    if len(title_words & claim_words) >= 2 and overlap >= 0.6:
        return 3
    sentence_ids = claim.get("sentence_ids", [])
    if sentence_ids and min(sentence_ids) <= 3:
        return 2
    return 1


def score_article(article: dict[str, Any]) -> dict[str, Any]:
    checkable = [claim for claim in article.get("claims", []) if claim["claim_type"] in CHECKABLE_TYPES]
    checked = [
        claim
        for claim in checkable
        if claim.get("verification", {}).get("verdict")
        in {"supported", "partially_supported", "contradicted"}
    ]
    points = {"supported": 1.0, "partially_supported": 0.5, "contradicted": 0.0}
    denominator = sum(claim_weight(article, claim) for claim in checked)
    grounding_score = None
    if denominator:
        grounding_score = round(
            100
            * sum(
                claim_weight(article, claim)
                * points[claim["verification"]["verdict"]]
                for claim in checked
            )
            / denominator,
            1,
        )
    coverage = round(100 * len(checked) / len(checkable), 1) if checkable else None
    if coverage is not None and coverage >= 80 and len(checked) >= 3:
        confidence = "High"
    elif coverage is not None and coverage >= 50 and checked:
        confidence = "Medium"
    else:
        confidence = "Low"
    return {
        "grounding_score": grounding_score,
        "checkable_claims": len(checkable),
        "checked_claims": len(checked),
        "coverage_percent": coverage,
        "confidence": confidence,
        "confidence_note": "Heuristic based on verification coverage; not statistically calibrated.",
    }


def verify_article(
    article: dict[str, Any],
    index: Any,
    cache_dir: Path,
    model_call: Callable[..., dict[str, Any]],
    *,
    client: Any | None = None,
    model: str | None = None,
) -> None:
    prepared = []
    for number, claim in enumerate(article.get("claims", []), start=1):
        if claim["claim_type"] not in CHECKABLE_TYPES:
            claim["verification"] = {
                "status": "excluded",
                "verdict": None,
                "reason": f"{claim['claim_type']} claims are excluded from policy grounding scores.",
                "documents_searched": [],
                "review_required": False,
                "key_fact_ids": [],
                "retrieved_evidence_ids": [],
                "evidence": [],
            }
            continue

        route = route_claim(claim, article)
        if not route["documents"]:
            claim["verification"] = {
                "status": "outside_current_evidence",
                "verdict": "not_verifiable",
                "reason": route["reason"],
                "documents_searched": [],
                "review_required": True,
                "key_fact_ids": [],
                "retrieved_evidence_ids": [],
                "evidence": [],
            }
            continue
        prepared.append(
            {
                "claim_id": f"C{number}",
                "claim": claim,
                "route": route,
                "retrieval": retrieve_claim(index, claim, route),
            }
        )

    for start in range(0, len(prepared), VERIFY_BATCH_SIZE):
        batch = prepared[start : start + VERIFY_BATCH_SIZE]
        raw = model_call(
            VERIFICATION_SYSTEM_PROMPT,
            verification_prompt(article, batch),
            VERIFICATION_SCHEMA,
            cache_dir,
            client=client,
            model=model,
        )
        returned = {
            result.get("claim_id"): result
            for result in raw.get("results", [])
            if isinstance(result, dict)
        }
        for item in batch:
            apply_model_result(item, returned.get(item["claim_id"]))

    article["fact_check"] = score_article(article)


def verify_articles(
    articles: list[dict[str, Any]],
    index: Any,
    cache_dir: Path,
    model_call: Callable[..., dict[str, Any]],
    *,
    client: Any | None = None,
    model: str | None = None,
) -> None:
    for article in articles:
        if article.get("extraction_status") == "complete":
            verify_article(
                article,
                index,
                cache_dir,
                model_call,
                client=client,
                model=model,
            )
        else:
            article["fact_check"] = score_article(article)
