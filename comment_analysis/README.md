# Comment Analysis

This folder contains the outputs from the comment analysis pipeline. The analysis includes **sentiment analysis, emotion detection, toxicity analysis, TF-IDF analysis, word/phrase frequency analysis, and BERTopic topic modeling**.

## Comment Analysis Scripts

This folder contains two scripts for analyzing cleaned comment data.

### `comment_sentiment_emotion_toxicity.py`

Runs transformer-based analysis on each comment and produces:

* Sentiment scores and labels
* Emotion scores and labels
* Toxicity scores
* A combined comment-level CSV containing all model outputs
* Summary CSV files for sentiment, emotion, and toxicity

The script is GPU-enabled and will automatically use CUDA when available.

**Input:**
`comments_no_pii_clean.csv`

**Main output:**
[`comments_analysis_output.csv`](https://github.com/kenenwodo/innovation-challenge-msftxcci-2026/blob/main/comment_analysis/comments_analysis_output.csv)

---

### `comment_words_phrases_bertopic.py`

Performs lexical and topic analysis on the cleaned comments and produces:

* Most frequent words
* Most frequent bigrams and trigrams
* TF-IDF term rankings
* BERTopic topic information
* BERTopic topic assignments for each comment

**Input:**
`comments_no_pii_clean.csv`

**Main outputs:**

* `top_words_and_phrases.csv`
* `bertopic_topic_info.csv`
* `comments_with_bertopic.csv`

## Files

### `comments_analysis_output.csv`

Main comment-level analysis dataset containing sentiment, emotion, toxicity, and text-level metrics.

| Column                    | Description                                                        |
| ------------------------- | ------------------------------------------------------------------ |
| `comment_id`              | Unique identifier for the comment                                  |
| `comment_url`             | URL associated with the comment                                    |
| `title`                   | Title associated with the source comment                           |
| `posted_date`             | Date the comment was posted                                        |
| `comment_clean`           | Cleaned comment text used for analysis                             |
| `sentiment_negative`      | Predicted negative sentiment score                                 |
| `sentiment_neutral`       | Predicted neutral sentiment score                                  |
| `sentiment_positive`      | Predicted positive sentiment score                                 |
| `sentiment_label`         | Predicted sentiment category                                       |
| `sentiment_confidence`    | Confidence of the predicted sentiment label                        |
| `sentiment_signed`        | Signed sentiment score representing negative-to-positive direction |
| `sentiment_continuous`    | Continuous sentiment score                                         |
| `emotion_neutral`         | Neutral emotion score                                              |
| `emotion_sadness`         | Sadness score                                                      |
| `emotion_anger`           | Anger score                                                        |
| `emotion_disgust`         | Disgust score                                                      |
| `emotion_fear`            | Fear score                                                         |
| `emotion_surprise`        | Surprise score                                                     |
| `emotion_joy`             | Joy score                                                          |
| `emotion_label`           | Predicted dominant emotion                                         |
| `emotion_confidence`      | Confidence of the predicted emotion                                |
| `toxicity_toxic`          | Toxicity score                                                     |
| `toxicity_severe_toxic`   | Severe toxicity score                                              |
| `toxicity_obscene`        | Obscene-language score                                             |
| `toxicity_threat`         | Threat score                                                       |
| `toxicity_insult`         | Insult score                                                       |
| `toxicity_identity_hate`  | Identity-hate score                                                |
| `toxicity_flag_0_5`       | Binary toxicity flag using a 0.5 threshold                         |
| `comment_word_count`      | Number of words in the cleaned comment                             |
| `is_exact_duplicate_text` | Indicates whether the same comment text appears elsewhere          |
| `toxicity`                | Overall toxicity value                                             |

---

### `comments_with_bertopic.csv`

Contains BERTopic topic-modeling results for individual comments.

| Column                    | Description                                                              |
| ------------------------- | ------------------------------------------------------------------------ |
| `_original_index`         | Original row/index of the comment                                        |
| `comment_id`              | Unique identifier for the comment                                        |
| `Topic`                   | BERTopic-assigned topic ID                                               |
| `Name`                    | Automatically generated topic name                                       |
| `Primary_Concern`         | Reviewed, human-readable summary of the topic's primary concern          |
| `Top_n_words`             | Top words associated with the topic                                      |
| `Probability`             | Probability/confidence of the topic assignment                           |

---

### `bertopic_topic_info.csv`

Summary of the topics discovered by BERTopic.

| Column                | Description                                    |
| --------------------- | ---------------------------------------------- |
| `Topic`               | BERTopic topic ID                              |
| `Count`               | Number of comments assigned to the topic       |
| `Name`                | Automatically generated topic name             |
| `Primary_Concern`     | Reviewed, human-readable primary concern label |
| `Representation`      | Most representative keywords for the topic     |
| `Representative_Docs` | Example comments that best represent the topic |

> Topic `-1` represents comments BERTopic classified as outliers rather than assigning to a specific topic.

---

### `tfidf_top_terms_overall.csv`

Contains TF-IDF results identifying terms that are important across the comment corpus.

| Column           | Description                                      |
| ---------------- | ------------------------------------------------ |
| `term`           | Word or term identified by TF-IDF                |
| `mean_tfidf`     | Average TF-IDF score for the term                |
| `document_count` | Number of comments/documents containing the term |

---

### `top_words_and_phrases.csv`

Contains the most frequently occurring words and phrases in the comments.

| Column       | Description                                        |
| ------------ | -------------------------------------------------- |
| `ngram_type` | Type of term, such as unigram or multi-word phrase |
| `term`       | Word or phrase                                     |
| `count`      | Number of occurrences in the corpus                |

---

## Visualizations

The folder also contains distribution plots generated from the comment analysis:

* `sentiment_distribution.png` — distribution of predicted sentiment labels
* `emotion_distribution.png` — distribution of predicted emotion labels
* `toxicity_distribution.png` — distribution of toxicity results

## Analysis Overview

The outputs in this folder can be used to examine:

* Overall sentiment toward the analyzed topic
* Emotional patterns within public comments
* Toxic or harmful language
* Common words and phrases
* Important terms using TF-IDF
* Recurring themes and discussion topics identified through BERTopic

This version is ready to save directly as `comment_analysis/README.md`.
