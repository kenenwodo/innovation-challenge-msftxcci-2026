#!/usr/bin/env python3
"""
Parts B + C from comment_sentiment_emotion_topics_lloom.ipynb

Part B:
  - top words / bigrams / trigrams
  - TF-IDF
  - NMF themes + word clouds
  - optional BERTopic

Part C:
  - LLooM discovery sample
  - optional LLooM concept induction
  - optional full-corpus LLooM scoring

Designed to run independently from Part A and use CUDA automatically for BERTopic embeddings.
Expected cleaned input column: comment_clean
"""

from pathlib import Path
from collections import Counter
import argparse
import asyncio
import html
import os
import re
import warnings

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 160)

# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_INPUT_CSV = "comments_no_pii.csv"
DEFAULT_OUTPUT_DIR = "topic_lloom_outputs"

TEXT_COL = "comment_clean"
ID_COL = "comment_id"

RANDOM_SEED = 42

# Part B
N_NMF_THEMES = 8
RUN_BERTOPIC = True
N_BERTOPIC_CATEGORIES = 10

# Part C
# Keep False until OPENAI_API_KEY is configured.
RUN_LLOOM = False
LLOOM_DISCOVERY_SAMPLE = 1000
LLOOM_MAX_CONCEPTS = 8
LLOOM_SEED = (
    "arguments, concerns, support, opposition, and perceived impacts "
    "expressed in the comments"
)
LLOOM_SCORE_FULL_DATASET = False
LLOOM_SCORE_BATCH_SIZE = 5

DEFAULT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him", "his",
    "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on",
    "or", "our", "ours", "she", "that", "the", "their", "theirs", "them",
    "they", "this", "to", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "would", "you", "your", "yours",
    "http", "https", "www", "url", "amp"
}
EXTRA_STOPWORDS = set()
STOPWORDS = DEFAULT_STOPWORDS | EXTRA_STOPWORDS


# ============================================================
# HELPERS
# ============================================================

def clean_comment_text(value):
    if pd.isna(value):
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " URL ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_topics(text):
    text = clean_comment_text(text).lower()
    text = text.replace("_", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9'#+.\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize_for_topics(text, min_len=3):
    raw_terms = re.findall(
        r"[a-z][a-z0-9'#+.\-]*",
        normalize_for_topics(text),
    )
    terms = [t.strip("'#+.-") for t in raw_terms]
    return [
        t for t in terms
        if len(t) >= min_len
        and t not in STOPWORDS
        and not t.isdigit()
    ]


def clean_for_topics(text):
    return " ".join(tokenize_for_topics(text))


def load_clean_comments(input_csv):
    input_csv = Path(input_csv)

    if not input_csv.exists():
        raise FileNotFoundError(f"Could not find: {input_csv.resolve()}")

    df = pd.read_csv(input_csv, low_memory=False)

    if TEXT_COL not in df.columns:
        raise ValueError(
            f"Expected a column named {TEXT_COL!r}.\n"
            f"Available columns: {df.columns.tolist()}"
        )

    if ID_COL not in df.columns:
        print(f"{ID_COL!r} not found; creating row IDs.")
        df[ID_COL] = np.arange(1, len(df) + 1)

    df[TEXT_COL] = df[TEXT_COL].fillna("").astype(str).map(clean_comment_text)
    df = df[df[TEXT_COL].str.strip().ne("")].copy().reset_index(drop=True)

    df["comment_text_nlp"] = df[TEXT_COL].map(clean_for_topics)

    print(f"Loaded {len(df):,} cleaned comments.")
    print(f"Text column: {TEXT_COL}")
    print(f"ID column:   {ID_COL}")

    return df


# ============================================================
# PART B — LEXICAL + TOPIC/THEME ANALYSIS
# ============================================================

def run_part_b(df, output_dir, run_bertopic=True):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import NMF
    from wordcloud import WordCloud

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("PART B — Lexical and topic/theme analysis")
    print("=" * 70)

    # --------------------------------------------------------
    # B1. Top words, bigrams, and trigrams
    # --------------------------------------------------------
    print("\n[B1] Top words, bigrams, and trigrams")

    row_tokens = [tokenize_for_topics(text) for text in df[TEXT_COL].fillna("")]

    unigram_counts = Counter(t for toks in row_tokens for t in toks)
    bigram_counts = Counter()
    trigram_counts = Counter()

    for toks in row_tokens:
        bigram_counts.update(zip(toks, toks[1:]))
        trigram_counts.update(zip(toks, toks[1:], toks[2:]))

    rows = []

    for term, count in unigram_counts.most_common(100):
        rows.append({"ngram_type": "unigram", "term": term, "count": count})

    for terms, count in bigram_counts.most_common(100):
        rows.append({
            "ngram_type": "bigram",
            "term": " ".join(terms),
            "count": count,
        })

    for terms, count in trigram_counts.most_common(100):
        rows.append({
            "ngram_type": "trigram",
            "term": " ".join(terms),
            "count": count,
        })

    top_phrases = pd.DataFrame(rows)
    top_phrases.to_csv(
        output_dir / "top_words_and_phrases.csv",
        index=False,
    )

    print(top_phrases.head(30).to_string(index=False))

    # --------------------------------------------------------
    # B2. TF-IDF
    # --------------------------------------------------------
    print("\n[B2] TF-IDF")

    topic_docs = df["comment_text_nlp"].fillna("").astype(str)
    topic_docs = topic_docs[topic_docs.str.strip().ne("")]

    if len(topic_docs) < 2:
        raise ValueError("Not enough non-empty comments for TF-IDF/topic analysis.")

    min_df = (
        1 if len(topic_docs) < 30
        else 2 if len(topic_docs) < 100
        else 3
    )

    tfidf_vectorizer = TfidfVectorizer(
        stop_words=list(STOPWORDS),
        ngram_range=(1, 3),
        min_df=min_df,
        max_df=0.95,
        max_features=5000,
    )

    tfidf_X = tfidf_vectorizer.fit_transform(topic_docs)
    tfidf_terms_arr = tfidf_vectorizer.get_feature_names_out()
    mean_scores = np.asarray(tfidf_X.mean(axis=0)).ravel()
    doc_counts = np.asarray((tfidf_X > 0).sum(axis=0)).ravel()

    tfidf_terms = pd.DataFrame({
        "term": tfidf_terms_arr,
        "mean_tfidf": mean_scores,
        "document_count": doc_counts,
    }).sort_values(
        ["mean_tfidf", "document_count"],
        ascending=[False, False],
    )

    tfidf_terms.to_csv(
        output_dir / "tfidf_top_terms_overall.csv",
        index=False,
    )

    print(tfidf_terms.head(30).to_string(index=False))

    # --------------------------------------------------------
    # B3. NMF themes
    # --------------------------------------------------------
    print("\n[B3] NMF themes")

    nmf_dir = output_dir / "nmf_theme_wordclouds"
    nmf_dir.mkdir(parents=True, exist_ok=True)

    nmf_vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words=list(STOPWORDS),
        max_df=0.95,
        min_df=min_df,
        max_features=8000,
        ngram_range=(1, 2),
    )

    nmf_X = nmf_vectorizer.fit_transform(topic_docs)
    nmf_terms = nmf_vectorizer.get_feature_names_out()

    n_themes = min(
        N_NMF_THEMES,
        nmf_X.shape[0],
        nmf_X.shape[1],
    )

    nmf_model = NMF(
        n_components=n_themes,
        random_state=RANDOM_SEED,
        init="nndsvda",
        max_iter=1000,
    )

    doc_theme_matrix = nmf_model.fit_transform(nmf_X)
    theme_term_matrix = nmf_model.components_

    def top_theme_terms(theme_idx, n=12):
        ids = theme_term_matrix[theme_idx].argsort()[::-1][:n]
        return [nmf_terms[i] for i in ids]

    dominant_theme = doc_theme_matrix.argmax(axis=1)
    theme_strength = doc_theme_matrix.max(axis=1)

    theme_labels = {}
    theme_rows = []

    for i in range(n_themes):
        words = top_theme_terms(i, 12)
        label = f"Theme {i + 1}: " + " / ".join(words[:3])
        theme_labels[i] = label

        theme_rows.append({
            "theme_id": i + 1,
            "theme_label": label,
            "top_terms": ", ".join(words),
            "n_comments": int((dominant_theme == i).sum()),
        })

    nmf_theme_summary = (
        pd.DataFrame(theme_rows)
        .sort_values("n_comments", ascending=False)
        .reset_index(drop=True)
    )

    nmf_theme_summary.to_csv(
        output_dir / "nmf_theme_summary.csv",
        index=False,
    )

    print(nmf_theme_summary.to_string(index=False))

    df["nmf_theme_id"] = pd.NA
    df["nmf_theme_label"] = pd.NA
    df["nmf_theme_strength"] = np.nan

    df.loc[topic_docs.index, "nmf_theme_id"] = dominant_theme + 1
    df.loc[topic_docs.index, "nmf_theme_label"] = [
        theme_labels[i] for i in dominant_theme
    ]
    df.loc[topic_docs.index, "nmf_theme_strength"] = theme_strength

    print("\nCreating NMF word clouds...")

    for i in range(n_themes):
        top_ids = theme_term_matrix[i].argsort()[::-1][:150]

        frequencies = {
            nmf_terms[j]: float(theme_term_matrix[i][j])
            for j in top_ids
            if theme_term_matrix[i][j] > 0
        }

        if not frequencies:
            continue

        wc = WordCloud(
            width=1600,
            height=900,
            background_color="white",
            max_words=150,
            collocations=False,
        ).generate_from_frequencies(frequencies)

        plt.figure(figsize=(9, 5))
        plt.imshow(wc, interpolation="bilinear")
        plt.axis("off")
        plt.title(theme_labels[i])
        plt.tight_layout()
        plt.savefig(
            nmf_dir / f"theme_{i + 1:02d}.png",
            dpi=180,
            bbox_inches="tight",
        )
        plt.close()

    df.to_csv(
        output_dir / "comments_with_nmf_themes.csv",
        index=False,
    )

    # --------------------------------------------------------
    # B4. BERTopic
    # --------------------------------------------------------
    if run_bertopic:
        print("\n[B4] BERTopic")
        print("Loading embedding model...")

        try:
            import torch
            if torch.cuda.is_available():
                print("GPU:", torch.cuda.get_device_name(0))
            else:
                print("GPU not detected — BERTopic embeddings will run on CPU.")
        except Exception:
            pass

        from bertopic import BERTopic
        from sentence_transformers import SentenceTransformer
        from sklearn.feature_extraction.text import CountVectorizer
        from umap import UMAP
        from hdbscan import HDBSCAN

        bertopic_docs = df[TEXT_COL].fillna("").astype(str).str.strip()
        bertopic_docs = bertopic_docs[bertopic_docs.ne("")]

        min_topic_size = max(
            10,
            min(50, max(10, len(bertopic_docs) // 100)),
        )

        embedding_model = SentenceTransformer(
            "all-MiniLM-L6-v2",
            device="cuda" if _cuda_available() else "cpu",
        )

        vectorizer_model = CountVectorizer(
            stop_words=list(STOPWORDS),
            ngram_range=(1, 3),
            min_df=min_df,
        )

        umap_model = UMAP(
            n_neighbors=15,
            n_components=5,
            min_dist=0.0,
            metric="cosine",
            random_state=RANDOM_SEED,
        )

        hdbscan_model = HDBSCAN(
            min_cluster_size=min_topic_size,
            metric="euclidean",
            cluster_selection_method="eom",
            prediction_data=True,
        )

        bertopic_model = BERTopic(
            embedding_model=embedding_model,
            vectorizer_model=vectorizer_model,
            umap_model=umap_model,
            hdbscan_model=hdbscan_model,
            nr_topics=N_BERTOPIC_CATEGORIES,
            calculate_probabilities=False,
            verbose=True,
        )

        topic_ids, _ = bertopic_model.fit_transform(
            bertopic_docs.tolist()
        )

        topic_info = bertopic_model.get_topic_info()
        topic_info.to_csv(
            output_dir / "bertopic_topic_info.csv",
            index=False,
        )

        doc_info = bertopic_model.get_document_info(
            bertopic_docs.tolist()
        )

        doc_info.insert(
            0,
            "_original_index",
            bertopic_docs.index.to_numpy(),
        )

        bertopic_assignments = (
            df.loc[
                bertopic_docs.index,
                [ID_COL, TEXT_COL],
            ]
            .reset_index()
            .rename(columns={"index": "_original_index"})
            .merge(
                doc_info,
                on="_original_index",
                how="left",
            )
        )

        bertopic_assignments.to_csv(
            output_dir / "comments_with_bertopic_categories.csv",
            index=False,
        )

        print("\nTop BERTopic categories:")
        print(topic_info.head(20).to_string(index=False))

    else:
        print("\n[B4] BERTopic skipped.")

    return df


def _cuda_available():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


# ============================================================
# PART C — LLOOM CONCEPT INDUCTION
# ============================================================

def prepare_lloom_sample(df, output_dir):
    print("\n" + "=" * 70)
    print("PART C — LLooM concept induction")
    print("=" * 70)

    source_cols = [ID_COL, TEXT_COL]

    # These are optional. They will be included automatically if you later
    # run this script on a Part-A output file containing these columns.
    for col in [
        "sentiment_label",
        "sentiment_continuous",
        "emotion_label",
        "toxicity",
    ]:
        if col in df.columns:
            source_cols.append(col)

    source_df = df[source_cols].copy()
    source_df = source_df[source_df[TEXT_COL].ne("")].copy()

    sample_n = min(
        LLOOM_DISCOVERY_SAMPLE,
        len(source_df),
    )

    # Stratify by sentiment when available; otherwise use a random sample.
    if (
        "sentiment_label" in source_df.columns
        and source_df["sentiment_label"].nunique() > 1
    ):
        pieces = []
        labels = (
            source_df["sentiment_label"]
            .dropna()
            .unique()
            .tolist()
        )

        per_label = max(1, sample_n // len(labels))

        for label in labels:
            group = source_df[
                source_df["sentiment_label"] == label
            ]
            pieces.append(
                group.sample(
                    n=min(per_label, len(group)),
                    random_state=RANDOM_SEED,
                )
            )

        discovery_df = (
            pd.concat(pieces)
            .drop_duplicates(subset=[ID_COL])
        )

        if len(discovery_df) < sample_n:
            remaining = source_df.drop(
                index=discovery_df.index,
                errors="ignore",
            )

            extra_n = min(
                sample_n - len(discovery_df),
                len(remaining),
            )

            if extra_n > 0:
                discovery_df = pd.concat([
                    discovery_df,
                    remaining.sample(
                        extra_n,
                        random_state=RANDOM_SEED,
                    ),
                ])

    else:
        discovery_df = source_df.sample(
            n=sample_n,
            random_state=RANDOM_SEED,
        )

    discovery_df = discovery_df.reset_index(drop=True)

    print(
        f"LLooM discovery sample: "
        f"{len(discovery_df):,} comments"
    )

    discovery_df.to_csv(
        Path(output_dir) / "lloom_discovery_sample.csv",
        index=False,
    )

    return source_df, discovery_df


async def run_lloom(
    source_df,
    discovery_df,
    output_dir,
    score_full=False,
):
    if not os.environ.get("OPENAI_API_KEY"):
        raise EnvironmentError(
            "OPENAI_API_KEY is not set.\n"
            "Set it before running LLooM."
        )

    import text_lloom.workbench as wb

    print("\nInitializing LLooM...")

    l = wb.lloom(
        df=discovery_df,
        text_col=TEXT_COL,
        id_col=ID_COL,
    )

    print("\nGeneration cost estimate:")
    print(l.estimate_gen_cost(verbose=True))

    print("\nGenerating LLooM concepts...")

    lloom_sample_scores = await l.gen_auto(
        max_concepts=LLOOM_MAX_CONCEPTS,
        seed=LLOOM_SEED,
    )

    lloom_sample_scores.to_csv(
        Path(output_dir) / "lloom_discovery_sample_scores.csv",
        index=False,
    )

    lloom_concepts = l.export_df()

    lloom_concepts.to_csv(
        Path(output_dir) / "lloom_concepts_summary.csv",
        index=False,
    )

    print("\nLLooM concepts:")
    print(lloom_concepts.to_string(index=False))

    if score_full:
        print(
            f"\nPreparing to score "
            f"{len(source_df):,} comments."
        )

        print("\nEstimated full-corpus scoring cost:")
        print(l.estimate_score_cost(verbose=True))

        full_scores = await l.score(
            df=source_df,
            score_all=True,
            batch_size=LLOOM_SCORE_BATCH_SIZE,
        )

        full_scores.to_csv(
            Path(output_dir) / "lloom_full_corpus_scores.csv",
            index=False,
        )

        full_summary = l.export_df()

        full_summary.to_csv(
            Path(output_dir)
            / "lloom_full_corpus_concept_summary.csv",
            index=False,
        )

        print("\nFull-corpus LLooM summary:")
        print(full_summary.to_string(index=False))


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run Parts B and C: lexical/topic analysis + LLooM "
            "on a cleaned comment CSV."
        )
    )

    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_CSV,
        help=f"Input CSV. Default: {DEFAULT_INPUT_CSV}",
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )

    parser.add_argument(
        "--skip-bertopic",
        action="store_true",
        help="Run NMF/TF-IDF but skip BERTopic.",
    )

    parser.add_argument(
        "--lloom",
        action="store_true",
        help="Enable LLooM concept induction.",
    )

    parser.add_argument(
        "--lloom-full",
        action="store_true",
        help="Also score the full corpus with LLooM.",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Input:", Path(args.input).resolve())
    print("Output:", output_dir.resolve())

    df = load_clean_comments(args.input)

    df = run_part_b(
        df=df,
        output_dir=output_dir,
        run_bertopic=not args.skip_bertopic,
    )

    source_df, discovery_df = prepare_lloom_sample(
        df=df,
        output_dir=output_dir,
    )

    if args.lloom or RUN_LLOOM:
        asyncio.run(
            run_lloom(
                source_df=source_df,
                discovery_df=discovery_df,
                output_dir=output_dir,
                score_full=args.lloom_full or LLOOM_SCORE_FULL_DATASET,
            )
        )
    else:
        print(
            "\nLLooM generation skipped. "
            "The 1,000-comment discovery sample was still saved."
        )
        print(
            "To run LLooM later, configure OPENAI_API_KEY "
            "and add --lloom."
        )

    print("\nDone.")
    print("Outputs saved to:")
    print(output_dir.resolve())


if __name__ == "__main__":
    main()
