"""Download written comment text from Regulations.gov."""

import html
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


# Regulations.gov: webpage comment text with API key rotation.
# Downloads only the written comment text shown on comment pages for
# ICEB-2025-0001-0001. No PDFs or attachment text are downloaded.

# 1. Settings
DOCUMENT_ID = "ICEB-2025-0001-0001"

API_KEYS = [
    "zgfiEGwgj1pnI4dzErbPhCGNknJjPQ4WFlpQh62R",
    "fHswWCgqaAPksyRaJ8lp5AJncUb0tlDLPNn9XTB4",
    "COwIGgYsIp9jkMQAWLGfijmFfiaaSDXUxus0Wcq2",
    "W8gVxXDZiZZBO47EAqCHKvkYaeHTGYjEIJ2mBs9g",
    "TZbljDFSP1hcZJoNaNltw9Kh0jTlr6hTCkEq1b9t",
    "wIqdBiq6a1es3g2NZAXfgjhpqqkEk3GAS7RIeUIv",
]

# Or paste them locally:
# API_KEYS = ["KEY_1", "KEY_2", "KEY_3", "KEY_4", "KEY_5", "KEY_6"]

API_KEYS = [key for key in API_KEYS if key]
if not API_KEYS:
    raise ValueError("No API keys found. Add at least one key before continuing.")

print(f"Loaded {len(API_KEYS)} API key(s).")

PAGE_SIZE = 250
PAGES_PER_BATCH = 20
CHECKPOINT_EVERY = 100
REQUEST_DELAY = 0.10

ID_CHECKPOINT_FILE = Path(f"{DOCUMENT_ID}_comment_ids.csv")
TEXT_CHECKPOINT_FILE = Path(f"{DOCUMENT_ID}_webpage_text_checkpoint.csv")
OUTPUT_FILE = Path(f"{DOCUMENT_ID}_webpage_comment_text.csv")


# 2. API-key rotation and cooldown handling
key_available_at = {index: 0 for index in range(len(API_KEYS))}
next_key_index = 0


def get_available_key():
    global next_key_index
    while True:
        now = time.time()
        for _ in range(len(API_KEYS)):
            index = next_key_index
            next_key_index = (next_key_index + 1) % len(API_KEYS)
            if key_available_at[index] <= now:
                return index, API_KEYS[index]
        earliest = min(key_available_at.values())
        wait_seconds = max(1, earliest - now)
        print(
            f"All {len(API_KEYS)} API keys are cooling down. "
            f"Waiting {wait_seconds:.0f} seconds..."
        )
        time.sleep(wait_seconds)


def regulations_get(url, params=None, max_retries=50):
    params = {} if params is None else params
    attempts = 0
    while attempts < max_retries:
        key_index, api_key = get_available_key()
        request_params = params.copy()
        request_params["api_key"] = api_key
        try:
            response = requests.get(url, params=request_params, timeout=60)
            if response.status_code == 429:
                try:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                except (TypeError, ValueError):
                    retry_after = 60
                key_available_at[key_index] = time.time() + retry_after
                print(
                    f"Key {key_index + 1} rate limited. Cooldown: "
                    f"{retry_after}s. Trying another key..."
                )
                attempts += 1
                continue
            if response.status_code in (500, 502, 503, 504):
                wait = min(60, 2 ** min(attempts, 6))
                print(f"Server error {response.status_code}. Waiting {wait}s...")
                time.sleep(wait)
                attempts += 1
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            wait = min(60, 2 ** min(attempts, 6))
            print(f"Request failed: {exc}. Retrying in {wait}s...")
            time.sleep(wait)
            attempts += 1
    raise RuntimeError(f"Request failed after {max_retries} attempts: {url}")


# 3. Test one known comment
TEST_COMMENT_ID = "ICEB-2025-0001-3802"
response = regulations_get(f"https://api.regulations.gov/v4/comments/{TEST_COMMENT_ID}")
attributes = response.json()["data"]["attributes"]
raw = attributes.get("comment")
clean = html.unescape(raw).strip() if isinstance(raw, str) else raw
print("COMMENT TEXT:\n")
print(clean)


# 4. Get the document object ID and total comment count
response = regulations_get(f"https://api.regulations.gov/v4/documents/{DOCUMENT_ID}")
object_id = response.json()["data"]["attributes"]["objectId"]
print("Object ID:", object_id)

comments_url = "https://api.regulations.gov/v4/comments"
response = regulations_get(
    comments_url,
    params={"filter[commentOnId]": object_id, "page[size]": 5},
)
total_comments = response.json().get("meta", {}).get("totalElements")
print("Total comments:", total_comments)


# 5. Collect all comment IDs.
def utc_to_eastern_filter(timestamp):
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S")


if ID_CHECKPOINT_FILE.exists():
    id_df = pd.read_csv(ID_CHECKPOINT_FILE)
    comment_ids = id_df["comment_id"].astype(str).tolist()
    print(f"Loaded {len(comment_ids):,} comment IDs from checkpoint.")
else:
    all_comments = {}
    cursor_date = None
    previous_cursor = None
    batch = 1
    while True:
        print(f"\n--- ID Batch {batch} ---")
        batch_last = None
        reached_end = False
        for page in range(1, PAGES_PER_BATCH + 1):
            params = {
                "filter[commentOnId]": object_id,
                "page[size]": PAGE_SIZE,
                "page[number]": page,
                "sort": "lastModifiedDate,documentId",
            }
            if cursor_date is not None:
                params["filter[lastModifiedDate][ge]"] = cursor_date
            response = regulations_get(comments_url, params=params)
            records = response.json().get("data", [])
            if not records:
                reached_end = True
                break
            batch_last = records[-1]
            for record in records:
                all_comments[record["id"]] = record
            print(f"Page {page:>2}: {len(records):>3} | unique IDs: {len(all_comments):,}")
            if total_comments is not None and len(all_comments) >= total_comments:
                reached_end = True
                break
            if len(records) < PAGE_SIZE:
                reached_end = True
                break
            time.sleep(REQUEST_DELAY)
        if reached_end:
            break
        last_modified = batch_last.get("attributes", {}).get("lastModifiedDate")
        if not last_modified:
            raise RuntimeError("Missing lastModifiedDate for pagination")
        previous_cursor = cursor_date
        cursor_date = utc_to_eastern_filter(last_modified)
        if cursor_date == previous_cursor:
            raise RuntimeError("Pagination cursor did not advance")
        batch += 1
    comment_ids = list(all_comments.keys())
    pd.DataFrame({"comment_id": comment_ids}).to_csv(ID_CHECKPOINT_FILE, index=False)
    print(f"Saved {len(comment_ids):,} comment IDs.")


# 6. Download only the webpage comment text.
def get_comment_text(comment_id):
    response = regulations_get(f"https://api.regulations.gov/v4/comments/{comment_id}")
    attributes = response.json()["data"].get("attributes", {})
    raw_comment = attributes.get("comment")
    clean_comment = (
        html.unescape(raw_comment).strip()
        if isinstance(raw_comment, str)
        else raw_comment
    )
    return {
        "comment_id": comment_id,
        "comment_url": f"https://www.regulations.gov/comment/{comment_id}",
        "title": attributes.get("title"),
        "posted_date": attributes.get("postedDate"),
        "comment": clean_comment,
    }


if TEXT_CHECKPOINT_FILE.exists():
    existing_df = pd.read_csv(TEXT_CHECKPOINT_FILE)
    rows = existing_df.to_dict("records")
    completed_ids = set(existing_df["comment_id"].astype(str))
    print(f"Resuming from {len(completed_ids):,} saved comments.")
else:
    rows = []
    completed_ids = set()

remaining_ids = [comment_id for comment_id in comment_ids if comment_id not in completed_ids]
print("Comments remaining:", f"{len(remaining_ids):,}")

for index, comment_id in enumerate(remaining_ids, start=1):
    try:
        rows.append(get_comment_text(comment_id))
    except Exception as exc:
        print(f"Failed {comment_id}: {exc}")
        continue
    if index % CHECKPOINT_EVERY == 0:
        checkpoint_df = pd.DataFrame(rows).drop_duplicates("comment_id", keep="last")
        checkpoint_df.to_csv(TEXT_CHECKPOINT_FILE, index=False)
        print(f"Saved {len(checkpoint_df):,} comments")
    time.sleep(REQUEST_DELAY)

text_df = pd.DataFrame(rows).drop_duplicates("comment_id", keep="last")
text_df.to_csv(TEXT_CHECKPOINT_FILE, index=False)
print(f"Detailed comment records saved: {len(text_df):,}")


# 7. Keep only comments that contain written webpage text.
text_df = pd.read_csv("comments.csv")
has_text = text_df["comment"].fillna("").astype(str).str.strip().ne("")
webpage_text_df = text_df.loc[has_text].copy()

print("Comment pages checked:", f"{len(text_df):,}")
print("Pages with written text:", f"{len(webpage_text_df):,}")
print("Pages without written text:", f"{(~has_text).sum():,}")


# 8. Verify the example comment.
webpage_text_df = pd.read_csv("comments.csv")
test_comment_id = "ICEB-2025-0001-3802"
webpage_text_df["comment_id"] = webpage_text_df["comment_id"].astype(str).str.strip()
example = webpage_text_df[webpage_text_df["comment_id"] == test_comment_id]

if not example.empty:
    print("Comment found!\n")
    print(example.iloc[0]["comment"])
else:
    print("Example comment was not found in comments.csv.")