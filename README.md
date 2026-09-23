# innovation-challenge-msftxcci-2026

The codebase for our Innovation Challenge Hackathon.

## What `fetch_regulations.py` does

The script uses the [Regulations.gov v4 API](https://open.gsa.gov/api/regulationsgov/) to download a rule document and its public comments, then **filters** comments so downstream work can focus on text-only submissions.

1. **Get the document.** It saves the document metadata and downloads the Federal Register rule files (typically `content.html` and `content.pdf` under `document/`).

2. **List every comment.** For example, document `ICEB-2025-0001-0001` has 21,923 comments. The API returns at most 5,000 results per query, so the script follows the API documentation: after each batch, it starts a new query using `filter[lastModifiedDate][ge]` from the last comment’s modified date until all IDs are collected. IDs are cached in `comment_ids.json`.

3. **Check each comment.** The comment list does not include body text, so the script requests each comment individually and classifies it:

   - **Kept** (`text_comments.jsonl`): comments whose text is real content (not just a pointer to an attachment).
   - **Skipped** (`skipped_comments.jsonl`): comments with an attached file, empty text, text that is only something like “See attached file(s).”, or withdrawn comments.

The filter is **strict by default**. Some commenters attach a file and also write a short cover note, such as “Please see attached file with the Society’s comments…”. Those are skipped unless you pass **`--allow-attachments`**. With that flag, a comment that has an attachment is kept as long as its text is more than a “see attached” placeholder line.

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

| Path | Description |
|------|-------------|
| `document.json` | Document metadata from the [Regulations.gov v4 API](https://open.gsa.gov/api/regulationsgov/), including attachment metadata |
| `document/*` | Downloaded document files (e.g. `content.html`, `content.pdf`) |
| `comment_ids.json` | Cached list of all comment IDs on the document |
| `text_comments.jsonl` | Kept comments, one JSON object per line (plain-text `commentText`, metadata) |
| `skipped_comments.jsonl` | Skipped comments with a `reason` (withdrawn, placeholder-only text, attachments, etc.) |
| `text_comments.csv` | Flat CSV export of the kept comments |

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

   ```bash
   .venv/bin/python fetch_regulations.py
   ```

### Useful options

- `--out-dir PATH` — output directory (default: `data/<document_id>`)
- `--allow-attachments` — see [What it does](#what-fetch_regulationspy-does) above
- `--limit N` — process at most N new comments (for testing)
- `--api-key KEY` — override the key from `.env`

If no key is configured, the script falls back to `DEMO_KEY` (strict rate limits).
