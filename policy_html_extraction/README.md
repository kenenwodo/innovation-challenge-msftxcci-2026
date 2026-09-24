# Policy Version & Change Analyzer

The **Policy Version & Change Analyzer** is a PolicyLens component that extracts structured regulatory provisions from two official versions of a policy and identifies how the policy changed between versions.

The pipeline uses official Federal Register text, Azure OpenAI GPT-4.1-mini, deterministic evidence verification, semantic version comparison, and human-review flags to produce an evidence-grounded policy change report.

For the current PolicyLens use case:

- **V1:** Proposed Rule — Federal Register Document `2025-16554`
- **V2:** Final Rule — Federal Register Document `2026-14439`

The system compares the two versions at the **regulatory provision level**, rather than relying on raw text differences.

---

## What This Component Does

The analyzer performs five main steps:

1. **Extract official policy text** from the Federal Register HTML documents.
2. **Extract structured regulatory provisions** from each version using GPT-4.1-mini.
3. **Verify evidence quotes** against the extracted official source text.
4. **Semantically compare V1 and V2 provisions** to identify unchanged, modified, added, and removed candidates.
5. **Apply deterministic validation checks** and flag comparisons that require analyst review.

The final output preserves both the AI interpretation and the supporting policy evidence so that analysts can review how each conclusion was reached.

---

## Pipeline

```text
Official V1 HTML                    Official V2 HTML
       │                                   │
       ▼                                   ▼
extract_html.py                     extract_html.py
       │                                   │
       ▼                                   ▼
Official V1 text                    Official V2 text
       │                                   │
       ▼                                   ▼
extract_provisions.py               extract_provisions.py
       │                                   │
       ▼                                   ▼
Structured V1 provisions            Structured V2 provisions
       │                                   │
       └──────────────┬────────────────────┘
                      │
                      ▼
              compare_versions.py
                      │
                      ▼
             Semantic comparison
                      │
                      ▼
              validate_changes.py
                      │
                      ▼
        policy_changes_validated.json
                      │
                      ▼
      generate_comparison_markdown.py
                      │
                      ▼
        Human-readable Markdown report
```

---

## Project Structure

```text
policy_html_extraction/
│
├── data/
│   ├── v1_official.html
│   ├── v1_official.pdf
│   ├── v2_official.html
│   └── v2_official.pdf
│
├── output/
│   ├── v1_official_text.txt
│   ├── v1_policies.json
│   ├── v1_policies.md
│   ├── v2_official_text.txt
│   ├── v2_policies.json
│   ├── v2_policies.md
│   ├── policy_changes.json
│   ├── policy_changes_validated.json
│   └── policy_changes_validated.md
│
├── src/
│   ├── extract_html.py
│   ├── extract_provisions.py
│   ├── compare_versions.py
│   ├── validate_changes.py
│   ├── generate_markdown.py
│   └── generate_comparison_markdown.py
│
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Create a virtual environment

From the `policy_html_extraction` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

The current implementation uses:

- `requests`
- `beautifulsoup4`
- `openai`
- `python-dotenv`
- `pydantic`

### 3. Configure Azure OpenAI

Create a `.env` file inside `policy_html_extraction/`:

```text
AZURE_OPENAI_ENDPOINT=<your Azure OpenAI endpoint>
AZURE_OPENAI_API_KEY=<your Azure OpenAI API key>
AZURE_OPENAI_MODEL=gpt-4.1-mini
```

**Do not commit `.env` to Git.**

The `.gitignore` should include:

```text
.env
.venv/
__pycache__/
*.pyc
.DS_Store
```

---

## Running the Pipeline

Run the commands from the `policy_html_extraction` directory.

### Step 1 — Extract Official HTML Text

```bash
python src/extract_html.py v1
python src/extract_html.py v2
```

This creates:

```text
output/v1_official_text.txt
output/v2_official_text.txt
```

The HTML source is used as the primary machine-readable policy source because it preserves the Federal Register text while avoiding many of the layout artifacts introduced by PDF extraction.

---

### Step 2 — Extract Structured Regulatory Provisions

```bash
python src/extract_provisions.py v1
python src/extract_provisions.py v2
```

GPT-4.1-mini converts the relevant official policy sections into structured regulatory provisions containing fields such as:

- policy name
- affected groups
- current rule
- proposed/final rule
- plain-English explanation
- requirements
- restrictions
- exceptions
- time limits
- CFR references
- source section
- evidence quote

The results are written to:

```text
output/v1_policies.json
output/v2_policies.json
```

---

### Step 3 — Generate Human-Readable Provision Reports

```bash
python src/generate_markdown.py v1
python src/generate_markdown.py v2
```

This creates:

```text
output/v1_policies.md
output/v2_policies.md
```

---

### Step 4 — Compare Policy Versions

```bash
python src/compare_versions.py
```

The comparator semantically matches V1 and V2 provisions and identifies policy areas as:

- `unchanged`
- `modified`
- `added`
- `removed`

The comparison is evidence-grounded: GPT receives the extracted provisions and their evidence rather than being asked to compare the policies from model memory.

The initial comparison is saved to:

```text
output/policy_changes.json
```

The comparator also performs coverage checks to ensure that every extracted V1 and V2 provision is accounted for in the comparison.

---

### Step 5 — Validate Comparison Results

```bash
python src/validate_changes.py
```

This applies deterministic validation checks to the semantic comparison.

The validator checks:

- required comparison fields
- presence of V1 and V2 provision IDs where required
- presence of evidence for matched provisions
- suspicious `modified` classifications where normalized rules are identical
- `added` and `removed` candidates that require source-level confirmation

The validated output is written to:

```text
output/policy_changes_validated.json
```

---

### Step 6 — Generate Final Human-Readable Report

```bash
python src/generate_comparison_markdown.py
```

This produces:

```text
output/policy_changes_validated.md
```

This is the primary human-readable version-comparison report.

---

## Current Results

### Provision Extraction

| Version | Provisions Extracted | Exact Evidence Matches | Manual Evidence Verification |
|---|---:|---:|---:|
| V1 — Proposed Rule | 27 | 24/27 | 3/27 |
| V2 — Final Rule | 24 | 18/24 | 6/24 |

An item requiring manual evidence verification is **not necessarily incorrect or unsupported**.

The evidence verifier performs an exact-text check against the extracted official source. Manual verification may be required when an AI-generated evidence quote combines nearby passages or when formatting and spacing prevent an exact contiguous match.

These provisions are retained rather than discarded so that an analyst can verify them against the official source.

### Version Comparison

The current comparison produced:

| Result | Count |
|---|---:|
| Policy areas compared | 23 |
| Unchanged | 13 |
| Modified | 6 |
| Added candidates | 4 |
| Passed automated validation checks | 19/23 |
| Analyst review required | 4/23 |

The four `added` classifications remain **candidates requiring analyst review**. A provision appearing only in the V2 extracted dataset is not treated as definitive proof that the underlying policy language was absent from V1.

See the complete report:

```text
output/policy_changes_validated.md
```

For application/programmatic use:

```text
output/policy_changes_validated.json
```

---

## Evidence Verification

Each extracted provision contains an `evidence_quote` tied to the official policy text.

PolicyLens performs deterministic verification after AI extraction by checking whether that evidence can be matched against the extracted official source.

This separates two concepts:

```text
AI interpretation
        │
        ▼
Structured provision
        │
        ▼
Deterministic evidence check
        │
        ├── Exact match verified
        │
        └── Manual verification required
```

A failed exact-text match does **not** automatically mean the underlying interpretation is false. Instead, the provision is preserved and surfaced for analyst verification.

---

## Responsible AI Design

This component follows an **AI-assisted, human-reviewed** approach.

### Evidence Grounding

Policy interpretations are connected to evidence from official policy text rather than relying solely on model-generated summaries.

### Source Separation

The pipeline maintains a distinction between:

- official policy language
- structured AI interpretation
- semantic comparison
- validation status

### Uncertainty Preservation

The system does not force uncertain cases into confident conclusions.

When automated checks cannot sufficiently confirm a comparison, the result is marked:

```text
review_required
```

### Conservative Added/Removed Classification

Absence from an extracted provision dataset is not treated as proof of absence from the source document.

Therefore, candidate additions or removals are escalated for human review.

### Human Analyst as Final Decision-Maker

PolicyLens is intended to support analysts, not replace policy or legal judgment.

Automated validation checks improve traceability and consistency, but they do not constitute independent legal verification.

---

## Key Output for PolicyLens

For integration into the PolicyLens application, the primary machine-readable artifact is:

```text
output/policy_changes_validated.json
```

The UI can use this file to display:

```text
Policy Area
    │
    ├── V1 Rule
    ├── V2 Rule
    ├── Change Type
    ├── What Changed
    ├── Practical Effect
    ├── Supporting Evidence
    ├── Confidence
    └── Validation Status
```

Items marked `review_required` should be clearly surfaced as requiring analyst verification rather than presented as confirmed policy changes.

---

## Technology

- Python
- BeautifulSoup
- Azure OpenAI
- GPT-4.1-mini
- Pydantic
- Federal Register / GPO official policy text
- JSON
- Markdown

---

## PolicyLens

This analyzer is one component of **PolicyLens — Evidence-grounded intelligence connecting policy text, public response, and news.**

The broader PolicyLens system is designed to connect authoritative policy language with public comments, news coverage, and evidence-grounded analysis while preserving source attribution, uncertainty, and human oversight.