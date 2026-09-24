#!/usr/bin/env python3
"""
Find YouTube videos about the DHS duration of status rule (Docket ICEB-2025-0001).

Uses yt-dlp's built-in YouTube search: free, no API key. (yt-dlp cannot search
Facebook, TikTok or Instagram; add those links to video_urls.txt by hand.)

  1. Runs each search query on YouTube
  2. Fetches full details for each result (publish date, views, description)
  3. Keeps videos published since the proposed rule and flags likely relevant ones
  4. Saves everything to found_videos.csv and writes the likely relevant links that
     are not already in video_urls.txt to found_video_urls.txt for you to review

Setup:  pip install yt-dlp pandas
Run:    python3 find_videos.py
        python3 find_videos.py --per-query 30 --since 2025-08-01
Then review found_video_urls.txt, copy the good lines into video_urls.txt, and run
transcribe_videos.py --file video_urls.txt
"""
import argparse
import os
import re
import time

import pandas as pd
import yt_dlp

QUERIES = [
    "duration of status rule international students",
    "DHS duration of status final rule",
    "F-1 visa duration of status DHS rule",
    "fixed admission period F-1 J-1 students",
    "duration of status rule court blocked",
    "duration of status injunction judge",
    "D/S rule international students 2026",
    # proposal and comment period (Aug-Sep 2025)
    "DHS proposed rule duration of status 2025",
    "proposed rule end duration of status comment",
    # court ruling (Sep 2026)
    "court blocks F-1 duration of status rule",
    "F-1 rule blocked judge September 2026",
    "duration of status rule postponed",
]

# Relevance terms, checked in title + description
STRONG = ["duration of status", "d/s rule", "fixed admission period", "fixed period of admission",
          "iceb-2025-0001", "fixed time period of admission"]
SUPPORT = ["f-1", "f1 visa", "j-1", "international student", "dhs", "homeland security",
           "sevp", "sevis", "opt", "grace period", "extension of stay", "injunction"]

MILESTONES = [
    ("2025-08-28", "before_proposed_rule"),
    ("2025-09-30", "proposed_rule_comment_period"),
    ("2026-07-16", "between_comments_and_final"),   # final text public Jul 16
    ("2026-09-14", "final_rule"),
    ("9999-12-31", "injunction"),
]
OUT_CSV = "found_videos.csv"
REVIEW_FILE = "found_video_urls.txt"
URL_FILE = "video_urls.txt"


def milestone(date):
    if not date:
        return None
    for cutoff, label in MILESTONES:
        if date < cutoff:
            return label


def fmt_date(info):
    d = info.get("upload_date") or info.get("release_date")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if d and len(d) == 8 else None


def relevance(title, desc):
    text = f"{title or ''} {desc or ''}".lower()
    strong = [t for t in STRONG if t in text]
    support = [t for t in SUPPORT if re.search(r"\b" + re.escape(t) + r"\b", text)]
    likely = bool(strong) or len(support) >= 3
    return strong + support, likely


def search(query, n, ydl_flat):
    res = ydl_flat.extract_info(f"ytsearch{n}:{query}", download=False)
    return [e for e in (res or {}).get("entries", []) if e and e.get("id")]


def details(video_id, ydl_full):
    return ydl_full.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--per-query", type=int, default=20, help="results per search query")
    p.add_argument("--since", default="2025-08-01", help="keep videos published on/after (YYYY-MM-DD)")
    args = p.parse_args()

    flat_opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    full_opts = {"quiet": True, "no_warnings": True, "skip_download": True}

    old = pd.read_csv(OUT_CSV) if os.path.exists(OUT_CSV) else pd.DataFrame()
    known = set(old["video_id"]) if len(old) else set()

    # 1) search
    hits = {}
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        for q in QUERIES:
            try:
                entries = search(q, args.per_query, ydl)
            except Exception as ex:
                print(f"search failed for '{q}': {ex}")
                continue
            new = 0
            for e in entries:
                if e["id"] not in hits:
                    hits[e["id"]] = q
                    new += 1
            print(f"'{q}': {len(entries)} results, {new} new")
            time.sleep(1)

    todo = [v for v in hits if v not in known]
    print(f"\n{len(hits)} unique videos found, {len(todo)} not seen before; fetching details...")

    # 2) details for new videos
    rows = []
    with yt_dlp.YoutubeDL(full_opts) as ydl:
        for n, vid in enumerate(todo, 1):
            try:
                info = details(vid, ydl)
            except Exception as ex:
                print(f"  [{n}/{len(todo)}] {vid}: FAILED ({str(ex)[:80]})")
                continue
            date = fmt_date(info)
            terms, likely = relevance(info.get("title"), info.get("description"))
            rows.append({
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
                "published_date": date,
                "milestone_window": milestone(date),
                "duration_sec": info.get("duration"),
                "is_short": (info.get("duration") or 0) <= 60,
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "comment_count": info.get("comment_count"),
                "has_captions": bool(info.get("subtitles") or info.get("automatic_captions")),
                "matched_query": hits[vid],
                "relevance_terms": ", ".join(terms),
                "likely_relevant": likely,
                "in_date_range": bool(date and date >= args.since),
                "description": (info.get("description") or "")[:500],
            })
            flag = "RELEVANT" if likely else "maybe   "
            print(f"  [{n}/{len(todo)}] {flag} {date or '????-??-??'}  "
                  f"{(info.get('view_count') or 0):>9,} views  {(info.get('title') or '')[:60]}")
            time.sleep(1)

    df = pd.concat([old, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("video_id")
    df = df.sort_values("published_date", na_position="last")
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # 3) review file: likely relevant, in date range, not already in video_urls.txt
    listed = set()
    if os.path.exists(URL_FILE):
        with open(URL_FILE) as f:
            listed = {line.split()[0] for line in f if line.strip() and not line.startswith("#")}
    keep = df[df["likely_relevant"].astype(bool) & df["in_date_range"].astype(bool)
              & ~df["url"].isin(listed)]
    with open(REVIEW_FILE, "w") as f:
        f.write("# Candidates from find_videos.py: review, then copy good lines into video_urls.txt\n")
        for _, r in keep.iterrows():
            f.write(f"{r['url']} {r['published_date']}   # {r['channel']} | "
                    f"{int(r['view_count']) if pd.notna(r['view_count']) else 0:,} views | "
                    f"{str(r['title'])[:70]}\n")

    print(f"\nSaved {len(df)} videos to {OUT_CSV}")
    print(f"{len(keep)} likely relevant new videos written to {REVIEW_FILE}")
    if len(keep):
        print(keep.groupby("milestone_window").size().to_string())


if __name__ == "__main__":
    main()
