# News Fact-Checker

The fact-checker checks whether news coverage of DHS rule **ICEB-2025-0001** is grounded in the rule itself. The rule has two versions:

- the proposed rule, FR Doc 2025-16554, published 2025-08-28
- the final rule, FR Doc 2026-14439, published 2026-07-17 and effective 2026-09-15

For each article, the fact-checker aims to:

- split the article into claims about the policy and opinions people express in it
- check each policy claim against the text of the version the article is about
- give a confidence score with cited evidence (paragraph ID, CFR citation, Federal Register page)
- keep policy language, factual reporting, public opinion and AI interpretation apart

Input: [`gdelt_news_articles_v3-112.csv`](gdelt_news_articles_v3-112.csv). It has 112 rows, of which 96 have text; the 16 empty rows are all reposts.

## Pipeline status

| Step | What it does | Status |
|---|---|---|
| **0. Evidence index** | Parse both rule versions into citable paragraphs, tag evidence tiers, build keyword and embedding search, verify a key-facts sheet | **Done** (this document) |
| 1. Prep | Drop empty reposts and count them as reach, assign each article's rule version, split articles into numbered sentences | Planned |
| 2. Classify sentences | One LLM call per article: policy claim / government position / attributed opinion / journalist interpretation / outside fact | Planned |
| 3–4. Retrieve and verify | Key facts first, then paragraphs. Verdicts: Supported / Oversimplified / Outdated / Contradicted / Not in rule | Planned |
| 5. Confidence | Judge agreement, evidence tier, exact-quote and number checks, retrieval margin | Planned |
| 6. Opinions | Speaker type, stance, target provision, reasons. Linked to DHS's responses in final rule §IV | Planned |
| 7–8. Report, review, evaluation | Analyst review queue, hand-labeled gold set, calibration | Planned |

---

## Quick start

Run from the repo root with the project venv. Both rule HTML files must already be in `data/`; `fetch_regulations.py ICEB-2025-0001-0001 --final-rule` fetches them.

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python news_analysis/src/build_evidence_index.py
```

```bash
.venv/bin/python news_analysis/src/evidence_index.py "grace period cut to 30 days" --docs final
```

```bash
.venv/bin/python news_analysis/src/evidence_index.py "students can extend their stay" --facts
```

```bash
.venv/bin/python news_analysis/src/eval_retrieval.py
```

The build takes about 4 minutes on the Intel i7 MacBook, almost all of it embedding. The first run also downloads the embedding model, about 130 MB. `--no-embeddings` builds in seconds, and keyword search still works without embeddings. The build exits non-zero if any key-fact quote cannot be found.

---

## Step 0: the evidence index

### What gets built

Code:

| File | Purpose |
|---|---|
| [`src/build_evidence_index.py`](src/build_evidence_index.py) | Parses both versions, tags tiers and voices, embeds paragraphs, verifies key facts |
| [`src/evidence_index.py`](src/evidence_index.py) | `EvidenceIndex`: hybrid search over paragraphs (`search`) and key facts (`search_facts`), plus a command line |
| [`src/eval_retrieval.py`](src/eval_retrieval.py) | Retrieval check against real article sentences |
| [`key_facts.json`](key_facts.json) | Hand-curated key facts, currently an AI draft (see below) |
| [`retrieval_cases.json`](retrieval_cases.json) | 16 article sentences with the paragraphs and key facts they should retrieve |

Outputs in `output/evidence/`:

| File | Size | Contents |
|---|---|---|
| `evidence_paragraphs.jsonl` | 3.0 MB | 2,607 citable records from both versions: paragraphs, list items, tables and footnotes |
| `embeddings.npy` + `embeddings_meta.json` | 4.0 MB | 384-dimensional `BAAI/bge-small-en-v1.5` vectors, in the same order as the records |
| `key_facts_verified.json` | 66 KB | The key-facts sheet, with the paragraph each quote was found in |
| `proposed_rule_sections.jsonl/.md`, `final_rule_sections.jsonl/.md` | 3.7 MB | Full parsed section trees. The `.md` files are for people browsing citations. |
| `rule_versions.json` | — | Document numbers and dates, copied from `data/ICEB-2025-0001-21962/rule_versions.json` |

### Evidence record

```json
{
  "evidence_id": "final:8 CFR 214.2/p6",
  "doc": "final", "fr_doc": "2026-14439",
  "section_id": "8 CFR 214.2", "section_path": ["PART 214--NONIMMIGRANT CLASSES", "§ 214.2 Special requirements ..."],
  "tier": "A", "source_type": "regulatory_text", "voice": "regulation", "comment_block": null,
  "kind": "text", "cfr_citation": "8 CFR 214.2(f)(5)(i)", "fr_cite": "91 FR 45124",
  "text": "(5) Period of stay--(i) General. An F-1 student is admitted for a fixed period of time, ..."
}
```

`evidence_id` is `<version>:<para_id>`. Paragraph IDs repeat across the two versions, so the version prefix is needed.

### Evidence tiers

Each record has a tier. The tier says how much weight the paragraph can carry when checking a claim about the rule:

| Tier | Meaning | Proposed | Final |
|---|---|---:|---:|
| **A** binding text | CFR amendments and amendatory instructions | 170 | 172 |
| **B** agency statement | DHS's description of the rule, its dates and its rationale, including the SUMMARY, DATES and statutory-requirements sections such as the Congressional Review Act | 414 | 465 |
| **C** agency estimate | Cost-benefit and regulatory flexibility analysis | 58 | 27 |
| **D** comment record | Final rule §II.B and §IV: what commenters said and DHS's responses | 0 | 1,178 |
| **X** reference only | Document header, contacts, acronym list, list of subjects, signature | 57 | 66 |

Rules for later steps:

- **Claims about what the rule says or does** are checked against tiers A and B only. `POLICY_TIERS` is the default in `search()`.
- **Tier D never supports a policy claim.** In §IV, DHS paraphrases commenters, so "commenters said the cap harms PhD students" is not policy language. Tier D can support claims about the comment process itself, such as "almost 22,000 comments", and it links in-article opinions to DHS's responses (step 6).
- **Tier C statements are DHS projections, not measured facts.**
- **Footnotes are excluded from search by default** (`footnotes=True` includes them). They are mostly DHS's source citations, such as DOJ press releases. Before they were excluded, they outranked the regulation for "Chinese journalists … 90 days".

### Voice and comment blocks

In the final rule's §IV, each topic runs as a paragraph starting "Comments:", then more commenter paragraphs, then a paragraph starting "Response:", then more DHS paragraphs. The builder tags each tier-D paragraph with a `voice`:

- `commenters`: 441 paragraphs
- `agency`: 737 paragraphs

It also gives both halves of each exchange a shared `comment_block` ID, such as `final:IV.B.1#c1`. The final rule has 214 such blocks. Outside tier D, the voice is `regulation` for tier A and `agency` for everything else.

### Key-facts sheet

[`key_facts.json`](key_facts.json) holds **34 facts** that news coverage repeats most often. Topics include:

- admission periods and the 4-year cap
- departure periods (60→30 days)
- extension of stay
- unlawful presence
- program changes
- transition rules
- I-visa limits
- dates
- comments
- costs
- DHS's stated rationale

Each fact has:

- a plain-English `statement`
- `claim_type` (rule provision / timeline / agency rationale / agency estimate / comment record)
- `applies_to`: the rule versions it is true of
- `values`: numbers and dates for exact comparison in step 4
- `change_from_proposed`
- `common_misreadings`
- verbatim `evidence` quotes

Quotes are stored **without paragraph IDs**. The builder finds each quote in the parsed text and records where it appears, preferring the strongest tier. This means the sheet survives parser changes. Any quote that is not found fails the build. Currently all 34 facts (80 quotes) are verified.

> **Status: draft.** Claude wrote the statements, values and misreadings from the rule text on 2026-09-24. The quotes are machine-verified; the interpretation is not. An analyst should review each fact and set `reviewed_by` before these are used in briefings.

### Search

`EvidenceIndex.search()` combines two rankings:

- **BM25 keyword scores**, which are good at numbers, CFR citations and form names. The tokenizer also splits compounds, so "4-year" matches "4 years" and "F-1" matches "F1".
- **Embedding similarity**, which is good at paraphrase. The embedding model is `BAAI/bge-small-en-v1.5`, run locally.

The two rankings are merged with reciprocal rank fusion (k=60).

Filters:

- `docs` (`proposed`, `final`)
- `tiers`
- `voices`
- `footnotes`

Each hit includes its keyword and embedding ranks, so a reviewer can see why it was returned.

`search_facts()` searches the key facts. Each fact is indexed on its statement **plus its quotes**, because the quotes carry the official wording.

### Retrieval check

[`retrieval_cases.json`](retrieval_cases.json) has 16 sentences copied from the articles. Each lists the paragraphs and key facts it should retrieve. The table shows how often the first relevant result ranks within the top 1, 3 or 5:

| Layer | Mode | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---:|---:|---:|---:|
| Key facts | keyword | 12 | 14 | 14 | 0.80 |
| Key facts | embedding | 11 | 15 | 16 | 0.83 |
| **Key facts** | **hybrid** | **12** | **15** | **16** | **0.85** |
| Paragraphs | keyword | 7 | 12 | 13 | 0.58 |
| Paragraphs | embedding | 7 | 11 | 13 | 0.58 |
| **Paragraphs** | **hybrid** | **9** | **10** | **13** | **0.63** |

What this shows:

- **Key facts are the stronger first layer.** Indexing statements alone gave hybrid MRR 0.78; adding the quotes raised it to 0.85.
- **The three paragraph misses retrieve on-topic text.** For the claims "admitted for a maximum of four years, after which they must apply…", "apply for an extension through USCIS" and "current visa holders will transition…", the top hits are DHS's own discussion paragraphs on the same topic, such as final §V.E.1 "Transition Period". They are not the CFR paragraph or summary bullet I labeled as the answer. I kept the labels strict rather than widening them after seeing the results.
- **These numbers are optimistic.** There are only 16 cases, and the same author (Claude) wrote the key facts and the labels. Step 8's hand-labeled gold set should replace this.

---

## Changes to `policy_analysis/src/extract_html.py`

Step 0 uses the existing HTML parser rather than a second one. The parser was written for the proposed rule, so it was generalized.

**Regression check:** running it with no arguments still writes `policy_analysis/output/rule_sections.jsonl` and `.md` **byte-identical** to the committed versions. I checked this with `cmp` after every change.

1. **Settings per version.** A `DOCUMENTS` config holds, for each version:
   - the outline numbering style: the proposed rule nests `V.E.iii.1`, the final rule nests `IV.B.2.a`
   - the acronym-list section
   - the section → source-type map, with a new source type `comment_response` for final §II.B and §IV

   A `--document final` CLI flag selects the final rule, and `parse_rule()`, `save_outputs()` and `print_report()` can be imported. The proposed rule is still the default.
2. **Lowercase outline headings** ("a. Fraud and Abuse…") are now recognized. The final rule has 92 of them, which previously merged into their parent sections.
3. **CFR "contents" tables** such as "Table 1 to Sec. 214.2--Section Contents" are kept as tables. Their rows ("(5) Period of stay") look like paragraph designators and were corrupting every citation after them. For example, English-language-training text was cited as `214.2(j)(20)(A)` instead of `214.2(f)(5)(i)(A)`.
4. **Wrapped cross-references** ("…in accordance with paragraph (f)(7) of this section and / Sec. 214.1 or timely filing…") no longer start a false `§ 214.1` section.
5. **Citation placement after omitted text:**
   - An inline stub like "(j) Exchange visitors--(1) \* \* \*" now counts as a stub.
   - After "\* \* \* \* \*", a designator may start the child level of the deepest paragraph, e.g. `(b) … * * * * * (2)`.

After these fixes, every CFR paragraph in the final rule is placed: **130 CFR citations, 0 irregular, 0 unplaced**. Before them, 65 could not be placed and many others were mis-cited. The proposed rule is unchanged at 135 citations with 0 unplaced.

---

## Findings that affect later steps

- **Correction to the plan: the Herald Goa congressional-review claim is largely supported.** The plan cited heraldgoa.in (2026-07-16), which says the rule "still requires congressional review before taking effect", as a likely error. The final rule's DATES section says it "has been classified as a major rule subject to congressional review" with an effective date of September 15, 2026. §VI.D adds that it takes effect at least 60 days after Congress receives DHS's report. The weaker part of the article is "until … an implementation date is announced", because the rule already sets one. This is now a test that the verifier does **not** over-flag claims.
- **The departure period differs for people already on D/S.** Students on D/S on September 15, 2026 keep a **60-day** departure period, running to November 14, 2030 (§214.1(m)(1)). "Grace period cut to 30 days" is true only for new fixed-period admissions.
- **OPT is extra time, not part of the 4 years.** §214.2(f)(5)(i) grants OPT as "additional time". An NPRM-era article saying the maximum stay is "four years, including optional practical training" is a likely misreading.
- **Unlawful presence has conditions.** DHS says people denied an extension "generally will begin to accrue unlawful presence the day after the issuance of the denial". A timely extension filing keeps a student in authorized stay until USCIS decides. Articles saying unlawful presence starts "immediately after the fixed admission period ends" leave that out.
- **Some final-rule changes the verifier should label Outdated when an NPRM-era claim contradicts them:**
  - graduate students may transfer with an SEVP exception
  - the delay authority for program-change limits ends September 14, 2028
  - the 30-day departure clock also runs when a program ends early
  - the program-level restriction applies only to programs completed after the effective date
- **The version framing is mixed.** 22 of the 58 final-rule-window articles with text still say "proposed".

## Environment notes

- The machine is an Intel (x86_64) Mac. PyTorch stopped publishing Intel-Mac wheels after **2.2.2**, which needs **NumPy < 2**. transformers 5.x fails to import with torch 2.2.2 (`NameError: name 'nn' is not defined`), so the pins are **transformers < 5** and **sentence-transformers 5.x**. The platform-specific pins are in [`requirements.txt`](../requirements.txt).
- The embedding model is cached in `~/.cache/huggingface/hub/`.

## Known limitations

- The key-facts statements and misreadings need analyst review (see above).
- Embeddings truncate at 512 tokens. 25 of the 2,607 records (1%) are longer; the longest paragraph is 505 words. Keyword search still covers their full text.
- §214.1(b)(1) sits inline in the §214.1(b) paragraph, because its heading contains parentheses. It is cited as `214.1(b)`.
- The final rule's full regulatory impact analysis is a separate docket document, so tier C holds only the summaries in the preamble.
- `output/evidence/` holds about 10 MB of generated files, including the 4 MB `embeddings.npy`. They can be rebuilt with one command, so decide whether to commit them or add them to `.gitignore`.

## Next: step 1

Step 1 prepares the articles:

- Drop the 16 empty reposts and record them as syndication reach.
- Assign each article a reference version by `seendate`: before 2026-07-16, the proposed rule; after, the final rule first, then the proposed rule.
- Split article text into numbered sentences with character offsets, so later steps cite sentence IDs and code, not the LLM, pulls the quotes.
