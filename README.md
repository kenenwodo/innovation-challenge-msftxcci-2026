# PolicyLens

**Evidence-grounded regulatory intelligence connecting policy text, public response, and news coverage.**

**Built on Microsoft Foundry:** policy extraction, version comparison and claim checking run on Azure OpenAI (`gpt-4.1-mini`) deployed in Microsoft Foundry.

| | |
| --- | --- |
| **Challenge** | Policy and Public Sentiment Analyst: turn legislation, regulations, news, and public feedback into transparent, evidence-grounded policy insights |
| **Hackathon** | Microsoft x CCI Innovation Challenge 2026 |

## Project description

Government analysts reviewing a new regulation have to read the rule itself, compare
it with earlier drafts, work through thousands of public comments, and track how news
outlets and online videos describe it, usually by hand, over weeks. PolicyLens brings
these sources into one evidence-grounded view.

For a live case study, the DHS rule replacing "duration of status" for international
students, exchange visitors and foreign media (docket ICEB-2025-0001), PolicyLens:

- **compares the proposed and final rule** provision by provision, showing what was
  unchanged, modified or added, with the exact rule text as evidence;
- **analyzes about 10,000 de-identified public comments** for sentiment, emotion and
  the main concerns people raised, keeping minority viewpoints visible;
- **fact-checks news articles and news videos** claim by claim against the official
  rule text (news also against the court order); for videos it separates whether a
  claim was accurate when published from whether it is still current after the court
  postponed the rule;
- **produces a downloadable analyst briefing** that pulls the findings together with
  citations.

Every output keeps official policy text, what sources said, and AI interpretation
apart, shows confidence and review flags, and leaves the final call to a human analyst.

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
     Regulations.gov                    Federal Register + court order
  docket ICEB-2025-0001             proposed rule (V1) and final rule (V2)
           |                                        |
           v                      +-----------------+-----------------+
    Public comments               |                 |                 |
           |                      v                 v                 v
           v               Policy extraction    News articles     Video news
  De-identified comment    and comparison       (GDELT): claims   (YouTube, Facebook):
  analysis: sentiment,     of V1 and V2         checked against   claims checked
  emotion, topics                 |             V1, V2 and the    against V1 and V2
           |                      |             court order           |
           |                      |                 |                 |
           +----------------------+-----+-----------+-----------------+
                                        |
                                        v
                   PolicyLens UI: evidence, verdicts, citations,
                   confidence and review flags for the analyst
```

The four components run independently. Comments form their own stream. The policy
comparison, news and video components all use both rule versions, V1 and V2, read
directly from the Federal Register; news also uses the court order. Everything meets
in the UI.

Outputs are committed as JSON, CSV, and Markdown so they can be inspected without rerunning model- or API-dependent pipelines.

## Components at a glance

Each component runs on its own and writes a small set of **UI files**: the final
outputs the app loads. Everything else in a component folder is source data,
intermediate output, or audit material.

| Component | What it does | Folder | UI files |
| --- | --- | --- | --- |
| **Policy versions** | Extracts provisions from the proposed rule (V1) and final rule (V2) and compares them: unchanged, modified, added/removed, with evidence, confidence and review flags. | `policy_html_extraction/` | `v1_policies.json`, `v2_policies.json`, `policy_changes_validated.json` |
| **Public comments** | Scores de-identified comments for sentiment, emotion and toxicity, and groups them into topics labeled by primary concern. | `comment_analysis/` | `comments_analysis_output.csv`, `bertopic_topic_info.csv`, `comments_with_bertopic.csv` |
| **News articles** | Collects coverage from GDELT, extracts claims and stakeholder viewpoints, and checks claims against the rules and court order. | `news_analysis/` | `articles_ui.csv`, `claims_ui.csv`, `claim_evidence_ui.csv`, `viewpoints_ui.csv` |
| **Video news** | Finds and transcribes news and explainer videos, then checks each claim against V1 and V2: accuracy when published, plus a Still current / Outdated badge. | `video_analysis/` | `video_claims_for_ui.csv` |
| **Analyst briefing** | Combines the four components into a downloadable PDF briefing with key points, citations and limits (no model calls). | `download_briefing_document/` | `PolicyLens_briefing_ICEB-2025-0001.pdf` |

## What the UI loads

### Policy versions: `policy_html_extraction/output/`

| File | Use in the UI |
| --- | --- |
| `v1_policies.json` | **Proposed rule (V1):** 27 structured provisions. The "what was proposed" view. |
| `v2_policies.json` | **Final rule (V2):** 24 structured provisions after the comment period. The "what the final rule says" view. |
| `policy_changes_validated.json` | **What changed:** 23 policy areas with change type, explanation, practical effect, evidence, confidence and review status (19 validated, 4 need review). |

Markdown versions of all three (`.md`) are in the same folder for reading. Details:
[`policy_html_extraction/README.md`](policy_html_extraction/README.md).

### Public comments: `comment_analysis/`

| File | Use in the UI |
| --- | --- |
| `comments_analysis_output.csv` | **Sentiment and emotion breakdown:** one row per comment (9,999) with `sentiment_label`, `emotion_label`, confidence scores and toxicity. |
| `bertopic_topic_info.csv` | **Topic clusters:** one row per topic. `Primary_Concern` is the readable label to display; `Count` is the cluster size. |
| `comments_with_bertopic.csv` | **Comments per topic:** each comment's `Topic` and `Primary_Concern`; join to the scores on `comment_id`. |

Display `Primary_Concern`, not the raw BERTopic `Name`. Topic `-1` is the
mixed/unclassified group, and topic `7` is comments whose content is in an attached
file. Do not display the `title` column of `comments_analysis_output.csv`: many
titles contain the commenter's name. Details:
[`comment_analysis/README.md`](comment_analysis/README.md).

### News articles: `news_analysis/output/fact_check/`

| File | Use in the UI | Join key |
| --- | --- | --- |
| `articles_ui.csv` | **Article cards:** 20 articles with title, URL, publisher, policy stage, grounding score and claim counts. | `article_id` |
| `claims_ui.csv` | **Claims per article:** up to 6 featured claims each (93) with verdict and explanation. | `article_id`, `claim_id` |
| `claim_evidence_ui.csv` | **Evidence per claim:** citations and source excerpts (354). | `claim_id` |
| `viewpoints_ui.csv` | **Stakeholder viewpoints:** attributed stances with source quotes (40). | `article_id` |

Use `articles_ui.csv` (20 articles) for article titles and URLs, not the 112-row
collection file `gdelt_news_articles_v3-112.csv`. A blank `grounding_score` means
"not scored", not 0%. Details: [`news_analysis/README.md`](news_analysis/README.md).

### Video news: `video_analysis/`

| File | Use in the UI |
| --- | --- |
| `video_claims_for_ui.csv` | **Video claim cards:** one row per claim (111 claims from 10 videos) with speaker type, publish date, timestamp, quote, evidence from V1/V2 with Federal Register links, and two badges: `verdict_at_publish` (Supported, Partly supported, Contradicted, Not in rule text, Different rule, Not checked) and `currency_status` (Still current / Outdated, an AI suggestion the analyst can change). |

Group rows by `video_key` for the video list. The file is privacy-cleaned; the other
files in the folder (`claim_results.csv`, `transcripts/`) are not and are kept only
for reproducibility. Details: [`video_analysis/README.md`](video_analysis/README.md).

### Analyst briefing: `download_briefing_document/`

| File | Use in the UI |
| --- | --- |
| `PolicyLens_briefing_ICEB-2025-0001.pdf` | **"Download briefing" button:** a 6-page PDF with key points, the rule timeline, what changed, comment concerns, news and video accuracy, and limits. |
| `generate_briefing.py` | Regenerates the PDF from the files above; `build_briefing_bytes()` builds it on click for a download button. |

Details: [`download_briefing_document/README.md`](download_briefing_document/README.md).

### Shown across all panels

- Label each layer: **official rule text**, **what a source said** (article, video,
  comment), and **AI interpretation** (verdicts, labels, topics).
- Show review flags and unresolved states rather than hiding them, and keep the final
  call with the analyst.

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
├── video_analysis/               # Video discovery, transcription, and claim checking
│   ├── transcripts/
│   ├── rule_text/
│   ├── video_claims_for_ui.csv
│   └── README.md
└── download_briefing_document/   # Analyst briefing PDF generator
    ├── generate_briefing.py
    ├── PolicyLens_briefing_ICEB-2025-0001.pdf
    └── README.md
```

The official proposed rule, final rule, and court-order snapshots used by the evidence pipelines are committed under `data/`. Runtime caches, local environments, logs, and secrets remain excluded by `.gitignore`.

## Other committed outputs

You do not need API credentials or a GPU to inspect the results. Besides the UI files
above, these are useful for reading, auditing or debugging:

| Artifact | Purpose |
| --- | --- |
| [`policy_analysis/output/proposed_policies_final.json`](policy_analysis/output/proposed_policies_final.json) | Earlier proposed-rule policy areas (13), from the PDF pipeline |
| [`policy_html_extraction/output/policy_changes_validated.md`](policy_html_extraction/output/policy_changes_validated.md) | Human-readable comparison with review flags |
| [`comment_analysis/top_words_and_phrases.csv`](comment_analysis/top_words_and_phrases.csv) | Frequent terms and phrases |
| [`comment_analysis/tfidf_top_terms_overall.csv`](comment_analysis/tfidf_top_terms_overall.csv) | Corpus-level TF-IDF terms |
| [`news_analysis/gdelt_news_articles_v3-112.csv`](news_analysis/gdelt_news_articles_v3-112.csv) | All 112 collected news rows with article text |
| [`news_analysis/output/fact_check/results.json`](news_analysis/output/fact_check/results.json) | Complete news fact-check for all 88 canonical articles |
| [`news_analysis/output/fact_check/report.md`](news_analysis/output/fact_check/report.md) | Human-readable news fact-check summary |
| [`video_analysis/claim_results.csv`](video_analysis/claim_results.csv) | Full internal video results (not privacy-cleaned) |
| [`video_analysis/transcripts/`](video_analysis/transcripts/) | Video transcripts with timestamps |

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

### 7. Build the analyst briefing

From the repository root, after the outputs above exist:

```bash
python -m pip install reportlab pandas
python download_briefing_document/generate_briefing.py
```

Writes `download_briefing_document/PolicyLens_briefing_ICEB-2025-0001.pdf`. No API keys
are needed; every figure is computed from the committed outputs.

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
