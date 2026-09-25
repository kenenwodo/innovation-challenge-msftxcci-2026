# PolicyLens

**Evidence-grounded regulatory intelligence connecting policy text, public response, and news coverage.**

PolicyLens is a research prototype built for the Microsoft x CCI 2026 Innovation Challenge. It brings together five evidence streams that are usually reviewed separately:

- official proposed and final rule text;
- structured, source-linked policy provisions and version changes;
- de-identified public-comment analysis;
- news coverage and policy-grounded claim verification; and
- video transcripts and claim-level checks against the rule text.

The current case study follows DHS/ICE rulemaking for F, J, and I nonimmigrants:

| Source | Identifier |
| --- | --- |
| Docket | `ICEB-2025-0001` |
| Regulations.gov document | `ICEB-2025-0001-0001` |
| Proposed rule | Federal Register document `2025-16554` |
| Final rule | Federal Register document `2026-14439` |

> [!IMPORTANT]
> PolicyLens supports research and analyst review. Its model-generated interpretations, classifications, and summaries are not legal advice, official agency findings, or substitutes for the underlying Federal Register text.

## How the system fits together

```text
Regulations.gov                       Federal Register
      |                                      |
      v                                      v
Document + comments                  Official policy text
      |                               |              |
      v                               v              v
De-identified comment analysis  Policy extraction  Version comparison
      |                               |              |
      +-------------------------------+--------------+
                                      |
                     +----------------+----------------+
                     |                                 |
              News articles                    Video transcripts
                     |                                 |
                     v                                 v
              Claim extraction                  Claim extraction
                     +----------------+----------------+
                                      |
                                      v
                    Policy-grounded evidence, verdicts,
                    citations, confidence, and review flags
```

Outputs are committed as JSON, CSV, and Markdown so they can be inspected without rerunning model- or API-dependent pipelines.

## Current results

The committed artifacts currently contain:

| Evidence stream | Result |
| --- | ---: |
| Comments listed for the example Regulations.gov document | 21,923 |
| Comments in the working corpus | 10,000 |
| Nonblank comments analyzed | 9,999 |
| Consolidated proposed-rule policy areas | 13 |
| Proposed-rule provisions extracted | 27 |
| Final-rule provisions extracted | 24 |
| Policy areas compared | 23 |
| Comparison outcomes | 13 unchanged, 6 modified, 4 added candidates |
| Comparisons passing automated validation | 19 of 23 |
| Comparisons requiring analyst review | 4 of 23 |
| Unique news URLs collected | 112 |
| Canonical news articles fact-checked | 88 |
| News claims / viewpoints / evidence citations | 1,188 / 196 / 3,489 |
| Videos analyzed / claims prepared for the UI | 10 / 111 |

An `added` result is a review candidate, not proof that the language was absent from the earlier rule. See [Responsible use and limitations](#responsible-use-and-limitations).

## Repository structure

```text
.
├── data/                         # Committed rule, docket, and court source snapshots
├── misc/                         # Ingestion utility and shared prototype setup
│   ├── fetch_regulations.py      # Resumable Regulations.gov document/comment ingestion
│   ├── requirements.txt
│   └── pytest.ini
├── policy_analysis/              # Proposed-rule extraction and consolidation
│   ├── src/
│   ├── output/
│   └── requirements.txt
├── policy_html_extraction/       # Proposed-vs-final provision comparison and validation
│   ├── src/
│   ├── output/
│   ├── requirements.txt
│   └── README.md
├── comment_analysis/             # Comment-level classifiers, lexical analysis, and topics
│   ├── comment_sentiment_emotion_toxicity.py
│   ├── comment_topics_bertopic.py
│   ├── comments_no_pii.csv
│   └── README.md
├── news_analysis/                # Collection, evidence indexing, and article fact-checking
│   ├── src/
│   ├── tests/
│   ├── output/
│   ├── gdelt_wide_search_v2.py
│   └── README.md
└── video_analysis/               # Video discovery, transcription, and claim checking
    ├── transcripts/
    ├── rule_text/
    ├── video_claims_for_ui.csv
    └── README.md
```

The official proposed rule, final rule, and court-order snapshots used by the evidence pipelines are committed under `data/`. Runtime caches, local environments, logs, and secrets remain excluded by `.gitignore`.

## Start with the committed outputs

You do not need API credentials or a GPU to inspect the existing results.

| Artifact | Purpose |
| --- | --- |
| [`policy_analysis/output/proposed_policies_final.json`](policy_analysis/output/proposed_policies_final.json) | Application-ready proposed-rule policy areas |
| [`policy_analysis/output/proposed_policies_final.md`](policy_analysis/output/proposed_policies_final.md) | Human-readable proposed-rule analysis |
| [`policy_html_extraction/output/policy_changes_validated.json`](policy_html_extraction/output/policy_changes_validated.json) | Validated proposed-vs-final comparison |
| [`policy_html_extraction/output/policy_changes_validated.md`](policy_html_extraction/output/policy_changes_validated.md) | Human-readable comparison with review flags |
| [`comment_analysis/comments_analysis_output.csv`](comment_analysis/comments_analysis_output.csv) | Comment-level sentiment, emotion, and toxicity scores |
| [`comment_analysis/bertopic_topic_info.csv`](comment_analysis/bertopic_topic_info.csv) | Topic counts, keywords, and representative comments |
| [`comment_analysis/comments_with_bertopic.csv`](comment_analysis/comments_with_bertopic.csv) | Comment-level topic assignments |
| [`comment_analysis/top_words_and_phrases.csv`](comment_analysis/top_words_and_phrases.csv) | Frequent terms and phrases |
| [`comment_analysis/tfidf_top_terms_overall.csv`](comment_analysis/tfidf_top_terms_overall.csv) | Corpus-level TF-IDF terms |
| [`news_analysis/gdelt_news_articles_v3-112.csv`](news_analysis/gdelt_news_articles_v3-112.csv) | Deduplicated news metadata and retrieved article text |
| [`news_analysis/output/fact_check/articles_ui.csv`](news_analysis/output/fact_check/articles_ui.csv) | Presentation-ready article scores and summary counts |
| [`news_analysis/output/fact_check/results.json`](news_analysis/output/fact_check/results.json) | Complete claim, viewpoint, verdict, warning, and citation records |
| [`news_analysis/output/fact_check/report.md`](news_analysis/output/fact_check/report.md) | Human-readable news fact-check summary |
| [`video_analysis/video_claims_for_ui.csv`](video_analysis/video_claims_for_ui.csv) | Privacy-cleaned video claims, verdicts, currency, and citations |

## Setup

Create one virtual environment from the repository root:

```bash
python -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

Or on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the shared and policy-pipeline dependencies:

```bash
python -m pip install -r misc/requirements.txt
python -m pip install -r policy_analysis/requirements.txt
python -m pip install -r policy_html_extraction/requirements.txt
```

The comment-analysis pipelines additionally use PyTorch, Transformers, scikit-learn, BERTopic, Sentence Transformers, UMAP, HDBSCAN, Matplotlib, NumPy, and tqdm. Install a PyTorch build appropriate for your CPU or CUDA environment before running them. The video pipeline additionally uses `yt-dlp`, `faster-whisper`, and Sentence Transformers; see its module README for platform-specific guidance.

## Configuration

Create a root `.env` file for the proposed-rule pipeline and for the documented Regulations.gov command below:

```env
REGULATIONS_API_KEY=your_regulations_gov_key
AZURE_OPENAI_ENDPOINT=your_azure_openai_endpoint
AZURE_OPENAI_API_KEY=your_azure_openai_key
AZURE_OPENAI_MODEL=gpt-4.1-mini
```

The version-comparison scripts load Azure settings from `policy_html_extraction/.env`, so copy the three `AZURE_OPENAI_*` entries there as well.

Never commit either `.env` file. The ingestion utility otherwise looks for `misc/.env`; the command below explicitly points it to the root file. If no Regulations.gov key is configured, it falls back to `DEMO_KEY`, which has strict rate limits.

## Run the pipelines

### 1. Fetch a rule document and public comments

The ingestion script downloads document metadata and attachments, pages through all comment IDs, filters attachment-only or empty submissions, and writes resumable JSONL plus CSV outputs.

```bash
python misc/fetch_regulations.py ICEB-2025-0001-0001 --env-file .env
```

Useful options:

```text
--out-dir PATH         Write to a custom directory
--allow-attachments    Keep substantive text even when a file is attached
--limit N              Process at most N new comments
--export-csv           Rebuild the CSV without API calls
--api-key KEY          Override REGULATIONS_API_KEY
--env-file PATH        Load a different environment file
```

By default, files are written to `data/<document_id>/`. Completed comment IDs are skipped on later runs.

### 2. Extract proposed-rule policies

Place the proposed-rule PDF at `policy_analysis/data/2025-16554.pdf`, then run from the repository root:

```bash
python policy_analysis/src/extract_pdf.py
python policy_analysis/src/extract_policies.py
python policy_analysis/src/consolidate_policies.py
```

The first step is local. The extraction and consolidation steps call the configured Azure-hosted model and may incur usage costs. The final machine-readable artifact is `policy_analysis/output/proposed_policies_final.json`.

### 3. Compare proposed and final rules

Place the official V1 and V2 HTML files in `policy_html_extraction/data/` as `v1_official.html` and `v2_official.html`. Then:

```bash
cd policy_html_extraction
python src/extract_html.py v1
python src/extract_html.py v2
python src/extract_provisions.py v1
python src/extract_provisions.py v2
python src/generate_markdown.py v1
python src/generate_markdown.py v2
python src/compare_versions.py
python src/validate_changes.py
python src/generate_comparison_markdown.py
```

See [`policy_html_extraction/README.md`](policy_html_extraction/README.md) for the evidence-verification rules, output schema, and validation behavior.

### 4. Analyze de-identified comments

Run from `comment_analysis/` using the committed `comments_no_pii.csv` input:

```bash
python comment_sentiment_emotion_toxicity.py --input comments_no_pii.csv
python comment_topics_bertopic.py --input comments_no_pii.csv
```

The first script runs sentiment, emotion, and toxicity classifiers and automatically uses CUDA when available. The second produces TF-IDF, NMF, and BERTopic results; LLooM is optional and requires `OPENAI_API_KEY` plus the `--lloom` flag.

Use `--help` on either script for output-directory, batch-size, and skip options. See [`comment_analysis/README.md`](comment_analysis/README.md) for the output columns.

### 5. Collect and fact-check news coverage

Run from `news_analysis/`:

```bash
python gdelt_wide_search_v2.py
```

Use `--no-text` to collect metadata without retrieving article pages, or `--refresh` to ignore saved search checkpoints. The collector is deliberately paced for GDELT and caches article text so interrupted runs can resume.

From the repository root, rebuild the evidence index and run the policy-grounded fact checker:

```bash
python news_analysis/src/build_evidence_index.py
python news_analysis/src/eval_retrieval.py
python news_analysis/src/run_fact_checker.py --verify
python news_analysis/src/flatten_results_csv.py
python -m pytest -c misc/pytest.ini news_analysis/tests -q
```

The fact checker extracts claims and viewpoints, retrieves proposed-rule, final-rule, and court evidence with BM25, and preserves citations plus unresolved/review states. To regenerate exports from an existing completed run without model calls, use `python news_analysis/src/run_fact_checker.py --export-existing`. See [`news_analysis/README.md`](news_analysis/README.md) for the UI contract and audit artifacts.

### 6. Analyze video claims

Run from `video_analysis/`:

```bash
python find_videos.py
python transcribe_videos.py --file video_urls.txt
python claim_check.py transcripts/transcripts.csv
```

Discovery is optional. Transcription runs locally with Whisper; claim extraction and verdict generation use the configured Azure model. The presentation-facing output is `video_claims_for_ui.csv`, which removes email addresses and phone numbers and separates accuracy at publication from whether a supported claim is still current. See [`video_analysis/README.md`](video_analysis/README.md) for setup, output fields, and review guidance.

## Design principles

- **Evidence before summary:** policy interpretations retain source sections, quotes, and document identifiers.
- **Official and derived data stay separate:** source text, model interpretation, semantic comparison, and validation status are distinct fields and files.
- **Uncertainty is visible:** exact-evidence failures and uncertain additions are retained with review flags instead of silently discarded.
- **Human review is part of the system:** automated checks support, but do not replace, policy or legal judgment.
- **Privacy is a pipeline boundary:** comment models consume `comment_clean`, and the video UI consumes the privacy-cleaned claim export rather than internal debugging output.

## Responsible use and limitations

- The policy outputs are AI-assisted interpretations. Confirm conclusions against the official Federal Register documents.
- An exact-text evidence check can fail because of spacing, formatting, or a quote assembled from nearby passages; failure means review is required, not necessarily that the interpretation is wrong.
- Version comparison is only as complete as the extracted provisions. `added` and `removed` candidates require source-level review.
- PII removal is not guaranteed to catch every identifier. Audit de-identified comments before publishing or sharing them beyond the intended research context.
- Sentiment, emotion, toxicity, and topic labels are model estimates, not objective properties of commenters or their positions. Aggregate results and inspect representative examples.
- GDELT results reflect the configured queries, dates, indexing, and article availability; they are not a complete census of coverage.
- News verdicts depend on claim extraction and the available evidence index. Unresolved, conflicting, and review-required states must remain visible in downstream interfaces.
- The video analysis is a small English-language sample. Transcripts, claim boundaries, verdicts, and currency labels require analyst review.

## Technology

Python, pandas, PyTorch, Hugging Face Transformers, BERTopic, scikit-learn, Sentence Transformers, Whisper, BM25, BeautifulSoup, GDELT DOC API, Regulations.gov API, Federal Register source documents, Azure OpenAI, Pydantic, JSON, CSV, and Markdown.
