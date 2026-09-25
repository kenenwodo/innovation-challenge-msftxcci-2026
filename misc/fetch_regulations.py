#!/usr/bin/env python3
"""Fetch a Regulations.gov document and its text-only public comments.

With --final-rule, fetch the final rule published in the same docket instead
(document files only; a final rule takes no comments), so it can be diffed
against the proposed rule.

Uses the Regulations.gov v4 API (https://open.gsa.gov/api/regulationsgov/).

Outputs (in --out-dir):
  document.json            Document metadata (with attachments included)
  document/content.*       Downloaded document files (html, pdf, ...)
  comment_ids.json         Every comment ID on the document (listing cache)
  text_comments.jsonl      Comments kept, one JSON object per line
  skipped_comments.jsonl   Comments skipped, with the reason
  text_comments.csv        Flat CSV of the kept comments
  rule_versions.json       (--final-rule only) proposed -> final rule link

Runs are resumable: already-processed comment IDs are skipped on restart.

Usage:
  # Put your key in a .env file next to this script:
  #   REGULATIONS_API_KEY=your_key   (get one at https://open.gsa.gov/api/regulationsgov/)
  python3 fetch_regulations.py ICEB-2025-0001-0001
  python3 fetch_regulations.py ICEB-2025-0001-0001 --final-rule   # -> data/ICEB-2025-0001-21962
  python3 fetch_regulations.py ICEB-2025-0001-21962 --document-only
"""

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

API_BASE = "https://api.regulations.gov/v4"
PAGE_SIZE = 250
MAX_PAGES = 20  # the API caps a single query at 20 pages of 250 (5000 results)
RATE_LIMIT_SLEEP = 600
EASTERN = ZoneInfo("America/New_York")
# downloads.regulations.gov returns 403 for non-browser User-Agents (e.g. Python-urllib, curl)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PLACEHOLDER_RE = re.compile(
    r"^(please\s+)?(see|refer\s+to)\s+(the\s+)?attach(ed|ment|ments)"
    r"(\s+(file\(s\)|files?|letter|documents?|comments?|pdf))?[\s.!]*$",
    re.IGNORECASE,
)
CSV_FIELDS = [
    "id", "title", "postedDate", "receiveDate", "firstName", "lastName",
    "organization", "category", "city", "stateProvinceRegion", "country",
    "trackingNbr", "commentText",
]


class Client:
    def __init__(self, api_key):
        self.api_key = api_key
        self.requests_made = 0

    def get(self, path, params=None):
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe="[],")
        req = urllib.request.Request(
            url, headers={"X-Api-Key": self.api_key, "Accept": "application/vnd.api+json"}
        )
        for attempt in range(8):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests_made += 1
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    body = json.load(resp)
                    if remaining is not None and int(remaining) == 0:
                        log(f"Rate limit reached; sleeping {RATE_LIMIT_SLEEP}s")
                        time.sleep(RATE_LIMIT_SLEEP)
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    log(f"429 Too Many Requests; sleeping {RATE_LIMIT_SLEEP}s")
                    time.sleep(RATE_LIMIT_SLEEP)
                elif e.code >= 500:
                    wait = 2 ** attempt
                    log(f"HTTP {e.code} for {url}; retrying in {wait}s")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"HTTP {e.code} for {url}: {e.read()[:500]!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                wait = 2 ** attempt
                log(f"Network error ({e}); retrying in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"Giving up on {url}")


def load_dotenv(path):
    """Parse KEY=VALUE lines from a .env file (ignores blanks, comments, and 'export ')."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def html_to_text(raw):
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>|</p>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def is_placeholder(text):
    """True if the text is only a pointer to attachments (e.g. 'See attached file(s).')."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return all(PLACEHOLDER_RE.match(s) for s in sentences)


def to_eastern(iso_utc):
    """Convert '2020-08-10T15:58:52Z' to '2020-08-10 11:58:52' (Eastern), as the API expects."""
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S")


def fetch_document(client, document_id, out_dir):
    doc = client.get(f"/documents/{document_id}", {"include": "attachments"})
    (out_dir / "document.json").write_text(json.dumps(doc, indent=2))
    attrs = doc["data"]["attributes"]
    log(f"Document: {attrs.get('title')}")

    files_dir = out_dir / "document"
    files_dir.mkdir(exist_ok=True)
    for f in attrs.get("fileFormats") or []:
        url = f["fileUrl"]
        dest = files_dir / Path(urllib.parse.urlparse(url).path).name
        if dest.exists():
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                dest.write_bytes(resp.read())
            log(f"Downloaded {dest.name}")
        except Exception as e:
            log(f"Could not download {url}: {e}")
    return attrs


def find_final_rule(client, document_id):
    """Return (proposed_attrs, final_id, final_attrs) for the final rule in document_id's docket."""
    proposed = client.get(f"/documents/{document_id}")["data"]["attributes"]
    docket_id = proposed["docketId"]
    resp = client.get("/documents", {
        "filter[docketId]": docket_id,
        "filter[documentType]": "Rule",
        "sort": "-postedDate",
        "page[size]": 25,
    })
    rules = resp["data"]
    if not rules:
        sys.exit(f"No final rule (documentType 'Rule') posted yet in docket {docket_id}")
    if len(rules) > 1:
        others = ", ".join(r["id"] for r in rules[1:])
        log(f"Docket {docket_id} has {len(rules)} rules; using the latest ({rules[0]['id']}). Others: {others}")
    return proposed, rules[0]["id"], rules[0]["attributes"]


def write_rule_versions(path, proposed_id, proposed, proposed_dir, final_id, final, final_dir):
    def entry(doc_id, attrs, doc_dir):
        return {
            "id": doc_id,
            "documentType": attrs.get("documentType"),
            "frDocNum": attrs.get("frDocNum"),
            "postedDate": attrs.get("postedDate"),
            "dir": str(doc_dir),
        }
    path.write_text(json.dumps({
        "docketId": final.get("docketId"),
        "proposed": entry(proposed_id, proposed, proposed_dir),
        "final": {**entry(final_id, final, final_dir), "effectiveDate": final.get("effectiveDate")},
    }, indent=2))


def list_comment_ids(client, object_id, cache_path):
    """Return all comment IDs on a document, paging past the 5000-result cap
    by re-querying with filter[lastModifiedDate][ge] (per the API docs)."""
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        if cache.get("complete"):
            log(f"Loaded {len(cache['ids'])} comment IDs from cache")
            return cache["ids"]

    ids = {}
    since = None
    while True:
        new_in_batch = 0
        last_modified = None
        finished = False
        for page in range(1, MAX_PAGES + 1):
            params = {
                "filter[commentOnId]": object_id,
                "page[size]": PAGE_SIZE,
                "page[number]": page,
                "sort": "lastModifiedDate,documentId",
            }
            if since:
                params["filter[lastModifiedDate][ge]"] = since
            resp = client.get("/comments", params)
            for item in resp["data"]:
                if item["id"] not in ids:
                    ids[item["id"]] = True
                    new_in_batch += 1
                last_modified = item["attributes"]["lastModifiedDate"]
            total = resp["meta"].get("totalElements")
            log(f"Listing: page {page}, {len(ids)} IDs collected (this query reports {total})")
            if not resp["meta"].get("hasNextPage"):
                finished = True
                break
        if finished or new_in_batch == 0 or last_modified is None:
            break
        since = to_eastern(last_modified)

    id_list = list(ids)
    cache_path.write_text(json.dumps({"complete": True, "ids": id_list}))
    return id_list


def load_processed(*paths):
    done = set()
    for p in paths:
        if p.exists():
            with p.open() as fh:
                for line in fh:
                    if line.strip():
                        done.add(json.loads(line)["id"])
    return done


def classify(detail, allow_attachments):
    """Return (keep, reason, record) for a comment detail response."""
    data = detail["data"]
    attrs = data["attributes"]
    attachments = (data.get("relationships", {}).get("attachments", {}).get("data")) or []
    has_attachments = bool(attachments or detail.get("included"))
    text = html_to_text(attrs.get("comment"))

    record = {"id": data["id"], **attrs, "commentText": text, "hasAttachments": has_attachments}
    if attrs.get("withdrawn"):
        return False, "withdrawn", record
    if is_placeholder(text):
        return False, "no_text" if not text else "placeholder_text", record
    if has_attachments and not allow_attachments:
        return False, "has_attachments", record
    return True, None, record


def write_csv(jsonl_path, csv_path):
    with jsonl_path.open() as src, csv_path.open("w", newline="") as dst:
        writer = csv.DictWriter(dst, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        count = 0
        for line in src:
            if line.strip():
                writer.writerow(json.loads(line))
                count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("document_id", nargs="?", default="ICEB-2025-0001-0001")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parent / ".env")
    parser.add_argument("--api-key", help="Overrides REGULATIONS_API_KEY from the .env file")
    parser.add_argument("--out-dir", type=Path, help="Default: data/<document_id>")
    parser.add_argument(
        "--allow-attachments", action="store_true",
        help="Also keep comments that have attachments, as long as their text is "
             "more than an 'see attached' placeholder. By default any comment with "
             "an attachment is skipped.",
    )
    parser.add_argument(
        "--document-only", action="store_true",
        help="Fetch only the document metadata and files; skip comments.",
    )
    parser.add_argument(
        "--final-rule", action="store_true",
        help="Treat document_id as the proposed rule, find the final rule in the same "
             "docket, and fetch that instead (implies --document-only). Also writes "
             "rule_versions.json linking the two for v1-vs-v2 diffs.",
    )
    parser.add_argument("--limit", type=int, help="Process at most N new comments (for testing)")
    parser.add_argument(
        "--export-csv", action="store_true",
        help="Only rebuild text_comments.csv from the comments downloaded so far (no API calls). "
             "Don't run it while a fetch is in progress.",
    )
    args = parser.parse_args()

    if args.export_csv:
        out_dir = args.out_dir or Path("data") / args.document_id
        kept_path = out_dir / "text_comments.jsonl"
        if not kept_path.exists():
            sys.exit(f"No comments downloaded yet: {kept_path} not found")
        count = write_csv(kept_path, out_dir / "text_comments.csv")
        log(f"Wrote {count} comments to {out_dir / 'text_comments.csv'}")
        return

    if not args.api_key:
        args.api_key = load_dotenv(args.env_file).get("REGULATIONS_API_KEY") or "DEMO_KEY"
    if args.api_key == "DEMO_KEY":
        log(f"WARNING: using DEMO_KEY (10 requests/hour). Set REGULATIONS_API_KEY in {args.env_file}.")

    client = Client(args.api_key)
    proposed_id = args.document_id
    if args.final_rule:
        proposed, args.document_id, _ = find_final_rule(client, proposed_id)
        args.document_only = True
        log(f"Final rule for {proposed_id}: {args.document_id}")

    out_dir = args.out_dir or Path("data") / args.document_id
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_attrs = fetch_document(client, args.document_id, out_dir)
    if args.final_rule:
        write_rule_versions(out_dir / "rule_versions.json", proposed_id, proposed,
                            Path("data") / proposed_id, args.document_id, doc_attrs, out_dir)
    if args.document_only:
        log(f"Done (document only). {client.requests_made} API requests this run. Output in {out_dir}/")
        return

    comment_ids = list_comment_ids(client, doc_attrs["objectId"], out_dir / "comment_ids.json")

    kept_path = out_dir / "text_comments.jsonl"
    skipped_path = out_dir / "skipped_comments.jsonl"
    done = load_processed(kept_path, skipped_path)
    todo = [c for c in comment_ids if c not in done]
    if args.limit:
        todo = todo[: args.limit]
    log(f"{len(comment_ids)} comments total, {len(done)} already processed, {len(todo)} to fetch")

    kept = skipped = 0
    with kept_path.open("a") as kept_fh, skipped_path.open("a") as skipped_fh:
        for i, comment_id in enumerate(todo, 1):
            detail = client.get(f"/comments/{comment_id}", {"include": "attachments"})
            keep, reason, record = classify(detail, args.allow_attachments)
            if keep:
                kept_fh.write(json.dumps(record) + "\n")
                kept += 1
            else:
                skipped_fh.write(json.dumps({"id": comment_id, "reason": reason,
                                             "title": record.get("title")}) + "\n")
                skipped += 1
            kept_fh.flush()
            skipped_fh.flush()
            if i % 25 == 0 or i == len(todo):
                log(f"Details: {i}/{len(todo)} (kept {kept}, skipped {skipped})")

    write_csv(kept_path, out_dir / "text_comments.csv")
    log(f"Done. {client.requests_made} API requests this run. Output in {out_dir}/")


if __name__ == "__main__":
    main()
