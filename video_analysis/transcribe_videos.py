#!/usr/bin/env python3
"""
Transcribe public online videos (Facebook, YouTube, X, ...) locally and for free.

  1. yt-dlp downloads the audio track
  2. faster-whisper (open-source Whisper) transcribes it on your computer

Setup (once):
    pip install yt-dlp faster-whisper pandas

Usage:
    python3 transcribe_videos.py "https://www.facebook.com/.../videos/..."
    python3 transcribe_videos.py --file video_urls.txt          # one URL per line
                                                                # optional date after it:
                                                                # https://... 2026-09-14
    python3 transcribe_videos.py URL --model medium.en           # more accurate, slower
    python3 transcribe_videos.py URL --cookies-browser chrome    # if the site needs a login

Outputs (in transcripts/):
    <video_id>.txt            plain transcript
    <video_id>_segments.csv   timestamped segments (start, end, text)
    transcripts.csv           one row per video: metadata + full text
Videos already in transcripts.csv are skipped on later runs (use --force to redo).

Publish date: taken from the video page (upload_date, release_date or timestamp). If
the site gives none, the row is flagged date_missing=True; add the date after the URL
in your URL file and rerun that video with --force.
"""
import argparse
import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import yt_dlp
from faster_whisper import WhisperModel

OUT_DIR = "transcripts"
AUDIO_DIR = os.path.join(OUT_DIR, "audio")
INDEX_CSV = os.path.join(OUT_DIR, "transcripts.csv")

# Domain vocabulary nudges Whisper to spell names and terms correctly
PROMPT = ("Immigration law update on the DHS duration of status rule. "
          "F-1, J-1, I visa, D/S, SEVP, SEVIS, OPT, CPT, ICE, USCIS, "
          "Federal Register, preliminary injunction, Judge Saylor, NAFSA.")


# Rule milestones, same windows as the news search
MILESTONES = [
    ("2025-08-28", "before_proposed_rule"),
    ("2025-09-30", "proposed_rule_comment_period"),
    ("2026-07-16", "between_comments_and_final"),   # final text public Jul 16
    ("2026-09-14", "final_rule"),
    ("9999-12-31", "injunction"),
]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def pick_date(info):
    """Publish date from the video page, and which field it came from."""
    for key in ("upload_date", "release_date"):
        v = info.get(key)
        if v and len(str(v)) == 8:
            v = str(v)
            return f"{v[:4]}-{v[4:6]}-{v[6:]}", key
    for key in ("timestamp", "release_timestamp"):
        v = info.get(key)
        if v:
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d"), key
    return None, None


def milestone(date):
    if not date:
        return None
    for cutoff, label in MILESTONES:
        if date < cutoff:
            return label


def split_fb_title(raw):
    """Facebook titles look like '17K views · 183 reactions | Title': split them."""
    views = re.search(r"([\d.,]+[KMB]?) views", raw)
    reacts = re.search(r"([\d.,]+[KMB]?) reactions", raw)
    title = raw.split(" | ", 1)[1] if " | " in raw and (views or reacts) else raw
    return title.strip(), (views.group(1) if views else None), (reacts.group(1) if reacts else None)


def read_url_file(path):
    """One URL per line, optionally followed by a publish date (YYYY-MM-DD)."""
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            date = next((x for x in parts[1:] if DATE_RE.match(x)), None)
            items.append((parts[0], date))
    return items


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def download_audio(url, cookies_browser=None):
    opts = {
        "format": "bestaudio/best",          # audio only if offered, else the full file
        "outtmpl": os.path.join(AUDIO_DIR, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        path = downloads[0]["filepath"] if downloads else ydl.prepare_filename(info)
    return info, path


def transcribe(model, path, language):
    segments, info = model.transcribe(
        path,
        language=language,
        vad_filter=True,            # skip silence / music
        beam_size=5,
        initial_prompt=PROMPT,
    )
    rows = []
    for seg in segments:            # generator: transcription happens while iterating
        text = seg.text.strip()
        rows.append({"start": round(seg.start, 1), "end": round(seg.end, 1), "text": text})
        print(f"    [{hms(seg.start)}] {text}")
    return rows, info


def main():
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="*")
    p.add_argument("--file", help="text file with one video URL per line")
    p.add_argument("--model", default="small.en",
                   help="tiny.en, base.en, small.en (default), medium.en, large-v3")
    p.add_argument("--language", default="en", help='language code, or "auto" to detect')
    p.add_argument("--cookies-browser",
                   help="chrome, firefox, safari or edge: use that browser's login")
    p.add_argument("--keep-audio", action="store_true", help="keep downloaded audio files")
    p.add_argument("--force", action="store_true", help="re-transcribe videos already done")
    args = p.parse_args()

    items = [(u, None) for u in args.urls]
    if args.file:
        items += read_url_file(args.file)
    if not items:
        p.error("give at least one URL or --file")
    urls = [u for u, _ in items]
    manual_dates = {u: d for u, d in items if d}

    os.makedirs(AUDIO_DIR, exist_ok=True)
    index = pd.read_csv(INDEX_CSV) if os.path.exists(INDEX_CSV) else pd.DataFrame()
    done = set(index["url"]) if len(index) else set()

    print(f"Loading Whisper model '{args.model}' (first time downloads it)...")
    model = WhisperModel(args.model, device="auto", compute_type="int8")
    language = None if args.language == "auto" else args.language

    for n, url in enumerate(urls, 1):
        print(f"\n[{n}/{len(urls)}] {url}")
        if url in done and not args.force:
            print("    already transcribed, skipping (use --force to redo)")
            continue
        try:
            t0 = time.time()
            info, audio_path = download_audio(url, args.cookies_browser)
            title, fb_views, fb_reactions = split_fb_title(info.get("title") or "")
            pub_date, date_field = pick_date(info)
            if url in manual_dates:
                pub_date, date_field = manual_dates[url], "manual (URL file)"
            print(f"    downloaded: {title[:80]} ({hms(info.get('duration') or 0)}) "
                  f"in {time.time() - t0:.0f}s")
            if pub_date:
                print(f"    published:  {pub_date}  [{milestone(pub_date)}]  (from {date_field})")
            else:
                print("    published:  UNKNOWN - add the date after this URL in your URL file "
                      "and rerun with --force")

            t1 = time.time()
            segs, tinfo = transcribe(model, audio_path, language)
            text = " ".join(s["text"] for s in segs)
            print(f"    transcribed {len(segs)} segments, {len(text):,} chars "
                  f"in {time.time() - t1:.0f}s")
        except yt_dlp.utils.DownloadError as ex:
            print(f"    DOWNLOAD FAILED: {str(ex)[:200]}")
            print("    If the video needs a login, retry with --cookies-browser chrome")
            continue
        except Exception as ex:
            print(f"    FAILED: {ex.__class__.__name__}: {str(ex)[:200]}")
            continue

        vid = info.get("id", f"video{n}")
        txt_path = os.path.join(OUT_DIR, f"{vid}.txt")
        seg_path = os.path.join(OUT_DIR, f"{vid}_segments.csv")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(f"[{hms(s['start'])}] {s['text']}" for s in segs))
        pd.DataFrame(segs).to_csv(seg_path, index=False, encoding="utf-8-sig")

        row = {
            "source_type": "video",
            "platform": info.get("extractor_key"),
            "url": url,
            "video_id": vid,
            "title": title,
            "uploader": info.get("uploader") or info.get("channel"),
            "published_date": pub_date,
            "date_source": date_field,
            "date_missing": pub_date is None,
            "milestone_window": milestone(pub_date),
            "duration_sec": info.get("duration"),
            "view_count": info.get("view_count") or fb_views,
            "like_count": info.get("like_count") or fb_reactions,
            "comment_count": info.get("comment_count"),
            "language": tinfo.language,
            "n_segments": len(segs),
            "text_chars": len(text),
            "text": text,
            "transcript_file": txt_path,
            "segments_file": seg_path,
            "whisper_model": args.model,
            "transcribed_at": datetime.now().isoformat(timespec="seconds"),
        }
        index = index[index["url"] != url] if len(index) else index
        index = pd.concat([index, pd.DataFrame([row])], ignore_index=True)
        index.to_csv(INDEX_CSV, index=False, encoding="utf-8-sig")   # checkpoint per video
        print(f"    saved {txt_path}, {seg_path} and updated {INDEX_CSV}")

        if not args.keep_audio and os.path.exists(audio_path):
            os.remove(audio_path)

    print(f"\nDone. {len(index)} videos in {INDEX_CSV}")
    if len(index):
        show = [c for c in ("published_date", "milestone_window", "uploader", "title")
                if c in index.columns]
        print(index.sort_values(show[0])[show].to_string(index=False, max_colwidth=60))
        if "date_missing" in index.columns and index["date_missing"].fillna(False).astype(bool).any():
            print("\nSome videos have no publish date: add it after the URL in your URL file "
                  "and rerun those with --force.")


if __name__ == "__main__":
    main()
