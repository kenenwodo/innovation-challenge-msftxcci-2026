#!/usr/bin/env python3
"""
Claim check: verify what a video transcript or news article says about the DHS
duration of status rule (Docket ICEB-2025-0001) against the rule text itself.

  1. Downloads the proposed rule (v1, FR 2025-16554) and final rule (v2, FR 2026-14439)
     from the Federal Register API and splits them into paragraphs with section
     headings and page citations (cached in rule_text/).
  2. Extracts claims from each source with a free LLM API.
  3. Finds the most relevant paragraphs in v1 and v2 for each factual claim with a
     local embedding model (bge-small-en-v1.5, free, runs on your computer).
  4. Asks the LLM for a verdict using ONLY those paragraphs and a status timeline.

Setup (once):
    pip install openai sentence-transformers pandas requests

LLM API (pick one):
    Azure (team setup): the same .env the rest of the repo uses, in this folder or any
                        parent folder:  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
                        AZURE_OPENAI_MODEL (e.g. gpt-4.1-mini)
    Groq (free tier):   export GROQ_API_KEY=gsk_...   (console.groq.com)
    (GitHub Models was retired on July 30, 2026 and no longer works.)

Run:
    python3 claim_check.py transcripts/transcripts.csv --provider azure
    python3 claim_check.py transcripts/transcripts.csv --provider groq
    python3 claim_check.py transcripts/transcripts.csv --provider groq --model openai/gpt-oss-120b
    python3 claim_check.py transcripts/transcripts.csv --tag v2   # keep earlier outputs
Outputs:
    video_claims_for_ui.csv   one row per claim, PII removed: the file for the app UI
                              (columns explained in README.md)
    claim_results.csv         full internal results, for debugging
Sources already checked are cached in claim_cache.jsonl and skipped on later runs
(use --force to redo).
"""
import argparse
import json
import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")   # silence fork warning
import re
import sys
import time
import xml.etree.ElementTree as ET
import hashlib
from datetime import datetime

import numpy as np
import pandas as pd
import requests

# ============================================================== settings
RULES = {
    "v1": {"doc": "2025-16554", "label": "proposed rule", "published": "2025-08-28"},
    "v2": {"doc": "2026-14439", "label": "final rule", "published": "2026-07-17"},
}
RULE_DIR = "rule_text"
CACHE_FILE = "claim_cache.jsonl"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
TOP_K = 3                  # passages per rule version per claim
MAX_SOURCE_CHARS = 16000   # longer sources are cut (free-tier request size limits)
MAX_PASSAGE_CHARS = 1200

TIMELINE = """\
- 2025-08-28: Proposed rule published (FR 2025-16554). Comments due 2025-09-29.
- 2026-07-16: Final rule text made public (Federal Register public inspection, the day
  before publication).
- 2026-07-17: Final rule published (FR 2026-14439). Effective date: 2026-09-15.
- 2026-09-14: A federal court in Massachusetts postponed the final rule before it took
  effect, while the lawsuit continues (source: court docket, not the rule text)."""

PROVIDERS = {
    # Same connection style as the rest of the repo (policy_analysis, policy_html_extraction)
    # and the same Responses API call (client.responses.create) the team's scripts use.
    "azure": {"base_url_env": "AZURE_OPENAI_ENDPOINT", "key_env": "AZURE_OPENAI_API_KEY",
              "model_env": "AZURE_OPENAI_MODEL", "min_gap": 0.5, "api": "responses"},
    # Groq free tier (Aug 2026): llama-3.3-70b-versatile ~30 req/min, 1,000 req/day,
    # 12K tokens/min, 100K tokens/day. A verdict call is ~2-3K tokens, so pace ~13 s apart.
    "groq":  {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
              "model": "llama-3.3-70b-versatile", "min_gap": 13, "api": "chat"},
}

TOPICS = ["fixed admission periods", "extension-of-stay procedures",
          "F-1 departure period", "graduate transfers/program changes", "other"]

EXTRACT_SYSTEM = f"""You extract claims about one U.S. federal rule from a news article
or video transcript. The rule: the DHS "duration of status" rule (Docket ICEB-2025-0001),
which replaces duration of status (D/S) admission for F, J and I nonimmigrants with fixed
admission periods.

Return JSON: {{"claims": [ ... ]}}. Each claim is an object with:
- "claim": one self-contained statement in plain words (resolve pronouns, say who/what).
- "quote": the exact words from the source that make the claim, copied verbatim.
- "timestamp": the [h:mm:ss] marker where the quote starts, if the source has markers; else "".
- "type": "fact" (checkable statement about THIS rule, its content, process, dates, or legal
  status), "opinion", "prediction", "other_rule" (a statement about a DIFFERENT regulation,
  e.g. an H-1B grace period proposal), or "other" (greetings, promotion, advice).
- "about": "rule_content", "rulemaking_process", "legal_status", "court_case", or "other".
- "topic": one of {json.dumps(TOPICS)}.
Include claims the speaker attributes to others as their own items (e.g. "DHS argued the
rule was needed for national security", "the judge found the justification weak"), and
statements about dates and timing (e.g. "DHS announced this a few months ago").
Only include claims about this rule and its surroundings. Merge duplicates. At most 15 claims."""

VERDICT_SYSTEM = """You check ONE claim against the text of a U.S. federal rule.
You get the claim, the date its source was published, a status timeline, and numbered
passages from the PROPOSED rule (v1, published 2025-08-28) and the FINAL rule
(v2, published 2026-07-17). Use ONLY the passages and the timeline, no outside knowledge.

First write your reasoning in "explanation", then answer the questions so they match it.

1. "addressed": do the passages or timeline directly confirm or refute the claim?
   Set false when they are silent or only loosely related, when the claim is about what
   a judge or court said or found (the court order is not among the passages), or when
   the claim is about a different regulation (e.g. an H-1B or work-visa grace period).
2. "accurate_at_publish": judged AS OF THE SOURCE'S PUBLISH DATE, using the rule version
   public then and only timeline events up to that date: "yes", "partly" (core right but
   a detail wrong, missing or overstated) or "no". Use "no" ONLY when a passage or the
   timeline directly states the opposite. Judge the claim as the speaker meant it.
   Check numbers, dates and durations exactly.
   Note: from 2026-09-14 the rule is postponed and not in effect, and duration of status
   continues; statements saying so on or after that date are accurate.
3. "changed_since": did something AFTER the publish date make it untrue (a later timeline
   event, or the final rule changing a proposed-rule provision the claim describes)?
   Mentioning or depending on an event is not a change.

Return JSON with the keys in this order:
{"explanation": "2-3 sentences: what the passage or timeline says and how it compares",
 "addressed": true|false, "accurate_at_publish": "yes"|"partly"|"no",
 "changed_since": true|false, "basis": "v1"|"v2"|"both"|"timeline"|"none",
 "evidence_used": [passage numbers], "confidence": a number from 0 to 1}"""


# ============================================================== rule text
def _remove_keep_tail(parent, child):
    """Remove an XML element without losing the text that follows it."""
    idx = list(parent).index(child)
    if child.tail:
        if idx > 0:
            prev = parent[idx - 1]
            prev.tail = (prev.tail or "") + child.tail
        else:
            parent.text = (parent.text or "") + child.tail
    parent.remove(child)


def parse_fr_xml(xml_text, version, citation_volume):
    root = ET.fromstring(xml_text)
    for parent in list(root.iter()):                 # drop footnotes and footnote refs
        for child in list(parent):
            if child.tag in ("FTNT", "FTREF"):
                _remove_keep_tail(parent, child)
    heading, page, paras = "", "", []
    for el in root.iter():
        if el.tag == "HD":
            heading = " ".join("".join(el.itertext()).split())
        elif el.tag == "SECTNO":
            heading = "Regulatory text " + " ".join("".join(el.itertext()).split())
        elif el.tag == "PRTPAGE":
            page = el.get("P", page)
        elif el.tag in ("P", "FP"):
            text = " ".join("".join(el.itertext()).split())
            if len(text) >= 40:
                paras.append({"version": version, "section": heading,
                              "citation": f"{citation_volume} FR {page}" if page else "",
                              "text": text})
    return paras


def load_rules():
    os.makedirs(RULE_DIR, exist_ok=True)
    all_paras = []
    for version, info in RULES.items():
        cache = os.path.join(RULE_DIR, f"{version}_{info['doc']}_paragraphs.json")
        if os.path.exists(cache):
            with open(cache, encoding="utf-8") as f:
                paras = json.load(f)
        else:
            print(f"Downloading {info['label']} ({info['doc']}) from the Federal Register...")
            meta = requests.get(f"https://www.federalregister.gov/api/v1/documents/{info['doc']}.json",
                                timeout=30).json()
            xml = requests.get(meta["full_text_xml_url"], timeout=120)
            xml.raise_for_status()
            with open(os.path.join(RULE_DIR, f"{version}_{info['doc']}.xml"), "w", encoding="utf-8") as f:
                f.write(xml.text)
            volume = (meta.get("citation") or "").split(" FR ")[0] or "?"
            paras = parse_fr_xml(xml.text, version, volume)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(paras, f)
        print(f"  {version} {info['label']}: {len(paras)} paragraphs")
        all_paras.extend(paras)
    return all_paras


def embed_rules(paras):
    from sentence_transformers import SentenceTransformer
    print(f"Loading embedding model {EMBED_MODEL} (first time downloads it)...")
    model = SentenceTransformer(EMBED_MODEL)
    cache = os.path.join(RULE_DIR, f"embeddings_{len(paras)}.npy")
    if os.path.exists(cache):
        emb = np.load(cache)
    else:
        print(f"Embedding {len(paras)} rule paragraphs (one-time, a few minutes)...")
        emb = model.encode([p["section"] + ": " + p["text"] for p in paras],
                           normalize_embeddings=True, batch_size=32, show_progress_bar=True)
        np.save(cache, emb)
    return model, emb


def retrieve(model, emb, paras, claim):
    q = model.encode([QUERY_PREFIX + claim], normalize_embeddings=True)[0]
    scores = emb @ q
    hits = []
    for version in RULES:
        idx = [i for i, p in enumerate(paras) if p["version"] == version]
        best = sorted(idx, key=lambda i: -scores[i])[:TOP_K]
        hits += [(i, float(scores[i])) for i in best]
    return hits


LAST_TIMELINE_EVENT = "2026-09-14"   # update when you add events to TIMELINE

# ---- UI output settings
DOCKET_ID = "ICEB-2025-0001"
UI_CSV = "video_claims_for_ui.csv"
# Speaker type per channel/page name. "individual" = a private person: their name is
# replaced by "Individual creator" everywhere in the UI file. Add new speakers here.
SPEAKER_TYPES = {
    "Bethel Law Group": "law_firm",
    "Nova Law Group": "law_firm",
    "Law Office of Louis S. Haskell": "law_firm",
    "FJ Diza, US Immigration Attorney": "attorney",
    "Maven Consulting Services": "consultancy",
    "GoElite": "consultancy",
    "Bloomberg Television": "news_outlet",
    "CSULB CIE": "university",
    "Visa Guide USA": "creator",
    "Gautham Kolluri": "individual",
}
MILESTONES = [                        # (window starts before this date, label)
    ("2025-08-28", "before_proposed_rule"),
    ("2025-09-30", "proposed_rule_comment_period"),
    ("2026-07-16", "between_comments_and_final"),
    ("2026-09-14", "final_rule"),
    ("9999-12-31", "injunction"),
]
AT_PUBLISH = {"yes": "Supported", "partly": "Partly supported", "no": "Contradicted"}


def at_publish_verdict(r, claim=None):
    """Accuracy on the publish date only (the 'still current?' question is separate)."""
    if not r.get("addressed", True):
        return "Not in rule text"
    if claim and claim.get("about") == "court_case":
        return "Not in rule text"          # the court order is not a source yet
    return AT_PUBLISH.get(str(r.get("accurate_at_publish", "")).lower(), "Supported")


def suggests_outdated(r, source_date):
    if source_date and source_date >= LAST_TIMELINE_EVENT:
        return False
    return bool(r.get("changed_since"))


def verdict_label(r, source_date):
    """Turn the model's three answers into one verdict, the same way every time."""
    if not r.get("addressed", True):
        return "Not in rule text"
    acc = str(r.get("accurate_at_publish", "")).lower()
    changed = bool(r.get("changed_since"))
    if source_date and source_date >= LAST_TIMELINE_EVENT:
        changed = False                      # nothing known happened after this source
    if acc == "no":
        return "Contradicted"
    if changed:
        return "Outdated"
    return "Partly supported" if acc == "partly" else "Supported"


# ============================================================== LLM
_last_call = [0.0]


def llm_json(client, cfg, system, user, temperature=0.0, retries=6):
    import openai
    for attempt in range(1, retries + 1):
        gap = time.time() - _last_call[0]
        if gap < cfg["min_gap"]:
            time.sleep(cfg["min_gap"] - gap)
        _last_call[0] = time.time()
        try:
            if cfg.get("api") == "responses":
                resp = client.responses.create(model=cfg["model"], instructions=system,
                                               input=user, temperature=temperature)
                text = resp if isinstance(resp, str) else (resp.output_text or "")
            else:
                resp = client.chat.completions.create(
                    model=cfg["model"], temperature=temperature,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}])
                if isinstance(resp, str):
                    raise RuntimeError("The LLM API returned a non-JSON response:\n"
                                       + resp.strip()[:600])
                text = resp.choices[0].message.content or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", text, re.S)
                if m:
                    return json.loads(m.group(0))
                raise
        except (openai.NotFoundError, openai.AuthenticationError,
                openai.PermissionDeniedError, openai.BadRequestError) as ex:
            # Configuration problems: retrying will not help, so stop with details.
            raise RuntimeError(
                f"{ex.__class__.__name__} from {cfg['base_url']} with model "
                f"'{cfg['model']}':\n{str(ex)[:500]}") from None
        except openai.RateLimitError:
            wait = min(20 * attempt, 90)
            print(f"      rate limited, waiting {wait}s")
            time.sleep(wait)
        except (openai.APIError, json.JSONDecodeError) as ex:
            print(f"      LLM error ({ex.__class__.__name__}), retrying")
            time.sleep(5 * attempt)
    raise RuntimeError("LLM call failed after retries")


# ============================================================== sources
def hms(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def load_sources(path):
    df = pd.read_csv(path)
    sources = []
    if "source_type" in df.columns and "text" in df.columns:          # transcripts.csv
        for _, r in df.iterrows():
            text = str(r["text"])
            seg = r.get("segments_file")
            for cand in ([seg, os.path.join(os.path.dirname(path), os.path.basename(str(seg)))]
                         if isinstance(seg, str) else []):
                if os.path.exists(cand):
                    s = pd.read_csv(cand)
                    text = "\n".join(f"[{hms(a)}] {t}" for a, t in zip(s["start"], s["text"]))
                    break
            date = r.get("published_date")
            if not isinstance(date, str):            # rows from the older transcriber
                date = r.get("upload_date") if isinstance(r.get("upload_date"), str) else None
            val = lambda k: (None if k not in r or pd.isna(r.get(k)) else
                             (r.get(k).item() if hasattr(r.get(k), "item") else r.get(k)))
            sources.append({"url": r["url"], "title": r.get("title"), "kind": "video",
                            "outlet": r.get("uploader"), "date": date, "text": text,
                            "meta": {k: val(k) for k in ("platform", "video_id", "view_count",
                                                         "like_count", "duration_sec")}})
    elif "article_text" in df.columns:                                 # news CSV
        if "is_repost" in df.columns:
            df = df[~df["is_repost"].astype(bool)]
        df = df[df["article_text"].notna()
                & ~df["article_text"].astype(str).str.startswith("ERROR")]
        for _, r in df.iterrows():
            d = str(r.get("seendate", ""))
            sources.append({"url": r["url"], "title": r.get("title"), "kind": "news",
                            "outlet": r.get("domain"),
                            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None,
                            "text": str(r["article_text"])})
    else:
        sys.exit("Input CSV needs either a 'text' column (transcripts) or 'article_text' (news).")
    return sources


def load_cache():
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    cache[rec["url"]] = rec
                except json.JSONDecodeError:
                    pass
    return cache



# ============================================================== UI file
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")


INTRO_RE = re.compile(r"\b((?i:my name is|this is|i'm|i am))\s+((?i:attorney|lawyer|dr\.?)\s+)?"
                      r"([A-Z][\w'-]+(?:[\s-]+[A-Z][\w'-]+){0,3})")


def scrub(text, hide_names=()):
    """Remove emails, phone numbers, self-introductions and individuals' names."""
    if not isinstance(text, str):
        return text
    text = PHONE_RE.sub("[removed]", EMAIL_RE.sub("[removed]", text))
    text = INTRO_RE.sub(lambda m: f"{m.group(1)} {m.group(2) or ''}[name]", text)
    for name in hide_names:
        text = re.sub(re.escape(name), "[creator]", text, flags=re.I)
    return text


def split_title(raw):
    """Facebook titles look like '17K views · 183 reactions | Title | Page name'."""
    raw = raw if isinstance(raw, str) else ""
    views = re.search(r"([\d.,]+[KMB]?) views", raw)
    reacts = re.search(r"([\d.,]+[KMB]?) reactions", raw)
    title = raw.split(" | ", 1)[1] if (views or reacts) and " | " in raw else raw
    return title.strip(), (views.group(1) if views else None), (reacts.group(1) if reacts else None)


def to_count(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    m = re.match(r"^([\d.]+)\s*([KMB]?)$", str(v).replace(",", "").strip().upper())
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]) if m else None


def to_seconds(ts):
    parts = [int(x) for x in re.findall(r"\d+", str(ts or ""))]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def milestone(date):
    if not date:
        return None
    return next(label for cutoff, label in MILESTONES if str(date) < cutoff)


def parse_evidence_string(ev):
    """Older cached results stored evidence as one string."""
    items = []
    for part in (ev or "").split(" || "):
        bits = [b.strip() for b in part.split(" | ", 3)]
        if len(bits) == 4:
            v, sec, cit, txt = bits
            items.append({"version": v, "section": sec, "citation": cit, "text": txt,
                          "link": f"https://www.federalregister.gov/citation/{cit.replace(' ', '-')}"
                          if re.match(r"^\d+ FR \d+$", cit) else ""})
    return items


def write_ui_csv(sources, cache):
    rows, unknown = [], set()
    # names of every individual in the set are hidden in every row, not just their own
    hide = sorted({part for name, t in SPEAKER_TYPES.items() if t == "individual"
                   for part in [name] + [w for w in name.split() if len(w) > 2]},
                  key=len, reverse=True)
    for src in sources:
        rec = cache.get(src["url"])
        if not rec:
            continue
        meta = {**(rec.get("meta") or {}), **(src.get("meta") or {})}
        outlet = rec.get("outlet") or ""
        stype = SPEAKER_TYPES.get(outlet, "unknown")
        if stype == "unknown":
            unknown.add(outlet)
        speaker = "Individual creator" if stype == "individual" else outlet
        title, t_views, t_reacts = split_title(rec.get("title"))
        title = scrub(re.sub(r"\s*\|\s*" + re.escape(outlet) + r"\s*$", "", title), hide) if outlet else scrub(title, hide)
        url = rec["url"]
        platform = str(meta.get("platform") or "").lower() or url
        platform = ("YouTube" if "youtu" in platform else "Facebook" if "facebook" in platform
                    else "TikTok" if "tiktok" in platform else "Instagram" if "instagram" in platform
                    else "Other")
        video_key = str(meta.get("video_id") or hashlib.sha1(url.encode()).hexdigest()[:10])
        date = rec.get("date")
        for c in rec["claims"]:
            sec = to_seconds(c.get("timestamp"))
            ev = c.get("evidence_items") or parse_evidence_string(c.get("evidence"))
            vap = c.get("verdict_at_publish") or c.get("verdict")
            row = {
                "docket_id": DOCKET_ID,
                "video_key": video_key,
                "video_url": "" if stype == "individual" else url,   # personal page URLs name the person
                "platform": platform,
                "speaker": speaker,
                "speaker_type": stype,
                "video_title": title if len(title) <= 200 else title[:197].rstrip() + "...",
                "published_date": date,
                "milestone_window": milestone(date),
                "video_duration_sec": meta.get("duration_sec"),
                "view_count": to_count(meta.get("view_count")) or to_count(t_views),
                "like_count": to_count(meta.get("like_count")) or to_count(t_reacts),
                "claim_uid": hashlib.sha1(f"{url}|{c['claim_id']}".encode()).hexdigest()[:12],
                "claim_number": c["claim_id"],
                "timestamp": f"{sec // 60}:{sec % 60:02d}" if sec is not None else "",
                "timestamp_seconds": sec,
                "video_link_at_time": (f"{url}{'&' if '?' in url else '?'}t={sec}s"
                                       if sec is not None and platform == "YouTube"
                                       and stype != "individual" else ""),
                "speaker_quote": scrub(c.get("quote"), hide),
                "claim": scrub(c.get("claim"), hide),
                "claim_type": c.get("type"),
                "topic": c.get("topic"),
                "about": c.get("about"),
                "verdict_at_publish": "Supported" if vap == "Outdated" else vap,
                "model_suggests_outdated": bool(c.get("model_suggests_outdated",
                                                      c.get("verdict") == "Outdated")),
                "currency_status": "",      # filled below: Still current / Outdated
                "currency_source": "",      # "AI suggestion" until an analyst reviews it
                "confidence": c.get("confidence"),
                "verdict_basis": c.get("basis"),
                "ai_explanation": scrub(c.get("explanation"), hide),
            }
            if row["verdict_at_publish"] in ("Supported", "Partly supported"):
                row["currency_status"] = ("Outdated" if row["model_suggests_outdated"]
                                          else "Still current")
                row["currency_source"] = "AI suggestion"
            else:
                row["model_suggests_outdated"] = False   # no currency badge for these
            for k in (1, 2):
                e = ev[k - 1] if len(ev) >= k else {}
                row.update({f"evidence_{k}_version": e.get("version", ""),
                            f"evidence_{k}_citation": e.get("citation", ""),
                            f"evidence_{k}_section": e.get("section", ""),
                            f"evidence_{k}_text": e.get("text", ""),
                            f"evidence_{k}_link": e.get("link", "")})
            row.update({"analyst_review_status": "pending", "analyst_verdict": "",
                        "analyst_outdated": "", "analyst_note": "",
                        "model": rec.get("model"), "checked_at": rec.get("checked_at", "")})
            rows.append(row)

    ui = pd.DataFrame(rows)
    if len(ui):
        ui = ui.sort_values(["published_date", "video_key", "claim_number"])
        for col in ("view_count", "like_count", "video_duration_sec", "timestamp_seconds"):
            ui[col] = pd.to_numeric(ui[col], errors="coerce").round().astype("Int64")
    ui.to_csv(UI_CSV, index=False, encoding="utf-8-sig")
    print(f"Saved {len(ui)} claims from {ui['video_key'].nunique() if len(ui) else 0} videos "
          f"to {UI_CSV} (for the UI, PII removed)")
    if len(ui):
        print(ui["verdict_at_publish"].value_counts().to_string())
        print(ui["currency_status"].replace("", "(n/a)").value_counts().rename("currency").to_string())
    if unknown:
        print("Add these speakers to SPEAKER_TYPES (individuals must be listed to hide "
              "their names):", ", ".join(sorted(unknown)))


# ============================================================== main
def check_source(src, client, cfg, model, emb, paras, votes):
    text = src["text"][:MAX_SOURCE_CHARS]
    got = llm_json(client, cfg, EXTRACT_SYSTEM,
                   f"Source type: {src['kind']}\nPublished: {src['date']}\n"
                   f"Title: {src['title']}\n\nSOURCE TEXT:\n{text}")
    claims = got.get("claims", []) if isinstance(got, dict) else []
    print(f"    {len(claims)} claims extracted "
          f"({sum(c.get('type') == 'fact' for c in claims)} factual)")

    for n, c in enumerate(claims, 1):
        c["claim_id"] = n
        c["timestamp"] = str(c.get("timestamp") or "").strip("[] ")
        if c.get("type") not in ("opinion", "prediction", "other_rule", "other"):
            c["type"] = "fact"             # e.g. "legal_status" put in the wrong field
        if c.get("type") != "fact":
            label = "Different rule" if c.get("type") == "other_rule" else "Not checked"
            why = ("about a different regulation, not checked against this rule"
                   if label == "Different rule" else f"{c.get('type')}, not a factual claim")
            c.update(verdict=label, basis="", explanation=why,
                     confidence=None, evidence="", evidence_link="", top_similarity=None,
                     evidence_items=[], verdict_at_publish=label, model_suggests_outdated=False)
            continue
        hits = retrieve(model, emb, paras, c["claim"])
        block = []
        for k, (i, s) in enumerate(hits, 1):
            p = paras[i]
            block.append(f"[{k}] {p['version']} ({RULES[p['version']]['label']}) | {p['section']} | "
                         f"{p['citation']}\n{p['text'][:MAX_PASSAGE_CHARS]}")
        user = (f"CLAIM: {c['claim']}\nQUOTE FROM SOURCE: {c.get('quote', '')}\n"
                f"SOURCE PUBLISHED: {src['date']}\n\nSTATUS TIMELINE:\n{TIMELINE}\n\n"
                f"PASSAGES:\n" + "\n\n".join(block))
        results = [llm_json(client, cfg, VERDICT_SYSTEM, user, temperature=0.0 if v == 0 else 0.7)
                   for v in range(votes)]
        for r in results:
            r["verdict"] = verdict_label(r, src["date"])
        verdicts = [r.get("verdict") for r in results]
        top = max(set(verdicts), key=verdicts.count)
        best = next(r for r in results if r.get("verdict") == top)
        used = [int(u) for u in best.get("evidence_used", []) if str(u).isdigit()
                and 1 <= int(u) <= len(hits)]
        ev = [paras[hits[u - 1][0]] for u in used]
        c.update(
            verdict=top, basis=best.get("basis", ""), explanation=best.get("explanation", ""),
            addressed=best.get("addressed"),
            accurate_at_publish=best.get("accurate_at_publish"),
            changed_since=best.get("changed_since"),
            confidence=best.get("confidence"),
            agreement=f"{verdicts.count(top)}/{votes}" if votes > 1 else "",
            evidence=" || ".join(f"{p['version']} | {p['section']} | {p['citation']} | "
                                 f"{p['text'][:300]}" for p in ev),
            evidence_link=(f"https://www.federalregister.gov/citation/"
                           f"{ev[0]['citation'].replace(' ', '-')}" if ev and ev[0]["citation"] else ""),
            top_similarity=round(hits[0][1], 3),
            evidence_items=[{"version": p["version"], "section": p["section"],
                             "citation": p["citation"], "text": p["text"][:400],
                             "link": (f"https://www.federalregister.gov/citation/"
                                      f"{p['citation'].replace(' ', '-')}" if p["citation"] else "")}
                            for p in ev],
            verdict_at_publish=at_publish_verdict(best, c),
            model_suggests_outdated=suggests_outdated(best, src["date"]),
        )
        flag = "  (may be outdated)" if c["model_suggests_outdated"] else ""
        print(f"    [{n:2d}] {c['verdict_at_publish']:18s} {c['claim'][:70]}{flag}")
    return claims


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", help="transcripts.csv or the GDELT news CSV")
    p.add_argument("--provider", choices=PROVIDERS, default="azure")
    p.add_argument("--model", help="override the provider's default model, "
                                   "e.g. --provider groq --model openai/gpt-oss-120b")
    p.add_argument("--limit", type=int, help="only check the first N sources")
    p.add_argument("--votes", type=int, default=3,
                   help="ask for the verdict N times and take the majority (confidence check)")
    p.add_argument("--out", default="claim_results.csv")
    p.add_argument("--force", action="store_true", help="re-check sources already in the cache")
    p.add_argument("--tag", help="save this run separately, e.g. --tag v2 writes "
                                 "video_claims_for_ui_v2.csv, claim_results_v2.csv and "
                                 "claim_cache_v2.jsonl, leaving earlier files untouched")
    args = p.parse_args()

    global CACHE_FILE, UI_CSV
    if args.tag:
        CACHE_FILE = f"claim_cache_{args.tag}.jsonl"
        UI_CSV = f"video_claims_for_ui_{args.tag}.csv"
        if args.out == "claim_results.csv":
            args.out = f"claim_results_{args.tag}.csv"
        print(f"Run tag '{args.tag}': writing {UI_CSV}, {args.out}, cache {CACHE_FILE}")

    try:                                   # read the repo's .env if python-dotenv is installed
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    cfg = dict(PROVIDERS[args.provider])
    missing = [v for k, v in cfg.items() if k.endswith("_env") and not os.environ.get(v)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Put them in .env or export them "
                 f"(see the top of this file).")
    cfg["base_url"] = cfg.get("base_url") or os.environ[cfg["base_url_env"]]
    cfg["model"] = args.model or cfg.get("model") or os.environ[cfg["model_env"]]
    from openai import OpenAI
    client = OpenAI(base_url=cfg["base_url"], api_key=os.environ[cfg["key_env"]])

    paras = load_rules()
    model, emb = embed_rules(paras)
    sources = load_sources(args.input)[: args.limit]
    cache = load_cache()
    print(f"\n{len(sources)} sources to check with {args.provider} ({cfg['model']})\n")

    for n, src in enumerate(sources, 1):
        print(f"[{n}/{len(sources)}] {src['kind']} | {src['outlet']} | {src['date']} | {src['url'][:70]}")
        if src["url"] in cache and not args.force:
            print("    already checked (cached)")
            continue
        try:
            claims = check_source(src, client, cfg, model, emb, paras, args.votes)
        except Exception as ex:
            print(f"    FAILED: {ex}")
            continue
        rec = {"url": src["url"], "title": src["title"], "kind": src["kind"],
               "outlet": src["outlet"], "date": src["date"], "model": cfg["model"],
               "meta": src.get("meta", {}), "checked_at": datetime.now().isoformat(timespec="seconds"),
               "claims": claims}
        cache[src["url"]] = rec
        with open(CACHE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    rows = []
    for src in sources:
        rec = cache.get(src["url"])
        if not rec:
            continue
        for c in rec["claims"]:
            rows.append({"source_kind": rec["kind"], "outlet": rec["outlet"], "source_date": rec["date"],
                         "source_url": rec["url"], "source_title": rec["title"], **c,
                         "model": rec["model"]})
    out = pd.DataFrame(rows).drop(columns=["evidence_items"], errors="ignore")
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(out)} claims to {args.out} (internal)")
    write_ui_csv(sources, cache)


if __name__ == "__main__":
    main()
