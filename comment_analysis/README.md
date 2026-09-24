# Comment Analysis

This directory contains the public-comment analysis pipeline and its committed
outputs. The pipeline covers sentiment, emotion, toxicity, word and phrase
frequency, TF-IDF, NMF themes, and BERTopic topic modeling.

The analysis uses `comments_no_pii.csv` as the cleaned, de-identified input.

## Primary concern labels

Each row in `comments_with_bertopic.csv` now includes a `Primary_Concern`
column. The value is a reviewed, human-readable summary of the comment's
BERTopic category. It is assigned from the comment's `Topic` ID, so comments in
the same topic receive the same primary-concern label.

The labels were reviewed against the topic keywords and representative comments
in `bertopic_topic_info.csv`:

| Topic | Comments | Primary concern |
| ----: | -------: | --------------- |
| `-1` | 679 | Mixed or unclassified international student and research concerns |
| `0` | 7,152 | Fixed visa limits and loss of duration of status |
| `1` | 1,085 | Restrictions on second or same-level degrees |
| `2` | 472 | Economic and institutional impacts of international student restrictions |
| `3` | 192 | Loss of global talent and U.S. competitiveness |
| `4` | 135 | Disruption to physician training and patient care |
| `5` | 91 | Visa program integrity, security, and domestic worker protections |
| `6` | 69 | Foreign journalist visa limits and press freedom |
| `7` | 66 | Concern provided in an attached file |
| `8` | 58 | Clean energy research and workforce impacts |

Topic `-1` is BERTopic's outlier category. Its comments do not form one
sufficiently distinct topic, so the label intentionally identifies them as
mixed or unclassified.

## Main files

| File | Description |
| ---- | ----------- |
| `comments.csv` | Source comment export before PII cleaning |
| `comments_no_pii.csv` | Cleaned input used by the analysis scripts |
| `comments_analysis_output.csv` | Comment-level sentiment, emotion, toxicity, and text metrics |
| `comments_with_bertopic.csv` | Comment-level BERTopic assignment, confidence, keywords, and primary concern |
| `bertopic_topic_info.csv` | Topic counts, keywords, representative comments, and primary-concern labels |
| `tfidf_top_terms_overall.csv` | Corpus-wide TF-IDF term rankings |
| `top_words_and_phrases.csv` | Most frequent unigrams, bigrams, and trigrams |
| `sentiment_distribution.png` | Sentiment-label distribution |
| `emotion_distribution.png` | Dominant-emotion distribution |
| `toxicity_distribution.png` | Toxicity-score distribution |

## Scripts

### Sentiment, emotion, and toxicity

`comment_sentiment_emotion_toxicity.py` runs transformer models over the
cleaned comments. It uses CUDA when available and automatically reduces the
batch size after a CUDA out-of-memory error.

From this directory, run:

```bash
python comment_sentiment_emotion_toxicity.py \
  --input comments_no_pii.csv \
  --output-dir part_a_outputs
```

Useful options:

- `--batch-size N` sets the initial inference batch size (default: `128`).
- `--min-rows N` changes the input-size safety check; use `0` to disable it.
- `--skip-sentiment`, `--skip-emotion`, and `--skip-toxicity` omit individual
  model stages.

The main generated file is
`part_a_outputs/comments_part_a_analysis.csv`. The directory also contains
summary CSVs, stage checkpoints, and plots.

### Lexical analysis and topic modeling

`comment_topics_bertopic.py` generates word and phrase counts, TF-IDF results,
NMF themes and word clouds, BERTopic assignments, and the reviewed
`Primary_Concern` labels.

```bash
python comment_topics_bertopic.py \
  --input comments_no_pii.csv \
  --output-dir topic_lloom_outputs
```

Useful options:

- `--skip-bertopic` runs the lexical, TF-IDF, and NMF stages without BERTopic.
- `--lloom` enables LLooM concept induction and requires `OPENAI_API_KEY`.
- `--lloom-full` also scores the full corpus with LLooM.

BERTopic uses CUDA for sentence embeddings when a compatible GPU is available.
The primary BERTopic outputs are
`topic_lloom_outputs/bertopic_topic_info.csv` and
`topic_lloom_outputs/comments_with_bertopic.csv`.

## Output schemas

### `comments_with_bertopic.csv`

| Column | Description |
| ------ | ----------- |
| `_original_index` | Original row index in the cleaned input |
| `comment_id` | Regulations.gov comment identifier |
| `Topic` | BERTopic topic ID |
| `Name` | Automatically generated keyword-based topic name |
| `Primary_Concern` | Reviewed, human-readable summary of the assigned topic |
| `Top_n_words` | Highest-ranking words and phrases for the topic |
| `Probability` | Confidence of the topic assignment |

### `bertopic_topic_info.csv`

| Column | Description |
| ------ | ----------- |
| `Topic` | BERTopic topic ID |
| `Count` | Number of comments assigned to the topic |
| `Name` | Automatically generated keyword-based topic name |
| `Primary_Concern` | Reviewed primary-concern label |
| `Representation` | Highest-ranking topic keywords and phrases |
| `Representative_Docs` | Comments that best represent the topic |

### `comments_analysis_output.csv`

This file contains comment metadata and the following analysis groups:

- sentiment probabilities, dominant label, confidence, and continuous scores;
- emotion probabilities, dominant label, and confidence;
- toxicity-category scores and the `toxicity_flag_0_5` threshold flag; and
- comment word count and exact-duplicate indicator.

## Interpreting the results

BERTopic categories summarize recurring patterns in the corpus; they do not
prove that every sentence in a comment concerns only that topic. Likewise,
`Primary_Concern` is a topic-level interpretation rather than a separate model
prediction for each comment. Use the original cleaned text, assignment
probability, topic keywords, and representative comments when reviewing
borderline or high-impact cases.
