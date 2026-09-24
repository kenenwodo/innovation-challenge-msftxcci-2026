#!/usr/bin/env python3
"""Lean batch pipeline for the news policy-grounding prototype."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
                positions = []
                if isinstance(quote, str) and quote:
                    position = text.find(quote)
                    while position >= 0:
                        positions.append(position)
                        position = text.find(quote, position + 1)
                if not positions:
                    reason = "source quote was not found exactly in the article"
                else:
                    # Scraped stories sometimes repeat a paragraph verbatim. The
                    # first exact occurrence is a deterministic, valid citation.
                    start, end = positions[0], positions[0] + len(quote)
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
        client = OpenAI(base_url=base_url + "/", api_key=api_key)

    prompt = user_prompt
    last_error: Exception | None = None
    for attempt in range(2):
        response = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "fact_checker_response",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        try:
            parsed = json.loads(response.output_text)
            write_json(cache_path, parsed)
            return parsed
        except (AttributeError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 0:
                prompt += "\n\nThe previous response was invalid JSON. Return JSON only."
    raise ValueError(f"model returned invalid JSON twice: {last_error}")


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
    cleaned, warnings = validate_extraction(raw, article["article_text"], sentences)
    return {**cleaned, "extraction_warnings": warnings}


def public_article(article: dict[str, Any]) -> dict[str, Any]:
    """Remove full copyrighted article text from the compact result artifact."""
    return {key: value for key, value in article.items() if key != "article_text"}


def build_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# News Policy-Grounding Prototype",
            "",
            "> Session 03 extracts claims, retrieves primary policy evidence, and produces traceable verdicts.",
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
            f"- Atomic claims extracted: {summary['claims_extracted']}",
            f"- Attributed viewpoints extracted: {summary['viewpoints_extracted']}",
            f"- Invalid model items dropped: {summary['items_dropped']}",
            f"- Policy claims checked: {summary['claims_checked']}",
            f"- Supported claims: {summary['claims_supported']}",
            f"- Contradicted claims: {summary['claims_contradicted']}",
            f"- Articles scored: {summary['articles_scored']}",
            "",
        ]
    )


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
            }
        )
        if extract and article["has_text"]:
            extracted = extract_article(
                article, output_dir / "cache", client=client, model=model
            )
            article.update(extracted)
            article["extraction_status"] = "complete"
            for warning in extracted["extraction_warnings"]:
                print(f"WARNING {article['article_id']}: {warning}")

    if verify:
        if index is None:
            from evidence_index import EvidenceIndex

            index = EvidenceIndex.load()
        from verify_claims import verify_articles

        verify_articles(
            canonical_articles,
            index,
            output_dir / "cache",
            call_model_json,
            client=client,
            model=model,
        )

    verified_claims = [
        claim
        for article in canonical_articles
        for claim in article["claims"]
        if claim.get("verification", {}).get("verdict")
    ]

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
        "claims_extracted": sum(len(article["claims"]) for article in canonical_articles),
        "viewpoints_extracted": sum(
            len(article["viewpoints"]) for article in canonical_articles
        ),
        "items_dropped": sum(
            len(article["extraction_warnings"]) for article in canonical_articles
        ),
        "claims_checked": sum(
            claim["verification"]["verdict"]
            in {"supported", "partially_supported", "contradicted"}
            for claim in verified_claims
        ),
        "claims_supported": sum(
            claim["verification"]["verdict"] == "supported" for claim in verified_claims
        ),
        "claims_contradicted": sum(
            claim["verification"]["verdict"] == "contradicted" for claim in verified_claims
        ),
        "articles_scored": sum(
            article.get("fact_check", {}).get("grounding_score") is not None
            for article in canonical_articles
        ),
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
    write_json(output_dir / "results.json", results)
    atomic_write(output_dir / "report.md", build_report(summary))
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
