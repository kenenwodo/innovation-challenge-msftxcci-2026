#!/usr/bin/env python3
"""
GDELT news search for DHS Docket ICEB-2025-0001 (duration of status rule), v2.

What it does:
  1. Imports results from the previous run (v1 output CSV, if present)
  2. Searches GDELT for each window in SEARCHES (wide phrase + docket number)
  3. Fetches full article text (direct, then Internet Archive copy if blocked)
  4. Saves everything to one CSV and prints a status summary

Everything is checkpointed in gdelt_checkpoint/, so if it stops or a window
is rate-limited, just run it again: finished work is reused.

Setup (once):
    pip install requests pandas beautifulsoup4 trafilatura

Run:
    python3 gdelt_wide_search_v2.py
    python3 gdelt_wide_search_v2.py --no-text    # GDELT search only
    python3 gdelt_wide_search_v2.py --refresh    # ignore checkpoints, search all again

To pick up newer coverage for one window, delete gdelt_checkpoint/<window>.csv
and run again.

Output: gdelt_news_articles_v3-112.csv   Log: gdelt_run.log
"""
import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
import requests

# ============================================================== settings
API = "https://api.gdeltproject.org/api/v2/doc/doc"
TODAY = datetime.utcnow().strftime("%Y-%m-%d")

RULE_Q = '"duration of status" "international students"'
COURT_Q = '"duration of status" (Saylor OR injunction)'
DOCKET_Q = '"ICEB-2025-0001"'

SEARCHES = [
    ("nprm_published",          "2025-08-24", "2025-09-01", RULE_Q),
    ("comment_deadline",        "2025-09-25", "2025-10-03", RULE_Q),
    ("final_rule",              "2026-07-13", "2026-07-21", RULE_Q),
    ("injunction",              "2026-09-10", TODAY,        COURT_Q),
    ("nprm_published_docket",   "2025-08-24", "2025-09-01", DOCKET_Q),
    ("comment_deadline_docket", "2025-09-25", "2025-10-03", DOCKET_Q),
    ("final_rule_docket",       "2026-07-13", "2026-07-21", DOCKET_Q),
    ("injunction_docket",       "2026-09-10", TODAY,        DOCKET_Q),
]

OUT_CSV = "gdelt_news_articles_v3-112.csv"
LOG_FILE = "gdelt_run.log"
CHECKPOINT_DIR = "gdelt_checkpoint"
TEXT_CACHE = os.path.join(CHECKPOINT_DIR, "article_texts.jsonl")
IMPORT_MARKER = os.path.join(CHECKPOINT_DIR, ".imported_previous_windows")

PACE_SECONDS = 12     # minimum gap between GDELT requests
MAX_RETRIES = 8       # per GDELT request
TEXT_WORKERS = 6      # article pages fetched in parallel
MIN_CHARS = 300       # shorter text = probably a block page, not the article

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
KEYWORDS = ("regulations.gov", "federalregister.gov", "courtlistener.com",
            "ice.gov", "uscis.gov", "dhs.gov", ".pdf")
COLS = ["window", "seendate", "title", "url", "domain", "language",
        "sourcecountry", "socialimage"]

# ============================================================== logging
log = logging.getLogger("gdelt")


def setup_logging():
    log.setLevel(logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    logfile = logging.FileHandler(LOG_FILE, encoding="utf-8")
    logfile.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(console)
    log.addHandler(logfile)


def banner(title):
    log.info("")
    log.info("=" * 72)
    log.info(title)
    log.info("=" * 72)


def fmt_secs(s):
    s = int(s)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


# ============================================================== step 1: import previous run
def import_previous(refresh):
    banner("STEP 1/3  Import previous results")
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    if refresh:
        log.info("--refresh given: not importing anything")
        return
    if not os.path.exists(OUT_CSV):
        log.info(f"No previous {OUT_CSV} found; starting fresh")
        return
    old = pd.read_csv(OUT_CSV)
    log.info(f"Found {OUT_CSV} with {len(old)} rows")

    # Windows: import only once, so deleting a checkpoint later really re-searches it
    if os.path.exists(IMPORT_MARKER):
        log.info("Windows: already imported on an earlier run (using checkpoints)")
    else:
        for label, *_ in SEARCHES:
            part = old[old["window"] == label]
            if len(part) and not os.path.exists(window_path(label)):
                part[COLS].to_csv(window_path(label), index=False)
                log.info(f"  window {label:24s} imported {len(part)} articles")
        open(IMPORT_MARKER, "w").close()

    # Article text: merge any good text not already cached
    if "article_text" not in old.columns:
        return
    cache = load_text_cache()
    good = old[old["article_text"].notna()
               & ~old["article_text"].astype(str).str.startswith("ERROR")
               & ~old["url"].isin(cache.keys())]
    if len(good):
        with open(TEXT_CACHE, "a", encoding="utf-8") as f:
            for _, r in good.iterrows():
                src = r.get("text_source")
                links = r.get("related_links")
                f.write(json.dumps({
                    "url": r["url"],
                    "article_text": r["article_text"],
                    "related_links": "" if pd.isna(links) else links,
                    "text_source": "direct" if pd.isna(src) else src,
                }) + "\n")
    log.info(f"Article text: {len(good)} new texts added to cache "
             f"({len(cache) + len(good)} cached in total)")


# ============================================================== step 2: GDELT search
session = requests.Session()
_last_call = [0.0]


class RateLimited(Exception):
    pass


def window_path(label):
    return os.path.join(CHECKPOINT_DIR, f"{label}.csv")


def gdelt(query, params):
    params = {**params, "query": query, "format": "json"}
    for attempt in range(1, MAX_RETRIES + 1):
        gap = time.time() - _last_call[0]
        if gap < PACE_SECONDS:
            time.sleep(PACE_SECONDS - gap)
        _last_call[0] = time.time()
        t = time.time()
        try:
            r = session.get(API, params=params, timeout=90)
        except requests.RequestException as ex:
            log.info(f"      attempt {attempt}/{MAX_RETRIES}: network error "
                     f"({ex.__class__.__name__}), retrying in 30s")
            time.sleep(30)
            continue
        took = time.time() - t
        text = r.text.strip()
        if r.status_code == 429 or text.lower().startswith("please limit"):
            wait = min(15 * attempt, 60)
            log.info(f"      attempt {attempt}/{MAX_RETRIES}: rate limited "
                     f"(answer took {took:.1f}s), waiting {wait}s")
            time.sleep(wait)
            continue
        log.info(f"      GDELT answered in {took:.1f}s (HTTP {r.status_code})")
        if not text:
            return None
        try:
            return r.json()
        except json.JSONDecodeError:
            log.info(f"      GDELT message: {text[:200]}")
            return None
    raise RateLimited()


def ts(d):
    return d.strftime("%Y%m%d%H%M%S")


def fetch_range(query, start, end, depth=0):
    """Article list for [start, end); halves the range if it hits the 250 cap."""
    pad = "  " * depth
    log.info(f"    {pad}requesting {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}")
    data = gdelt(query, {"mode": "artlist", "maxrecords": 250, "sort": "datedesc",
                         "startdatetime": ts(start), "enddatetime": ts(end)})
    arts = (data or {}).get("articles", [])
    if len(arts) >= 250 and (end - start) > timedelta(hours=6):
        mid = start + (end - start) / 2
        log.info(f"    {pad}hit the 250 cap, splitting into two halves")
        return (fetch_range(query, start, mid, depth + 1)
                + fetch_range(query, mid, end, depth + 1))
    log.info(f"    {pad}-> {len(arts)} articles")
    return arts


def search_all(refresh):
    banner(f"STEP 2/3  GDELT search ({len(SEARCHES)} windows)")
    parts, status = [], []
    throttled = False
    for n, (label, s, e, q) in enumerate(SEARCHES, 1):
        log.info(f"[{n}/{len(SEARCHES)}] {label}  |  {s} to {e}  |  query: {q}")
        path = window_path(label)
        if throttled and not (os.path.exists(path) and not refresh):
            log.info("      skipped: GDELT is throttling this run; will search on next run")
            status.append((label, "FAILED", 0))
            continue
        if os.path.exists(path) and not refresh:
            part = pd.read_csv(path)
            log.info(f"      loaded from checkpoint: {len(part)} articles")
            parts.append(part)
            status.append((label, "checkpoint", len(part)))
            continue
        t0 = time.time()
        start = datetime.strptime(s, "%Y-%m-%d")
        end = datetime.strptime(e, "%Y-%m-%d") + timedelta(days=1)
        try:
            arts = fetch_range(q, start, end)
        except RateLimited:
            log.info(f"      FAILED after {MAX_RETRIES} rate-limited attempts "
                     f"({fmt_secs(time.time() - t0)}); will retry on next run")
            status.append((label, "FAILED", 0))
            throttled = True   # don't burn more time on GDELT this run
            continue
        part = pd.DataFrame([{"window": label, **{k: a.get(k) for k in COLS[1:]}}
                             for a in arts], columns=COLS)
        part.to_csv(path, index=False)
        log.info(f"      saved {len(part)} articles to {path} ({fmt_secs(time.time() - t0)})")
        parts.append(part)
        status.append((label, "searched", len(part)))

    log.info("")
    log.info("Search summary:")
    for label, st, cnt in status:
        log.info(f"  {label:26s} {st:11s} {cnt:4d} articles")

    if not parts:
        return pd.DataFrame(columns=COLS + ["found_by", "is_repost"]), status
    allrows = pd.concat(parts, ignore_index=True)
    found_by = allrows.groupby("url")["window"].agg(lambda w: ",".join(dict.fromkeys(w)))
    df = (allrows.drop_duplicates("url")
                 .sort_values("seendate")
                 .reset_index(drop=True))
    df["found_by"] = df["url"].map(found_by)
    # same headline on another site = wire repost (e.g. two copies of an ANI story)
    key = df["title"].astype(str).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    df["is_repost"] = key.duplicated()
    log.info(f"  -> {len(allrows)} hits, {len(df)} unique URLs, "
             f"{(~df['is_repost']).sum()} distinct stories, {df['is_repost'].sum()} reposts")
    return df, status


# ============================================================== step 3: article text
def load_text_cache():
    cache = {}
    if os.path.exists(TEXT_CACHE):
        with open(TEXT_CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    cache[rec["url"]] = rec
                except json.JSONDecodeError:
                    pass   # partial line from an interrupted run
    return cache


def extract(html_bytes, url):
    import trafilatura
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_bytes, "html.parser")
    links = {urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
             if any(k in a["href"].lower() for k in KEYWORDS)
             and "web.archive.org" not in a["href"]}
    text = trafilatura.extract(html_bytes, url=url, include_comments=False,
                               include_tables=False)
    if not text:
        text = "\n".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
    return text.strip(), " | ".join(sorted(links))


def fetch_article(url):
    """Direct fetch; if blocked, removed or too short, try the Internet Archive copy."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        text, links = extract(r.content, url)
        if len(text) >= MIN_CHARS:
            return {"article_text": text, "related_links": links, "text_source": "direct"}
        err = f"direct: only {len(text)} chars"
    except Exception as ex:
        err = f"direct: {ex}"[:160]
    try:
        q = requests.get("https://archive.org/wayback/available",
                         params={"url": url}, timeout=25).json()
        snap = q.get("archived_snapshots", {}).get("closest")
        if not snap or not snap.get("available"):
            raise RuntimeError("no archived copy")
        r = requests.get(f"https://web.archive.org/web/{snap['timestamp']}id_/{url}",
                         headers=HEADERS, timeout=40)
        r.raise_for_status()
        text, links = extract(r.content, url)
        if len(text) >= MIN_CHARS:
            return {"article_text": text, "related_links": links,
                    "text_source": f"wayback {snap['timestamp']}"}
        err += f" | wayback: only {len(text)} chars"
    except Exception as ex:
        err += f" | wayback: {ex}"[:120]
    return {"article_text": f"ERROR: {err}", "related_links": "", "text_source": None}


def add_text(df):
    banner("STEP 3/3  Article text")
    cache = load_text_cache()
    wanted = df.loc[~df["is_repost"], ["url", "domain"]]
    todo = wanted[~wanted["url"].isin(cache.keys())]
    log.info(f"{len(wanted)} distinct stories: {len(wanted) - len(todo)} already cached, "
             f"{len(todo)} to fetch (reposts skipped)")

    results = dict(cache)
    if len(todo):
        t0 = time.time()
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=TEXT_WORKERS) as pool, \
             open(TEXT_CACHE, "a", encoding="utf-8") as f:
            futures = {pool.submit(fetch_article, u): (u, d)
                       for u, d in zip(todo["url"], todo["domain"])}
            for k, fut in enumerate(as_completed(futures), 1):
                u, d = futures[fut]
                rec = {"url": u, **fut.result()}
                results[u] = rec
                if rec["text_source"]:
                    ok += 1
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    log.info(f"  [{k:3d}/{len(todo)}] OK    {rec['text_source']:24s} "
                             f"{len(rec['article_text']):6d} chars  {d}")
                else:
                    fail += 1
                    log.info(f"  [{k:3d}/{len(todo)}] FAIL  {d}: {rec['article_text'][7:110]}")
        log.info(f"  -> fetched {ok}, failed {fail} ({fmt_secs(time.time() - t0)}); "
                 f"failures are retried on the next run")

    got = {u: {k: results[u].get(k) for k in ("article_text", "text_source", "related_links")}
           for u in wanted["url"] if u in results}
    if got:
        df = df.join(pd.DataFrame.from_dict(got, orient="index"), on="url")
        df["text_chars"] = df["article_text"].str.len()
        df["mentions_docket"] = df["article_text"].str.contains("ICEB-2025-0001", na=False)
    return df


# ============================================================== main
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-text", action="store_true", help="skip fetching article pages")
    p.add_argument("--refresh", action="store_true",
                   help="ignore saved checkpoints and search every window again")
    args = p.parse_args()

    setup_logging()
    t0 = time.time()
    log.info(f"Run started. Output: {OUT_CSV}  |  checkpoints: {CHECKPOINT_DIR}/  |  log: {LOG_FILE}")

    import_previous(args.refresh)
    df, status = search_all(args.refresh)
    if not args.no_text and len(df):
        df = add_text(df)

    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # ---------------- final status
    banner("DONE  Summary")
    failed_windows = [lbl for lbl, st, _ in status if st == "FAILED"]
    log.info(f"Rows in CSV:              {len(df)}")
    log.info(f"Distinct stories:         {(~df['is_repost']).sum() if len(df) else 0}")
    log.info(f"Reposts (text skipped):   {df['is_repost'].sum() if len(df) else 0}")
    if "text_source" in df.columns:
        stories = df[~df["is_repost"]]
        src = stories["text_source"].fillna("")
        errors = stories[stories["article_text"].astype(str).str.startswith("ERROR")]
        log.info(f"Text OK:                  {(src != '').sum()}  "
                 f"(direct {(src == 'direct').sum()}, "
                 f"Internet Archive {src.str.startswith('wayback').sum()})")
        log.info(f"Text still missing:       {len(errors)}")
        for _, r in errors.iterrows():
            log.info(f"    {r['domain']}: {r['url']}")
        log.info(f"Mention ICEB-2025-0001:   {int(stories['mentions_docket'].sum())}")
    if failed_windows:
        log.info(f"Windows NOT searched:     {', '.join(failed_windows)}")
        log.info("    -> run the script again later to search just these")
    else:
        log.info("Windows NOT searched:     none")
    log.info(f"Saved to {OUT_CSV}. Total time {fmt_secs(time.time() - t0)}")


if __name__ == "__main__":
    main()
