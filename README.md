# innovation-challenge-msftxcci-2026

The codebase for our Innovation Challenge Hackathon.

## What `fetch_regulations.py` does

The script uses the [Regulations.gov v4 API](https://open.gsa.gov/api/regulationsgov/) to download a rule document and its public comments, then **filters** comments so downstream work can focus on text-only submissions.

1. **Get the document.** It saves the document metadata and downloads the Federal Register rule files (typically `content.html` and `content.pdf` under `document/`).
2. **List every comment.** For example, document `ICEB-2025-0001-0001` has 21,923 comments. The API returns at most 5,000 results per query, so the script follows the API documentation: after each batch, it starts a new query using `filter[lastModifiedDate][ge]` from the last comment’s modified date until all IDs are collected. IDs are cached in `comment_ids.json`.
3. **Check each comment.** The comment list does not include body text, so the script requests each comment individually and classifies it:
  - **Kept** (`text_comments.jsonl`): comments whose text is real content (not just a pointer to an attachment).
  - **Skipped** (`skipped_comments.jsonl`): comments with an attached file, empty text, text that is only something like “See attached file(s).”, or withdrawn comments.

The filter is **strict by default**. Some commenters attach a file and also write a short cover note, such as “Please see attached file with the Society’s comments…”. Those are skipped unless you pass `--allow-attachments`. With that flag, a comment that has an attachment is kept as long as its text is more than a “see attached” placeholder line.

## Repository structure

```
.
├── fetch_regulations.py   # Fetch a Regulations.gov docket document and public comments
├── AGENTS.md              # Notes for automated agents (e.g. use .venv for Python)
├── .env                   # API key (not committed; see below)
├── .venv/                 # Local Python virtual environment (not committed)
└── data/                  # Downloaded artifacts (not committed)
    └── <document_id>/     # One folder per Regulations.gov document ID
```



## Output artifacts

Running `fetch_regulations.py` writes everything under `data/<document_id>/` (or `--out-dir` if you set it). For each document:


| Path                     | Description                                                                                                                  |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `document.json`          | Document metadata from the [Regulations.gov v4 API](https://open.gsa.gov/api/regulationsgov/), including attachment metadata |
| `document/*`             | Downloaded document files (e.g. `content.html`, `content.pdf`)                                                               |
| `comment_ids.json`       | Cached list of all comment IDs on the document                                                                               |
| `text_comments.jsonl`    | Kept comments, one JSON object per line (plain-text `commentText`, metadata)                                                 |
| `skipped_comments.jsonl` | Skipped comments with a `reason` (withdrawn, placeholder-only text, attachments, etc.)                                       |
| `text_comments.csv`      | Flat CSV export of the kept comments                                                                                         |


Runs are **resumable**: comment IDs already present in `text_comments.jsonl` or `skipped_comments.jsonl` are not fetched again.

## Running `fetch_regulations.py`

1. Create a [Regulations.gov API key](https://open.gsa.gov/api/regulationsgov/) and add it to a `.env` file in the repo root:
  ```bash
   REGULATIONS_API_KEY=your_key_here
  ```
2. Use the project virtual environment and run the script with a document ID (default: `ICEB-2025-0001-0001`):
  ```bash
   python3 -m venv .venv
   .venv/bin/python fetch_regulations.py ICEB-2025-0001-0001
  ```
   Without a document argument, the default ID above is used:



### Useful options

- `--out-dir PATH` — output directory (default: `data/<document_id>`)
- `--allow-attachments` — see [What it does](#what-fetch_regulationspy-does) above
- `--limit N` — process at most N new comments (for testing)
- `--api-key KEY` — override the key from `.env`
- `--env-file PATH` — read the API key from a different `.env` file (default: `.env` next to the script)
- `--export-csv` — only rebuild `text_comments.csv` from the comments downloaded so far, without making any API calls. Don't run it while a fetch is in progress.
  ```bash
  .venv/bin/python fetch_regulations.py --export-csv
  ```

If no key is configured, the script falls back to `DEMO_KEY` (strict rate limits).


---

# Proposed Rule Policy Extraction

The `policy_analysis/` module extracts and analyzes proposed policy changes from the Federal Register proposed rule used in this project.

**Document:** Federal Register 2025-16554
**Docket:** ICEB-2025-0001
**Model:** GPT-4.1-mini through Microsoft Azure AI Foundry

The pipeline converts the proposed-rule PDF into structured policy provisions, including the proposed change, affected groups, restrictions, exceptions, time limits, CFR references, supporting evidence, and source pages.

## How It Works

```text
Federal Register PDF
        ↓
extract_pdf.py
        ↓
Page-aware extracted text
        ↓
extract_policies.py
        ↓
Candidate policy provisions
        ↓
consolidate_policies.py
        ↓
Final policy dataset
```

The final analysis consolidated the extracted provisions into **13 distinct policy areas**.

## Important Files

```text
policy_analysis/
├── data/
│   └── 2025-16554.pdf
│
├── src/
│   ├── extract_pdf.py          # Extracts page-aware text from the PDF
│   ├── extract_policies.py     # Uses GPT-4.1-mini to identify policy changes
│   ├── consolidate_policies.py # Deduplicates and quality-checks policies
│   ├── models.py               # Defines the policy data structure
│   └── test_azure.py           # Tests the Azure connection
│
├── output/
│   ├── extracted_text.txt
│   ├── proposed_policies_final.json
│   └── proposed_policies_final.md
│
└── requirements.txt
```

For downstream application development, use:

```text
policy_analysis/output/proposed_policies_final.json
```

The Markdown version is available for human review:

```text
policy_analysis/output/proposed_policies_final.md
```

## Setup

Run the following commands from the repository root.

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r policy_analysis/requirements.txt
```

### 3. Configure Azure AI Foundry

Create a `.env` file in the repository root:

```env
AZURE_OPENAI_ENDPOINT=YOUR_ENDPOINT
AZURE_OPENAI_API_KEY=YOUR_API_KEY
AZURE_OPENAI_MODEL=gpt-4.1-mini
```

Do not commit `.env` or API keys to GitHub.

### 4. Test the Azure connection

```bash
python policy_analysis/src/test_azure.py
```

## Run the Pipeline

Run the scripts in this order:

### Step 1 — Extract text from the PDF

```bash
python policy_analysis/src/extract_pdf.py
```

This reads:

```text
policy_analysis/data/2025-16554.pdf
```

and creates page-aware extracted text.

### Step 2 — Extract proposed policy changes

```bash
python policy_analysis/src/extract_policies.py
```

This uses GPT-4.1-mini to identify concrete proposed regulatory changes and their supporting evidence.

### Step 3 — Consolidate and quality-check the policies

```bash
python policy_analysis/src/consolidate_policies.py
```

This merges duplicate descriptions of the same underlying policy change while preserving distinct requirements, restrictions, exceptions, time limits, evidence, and source pages.

### Step 4 — Use the final output

```text
policy_analysis/output/proposed_policies_final.json
```

This is the structured output intended for the rest of the application.

## Important Notes

`extract_policies.py` and `consolidate_policies.py` make calls to the Azure-hosted GPT-4.1-mini model and may consume Azure credits. `extract_pdf.py` runs locally and does not use the model.

The generated policy descriptions are **AI-assisted interpretations** of the source document. The official Federal Register text remains authoritative. Evidence quotes and source-page references are retained to support human review.

If you only need the existing policy analysis for another component of the application, use `proposed_policies_final.json`; you do not need to rerun the pipeline.