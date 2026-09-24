# PolicyLens: video news claim check

Docket **ICEB-2025-0001**: DHS rule replacing "duration of status" (D/S) with fixed
admission periods for F, J and I visa holders.

This module takes news and explainer videos about the rule, transcribes them, pulls out
each claim the speaker makes, and checks it against the text of the proposed rule (v1)
and the final rule (v2). The result is one CSV file that the app UI reads directly.

---

## For the UI: `video_claims_for_ui.csv`

**One row per claim.** Video details (speaker, date, views) repeat on every row of that
video, so the file can be used as is:

- **Video list:** group rows by `video_key`.
- **Claim cards:** the rows within one `video_key`, ordered by `claim_number`.
- **Summary numbers:** count rows by `verdict_at_publish`, sum `view_count` once per video.

The file contains no personal information (see [Privacy](#privacy)), no full
transcripts, and no API keys.

Load it:

```python
import pandas as pd
claims = pd.read_csv("video_claims_for_ui.csv")
videos = claims.drop_duplicates("video_key")      # one row per video
```

### Columns

#### Video (same on every row of a video)

| Column | Type | Values / example |
|---|---|---|
| `docket_id` | text | `ICEB-2025-0001` |
| `video_key` | text | Stable ID of the video. Use it to group rows. |
| `video_url` | URL | Link to the video. **Empty for individual creators** (their page URL contains their name). |
| `platform` | text | `YouTube`, `Facebook`, `TikTok`, `Instagram`, `Other` |
| `speaker` | text | Channel or organization name, e.g. `Bloomberg Television`. Private individuals appear as `Individual creator`. |
| `speaker_type` | text | `law_firm`, `attorney`, `consultancy`, `news_outlet`, `university`, `government`, `advocacy`, `creator` (independent channel), `individual` (private person), `unknown` |
| `video_title` | text | Title, max 200 characters. Contact details removed. |
| `published_date` | date | `YYYY-MM-DD`. Claims are judged as of this date. |
| `milestone_window` | text | Stage of the rule when the video came out (see [Milestones](#milestones)). |
| `video_duration_sec` | integer | Length in seconds; may be empty. |
| `view_count` | integer | Views when collected; empty if the platform does not show it. |
| `like_count` | integer | Likes or reactions; may be empty. |

#### Claim

| Column | Type | Values / example |
|---|---|---|
| `claim_uid` | text | Stable ID of the claim. Use it to store analyst decisions. |
| `claim_number` | integer | Order within the video (1, 2, 3...). |
| `timestamp` | text | Where in the video the claim is made, `m:ss`, e.g. `1:36`. |
| `timestamp_seconds` | integer | Same in seconds, e.g. `96`. |
| `video_link_at_time` | URL | Opens the video at that moment. **YouTube only**; empty otherwise. |
| `speaker_quote` | text | The speaker's own words from the transcript. |
| `claim` | text | The claim restated as one clear sentence. |
| `claim_type` | text | `fact` (checked), `opinion`, `prediction`, `other_rule`, `other` |
| `topic` | text | `fixed admission periods`, `extension-of-stay procedures`, `F-1 departure period`, `graduate transfers/program changes`, `other`. Same labels as the comment analysis. |
| `about` | text | `rule_content`, `rulemaking_process`, `legal_status`, `court_case`, `other` |

#### Verdict

| Column | Type | Values / example |
|---|---|---|
| `verdict_at_publish` | text | Was the claim accurate **on the publish date**? See [Verdicts](#verdicts). |
| `model_suggests_outdated` | true/false | The AI's hint that something since the publish date may have made the claim untrue. |
| `currency_status` | text | Second badge: `Still current` or `Outdated`, for claims that were `Supported` or `Partly supported`. Empty for other verdicts. Starts from the AI's suggestion; the analyst can change it. |
| `currency_source` | text | `AI suggestion` until an analyst reviews it (the app then shows `Analyst`). |
| `confidence` | number 0–1 | The AI's confidence in `verdict_at_publish`. Empty for claims not checked. |
| `verdict_basis` | text | What the verdict rests on: `v1` (proposed rule), `v2` (final rule), `both`, `timeline`, `none` |
| `ai_explanation` | text | One or two sentences comparing the claim with the rule text. |

#### Evidence (rule text used for the verdict; up to 2 passages)

| Column | Type | Values / example |
|---|---|---|
| `evidence_1_version`, `evidence_2_version` | text | `v1` (proposed rule, Aug 28, 2025) or `v2` (final rule, Jul 17, 2026) |
| `evidence_1_citation`, `evidence_2_citation` | text | Federal Register citation, e.g. `91 FR 44976` |
| `evidence_1_section`, `evidence_2_section` | text | Section heading in the rule, e.g. `DATES` |
| `evidence_1_text`, `evidence_2_text` | text | The passage (up to 400 characters) |
| `evidence_1_link`, `evidence_2_link` | URL | Opens that page of the Federal Register |

Empty when the verdict did not rely on the rule text (e.g. `Not in rule text`).

#### Analyst review (empty; for the app to fill)

| Column | Values |
|---|---|
| `analyst_review_status` | Starts as `pending`. App sets `confirmed` or `changed`. |
| `analyst_verdict` | Analyst's accuracy verdict, if they change it (same values as `verdict_at_publish`). |
| `analyst_outdated` | Analyst's decision on the currency badge: `true` (Outdated), `false` (Still current), empty (not reviewed). When set, it replaces `currency_status` and the badge source becomes `Analyst`. |
| `analyst_note` | Free text. |

Store analyst decisions in the app, keyed by `claim_uid`. The CSV is regenerated when
new videos are checked, so decisions written into it would be lost.

#### Run info

| Column | Values |
|---|---|
| `model` | AI model used, e.g. `gpt-4.1-mini` |
| `checked_at` | When the video's claims were checked |

### Verdicts

`verdict_at_publish` answers one question: was the claim accurate on the day the video
was published, according to the rule text?

| Value | Meaning | Suggested color |
|---|---|---|
| `Supported` | The rule text or timeline confirms it | green |
| `Partly supported` | Core is right; a detail is wrong, missing or overstated | amber |
| `Contradicted` | The rule text says otherwise | red |
| `Not in rule text` | The rule cannot confirm or refute it (e.g. what the judge said) | gray |
| `Different rule` | About another regulation; not checked | light gray |
| `Not checked` | Opinion, prediction or advice | light gray |

### Currency badge: Still current / Outdated

Each checked claim shows **two badges**: the verdict above, and a currency badge that
answers "is it still true today?" (`currency_status`).

| Badge | Meaning | Suggested color |
|---|---|---|
| `Still current` | Nothing since the publish date has changed it | teal / blue outline |
| `Outdated` | Something since the publish date made it untrue, e.g. a video before the court ruling saying the rule "takes effect September 15" | purple |

- The badge starts as the **AI's suggestion**: label it, e.g. "Outdated · AI suggestion"
  (from `currency_source`).
- The analyst can **confirm or change** it (Still current / Outdated). Store the choice
  as `analyst_outdated` keyed by `claim_uid`, then show the badge as
  "Outdated · Analyst" or "Still current · Analyst".
- Only `Supported` and `Partly supported` claims get a currency badge. For the other
  verdicts it is empty (a claim that was wrong, unverifiable or not checked has no
  "still current" status).

Example card: **Supported** + **Outdated · AI suggestion**. A university video from
Aug 6 said the rule starts Sept 15: true when published, but the court postponed the
rule on Sept 14.

For accuracy percentages, count only `Supported`, `Partly supported` and
`Contradicted`; the other values are claims the rule text cannot judge.

### Milestones

| `milestone_window` | Dates |
|---|---|
| `before_proposed_rule` | before Aug 28, 2025 |
| `proposed_rule_comment_period` | Aug 28 – Sep 29, 2025 |
| `between_comments_and_final` | Sep 30, 2025 – Jul 15, 2026 |
| `final_rule` | Jul 16 – Sep 13, 2026 (text public Jul 16, published Jul 17) |
| `injunction` | from Sep 14, 2026 (court postponed the rule) |

### Showing a claim

Keep three layers visibly separate and labeled on every claim card:

1. **Speaker said:** `speaker_quote` (with `timestamp`, linked via `video_link_at_time` when available)
2. **Rule text:** `evidence_1_*` / `evidence_2_*`, with citation and link
3. **AI assessment:** the verdict badge (`verdict_at_publish`), the currency badge
   (`currency_status` + `currency_source`), `ai_explanation`, `confidence`

Always show this note on the panel:

> AI-generated verdicts, pending analyst review. Based on a small sample of
> English-language videos, not all video coverage.

---

## Privacy

What the CSV does to protect personal information:

- **Private individuals** (`speaker_type = individual`): name replaced by
  `Individual creator` everywhere, in every row; `video_url` left empty because
  personal page URLs contain the person's name.
- **Emails and phone numbers** are replaced with `[removed]` in titles, quotes, claims
  and explanations.
- **Self-introductions** ("my name is attorney ...", "I'm ...") have the name replaced
  with `[name]`.
- **No full transcripts** are included in this file; only short quotes.

Organizations, news outlets and public channels keep their names, since they publish
under them.

The other files in this folder (`transcripts/`, `claim_results.csv`) are kept for
reproducibility and are **not** privacy-cleaned. Use `video_claims_for_ui.csv` for
anything shown in the app.

---

## Setup, run and output

### 1. Setup (once)

Python 3.11 in its own environment is recommended:

```
conda create -n policylens python=3.11 -y
conda activate policylens
pip install yt-dlp faster-whisper openai sentence-transformers pandas requests python-dotenv
```

On Intel Macs, install these versions instead for the embedding model (PyTorch 2.2.2
is the last release for Intel Macs):

```
pip install "numpy<2" "torch==2.2.2" "transformers==4.44.2" "sentence-transformers==3.0.1"
```

#### Getting the Azure OpenAI details from Azure AI Foundry

The claim check uses **Azure OpenAI in Azure AI Foundry** (model: `gpt-4.1-mini`).
You need three values: the endpoint, an API key, and the deployment name.

1. Go to [ai.azure.com](https://ai.azure.com) (Azure AI Foundry) and open your project
   or Azure OpenAI resource.
2. **Deployment name:** open **Models + endpoints** (called **Deployments** in some
   views). Use the name in the deployment list, which can differ from the model name.
   If nothing is deployed, click **+ Deploy model → Deploy base model**, choose
   `gpt-4.1-mini`, keep **Global Standard**, and name the deployment `gpt-4.1-mini`.
3. **Endpoint and key:** open the deployment (or the resource's **Keys and Endpoint**
   page) and copy the **Azure OpenAI endpoint** and one of the **keys**.
4. Use the endpoint in the form `https://<resource-name>.openai.azure.com/openai/v1/`.
   The **project endpoint** Foundry also shows
   (`https://<resource-name>.services.ai.azure.com/api/projects/...`) does **not** work
   here; it fails with `Missing required query parameter: api-version`.

Portal menu names change from time to time; the three values above are what matter.

Create a `.env` file in this folder or any parent folder with those values.
**Never commit it.**

```
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/openai/v1/
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_MODEL=<deployment name, e.g. gpt-4.1-mini>
```

The endpoint must end in `/openai/v1/`. If `claim_check.py` stops with a connection
error: `DeploymentNotFound` means the deployment name is wrong; `Resource not found`
means the endpoint is wrong; `AuthenticationError` means the key is wrong.

### 2. Folder

```
video_analysis/
├── README.md                          # this file
├── video_claims_for_ui.csv            # ← the file for the UI (one row per claim, PII removed)
├── claim_results.csv                  # full internal results (debugging; not privacy-cleaned)
├── video_urls.txt                     # the 10 videos analyzed (input to transcribe_videos.py)
├── found_video_urls.txt               # YouTube search candidates (output of find_videos.py)
├── find_videos.py                     # step 1 (optional): search YouTube for videos
├── transcribe_videos.py               # step 2: download audio + transcribe (Whisper, local)
├── claim_check.py                     # step 3: extract + check claims, write the UI file
├── rule_text/                         # created by claim_check.py
│   ├── v1_2025-16554.xml              # proposed rule, full text from the Federal Register
│   ├── v1_2025-16554_paragraphs.json  # proposed rule split into paragraphs (458)
│   ├── v2_2026-14439.xml              # final rule, full text from the Federal Register
│   ├── v2_2026-14439_paragraphs.json  # final rule split into paragraphs (1,453)
│   └── embeddings_1911.npy            # embeddings of all 1,911 paragraphs (for evidence search)
└── transcripts/                       # created by transcribe_videos.py
    ├── transcripts.csv                # one row per video: metadata + full transcript text
    ├── audio/                         # temporary downloads (empty; audio is deleted after transcribing)
    ├── 1405856235086952.txt           # transcript with timestamps
    ├── 1405856235086952_segments.csv  # same, as timed segments (start, end, text)
    ├── 1402030935241396.txt
    ├── 1402030935241396_segments.csv
    ├── 1088712917225108.txt
    ├── 1088712917225108_segments.csv
    ├── 1598856874948750.txt
    ├── 1598856874948750_segments.csv
    ├── 1082728481141518.txt
    ├── 1082728481141518_segments.csv
    ├── bhfTiZPVdgM.txt
    ├── bhfTiZPVdgM_segments.csv
    ├── ONXytyxMyoM.txt
    ├── ONXytyxMyoM_segments.csv
    ├── 8wwO343OOzI.txt
    ├── 8wwO343OOzI_segments.csv
    ├── pm9dk_1i4Jc.txt
    ├── pm9dk_1i4Jc_segments.csv
    ├── BpgN9dlK3es.txt
    └── BpgN9dlK3es_segments.csv
```

Each video has two transcript files named by its video ID (the same as `video_key` in
`video_claims_for_ui.csv`):

| Video ID | Speaker | Platform | Published |
|---|---|---|---|
| `bhfTiZPVdgM` | GoElite | YouTube | 2025-10-10 |
| `ONXytyxMyoM` | Bloomberg Television | YouTube | 2026-07-17 |
| `8wwO343OOzI` | CSULB CIE | YouTube | 2026-08-06 |
| `pm9dk_1i4Jc` | Law Office of Louis S. Haskell | YouTube | 2026-08-13 |
| `1082728481141518` | Individual creator | Facebook | 2026-09-10 |
| `1405856235086952` | Bethel Law Group | Facebook | 2026-09-14 |
| `BpgN9dlK3es` | Visa Guide USA | YouTube | 2026-09-14 |
| `1402030935241396` | Maven Consulting Services | Facebook | 2026-09-15 |
| `1088712917225108` | Nova Law Group | Facebook | 2026-09-15 |
| `1598856874948750` | FJ Diza, US Immigration Attorney | Facebook | 2026-09-15 |

### 3. Run

Run from inside `video_analysis/`, in this order.

**Step 1 (optional): find videos**

```
python3 find_videos.py
```

Searches YouTube (free, no key) and writes candidates to `found_video_urls.txt`.
Review them and copy good lines into `video_urls.txt`. Facebook, TikTok and Instagram
cannot be searched; add those links to `video_urls.txt` by hand.

**Step 2: transcribe**

```
python3 transcribe_videos.py --file video_urls.txt
```

Downloads each video's audio and transcribes it on your computer (free). Records the
publish date, platform, views and milestone window. Videos already transcribed are
skipped; `--force` redoes them. If a site needs a login, add
`--cookies-browser chrome`.

**Step 3: check claims and write the UI file**

```
python3 claim_check.py transcripts/transcripts.csv
```

The first run downloads both rule versions from the Federal Register and embeds them
(a few minutes, once). Then, for each video, it extracts the claims, finds the most
relevant rule passages, and asks the AI for a verdict (3 votes per claim, majority
wins). About 1–2 minutes per video.

Useful options:

| Option | What it does |
|---|---|
| `--tag v2` | Save this run separately (`video_claims_for_ui_v2.csv`, `claim_results_v2.csv`, `claim_cache_v2.jsonl`); earlier files are not touched |
| `--force` | Recheck videos that were already checked |
| `--votes 1` | One verdict per claim instead of 3: faster, less stable |
| `--limit 3` | Only check the first 3 videos (for testing) |

### 4. Output

| File | What it is | In the repo? |
|---|---|---|
| `video_claims_for_ui.csv` | One row per claim, personal information removed. **The file for the UI.** | Yes |
| `claim_results.csv` | Full internal results for debugging. Not privacy-cleaned; its `verdict` column combines accuracy and outdated, so **use `video_claims_for_ui.csv` for the UI**. | Yes, for reference |
| `transcripts/` | Transcripts (`.txt`, timestamped `_segments.csv`) and `transcripts.csv` with video metadata. Not privacy-cleaned. | Yes, so the analysis can be rerun without retranscribing |
| `rule_text/` | Downloaded rule text and embeddings | Yes (also recreated automatically) |
| `found_video_urls.txt` | Candidate videos from the YouTube search | Yes, for reference |
| `claim_cache*.jsonl` | Saved results so reruns skip checked videos | No |
| `transcripts/audio/` | Downloaded audio (deleted after transcription by default) | No |
| `.env` | Azure key | **Never** |

The run ends with a summary, e.g.:

```
Saved 111 claims from 10 videos to video_claims_for_ui.csv (for the UI, PII removed)
verdict_at_publish
Supported           67
Not in rule text    19
Partly supported    11
Not checked         11
Contradicted         3
currency_status
Still current    69
(n/a)            33
Outdated          9
```

### Adding a video

1. Add its URL to `video_urls.txt`. If the site shows no publish date, add it after the
   URL: `https://... 2026-09-20`.
2. Add the channel or page name to `SPEAKER_TYPES` at the top of `claim_check.py` with
   its type. **Private individuals must be listed as `individual`** so their name is
   hidden. The script warns about any speaker not in the list.
3. Rerun steps 2 and 3. Only the new video is transcribed and checked.

### When something new happens (e.g. an appeal)

Add the event to `TIMELINE` in `claim_check.py`, update `LAST_TIMELINE_EVENT`, and
rerun step 3 with `--force` so claims are judged against the latest timeline.

---

## Known limits

- Small, English-only sample (10 videos from YouTube and Facebook).
- Claims about the court ruling are mostly `Not in rule text` until the court order is
  added as a source.
- Verdicts and currency badges are AI output and need analyst review. Known patterns:
  statements about the past ("previously...", "DHS announced...") are sometimes
  flagged as outdated, and claims about other regulations are sometimes checked
  against this rule. The analyst controls in the UI are there to correct these.
