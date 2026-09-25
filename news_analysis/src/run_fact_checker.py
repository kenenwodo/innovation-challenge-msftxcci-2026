#!/usr/bin/env python3
"""Lean batch pipeline for the news policy-grounding prototype."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from export_ui_csvs import validate_ui_exports, write_ui_exports


NEWS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = NEWS_DIR / "gdelt_news_articles_v3-112.csv"
DEFAULT_OUTPUT_DIR = NEWS_DIR / "output" / "fact_check"

# Intentionally simple: enough to collapse obvious syndicated copies without
# introducing a separate deduplication service.
SHINGLE_SIZE = 5
MIN_SHINGLES = 12
NEAR_DUPLICATE_THRESHOLD = 0.85

CLAIM_TYPES = {
    "policy",
    "government_position",
    "outside_fact",
    "interpretation",
    "prediction",
    "opinion",
}
STANCE_TYPES = {"support", "oppose", "mixed", "neutral", "unclear"}
CONTENT_FILTER_RESULT_KEY = "_content_filter"
MODEL_ERROR_RESULT_KEY = "_model_error"
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0

UI_CSV_FIELDS = [
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
    "source_urls_json",
    "source_article_ids_json",
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
    "claims_json",
    "viewpoints_json",
    "extraction_warnings_json",
    "extraction_error_json",
    "verification_warnings_json",
]
UI_CSV_JSON_FIELDS = {
    "source_urls_json",
    "source_article_ids_json",
    "claims_json",
    "viewpoints_json",
    "extraction_warnings_json",
    "extraction_error_json",
    "verification_warnings_json",
}

EXTRACTION_SYSTEM_PROMPT = """You extract policy claims and attributed viewpoints from news articles.

Return atomic claims: split a sentence containing multiple assertions into separate
claims. Use these claim types carefully:
- policy: what a rule says, requires, permits, changes, or its legal status
- government_position: an attributed government statement about the policy
- outside_fact: contextual reporting that cannot be checked against the policy text
- interpretation: an explanation of what the policy may mean or how it may operate
- prediction: a claim about a future consequence
- opinion: support, opposition, concern, praise, or another value judgment

Never label an opinion, interpretation, or prediction as a policy claim merely
because it discusses the rule. Extract a viewpoint only when the article attributes
it to a named person or organization. Preserve the stated stance, target provision,
and reason; do not infer a reason that is absent.

A court decision changing whether the rule can take effect is a policy claim. A
lawsuit filing, appeal, or party allegation is an outside fact unless the sentence
also states the rule's legal status. Split a court-status assertion from any separate
assertion about what the rule requires so each can be checked against the right source.

For every item, copy the smallest useful continuous source_quote exactly from the
article. Do not repair, paraphrase, or normalize source quotes. The application
will calculate sentence IDs and character offsets from that exact quote."""

SOURCE_QUOTE_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 1200}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_text": {"type": "string", "minLength": 1},
                    "claim_type": {"type": "string", "enum": sorted(CLAIM_TYPES)},
                    "speaker": {"type": ["string", "null"]},
                    "organization": {"type": ["string", "null"]},
                    "source_quote": SOURCE_QUOTE_SCHEMA,
                },
                "required": [
                    "claim_text",
                    "claim_type",
                    "speaker",
                    "organization",
                    "source_quote",
                ],
                "additionalProperties": False,
            },
        },
        "viewpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": ["string", "null"]},
                    "organization": {"type": ["string", "null"]},
                    "stance": {"type": "string", "enum": sorted(STANCE_TYPES)},
                    "target_provision": {"type": "string"},
                    "reason": {"type": "string"},
                    "source_quote": SOURCE_QUOTE_SCHEMA,
                },
                "required": [
                    "speaker",
                    "organization",
                    "stance",
                    "target_provision",
                    "reason",
                    "source_quote",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims", "viewpoints"],
    "additionalProperties": False,
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def article_id(url: str) -> str:
    """Return a short, stable ID without introducing a database."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def load_articles(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read the GDELT CSV and perform only low-risk normalization."""
    if not path.is_file():
        raise FileNotFoundError(f"input CSV does not exist: {path}")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")

    articles = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"title", "url", "domain", "seendate", "article_text"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"input CSV is missing columns: {', '.join(sorted(missing))}")

        for row in reader:
            text = (row.get("article_text") or "").strip()
            articles.append(
                {
                    "article_id": article_id(row["url"]),
                    "title": row["title"].strip(),
                    "url": row["url"].strip(),
                    "domain": row["domain"].strip(),
                    "seendate": row["seendate"].strip(),
                    "window": (row.get("window") or "").strip(),
                    "source_country": (row.get("sourcecountry") or "").strip() or None,
                    "is_repost": parse_bool(row.get("is_repost")),
                    "has_text": bool(text),
                    "text_chars": len(text),
                    "article_text": text,
                }
            )
            if limit is not None and len(articles) >= limit:
                break
    return articles


def normalized_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def text_shingles(value: str) -> set[tuple[str, ...]]:
    words = normalized_words(value)
    return {
        tuple(words[index : index + SHINGLE_SIZE])
        for index in range(len(words) - SHINGLE_SIZE + 1)
    }


def cluster_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse exact-title and obvious text duplicates, keeping the longest copy."""
    parents = list(range(len(articles)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    titles = [" ".join(normalized_words(article["title"])) for article in articles]
    shingles = [text_shingles(article["article_text"]) for article in articles]
    for left in range(len(articles)):
        for right in range(left + 1, len(articles)):
            same_title = bool(titles[left]) and titles[left] == titles[right]
            left_set, right_set = shingles[left], shingles[right]
            similar_text = False
            if len(left_set) >= MIN_SHINGLES and len(right_set) >= MIN_SHINGLES:
                similarity = len(left_set & right_set) / len(left_set | right_set)
                similar_text = similarity >= NEAR_DUPLICATE_THRESHOLD
            if same_title or similar_text:
                union(left, right)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, article in enumerate(articles):
        groups.setdefault(find(index), []).append(article)

    canonical_articles = []
    for group in groups.values():
        canonical = max(group, key=lambda article: article["text_chars"])
        merged = canonical.copy()
        merged["cluster_size"] = len(group)
        merged["source_urls"] = [article["url"] for article in group]
        merged["source_article_ids"] = [article["article_id"] for article in group]
        canonical_articles.append(merged)
    return canonical_articles


def split_sentences(text: str) -> list[dict[str, Any]]:
    """Return readable sentence units while preserving exact source offsets."""
    spans = []
    start = 0
    for whitespace in re.finditer(r"\s+", text):
        candidate = text[start : whitespace.start()].rstrip()
        next_char = text[whitespace.end() : whitespace.end() + 1]
        paragraph_break = "\n\n" in whitespace.group()
        punctuation_break = bool(
            re.search(r"[.!?][\"'”’\)\]]*$", candidate)
            and (
                not next_char
                or next_char.isupper()
                or next_char.isdigit()
                or next_char in '"“‘('
            )
        )
        if not paragraph_break and not punctuation_break:
            continue
        end = whitespace.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append(
                {
                    "sentence_id": len(spans) + 1,
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                }
            )
        start = whitespace.end()

    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append(
            {
                "sentence_id": len(spans) + 1,
                "start": start,
                "end": end,
                "text": text[start:end],
            }
        )
    return spans


def extraction_input(article: dict[str, Any], sentences: list[dict[str, Any]]) -> str:
    lines = [f"TITLE: {article['title']}", "", "ARTICLE SENTENCES:"]
    lines.extend(
        f"[S{sentence['sentence_id']} {sentence['start']}:{sentence['end']}] {sentence['text']}"
        for sentence in sentences
    )
    return "\n".join(lines)


def source_quote_spans(text: str, quote: str) -> list[tuple[int, int]]:
    """Locate exact quotes, tolerating only differences in whitespace runs."""
    exact = [(match.start(), match.end()) for match in re.finditer(re.escape(quote), text)]
    if exact:
        return exact
    parts = re.split(r"\s+", quote.strip())
    if not parts or not all(parts):
        return []
    whitespace_flexible = r"\s+".join(re.escape(part) for part in parts)
    return [
        (match.start(), match.end())
        for match in re.finditer(whitespace_flexible, text)
    ]


def validate_extraction(
    raw: dict[str, Any], text: str, sentences: list[dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Drop model items that cannot be traced exactly to the article."""
    sentence_by_id = {sentence["sentence_id"]: sentence for sentence in sentences}
    cleaned = {"claims": [], "viewpoints": []}
    warnings = []

    for kind in ("claims", "viewpoints"):
        items = raw.get(kind, [])
        if not isinstance(items, list):
            warnings.append(f"{kind}: model returned a non-list value")
            continue
        for number, item in enumerate(items, start=1):
            reason = None
            if not isinstance(item, dict):
                reason = "item is not an object"
            else:
                quote = item.get("source_quote")
                spans = (
                    source_quote_spans(text, quote)
                    if isinstance(quote, str) and quote
                    else []
                )
                if not spans:
                    reason = "source quote was not found exactly in the article"
                else:
                    # Scraped stories sometimes repeat a paragraph verbatim. The
                    # first source occurrence is a deterministic, valid citation.
                    start, end = spans[0]
                    sentence_ids = [
                        sentence_id
                        for sentence_id, sentence in sentence_by_id.items()
                        if sentence["start"] < end and sentence["end"] > start
                    ]
                    if not sentence_ids:
                        reason = "source quote does not overlap a sentence"
                    else:
                        item = {
                            **item,
                            "source_quote": text[start:end],
                            "start_char": start,
                            "end_char": end,
                            "sentence_ids": sentence_ids,
                        }
                if kind == "claims" and item.get("claim_type") not in CLAIM_TYPES:
                    reason = "invalid claim type"
                if kind == "viewpoints" and item.get("stance") not in STANCE_TYPES:
                    reason = "invalid viewpoint stance"
                if kind == "viewpoints" and not (
                    item.get("speaker") or item.get("organization")
                ):
                    reason = "viewpoint has no attributed speaker or organization"

            if reason:
                warnings.append(f"{kind[:-1]} {number}: {reason}")
            else:
                cleaned[kind].append(item)
    return cleaned, warnings


def atomic_write(path: Path, text: str) -> None:
    """Avoid leaving half-written demo artifacts after an interrupted run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def compact_json(value: Any) -> str:
    """Encode a nested CSV cell as standard, compact JSON."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def article_csv_row(article: dict[str, Any]) -> dict[str, Any]:
    """Flatten one public article while preserving nested analysis as JSON."""
    claims = article.get("claims", [])
    viewpoints = article.get("viewpoints", [])
    fact_check = article.get("fact_check") or {}

    def verdict_count(verdict: str) -> int:
        return sum(
            claim.get("verification", {}).get("verdict") == verdict
            for claim in claims
        )

    return {
        "article_id": article.get("article_id"),
        "title": article.get("title"),
        "url": article.get("url"),
        "domain": article.get("domain"),
        "seendate": article.get("seendate"),
        "window": article.get("window"),
        "source_country": article.get("source_country"),
        "is_repost": article.get("is_repost"),
        "has_text": article.get("has_text"),
        "text_chars": article.get("text_chars"),
        "cluster_size": article.get("cluster_size"),
        "source_urls_json": compact_json(article.get("source_urls", [])),
        "source_article_ids_json": compact_json(
            article.get("source_article_ids", [])
        ),
        "extraction_status": article.get("extraction_status"),
        "grounding_score": fact_check.get("grounding_score"),
        "confidence": fact_check.get("confidence"),
        "confidence_note": fact_check.get("confidence_note"),
        "checkable_claims": fact_check.get("checkable_claims"),
        "checked_claims": fact_check.get("checked_claims"),
        "coverage_percent": fact_check.get("coverage_percent"),
        "claim_count": len(claims),
        "viewpoint_count": len(viewpoints),
        "supported_count": verdict_count("supported"),
        "partially_supported_count": verdict_count("partially_supported"),
        "contradicted_count": verdict_count("contradicted"),
        "not_verifiable_count": verdict_count("not_verifiable"),
        "review_required_count": sum(
            bool(claim.get("verification", {}).get("review_required"))
            for claim in claims
        ),
        "claims_json": compact_json(claims),
        "viewpoints_json": compact_json(viewpoints),
        "extraction_warnings_json": compact_json(
            article.get("extraction_warnings", [])
        ),
        "extraction_error_json": compact_json(article.get("extraction_error")),
        "verification_warnings_json": compact_json(
            article.get("verification_warnings", [])
        ),
    }


def write_articles_csv(path: Path, articles: list[dict[str, Any]]) -> None:
    """Write the one-row-per-article UI artifact atomically."""
    buffer = StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=UI_CSV_FIELDS, lineterminator="\n", extrasaction="raise"
    )
    writer.writeheader()
    writer.writerows(article_csv_row(article) for article in articles)
    atomic_write(path, buffer.getvalue())


def content_filter_details(error: Exception) -> dict[str, Any] | None:
    """Return safe Azure content-filter metadata, or None for another error."""
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        response = getattr(error, "response", None)
        try:
            body = response.json() if response is not None else None
        except (AttributeError, TypeError, ValueError):
            body = None
    if not isinstance(body, dict):
        body = {}

    payload = body.get("error", body)
    if not isinstance(payload, dict):
        payload = {}
    inner = payload.get("innererror", {})
    if not isinstance(inner, dict):
        inner = {}

    codes = {
        str(value).lower().replace("_", "")
        for value in (
            getattr(error, "code", None),
            payload.get("code"),
            inner.get("code"),
        )
        if value
    }
    message = str(payload.get("message") or error).lower()
    if not (
        {"contentfilter", "contentfiltered"} & codes
        or "content management policy" in message
    ):
        return None

    filtered_categories = {}
    filters = payload.get("content_filters", [])
    if isinstance(filters, list):
        for filter_item in filters:
            if not isinstance(filter_item, dict):
                continue
            results = filter_item.get("content_filter_results", {})
            if not isinstance(results, dict):
                continue
            for category, result in results.items():
                if isinstance(result, dict) and result.get("filtered"):
                    filtered_categories[category] = result.get("severity") or "filtered"

    return {
        "provider": "azure_openai",
        "code": payload.get("code") or inner.get("code") or "content_filter",
        "param": payload.get("param"),
        "filtered_categories": filtered_categories,
        "request_id": payload.get("request_id") or getattr(error, "request_id", None),
    }


def content_filter_warning(details: dict[str, Any]) -> str:
    categories = details.get("filtered_categories", {})
    category_text = ", ".join(
        f"{category}={severity}" for category, severity in sorted(categories.items())
    )
    request_id = details.get("request_id")
    suffixes = []
    if category_text:
        suffixes.append(category_text)
    if request_id:
        suffixes.append(f"request_id={request_id}")
    suffix = f" ({'; '.join(suffixes)})" if suffixes else ""
    return f"Azure OpenAI blocked the prompt with its content filter{suffix}"


def max_output_tokens() -> int:
    value = os.environ.get(
        "AZURE_OPENAI_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)
    )
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(
            "AZURE_OPENAI_MAX_OUTPUT_TOKENS must be a positive integer"
        ) from error
    if parsed < 1:
        raise RuntimeError("AZURE_OPENAI_MAX_OUTPUT_TOKENS must be a positive integer")
    return parsed


def request_timeout_seconds() -> float:
    value = os.environ.get(
        "AZURE_OPENAI_TIMEOUT_SECONDS", str(DEFAULT_REQUEST_TIMEOUT_SECONDS)
    )
    try:
        parsed = float(value)
    except ValueError as error:
        raise RuntimeError(
            "AZURE_OPENAI_TIMEOUT_SECONDS must be a positive number"
        ) from error
    if parsed <= 0:
        raise RuntimeError("AZURE_OPENAI_TIMEOUT_SECONDS must be a positive number")
    return parsed


def timeout_error_details(error: Exception) -> dict[str, Any] | None:
    if error.__class__.__name__ != "APITimeoutError":
        return None
    return {
        "type": "request_timeout",
        "message": (
            f"Azure OpenAI did not respond within {request_timeout_seconds():g} seconds"
        ),
        "request_id": getattr(error, "request_id", None),
    }


def invalid_json_details(response: Any, error: Exception) -> dict[str, Any]:
    incomplete = getattr(response, "incomplete_details", None)
    return {
        "type": "invalid_json",
        "message": str(error),
        "response_id": getattr(response, "id", None),
        "response_status": getattr(response, "status", None),
        "incomplete_reason": getattr(incomplete, "reason", None),
    }


def call_model_json(
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    cache_dir: Path,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Call Azure for strict JSON, cache the result, and retry invalid JSON once."""
    if client is None:
        from dotenv import load_dotenv

        load_dotenv()
        model = model or os.environ.get("AZURE_OPENAI_MODEL")
    elif not model:
        model = "test-model"

    if not model:
        raise RuntimeError("AZURE_OPENAI_MODEL is required for a model call")

    cache_material = json.dumps(
        {
            "model": model,
            "system": system_prompt,
            "user": user_prompt,
            "schema": schema,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.is_file():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    if client is None:
        from openai import OpenAI

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not endpoint or not api_key:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required "
                "for an uncached model call"
            )
        # The repository stores a Foundry project endpoint. Its OpenAI-compatible
        # Responses API lives under /openai/v1/.
        base_url = endpoint.rstrip("/")
        if not base_url.endswith("/openai/v1"):
            base_url += "/openai/v1"
        client = OpenAI(
            base_url=base_url + "/",
            api_key=api_key,
            timeout=request_timeout_seconds(),
            # The batch is resumable, so fail one request promptly rather than
            # allowing SDK retries to make the terminal appear frozen.
            max_retries=0,
        )

    prompt = user_prompt
    last_error: Exception | None = None
    last_response: Any | None = None
    for attempt in range(2):
        try:
            response = client.responses.create(
                model=model,
                instructions=system_prompt,
                input=prompt,
                max_output_tokens=max_output_tokens(),
                timeout=request_timeout_seconds(),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "fact_checker_response",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as error:
            details = content_filter_details(error)
            if details is not None:
                # Do not cache policy blocks: a later filter configuration change
                # should be able to retry the original, unmodified source text.
                return {CONTENT_FILTER_RESULT_KEY: details}
            details = timeout_error_details(error)
            if details is not None:
                return {MODEL_ERROR_RESULT_KEY: details}
            raise
        last_response = response
        try:
            parsed = json.loads(response.output_text)
            write_json(cache_path, parsed)
            return parsed
        except (AttributeError, TypeError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 0:
                prompt += (
                    "\n\nThe previous response was incomplete or invalid JSON. "
                    "Return a complete JSON object only and keep each field concise."
                )
    return {
        MODEL_ERROR_RESULT_KEY: invalid_json_details(last_response, last_error)
    }


def extract_article(
    article: dict[str, Any],
    cache_dir: Path,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    sentences = split_sentences(article["article_text"])
    raw = call_model_json(
        EXTRACTION_SYSTEM_PROMPT,
        extraction_input(article, sentences),
        EXTRACTION_SCHEMA,
        cache_dir,
        client=client,
        model=model,
    )
    if CONTENT_FILTER_RESULT_KEY in raw:
        return {
            "claims": [],
            "viewpoints": [],
            "extraction_warnings": [],
            "extraction_error": {
                "type": "content_filter",
                **raw[CONTENT_FILTER_RESULT_KEY],
            },
        }
    if MODEL_ERROR_RESULT_KEY in raw:
        return {
            "claims": [],
            "viewpoints": [],
            "extraction_warnings": [],
            "extraction_error": raw[MODEL_ERROR_RESULT_KEY],
        }
    cleaned, warnings = validate_extraction(raw, article["article_text"], sentences)
    return {**cleaned, "extraction_warnings": warnings, "extraction_error": None}


def public_article(article: dict[str, Any]) -> dict[str, Any]:
    """Remove full copyrighted article text from the compact result artifact."""
    return {key: value for key, value in article.items() if key != "article_text"}


def claim_summary_fields(articles: list[dict[str, Any]]) -> dict[str, int]:
    """Return verdict totals shared by fresh runs and offline UI exports."""
    claims = [claim for article in articles for claim in article.get("claims", [])]
    verified_claims = [
        claim for claim in claims if claim.get("verification", {}).get("verdict")
    ]
    return {
        "claims_checked": sum(
            claim["verification"]["verdict"]
            in {"supported", "partially_supported", "contradicted"}
            for claim in verified_claims
        ),
        "claims_supported": sum(
            claim["verification"]["verdict"] == "supported"
            for claim in verified_claims
        ),
        "claims_partially_supported": sum(
            claim["verification"]["verdict"] == "partially_supported"
            for claim in verified_claims
        ),
        "claims_contradicted": sum(
            claim["verification"]["verdict"] == "contradicted"
            for claim in verified_claims
        ),
        "claims_not_verifiable": sum(
            claim["verification"]["verdict"] == "not_verifiable"
            for claim in verified_claims
        ),
        "claims_review_required": sum(
            bool(claim.get("verification", {}).get("review_required"))
            for claim in claims
        ),
        "articles_scored": sum(
            article.get("fact_check", {}).get("grounding_score") is not None
            for article in articles
        ),
    }


def build_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# News Policy-Grounding Prototype",
            "",
            "> The JSON and UI-facing CSV contain the same article analysis.",
            "",
            "## Input summary",
            "",
            f"- CSV rows loaded: {summary['article_count']}",
            f"- Rows with article text: {summary['with_text']}",
            f"- Missing-text rows: {summary['missing_text']}",
            f"- Rows marked as reposts: {summary['reposts']}",
            f"- Canonical story clusters: {summary['canonical_count']}",
            f"- Duplicate rows collapsed: {summary['duplicates_collapsed']}",
            f"- Stories extracted: {summary['stories_extracted']}",
            f"- Stories blocked by the content filter: {summary['stories_content_filtered']}",
            f"- Stories skipped after invalid model output: {summary['stories_model_errors']}",
            f"- Atomic claims extracted: {summary['claims_extracted']}",
            f"- Attributed viewpoints extracted: {summary['viewpoints_extracted']}",
            f"- Invalid model items dropped: {summary['items_dropped']}",
            f"- Policy claims checked: {summary['claims_checked']}",
            f"- Claims whose verification was content-filtered: {summary['claims_verification_filtered']}",
            f"- Claims whose verification had invalid model output: {summary['claims_verification_errors']}",
            f"- Supported claims: {summary['claims_supported']}",
            f"- Partially supported claims: {summary['claims_partially_supported']}",
            f"- Contradicted claims: {summary['claims_contradicted']}",
            f"- Not-verifiable claims: {summary['claims_not_verifiable']}",
            f"- Claims marked for analyst review: {summary['claims_review_required']}",
            f"- Articles scored: {summary['articles_scored']}",
            f"- UI CSV article rows: {summary['canonical_count']}",
            "",
        ]
    )


def write_result_artifacts(output_dir: Path, results: dict[str, Any]) -> None:
    """Write matching audit, UI, and concise report artifacts."""
    write_json(output_dir / "results.json", results)
    write_articles_csv(output_dir / "results.csv", results["articles"])
    write_ui_exports(output_dir, results["articles"])
    atomic_write(output_dir / "report.md", build_report(results["summary"]))
    validate_result_artifacts(output_dir)


def read_completed_results(output_dir: Path) -> dict[str, Any]:
    """Load and minimally validate a completed result artifact."""
    results_path = output_dir / "results.json"
    if not results_path.is_file():
        raise FileNotFoundError(f"completed results JSON does not exist: {results_path}")
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"completed results JSON is invalid: {error}") from error
    if not isinstance(results, dict) or not isinstance(results.get("articles"), list):
        raise ValueError("completed results JSON must contain an articles list")
    if not isinstance(results.get("summary"), dict):
        raise ValueError("completed results JSON must contain a summary object")
    return results


def allow_large_csv_fields() -> None:
    """Raise Python's CSV field limit for nested claim/evidence payloads."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def validate_result_artifacts(output_dir: Path) -> dict[str, int]:
    """Check the UI CSV against the authoritative completed JSON."""
    results = read_completed_results(output_dir)
    articles = results["articles"]
    summary = results["summary"]
    csv_path = output_dir / "results.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(f"UI results CSV does not exist: {csv_path}")

    allow_large_csv_fields()
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != UI_CSV_FIELDS:
                raise ValueError("UI results CSV columns do not match the data contract")
            rows = list(reader)
    except csv.Error as error:
        raise ValueError(f"UI results CSV is invalid: {error}") from error

    article_ids = [article.get("article_id") for article in articles]
    row_ids = [row.get("article_id") for row in rows]
    if any(not value for value in article_ids) or len(set(article_ids)) != len(article_ids):
        raise ValueError("results JSON contains blank or duplicate article IDs")
    if any(not value for value in row_ids) or len(set(row_ids)) != len(row_ids):
        raise ValueError("UI results CSV contains blank or duplicate article IDs")
    if len(rows) != len(articles) or set(row_ids) != set(article_ids):
        raise ValueError("UI results CSV article rows do not match results JSON")
    if summary.get("canonical_count") != len(articles):
        raise ValueError("results JSON canonical count does not match its article list")

    rows_by_id = {row["article_id"]: row for row in rows}
    for article in articles:
        article_identifier = article["article_id"]
        actual = rows_by_id[article_identifier]
        expected = article_csv_row(article)
        for field in UI_CSV_FIELDS:
            if field in UI_CSV_JSON_FIELDS:
                try:
                    actual_value = json.loads(actual[field])
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{article_identifier} has invalid JSON in {field}: {error}"
                    ) from error
                expected_value = json.loads(expected[field])
                if actual_value != expected_value:
                    raise ValueError(
                        f"{article_identifier} {field} does not match results JSON"
                    )
            else:
                expected_value = "" if expected[field] is None else str(expected[field])
                if actual[field] != expected_value:
                    raise ValueError(
                        f"{article_identifier} {field} does not match results JSON"
                    )

    computed_summary = claim_summary_fields(articles)
    for field, value in computed_summary.items():
        if summary.get(field) != value:
            raise ValueError(f"results JSON summary field {field} is inconsistent")
    claim_count = sum(len(article.get("claims", [])) for article in articles)
    viewpoint_count = sum(len(article.get("viewpoints", [])) for article in articles)
    if summary.get("claims_extracted") != claim_count:
        raise ValueError("results JSON claim total is inconsistent")
    if summary.get("viewpoints_extracted") != viewpoint_count:
        raise ValueError("results JSON viewpoint total is inconsistent")

    citation_count = sum(
        len(claim.get("verification", {}).get("evidence", []))
        for article in articles
        for claim in article.get("claims", [])
    )
    validate_ui_exports(output_dir, articles)
    return {
        "article_rows": len(rows),
        "columns": len(UI_CSV_FIELDS),
        "claims": claim_count,
        "viewpoints": viewpoint_count,
        "citations": citation_count,
        "blank_grounding_scores": sum(
            row["grounding_score"] == "" for row in rows
        ),
    }


def export_existing_results(output_dir: Path) -> dict[str, Any]:
    """Build UI artifacts from a completed results.json without model calls."""
    results = read_completed_results(output_dir)
    results["summary"].update(claim_summary_fields(results["articles"]))
    write_result_artifacts(output_dir, results)
    return results


def run(
    input_path: Path,
    output_dir: Path,
    limit: int | None = None,
    *,
    extract: bool = False,
    verify: bool = False,
    client: Any | None = None,
    model: str | None = None,
    index: Any | None = None,
) -> dict[str, Any]:
    extract = extract or verify
    articles = load_articles(input_path, limit)
    canonical_articles = cluster_articles(articles)

    for article in canonical_articles:
        article.update(
            {
                "extraction_status": "not_run" if article["has_text"] else "missing_text",
                "claims": [],
                "viewpoints": [],
                "extraction_warnings": [],
                "extraction_error": None,
                "verification_warnings": [],
            }
        )
        if extract and article["has_text"]:
            extracted = extract_article(
                article, output_dir / "cache", client=client, model=model
            )
            article.update(extracted)
            if article["extraction_error"]:
                if article["extraction_error"]["type"] == "content_filter":
                    article["extraction_status"] = "content_filtered"
                    warning = content_filter_warning(article["extraction_error"])
                else:
                    article["extraction_status"] = "model_error"
                    details = article["extraction_error"]
                    if details["type"] == "request_timeout":
                        warning = details["message"]
                    else:
                        response_status = details.get("response_status") or "unknown"
                        incomplete_reason = details.get("incomplete_reason") or "unknown"
                        warning = (
                            "Azure OpenAI returned invalid JSON twice "
                            f"(status={response_status}; reason={incomplete_reason}; "
                            f"{details['message']})"
                        )
                print(
                    f"WARNING {article['article_id']}: extraction: {warning}; "
                    "the article was left unmodified and skipped"
                )
            else:
                article["extraction_status"] = "complete"
            for warning in extracted["extraction_warnings"]:
                print(f"WARNING {article['article_id']}: {warning}")

    if verify:
        if index is None:
            from evidence_index import EvidenceIndex

            index = EvidenceIndex.load()
        from verify_claims import verify_articles

        stories_to_verify = sum(
            article["extraction_status"] == "complete"
            for article in canonical_articles
        )
        print(
            f"Extraction complete. Starting verification for "
            f"{stories_to_verify} stories...",
            flush=True,
        )
        verify_articles(
            canonical_articles,
            index,
            output_dir / "cache",
            call_model_json,
            client=client,
            model=model,
        )
        for article in canonical_articles:
            for warning in article.get("verification_warnings", []):
                print(f"WARNING {article['article_id']}: verification: {warning}")

    summary = {
        "article_count": len(articles),
        "with_text": sum(article["has_text"] for article in articles),
        "missing_text": sum(not article["has_text"] for article in articles),
        "reposts": sum(article["is_repost"] for article in articles),
        "canonical_count": len(canonical_articles),
        "duplicates_collapsed": len(articles) - len(canonical_articles),
        "stories_extracted": sum(
            article["extraction_status"] == "complete" for article in canonical_articles
        ),
        "stories_content_filtered": sum(
            article["extraction_status"] == "content_filtered"
            for article in canonical_articles
        ),
        "stories_model_errors": sum(
            article["extraction_status"] == "model_error"
            for article in canonical_articles
        ),
        "claims_extracted": sum(len(article["claims"]) for article in canonical_articles),
        "viewpoints_extracted": sum(
            len(article["viewpoints"]) for article in canonical_articles
        ),
        "items_dropped": sum(
            len(article["extraction_warnings"]) for article in canonical_articles
        ),
        "claims_verification_filtered": sum(
            claim.get("verification", {}).get("status") == "content_filtered"
            for article in canonical_articles
            for claim in article["claims"]
        ),
        "claims_verification_errors": sum(
            claim.get("verification", {}).get("status") == "model_error"
            for article in canonical_articles
            for claim in article["claims"]
        ),
        **claim_summary_fields(canonical_articles),
    }
    from verify_claims import POLICY_TIMELINE

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(input_path.resolve()),
        "input_sha256": file_hash(input_path),
        "policy_timeline": POLICY_TIMELINE,
        "summary": summary,
        "articles": [public_article(article) for article in canonical_articles],
    }
    write_result_artifacts(output_dir, results)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--extract", action="store_true", help="call Azure to extract claims and viewpoints"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="extract claims, retrieve policy evidence, and verify them with Azure",
    )
    parser.add_argument(
        "--export-existing",
        action="store_true",
        help="rebuild results.csv and report.md from an existing completed results.json",
    )
    parser.add_argument(
        "--validate-export",
        action="store_true",
        help="validate the completed results.csv against results.json without model calls",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate_export:
            validation = validate_result_artifacts(args.output_dir)
            print(
                f"Validated {validation['article_rows']} article rows, "
                f"{validation['columns']} columns, {validation['claims']} claims, "
                f"{validation['viewpoints']} viewpoints, and "
                f"{validation['citations']} evidence citations."
            )
            return 0
        if args.export_existing:
            results = export_existing_results(args.output_dir)
        else:
            results = run(
                args.input,
                args.output_dir,
                args.limit,
                extract=args.extract,
                verify=args.verify,
            )
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}")
        return 1
    summary = results["summary"]
    print(
        f"Loaded {summary['article_count']} rows into {summary['canonical_count']} "
        f"story clusters; extracted {summary['claims_extracted']} claims and "
        f"{summary['viewpoints_extracted']} viewpoints; checked "
        f"{summary['claims_checked']} policy claims. Results: {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
