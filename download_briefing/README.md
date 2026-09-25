# Analyst briefing (PDF)

Builds a **one-page PDF briefing** for docket **ICEB-2025-0001** (the DHS duration of
status rule) from the committed outputs of all four PolicyLens components: policy
versions, public comments, news articles and news videos. The analyst downloads it from
the UI, reviews it, and records the final decision in it.

A detailed version (about 6 pages) is available with `--full`.

## Files

```
download_briefing/
├── README.md
├── generate_briefing.py                        # builds the PDFs
├── PolicyLens_briefing_ICEB-2025-0001.pdf      # one-page briefing (for the UI button)
└── PolicyLens_briefing_ICEB-2025-0001_full.pdf # detailed briefing
```

## How it works

`generate_briefing.py` does not call any AI model or API. It:

1. **reads the committed output files** of the other components (paths below);
2. **computes every figure** from them with pandas (counts, shares, averages,
   contradicted claims, possibly outdated claims, reach);
3. **lays out the PDF** with ReportLab and writes it to this folder.

Anyone who runs it on the same files gets the same briefing, apart from the date stamp.
When a component's outputs change, rerun the script and the briefing updates.

## What is in the briefing

**One-page briefing (default):**

| Part | Source file(s) |
|---|---|
| **Status:** proposed rule, final rule, effective date, court postponement | `data/ICEB-2025-0001-21962/rule_versions.json`, `data/court/1-26-cv-13799-FDS/source.json` |
| **Key points:** one line each for policy, comments, news, video | calculated from the panels below |
| **What changed, V1 vs V2:** unchanged / modified / added, key modifications | `policy_html_extraction/output/policy_changes_validated.json` |
| **Public comments:** sentiment, top concerns, minority views | `comment_analysis/comments_analysis_output.csv`, `comment_analysis/bertopic_topic_info.csv` |
| **News articles:** grounding score, claim verdicts, stakeholder stances | `news_analysis/output/fact_check/articles_ui.csv`, `claims_ui.csv`, `viewpoints_ui.csv` |
| **News videos:** verdicts, possibly outdated claims, reach, most-watched video | `video_analysis/video_claims_for_ui.csv` |
| **Items for the analyst to decide**, each with **Confirm / Reject** | calculated from all of the above |
| **Analyst decision:** **Approve / Approve with changes / Do not approve**, Reviewer, Date, Notes | fixed layout |
| **How this was made, and limits** | fixed text |

**Detailed briefing (`--full`):** the same sources, with every policy change (plus
`v1_policies.json` and `v2_policies.json`), all comment topic clusters, the contradicted
news and video claims with their citations (`claim_evidence_ui.csv`), a balanced list of
stakeholder viewpoints, a per-video table, and the same Analyst decision block.

A few values are set in the script rather than read from files; edit them at the top of
`generate_briefing.py` if they change:

- `TOTAL_COMMENTS_ON_DOCKET = 21923`: comments listed on Regulations.gov
- the comment deadline (Sep 29, 2025) in the timeline
- the "How this was made" and limits text

## The analyst makes the final call

The decision controls are **fillable PDF fields**. The analyst can tick the Confirm /
Reject boxes and the Approve / Approve with changes / Do not approve boxes, and type in
Reviewer, Date and Notes, in any PDF reader (Acrobat, Preview, Edge, Chrome). Saving the
file keeps the decision, so the signed-off briefing can be shared. The boxes also print
empty for sign-off on paper.

## Privacy

No commenter names, comment text or private individuals appear in the briefing:

- comments appear only as counts and topic labels (`Primary_Concern`);
- news viewpoints are listed by organization, and individuals quoted in the press are
  shown as "Quoted individual";
- video speakers come from the privacy-cleaned `video_claims_for_ui.csv`.

## Run

Install the two dependencies once:

```
pip install reportlab pandas
```

Then run from **this folder**:

```
python3 generate_briefing.py            # one-page briefing
python3 generate_briefing.py --full     # detailed briefing
```

or from the **repository root**:

```
python3 download_briefing/generate_briefing.py
python3 download_briefing/generate_briefing.py --full
```

Each run prints the path of the saved PDF and takes about a second.

| Option | What it does |
|---|---|
| `--full` | Build the detailed briefing (`..._full.pdf`) instead of the one-pager |
| `--out path.pdf` | Save under another name or location |
| `--repo path` | Repository root, if this folder is not directly inside it (default: the folder above this one) |

If an input file is missing, the script lists it and that section is skipped or
shortened instead of failing.

## Checking the numbers

Every figure can be traced to its source file. For example, the one-pager reports 111
video claims, 67 of them Supported. From the repository root:

```
python3 -c "import pandas as pd; v=pd.read_csv('video_analysis/video_claims_for_ui.csv'); print(len(v), v.verdict_at_publish.value_counts().to_dict())"
```

prints `111` and `'Supported': 67`.

## UI: "Download briefing" button

**Option A: serve the committed PDF.** Link the button to
`download_briefing/PolicyLens_briefing_ICEB-2025-0001.pdf`.

**Option B: build it on click**, so it always reflects the latest outputs. In Streamlit,
with the app running from the repository root:

```python
from download_briefing.generate_briefing import build_briefing_bytes

st.download_button(
    "Download analyst briefing (PDF)",
    data=build_briefing_bytes(),               # one page; full=True for the detailed one
    file_name="PolicyLens_briefing_ICEB-2025-0001.pdf",
    mime="application/pdf",
)
```

## Limits

The briefing summarizes AI-assisted outputs: verdicts, labels, topics, "added" policy
changes and "outdated" flags are review candidates, not findings. Samples are
English-only and not representative of all coverage or public opinion, and
attachment-only comments are not in the text analysis. It is not legal advice; the
Federal Register text is authoritative.
