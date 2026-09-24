#!/usr/bin/env python3
"""
Part A — GPU sentiment, emotion, and toxicity analysis

Optimized for CUDA GPUs such as an NVIDIA RTX 5090.
Default input: comments_no_pii.csv

Expected ~10,000 comments. The script refuses to silently analyze a tiny
partial recovery if the CSV is malformed.

Outputs:
  part_a_outputs/
    comments_part_a_analysis.csv
    sentiment_summary.csv
    emotion_summary.csv
    toxicity_summary.csv
    corpus_summary.csv
    checkpoint_after_sentiment.csv
    checkpoint_after_emotion.csv
    checkpoint_after_toxicity.csv
    plots/
"""

from pathlib import Path
import argparse
import gc
import html
import os
import random
import re
import sys
import warnings

import numpy as np
import pandas as pd

# Headless-safe plotting for remote GPU servers.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tqdm.auto import tqdm

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("display.max_columns", None)

# ============================================================
# CONFIGURATION
# ============================================================

DEFAULT_INPUT_CSV = "comments_no_pii.csv"
DEFAULT_OUTPUT_DIR = "part_a_outputs"

# You said this corpus contains about 10,000 comments.
# This prevents a malformed CSV from silently giving you only ~1,700 rows.
MIN_EXPECTED_ROWS = 9000

TEXT_COL = None
ID_COL = None
GROUP_COL = None

DROP_EMPTY_COMMENTS = True
DROP_EXACT_DUPLICATES = False

RUN_SENTIMENT = True
RUN_EMOTION = True
RUN_TOXICITY = True

SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"
TOXICITY_MODEL = "unitary/toxic-bert"

# RTX 5090 starting point.
# The script automatically halves this if a CUDA OOM occurs.
DEFAULT_BATCH_SIZE = 128
MAX_LENGTH = 512

RANDOM_SEED = 42


# ============================================================
# ENVIRONMENT
# ============================================================

try:
    import torch
except ImportError as exc:
    raise ImportError(
        "PyTorch is required. Install your CUDA build of PyTorch first."
    ) from exc

from transformers import (
    pipeline,
    AutoTokenizer,
    AutoModelForSequenceClassification,
)

CUDA = torch.cuda.is_available()
DEVICE = 0 if CUDA else -1
TORCH_DEVICE = torch.device("cuda:0" if CUDA else "cpu")

if CUDA:
    # Good defaults for modern NVIDIA GPUs.
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True


# ============================================================
# HELPERS
# ============================================================

TEXT_CANDIDATES = [
    # Prefer the cleaned field you created earlier.
    "comment_clean",
    "comment_text_clean",
    "comment_text",
    "COMMENT TEXT",
    "Comment Text",
    "comment",
    "Comment",
    "comments",
    "Comments",
    "text",
    "Text",
    "attributes.comment",
    "attributes_comment",
    "commentText",
    "comment_body",
    "body",
]

ID_CANDIDATES = [
    "comment_id",
    "commentId",
    "document_id",
    "documentId",
    "id",
    "ID",
    "objectId",
]


def first_existing(candidates, columns):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def clean_comment_text(value):
    if pd.isna(value):
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " URL ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_label(label):
    return re.sub(
        r"[^a-z0-9]+",
        "_",
        str(label).strip().lower(),
    ).strip("_")


def ensure_candidate_list(item):
    return [item] if isinstance(item, dict) else item


def free_gpu_memory():
    gc.collect()
    if CUDA:
        torch.cuda.empty_cache()


def print_gpu_info():
    print("=" * 72)
    print("PART A — Sentiment, Emotion, and Toxicity")
    print("=" * 72)
    print("PyTorch:", torch.__version__)
    print("CUDA build:", torch.version.cuda)
    print("CUDA available:", CUDA)

    if CUDA:
        props = torch.cuda.get_device_properties(0)
        print("GPU:", torch.cuda.get_device_name(0))
        print(f"VRAM: {props.total_memory / (1024**3):.1f} GB")
        print("Architecture support:", torch.cuda.get_arch_list())
    else:
        print("WARNING: GPU was not detected. Inference will use CPU.")


def load_csv_safely(path, min_rows):
    """
    Read the CSV normally first.

    If pandas reports malformed quoting, try the Python parser only to diagnose
    how much can be recovered. Do NOT silently continue with a tiny fraction of
    the expected ~10k-row corpus.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Could not find: {path.resolve()}")

    try:
        df = pd.read_csv(
            path,
            low_memory=False,
            encoding_errors="replace",
        )
    except pd.errors.ParserError as exc:
        print("\nCSV PARSER ERROR")
        print(exc)
        print("\nTrying a tolerant diagnostic read...")

        try:
            recovered = pd.read_csv(
                path,
                engine="python",
                on_bad_lines="warn",
                encoding_errors="replace",
            )
        except Exception as recovery_exc:
            raise RuntimeError(
                "The CSV appears malformed and could not be recovered. "
                "Re-export or re-upload comments_no_pii.csv."
            ) from recovery_exc

        print(f"Tolerant parser recovered {len(recovered):,} rows.")

        if min_rows and len(recovered) < min_rows:
            raise RuntimeError(
                f"\nSTOPPING: only {len(recovered):,} rows were recoverable, "
                f"but this dataset should contain about 10,000 comments.\n"
                "Do not analyze this partial file. Re-export or re-upload "
                "comments_no_pii.csv, then run this script again."
            )

        print(
            "WARNING: continuing with the tolerant parse because it still "
            "contains the expected number of rows."
        )
        df = recovered

    if min_rows and len(df) < min_rows:
        raise RuntimeError(
            f"Loaded only {len(df):,} rows, but at least {min_rows:,} "
            "were expected. Check comments_no_pii.csv before analysis."
        )

    return df


def build_export_columns(df):
    """
    Keep the compact metadata requested earlier, plus Part A outputs.
    """
    preferred_base = [
        "comment_id",
        "comment_url",
        "title",
        "posted_date",
        "comment_clean",
    ]

    base_cols = [c for c in preferred_base if c in df.columns]

    # If comment_clean did not exist in the input, include our generated field.
    if "comment_clean" not in base_cols and "comment_clean" in df.columns:
        base_cols.append("comment_clean")

    analysis_prefixes = (
        "sentiment_",
        "emotion_",
        "toxicity_",
    )

    analysis_cols = [
        c for c in df.columns
        if c.startswith(analysis_prefixes)
    ]

    for c in [
        "comment_word_count",
        "is_exact_duplicate_text",
        "toxicity",
    ]:
        if c in df.columns and c not in analysis_cols:
            analysis_cols.append(c)

    # Preserve order and remove duplicates.
    cols = []
    for c in base_cols + analysis_cols:
        if c not in cols:
            cols.append(c)

    return cols


def save_checkpoint(df, output_dir, name):
    cols = build_export_columns(df)
    path = Path(output_dir) / name
    df[cols].to_csv(path, index=False)
    print(f"Checkpoint saved: {path.resolve()}")
    return path


# ============================================================
# GPU BATCHED PIPELINE
# ============================================================

def run_pipeline_gpu(
    clf,
    texts,
    initial_batch_size,
    desc,
    max_length=MAX_LENGTH,
):
    """
    Batched Hugging Face pipeline inference with automatic OOM recovery.
    """
    outputs = []
    i = 0
    batch_size = initial_batch_size

    pbar = tqdm(
        total=len(texts),
        desc=desc,
        unit="comments",
    )

    while i < len(texts):
        current = texts[i:i + batch_size]

        try:
            out = clf(
                current,
                batch_size=batch_size,
                truncation=True,
                max_length=max_length,
            )
            outputs.extend(out)
            i += len(current)
            pbar.update(len(current))

        except torch.cuda.OutOfMemoryError:
            free_gpu_memory()

            if batch_size <= 8:
                pbar.close()
                raise

            old_batch = batch_size
            batch_size = max(8, batch_size // 2)

            print(
                f"\nCUDA OOM at batch size {old_batch}. "
                f"Retrying with batch size {batch_size}..."
            )

    pbar.close()
    return outputs, batch_size


def load_classifier(model_name):
    """
    Load classifier in FP16 on CUDA to reduce memory and increase throughput.
    """
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    kwargs = {}
    if CUDA:
        kwargs["torch_dtype"] = torch.float16

    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        **kwargs,
    )

    clf = pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        device=DEVICE,
        top_k=None,
    )

    return clf, model, tokenizer


# ============================================================
# SENTIMENT
# ============================================================

def run_sentiment(df, output_dir, batch_size):
    print("\n" + "-" * 72)
    print("SENTIMENT")
    print("-" * 72)
    print("Model:", SENTIMENT_MODEL)

    clf, model, tokenizer = load_classifier(SENTIMENT_MODEL)

    outputs, used_batch_size = run_pipeline_gpu(
        clf,
        df["comment_clean"].tolist(),
        initial_batch_size=batch_size,
        desc="Sentiment",
    )

    fallback_map = {
        "label_0": "negative",
        "label_1": "neutral",
        "label_2": "positive",
    }

    top_labels = []
    top_scores = []
    signed = []
    continuous = []

    for row_i, candidates in enumerate(outputs):
        candidates = ensure_candidate_list(candidates)
        score_map = {}

        for candidate in candidates:
            raw_label = safe_label(candidate["label"])
            label = fallback_map.get(raw_label, raw_label)
            score = float(candidate["score"])
            score_map[label] = score
            df.at[row_i, f"sentiment_{label}"] = score

        best_label = max(score_map, key=score_map.get)
        top_labels.append(best_label)
        top_scores.append(score_map[best_label])
        signed.append(
            {
                "negative": -1,
                "neutral": 0,
                "positive": 1,
            }.get(best_label, np.nan)
        )
        continuous.append(
            score_map.get("positive", 0.0)
            - score_map.get("negative", 0.0)
        )

    df["sentiment_label"] = top_labels
    df["sentiment_confidence"] = top_scores
    df["sentiment_signed"] = signed
    df["sentiment_continuous"] = continuous

    summary = (
        df.groupby("sentiment_label", dropna=False)
        .agg(
            comments=("comment_clean", "size"),
            mean_confidence=("sentiment_confidence", "mean"),
            mean_continuous=("sentiment_continuous", "mean"),
        )
        .reset_index()
        .sort_values("comments", ascending=False)
    )

    summary["fraction"] = summary["comments"] / len(df)
    summary.to_csv(
        Path(output_dir) / "sentiment_summary.csv",
        index=False,
    )

    print("\nSentiment summary:")
    print(summary.to_string(index=False))
    print("Final sentiment batch size:", used_batch_size)

    del clf, model, tokenizer, outputs
    free_gpu_memory()

    save_checkpoint(
        df,
        output_dir,
        "checkpoint_after_sentiment.csv",
    )

    return df, used_batch_size


# ============================================================
# EMOTION
# ============================================================

def run_emotion(df, output_dir, batch_size):
    print("\n" + "-" * 72)
    print("EMOTION")
    print("-" * 72)
    print("Model:", EMOTION_MODEL)

    clf, model, tokenizer = load_classifier(EMOTION_MODEL)

    outputs, used_batch_size = run_pipeline_gpu(
        clf,
        df["comment_clean"].tolist(),
        initial_batch_size=batch_size,
        desc="Emotion",
    )

    top_labels = []
    top_scores = []

    for row_i, candidates in enumerate(outputs):
        candidates = ensure_candidate_list(candidates)
        best = max(
            candidates,
            key=lambda x: float(x["score"]),
        )

        top_labels.append(safe_label(best["label"]))
        top_scores.append(float(best["score"]))

        for candidate in candidates:
            label = safe_label(candidate["label"])
            df.at[row_i, f"emotion_{label}"] = float(
                candidate["score"]
            )

    df["emotion_label"] = top_labels
    df["emotion_confidence"] = top_scores

    summary = (
        df.groupby("emotion_label", dropna=False)
        .agg(
            comments=("comment_clean", "size"),
            mean_confidence=("emotion_confidence", "mean"),
        )
        .reset_index()
        .sort_values("comments", ascending=False)
    )

    summary["fraction"] = summary["comments"] / len(df)
    summary.to_csv(
        Path(output_dir) / "emotion_summary.csv",
        index=False,
    )

    print("\nEmotion summary:")
    print(summary.to_string(index=False))
    print("Final emotion batch size:", used_batch_size)

    del clf, model, tokenizer, outputs
    free_gpu_memory()

    save_checkpoint(
        df,
        output_dir,
        "checkpoint_after_emotion.csv",
    )

    return df, used_batch_size


# ============================================================
# TOXICITY
# ============================================================

def run_toxicity(df, output_dir, initial_batch_size):
    print("\n" + "-" * 72)
    print("TOXICITY")
    print("-" * 72)
    print("Model:", TOXICITY_MODEL)

    tokenizer = AutoTokenizer.from_pretrained(TOXICITY_MODEL)

    kwargs = {}
    if CUDA:
        kwargs["torch_dtype"] = torch.float16

    model = AutoModelForSequenceClassification.from_pretrained(
        TOXICITY_MODEL,
        **kwargs,
    )

    model.to(TORCH_DEVICE)
    model.eval()

    id2label = {
        int(k): safe_label(v)
        for k, v in model.config.id2label.items()
    }

    texts = df["comment_clean"].tolist()
    all_prob_rows = []

    i = 0
    batch_size = initial_batch_size

    pbar = tqdm(
        total=len(texts),
        desc="Toxicity",
        unit="comments",
    )

    with torch.inference_mode():
        while i < len(texts):
            batch_texts = texts[i:i + batch_size]

            try:
                encoded = tokenizer(
                    batch_texts,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=MAX_LENGTH,
                )

                encoded = {
                    k: v.to(TORCH_DEVICE)
                    for k, v in encoded.items()
                }

                if CUDA:
                    with torch.autocast(
                        device_type="cuda",
                        dtype=torch.float16,
                    ):
                        logits = model(**encoded).logits
                else:
                    logits = model(**encoded).logits

                probs = (
                    torch.sigmoid(logits)
                    .float()
                    .cpu()
                    .numpy()
                )

                all_prob_rows.append(probs)
                i += len(batch_texts)
                pbar.update(len(batch_texts))

                del encoded, logits, probs

            except torch.cuda.OutOfMemoryError:
                free_gpu_memory()

                if batch_size <= 8:
                    pbar.close()
                    raise

                old_batch = batch_size
                batch_size = max(8, batch_size // 2)

                print(
                    f"\nCUDA OOM at batch size {old_batch}. "
                    f"Retrying with batch size {batch_size}..."
                )

    pbar.close()

    tox_probs = np.vstack(all_prob_rows)

    for label_id, label_name in id2label.items():
        df[f"toxicity_{label_name}"] = tox_probs[:, label_id]

    toxic_ids = [
        i for i, label in id2label.items()
        if label == "toxic"
    ]

    toxic_idx = toxic_ids[0] if toxic_ids else 0

    if not toxic_ids:
        print(
            "WARNING: exact 'toxic' label was not found; "
            "using the first model output."
        )

    df["toxicity"] = tox_probs[:, toxic_idx]
    df["toxicity_flag_0_5"] = df["toxicity"] >= 0.5

    summary = pd.DataFrame({
        "metric": [
            "mean",
            "median",
            "p90",
            "p95",
            "max",
            "fraction_at_or_above_0.5",
        ],
        "value": [
            df["toxicity"].mean(),
            df["toxicity"].median(),
            df["toxicity"].quantile(0.90),
            df["toxicity"].quantile(0.95),
            df["toxicity"].max(),
            df["toxicity_flag_0_5"].mean(),
        ],
    })

    summary.to_csv(
        Path(output_dir) / "toxicity_summary.csv",
        index=False,
    )

    print("\nToxicity summary:")
    print(summary.to_string(index=False))
    print("Final toxicity batch size:", batch_size)

    del model, tokenizer, tox_probs, all_prob_rows
    free_gpu_memory()

    save_checkpoint(
        df,
        output_dir,
        "checkpoint_after_toxicity.csv",
    )

    return df, batch_size


# ============================================================
# PLOTS
# ============================================================

def make_plots(df, output_dir):
    plot_dir = Path(output_dir) / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    if "sentiment_label" in df.columns:
        counts = (
            df["sentiment_label"]
            .value_counts()
            .reindex(["negative", "neutral", "positive"])
            .dropna()
        )

        plt.figure(figsize=(7, 4))
        counts.plot(kind="bar")
        plt.title("Comment Sentiment Distribution")
        plt.xlabel("Sentiment")
        plt.ylabel("Comments")
        plt.xticks(rotation=0)
        plt.tight_layout()
        plt.savefig(
            plot_dir / "sentiment_distribution.png",
            dpi=160,
            bbox_inches="tight",
        )
        plt.close()

    if "emotion_label" in df.columns:
        counts = df["emotion_label"].value_counts()

        plt.figure(figsize=(8, 4.5))
        counts.plot(kind="bar")
        plt.title("Comment Emotion Distribution")
        plt.xlabel("Emotion")
        plt.ylabel("Comments")
        plt.xticks(rotation=35, ha="right")
        plt.tight_layout()
        plt.savefig(
            plot_dir / "emotion_distribution.png",
            dpi=160,
            bbox_inches="tight",
        )
        plt.close()

    if "toxicity" in df.columns:
        plt.figure(figsize=(7, 4))
        plt.hist(df["toxicity"].dropna(), bins=40)
        plt.title("Comment Toxicity Score Distribution")
        plt.xlabel("Toxicity score")
        plt.ylabel("Comments")
        plt.tight_layout()
        plt.savefig(
            plot_dir / "toxicity_distribution.png",
            dpi=160,
            bbox_inches="tight",
        )
        plt.close()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Part A GPU sentiment, emotion, and toxicity analysis."
        )
    )

    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_CSV,
    )

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
    )

    parser.add_argument(
        "--min-rows",
        type=int,
        default=MIN_EXPECTED_ROWS,
        help=(
            "Safety check for this ~10k-comment corpus. "
            "Use 0 to disable."
        ),
    )

    parser.add_argument(
        "--skip-sentiment",
        action="store_true",
    )

    parser.add_argument(
        "--skip-emotion",
        action="store_true",
    )

    parser.add_argument(
        "--skip-toxicity",
        action="store_true",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print_gpu_info()
    print("Input:", Path(args.input).resolve())
    print("Output:", output_dir.resolve())
    print("Starting batch size:", args.batch_size)
    print("Maximum token length:", MAX_LENGTH)

    # --------------------------------------------------------
    # Load
    # --------------------------------------------------------
    raw_df = load_csv_safely(
        args.input,
        min_rows=args.min_rows,
    )

    print(
        f"\nLoaded {len(raw_df):,} rows x "
        f"{raw_df.shape[1]:,} columns"
    )

    # --------------------------------------------------------
    # Detect columns
    # --------------------------------------------------------
    text_col = (
        TEXT_COL
        if TEXT_COL is not None
        else first_existing(TEXT_CANDIDATES, raw_df.columns)
    )

    if text_col is None:
        raise ValueError(
            "Could not find the comment text column.\n"
            f"Available columns: {raw_df.columns.tolist()}"
        )

    id_col = (
        ID_COL
        if ID_COL is not None
        else first_existing(ID_CANDIDATES, raw_df.columns)
    )

    df = raw_df.copy()

    if id_col is None:
        id_col = "comment_id"
        df[id_col] = np.arange(1, len(df) + 1)

    print("Using text column:", text_col)
    print("Using ID column:", id_col)

    # --------------------------------------------------------
    # Clean
    # --------------------------------------------------------
    df["comment_clean"] = (
        df[text_col]
        .fillna("")
        .astype(str)
        .map(clean_comment_text)
    )

    df["comment_word_count"] = (
        df["comment_clean"]
        .str.findall(r"\b\w+\b")
        .str.len()
    )

    df["is_exact_duplicate_text"] = df.duplicated(
        "comment_clean",
        keep=False,
    )

    if DROP_EMPTY_COMMENTS:
        df = df[df["comment_clean"].ne("")].copy()

    if DROP_EXACT_DUPLICATES:
        df = df.drop_duplicates(
            "comment_clean",
            keep="first",
        ).copy()

    df = df.reset_index(drop=True)

    print(f"Rows retained after cleaning: {len(df):,}")

    if args.min_rows and len(df) < args.min_rows:
        raise RuntimeError(
            f"Only {len(df):,} non-empty comments remain, "
            f"below the safety threshold of {args.min_rows:,}."
        )

    # --------------------------------------------------------
    # Corpus summary
    # --------------------------------------------------------
    corpus_summary = pd.DataFrame({
        "metric": [
            "comments",
            "exact_duplicate_rows",
            "median_words",
            "mean_words",
            "p90_words",
        ],
        "value": [
            len(df),
            int(df["is_exact_duplicate_text"].sum()),
            float(df["comment_word_count"].median()),
            float(df["comment_word_count"].mean()),
            float(df["comment_word_count"].quantile(0.90)),
        ],
    })

    corpus_summary.to_csv(
        output_dir / "corpus_summary.csv",
        index=False,
    )

    print("\nCorpus summary:")
    print(corpus_summary.to_string(index=False))

    # --------------------------------------------------------
    # Models
    # --------------------------------------------------------
    batch_size = args.batch_size

    if RUN_SENTIMENT and not args.skip_sentiment:
        df, batch_size = run_sentiment(
            df,
            output_dir,
            batch_size,
        )

    if RUN_EMOTION and not args.skip_emotion:
        df, batch_size = run_emotion(
            df,
            output_dir,
            batch_size,
        )

    if RUN_TOXICITY and not args.skip_toxicity:
        df, batch_size = run_toxicity(
            df,
            output_dir,
            batch_size,
        )

    # --------------------------------------------------------
    # Plots + final export
    # --------------------------------------------------------
    make_plots(df, output_dir)

    export_cols = build_export_columns(df)
    final_path = (
        output_dir / "comments_part_a_analysis.csv"
    )

    df[export_cols].to_csv(
        final_path,
        index=False,
    )

    print("\n" + "=" * 72)
    print("DONE")
    print("=" * 72)
    print(f"Rows analyzed: {len(df):,}")
    print("Final file:")
    print(final_path.resolve())

    print("\nSaved columns:")
    for col in export_cols:
        print(" -", col)


if __name__ == "__main__":
    main()
