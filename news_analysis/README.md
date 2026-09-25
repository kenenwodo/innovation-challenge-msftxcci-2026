# News Analysis and Policy-Grounding Artifacts

This directory contains the news collection, evidence retrieval, AI-assisted
claim analysis, and export files for the duration-of-status policy demo. Start
with the four normalized CSVs below when integrating the results into the UI.

## UI handoff

The UI needs only these four files from `output/fact_check/`:

| File | Rows | What it provides | Join key |
|---|---:|---|---|
| `articles_ui.csv` | 20 | Article cards, policy stage, score, status, and summary counts. | `article_id` |
| `claims_ui.csv` | 93 | Up to six featured policy-related claims for each article. | `article_id`, `claim_id` |
| `claim_evidence_ui.csv` | 354 | Citations and source excerpts for featured claims. | `claim_id` |
| `viewpoints_ui.csv` | 40 | Attributed stakeholder viewpoints for each article. | `article_id` |

Start with `articles_ui.csv`. Use `article_id` to attach claims and viewpoints,
then use `claim_id` to attach evidence to each claim. The files are already in
display order. The 20 articles are a deterministic sample of the complete
88-article analysis; use the audit artifacts below when the full dataset is
needed.

### `articles_ui.csv` columns

| Column | Meaning |
|---|---|
| `article_id` | Stable article key used to join claims and viewpoints. |
| `title` | Canonical article headline. |
| `article_url` | Link to the source article. |
| `publisher_domain` | Publisher domain shown as source context. |
| `seen_at` | GDELT collection timestamp in ISO 8601 format; this is not a verified publication timestamp. |
| `publisher_country` | Source-country metadata. |
| `policy_stage` | `proposal_docket`, `proposed_rule`, `comment_deadline`, `final_rule`, or `injunction`. |
| `grounding_score` | Weighted 0–100 score; blank when no score applies. |
| `analysis_status` | `scored`, `no_policy_claims`, `not_scored`, or `unavailable`. |
| `policy_claims_total` | Policy-related claims considered for scoring. |
| `policy_claims_scored` | Claims assigned a supported, partially supported, or contradicted verdict and included in the score. |
| `supported_claims` | Claims supported by indexed evidence. |
| `partially_supported_claims` | Claims only partly supported by indexed evidence. |
| `contradicted_claims` | Claims contradicted by indexed evidence. |
| `unresolved_claims` | Claims the available indexed evidence could not resolve. |
| `claims_needing_review` | Claims flagged for analyst review. |

### `claims_ui.csv` columns

Each article has at most six featured claims, in `claim_rank` order.

| Column | Meaning |
|---|---|
| `article_id` | Parent article key. |
| `claim_id` | Stable claim key within the current extraction. |
| `claim_rank` | Display order from 1 to 6. |
| `claim_text` | Normalized atomic claim. |
| `claim_category` | `policy`, `government_position`, or `interpretation`. |
| `speaker` | Attributed speaker when present. |
| `organization` | Attributed organization when present. |
| `verdict` | `supported`, `partially_supported`, `contradicted`, or `unresolved`. |
| `verdict_explanation` | Evidence-based explanation of the verdict. |
| `needs_review` | Whether an analyst should review the claim. |

### `claim_evidence_ui.csv` columns

| Column | Meaning |
|---|---|
| `claim_id` | Parent claim key. |
| `evidence_rank` | Evidence display order within the claim. |
| `citation` | Regulation, Federal Register, or court citation. |
| `evidence_type` | Regulatory text, agency explanation, court text, or another source type. |
| `source_url` | Direct source URL when available. |
| `evidence_excerpt` | Source passage used to support or contradict the claim. |

### `viewpoints_ui.csv` columns

| Column | Meaning |
|---|---|
| `article_id` | Parent article key. |
| `viewpoint_id` | Stable viewpoint key within the current extraction. |
| `speaker` | Attributed person or group. |
| `organization` | Associated organization when present. |
| `stance` | `support`, `oppose`, `mixed`, `neutral`, or `unclear`. |
| `affected_provision` | Policy provision addressed by the viewpoint. |
| `reason` | Attributed reason for the stance. |
| `source_quote` | Exact article excerpt supporting the attribution. |

### Display rules

- A blank `grounding_score` means **not scored**, not `0%`.
- `analysis_status` is one of `scored`, `no_policy_claims`, `not_scored`, or
  `unavailable`; use it to render the appropriate score or empty state.
- Show score coverage with `policy_claims_scored` and `policy_claims_total`, for
  example, “8 of 10 policy-related claims included.”
- `seen_at` is the GDELT collection timestamp, not a verified publication time.
- Claim verdicts are `supported`, `partially_supported`, `contradicted`, or
  `unresolved`. Viewpoint stances are `support`, `oppose`, `mixed`, `neutral`,
  or `unclear`.
- Use a standards-compliant CSV parser because quoted excerpts may contain
  commas and newlines. Treat IDs as strings and preserve the supplied row order.

## Audit and evidence artifacts

### Complete fact-check outputs

The normalized UI files are a curated presentation layer. Use these files for
complete analysis, debugging, or audit work:

| File | Current rows | Purpose |
|---|---:|---|
| `output/fact_check/results.json` | 88 articles | Lossless machine-readable result, including all claims, viewpoints, verdicts, warnings, and cited evidence. |
| `output/fact_check/results.csv` | 88 articles | One row per canonical article with scalar UI fields and nested JSON detail cells. |
| `output/fact_check/results_flattened.csv` | 4,079 records | Long-form audit CSV with separate article, claim/evidence, and viewpoint rows and no nested JSON cells. |
| `output/fact_check/report.md` | — | Human-readable corpus and verdict summary. |

The committed result summarizes 112 collected rows into 88 canonical stories:
1,188 extracted claims, 196 attributed viewpoints, and 3,489 evidence
citations. The normalized UI subset is intentionally smaller.

In `results.csv`, a blank `grounding_score` is non-applicable, not zero.
`claims_json`, `viewpoints_json`, and warning/error columns contain JSON and
must be decoded after CSV parsing. An opinion, prediction, interpretation, or
outside fact can have a null verdict because it is outside the policy-checking
scope. `not_verifiable` instead means the claim was checkable but the indexed
evidence did not support a verdict.

### Evidence index outputs

`output/evidence/` contains the locally built retrieval corpus:

| File | Purpose |
|---|---|
| `proposed_rule_sections.jsonl` | Structured proposed-rule sections. |
| `proposed_rule_sections.md` | Human-readable rendering of the proposed-rule section tree. |
| `final_rule_sections.jsonl` | Structured final-rule sections. |
| `final_rule_sections.md` | Human-readable rendering of the final-rule section tree. |
| `evidence_paragraphs.jsonl` | Citable proposed-rule, final-rule, and court passages with provenance and evidence tiers. |
| `key_facts_verified.json` | Hand-curated key facts matched back to source passages. |
| `rule_versions.json` | Proposed/final rule identifiers and publication/effective-date metadata. |

### Provenance and interpretation boundaries

- The Federal Register rules and court filings under the repository-level
  `data/` directory are the authoritative sources. Extracted evidence files
  preserve citations and source text for retrieval but do not replace those
  official documents.
- `gdelt_news_articles_v3-112.csv` contains factual reporting collected from
  third-party publishers. Its `seendate` is a collection timestamp, and a
  publisher's statement is not converted into policy fact merely because it
  appears in an article.
- Viewpoints are attributed public or stakeholder positions supported by exact
  article excerpts. They are not treated as representative polling data.
- Extracted claims, verdict explanations, grounding scores, and review flags
  are AI-assisted interpretations. Conflicting evidence and unresolved claims
  remain visible, and a human analyst is responsible for final conclusions.

## Folder structure

```text
news_analysis/
├── README.md
├── gdelt_news_articles_v3-112.csv   # committed 112-row news input snapshot
├── gdelt_wide_search_v2.py          # optional live GDELT/article collector
├── key_facts.json                    # hand-curated facts checked against sources
├── retrieval_cases.json              # evidence-retrieval evaluation cases
├── src/
│   ├── build_evidence_index.py       # build source-derived evidence artifacts
│   ├── evidence_index.py             # BM25 retrieval implementation and CLI
│   ├── eval_retrieval.py             # retrieval evaluation
│   ├── run_fact_checker.py           # extraction, verification, exports, validation
│   ├── verify_claims.py              # evidence-aware claim verification
│   ├── export_ui_csvs.py             # normalized four-file UI export
│   └── flatten_results_csv.py        # readable long-form audit export
├── tests/                             # pipeline and export tests
└── output/
    ├── evidence/                      # committed source-derived retrieval artifacts
    └── fact_check/
        ├── articles_ui.csv            # committed UI artifacts
        ├── claims_ui.csv
        ├── claim_evidence_ui.csv
        ├── viewpoints_ui.csv
        ├── results.json               # committed full audit artifacts
        ├── results.csv
        ├── results_flattened.csv
        ├── report.md
        └── cache/                     # local model-response cache; ignored, not committed
```

The optional collector also creates `news_analysis/gdelt_checkpoint/` and
`news_analysis/gdelt_run.log`. They are resumability/debug artifacts and are
ignored by Git.

Evidence regeneration imports the shared parser at
`policy_analysis/src/extract_html.py` and expects the proposed rule, final rule,
rule metadata, court metadata, and court PDFs under the repository-level
`data/` tree. This directory is therefore exported as part of the complete
repository, not as a standalone application.

## Setup and sequential regeneration

Run the following commands from the repository root unless a step explicitly
changes directories.

### 1. Create the virtual environment and install dependencies

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

All project Python commands should use `.venv/bin/python`.

### 2. Configure Azure OpenAI for model-backed runs

Create a repository-root `.env` file. It is ignored by Git.

```env
AZURE_OPENAI_ENDPOINT=YOUR_ENDPOINT
AZURE_OPENAI_API_KEY=YOUR_API_KEY
AZURE_OPENAI_MODEL=YOUR_DEPLOYMENT_NAME
```

`AZURE_OPENAI_MAX_OUTPUT_TOKENS` and `AZURE_OPENAI_TIMEOUT_SECONDS` are optional
tuning variables. Never commit credentials.

### 3. Optionally refresh the news corpus

Skip this step to reproduce downstream files from the committed input snapshot.
The collector uses paths relative to `news_analysis`, so run it from that
directory:

```bash
(cd news_analysis && ../.venv/bin/python gdelt_wide_search_v2.py)
```

Useful options are `--no-text` to skip article-page extraction and `--refresh`
to ignore saved search checkpoints. The default run reuses local checkpoints
and article-text cache entries. GDELT results, current date windows, source-page
availability, and Internet Archive responses can change, so a live refresh is
not expected to reproduce the committed CSV byte for byte.

### 4. Build the evidence index

Confirm that the shared `data/` source files described above are present, then
run:

```bash
.venv/bin/python news_analysis/src/build_evidence_index.py
```

This parses both rule versions and the court ruling, writes all seven files in
`output/evidence/`, and fails when a hand-curated key-fact quotation cannot be
matched to an allowed source tier.

### 5. Evaluate retrieval

```bash
.venv/bin/python news_analysis/src/eval_retrieval.py
```

Add `--show-misses` to inspect missed evaluation cases. This step is diagnostic
and does not modify the evidence index.

### 6. Run full extraction and verification

```bash
.venv/bin/python news_analysis/src/run_fact_checker.py --verify
```

This reads `gdelt_news_articles_v3-112.csv`, clusters duplicate stories,
extracts claims and viewpoints, retrieves evidence, asks Azure OpenAI for
structured verdicts, and writes the complete and normalized fact-check
artifacts. Responses are cached by prompt, schema, and model under
`output/fact_check/cache/`. A rerun reuses matching cached responses; only cache
misses make model calls and consume Azure resources. Live model reruns can vary
from the committed snapshot.

For a small model-backed trial run, add `--limit N`. Do not use a limited run
when producing the final committed artifacts.

### 7. Rebuild presentation exports without model calls

When `output/fact_check/results.json` already contains a completed run, rebuild
`results.csv`, the report, and all four normalized UI CSVs without calling the
model:

```bash
.venv/bin/python news_analysis/src/run_fact_checker.py --export-existing
```

This is the preferred handoff-regeneration path for the committed result. To
create a different-size UI sample without changing the full audit artifacts:

```bash
.venv/bin/python news_analysis/src/export_ui_csvs.py --max-articles N
```

The committed/default UI contract uses 20 articles.

### 8. Generate the flattened audit CSV

```bash
.venv/bin/python news_analysis/src/flatten_results_csv.py
```

This expands every claim/evidence and viewpoint entry from `results.csv` into
readable long-form rows in `results_flattened.csv`.

### 9. Validate and test

Validate the committed result and every normalized UI join without model calls:

```bash
.venv/bin/python news_analysis/src/run_fact_checker.py --validate-export
```

Run the test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider news_analysis/tests -q
```

For the current committed snapshot, validation reports 88 article rows, 32
audit columns, 1,188 claims, 196 viewpoints, and 3,489 evidence citations; the
test suite contains 29 passing tests.
