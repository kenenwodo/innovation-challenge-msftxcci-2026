#!/usr/bin/env python3
"""
PolicyLens analyst briefing: builds a one-page PDF briefing (or, with --full, a
detailed multi-page one) for docket ICEB-2025-0001 from the
committed outputs of the four components (policy versions, public comments, news,
video). No API keys or model calls: every number and statement comes from the files.

Run from the repository root:
    pip install reportlab pandas
    python download_briefing/generate_briefing.py
    python download_briefing/generate_briefing.py --full   # detailed 6-page version

From the app (e.g. a Streamlit download button):
    from download_briefing.generate_briefing import build_briefing_bytes
    st.download_button("Download briefing (PDF)", build_briefing_bytes(),
                       file_name="PolicyLens_briefing_ICEB-2025-0001.pdf",
                       mime="application/pdf")
"""
import argparse
import html
import io
import json
import os
from datetime import datetime

import pandas as pd
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Flowable, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# ============================================================== settings
DOCKET = "ICEB-2025-0001"
RULE_TITLE = ("Establishing a Fixed Time Period of Admission and an Extension of Stay "
              "Procedure for Nonimmigrant Academic Students, Exchange Visitors, and "
              "Representatives of Foreign Information Media")
TOTAL_COMMENTS_ON_DOCKET = 21923   # comments listed on Regulations.gov for the document

FILES = {   # paths relative to the repository root
    "rule_versions": "data/ICEB-2025-0001-21962/rule_versions.json",
    "court": "data/court/1-26-cv-13799-FDS/source.json",
    "policy_changes": "policy_html_extraction/output/policy_changes_validated.json",
    "v1": "policy_html_extraction/output/v1_policies.json",
    "v2": "policy_html_extraction/output/v2_policies.json",
    "comments": "comment_analysis/comments_analysis_output.csv",
    "topics": "comment_analysis/bertopic_topic_info.csv",
    "news_articles": "news_analysis/output/fact_check/articles_ui.csv",
    "news_claims": "news_analysis/output/fact_check/claims_ui.csv",
    "news_evidence": "news_analysis/output/fact_check/claim_evidence_ui.csv",
    "news_viewpoints": "news_analysis/output/fact_check/viewpoints_ui.csv",
    "video": "video_analysis/video_claims_for_ui.csv",
}

NAVY = colors.HexColor("#1F3A5F")
GRAY = colors.HexColor("#5F6B7A")
LIGHT = colors.HexColor("#EEF2F6")
RULE_LINE = colors.HexColor("#C9D2DC")
BAR = colors.HexColor("#3B6EA8")

VERDICT_COLORS = {
    "Supported": "#2E7D32", "supported": "#2E7D32",
    "Partly supported": "#B7791F", "partially_supported": "#B7791F",
    "Contradicted": "#C62828", "contradicted": "#C62828",
    "Not in rule text": "#6B7280", "unresolved": "#6B7280",
    "Different rule": "#9CA3AF", "Not checked": "#9CA3AF",
}


# ============================================================== helpers
def load(repo, key):
    path = os.path.join(repo, FILES[key])
    if not os.path.exists(path):
        return None
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return pd.read_csv(path, low_memory=False)


def t(text, limit=None):
    """Safe text for ReportLab paragraphs: escape markup, keep to the built-in fonts."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = " ".join(str(text).split())
    if limit and len(s) > limit:
        s = s[: limit - 3].rstrip() + "..."
    s = s.encode("cp1252", "replace").decode("cp1252").replace("?", "?")
    return html.escape(s, quote=False)


def pct(n, d):
    return f"{100 * n / d:.0f}%" if d else "n/a"


def fmt_date(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%b %d, %Y")
    except ValueError:
        return str(iso)


def bar(value, max_value, width=1.6 * inch, height=8):
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=LIGHT, strokeColor=None))
    if max_value:
        d.add(Rect(0, 0, width * value / max_value, height, fillColor=BAR, strokeColor=None))
    return d


def styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=20, leading=24, textColor=NAVY, alignment=TA_LEFT,
                                spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=10, leading=13,
                                   textColor=GRAY),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=14, leading=18, textColor=NAVY, spaceBefore=14,
                             spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=NAVY, spaceBefore=8,
                             spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13),
        "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=10.5,
                                textColor=GRAY),
        "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", parent=ss["Normal"], fontName="Helvetica-Bold",
                                fontSize=8.5, leading=11),
        "bullet": ParagraphStyle("bullet", parent=ss["Normal"], fontSize=9.5, leading=13,
                                 leftIndent=12, bulletIndent=2, spaceAfter=3),
        "note": ParagraphStyle("note", parent=ss["Normal"], fontSize=8.5, leading=11.5,
                               textColor=colors.HexColor("#7A4E00"),
                               backColor=colors.HexColor("#FFF6E0"), borderPadding=6,
                               spaceBefore=6, spaceAfter=6),
    }


def table(rows, widths, S, header=True, zebra=True):
    data = [[c if not isinstance(c, str) else Paragraph(c, S["cellb"] if (header and i == 0)
                                                        else S["cell"])
             for c in row] for i, row in enumerate(rows)]
    tb = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE_LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), LIGHT)]
    tb.setStyle(TableStyle(style))
    return tb


def verdict_label(v):
    color = VERDICT_COLORS.get(v, "#374151")
    label = str(v).replace("_", " ")
    return f'<font color="{color}"><b>{t(label[:1].upper() + label[1:])}</b></font>'



# ============================================================== analyst decision controls
class CheckBox(Flowable):
    """Fillable PDF checkbox (click to tick in any PDF reader; also prints as a box)."""
    def __init__(self, name, size=13):
        super().__init__()
        self.name, self.size = name, size
        self.width = self.height = size

    def draw(self):
        self.canv.acroForm.checkbox(name=self.name, x=0, y=0, size=self.size, relative=True,
                                    borderWidth=1.5, borderColor=NAVY, fillColor=colors.white,
                                    buttonStyle="check", tooltip=self.name)


class TextField(Flowable):
    """Fillable PDF text field."""
    def __init__(self, name, width, height=16):
        super().__init__()
        self.name, self.width, self.height = name, width, height

    def draw(self):
        self.canv.acroForm.textfield(name=self.name, x=0, y=0, width=self.width,
                                     height=self.height, relative=True, borderWidth=1,
                                     borderColor=NAVY, fillColor=colors.white, fontSize=8,
                                     tooltip=self.name)


def decision_header(text, S, width):
    hs = ParagraphStyle("dh", parent=S["body"], fontName="Helvetica-Bold", fontSize=10.5,
                        leading=13, textColor=colors.white)
    tb = Table([[Paragraph(text, hs)]], colWidths=[width])
    tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    return tb


def review_table(items, S, width, font=8.6):
    """Each follow-up item gets bold Confirm / Reject checkboxes for the analyst."""
    cs = ParagraphStyle("rc", parent=S["body"], fontSize=font, leading=font + 2.4)
    hb = ParagraphStyle("rh", parent=cs, fontName="Helvetica-Bold", textColor=NAVY)
    rows = [[Paragraph("Item for the analyst to decide", hb), Paragraph("Confirm", hb),
             Paragraph("Reject", hb)]]
    for i, text in enumerate(items, 1):
        rows.append([Paragraph(text, cs), CheckBox(f"item_{i}_confirm"),
                     CheckBox(f"item_{i}_reject")])
    tb = Table(rows, colWidths=[width - 1.4 * inch, 0.7 * inch, 0.7 * inch])
    tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                            ("BOX", (0, 0), (-1, -1), 1.5, NAVY),
                            ("LINEBELOW", (0, 0), (-1, -2), 0.5, RULE_LINE),
                            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                            ("BACKGROUND", (1, 1), (-1, -1), colors.HexColor("#F4F7FB")),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    return tb


def signoff_block(S, width):
    """Prominent final decision: approve / approve with changes / do not approve."""
    lab = ParagraphStyle("so", parent=S["body"], fontName="Helvetica-Bold", fontSize=10,
                         leading=12, textColor=NAVY)
    small = ParagraphStyle("so2", parent=S["body"], fontName="Helvetica-Bold", fontSize=8.5,
                           leading=10, textColor=NAVY)
    choices = Table([[CheckBox("decision_approve", 15), Paragraph("APPROVE", lab),
                      CheckBox("decision_approve_with_changes", 15),
                      Paragraph("APPROVE WITH CHANGES", lab),
                      CheckBox("decision_do_not_approve", 15), Paragraph("DO NOT APPROVE", lab)]],
                    colWidths=[0.3 * inch, 1.0 * inch, 0.3 * inch, 2.05 * inch, 0.3 * inch,
                               1.5 * inch])
    choices.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    fields = Table([[Paragraph("Reviewer", small), TextField("reviewer", 2.2 * inch),
                     Paragraph("Date", small), TextField("date", 1.1 * inch),
                     Paragraph("Notes", small), TextField("notes", 1.8 * inch)]],
                   colWidths=[0.65 * inch, 2.3 * inch, 0.4 * inch, 1.2 * inch, 0.45 * inch,
                              1.9 * inch])
    fields.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    tb = Table([[decision_header("ANALYST DECISION: the final call rests with the reviewing "
                                 "analyst", S, width)],
                [choices], [fields]], colWidths=[width])
    tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 2, NAVY),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F4F7FB")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 1), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                            ("LEFTPADDING", (0, 1), (-1, -1), 8)]))
    return tb


# ============================================================== sections
def section_status(repo, S):
    out = [Paragraph("1. Where the rule stands", S["h1"])]
    rv, court = load(repo, "rule_versions"), load(repo, "court")
    rows = [["Date", "Event", "Source"]]
    if rv:
        rows.append([fmt_date(rv["proposed"]["postedDate"]),
                     "Proposed rule (V1) published; comment period opens",
                     f"FR {rv['proposed']['frDocNum']}"])
        rows.append(["Sep 29, 2025", "Comment period closes", "Federal Register"])
        rows.append([fmt_date(rv["final"]["postedDate"]), "Final rule (V2) published",
                     f"FR {rv['final']['frDocNum']}"])
        rows.append([fmt_date(rv["final"]["effectiveDate"]), "Scheduled effective date",
                     f"FR {rv['final']['frDocNum']}"])
    if court:
        rows.append([fmt_date(court["filed_date"]),
                     "Court postponed the final rule's effective date while the case "
                     "continues (preliminary relief)",
                     f"{t(court['court'])}, No. {t(court['case_number'])}"])
    rows = rows[:1] + sorted(rows[1:], key=lambda r: datetime.strptime(r[0], "%b %d, %Y")
                             if r[0][:3].isalpha() else datetime.max)
    out.append(table(rows, [1.0 * inch, 3.7 * inch, 2.3 * inch], S))
    if court:
        out.append(Paragraph(
            "<b>Current status:</b> the final rule has not taken effect; duration of status "
            "continues while the litigation proceeds. Confirm against the latest court docket.",
            S["body"]))
    return out


def section_changes(repo, S):
    out = [Paragraph("2. What changed between the proposed and final rule", S["h1"])]
    ch, v1, v2 = load(repo, "policy_changes"), load(repo, "v1"), load(repo, "v2")
    if not ch:
        return out + [Paragraph("Policy comparison output not found.", S["small"])]
    changes = ch["changes"]
    counts = pd.Series([c["change_type"] for c in changes]).value_counts()
    out.append(Paragraph(
        f"{len(changes)} policy areas compared "
        f"({v1['provision_count'] if v1 else '?'} provisions in V1, "
        f"{v2['provision_count'] if v2 else '?'} in V2): "
        f"<b>{counts.get('unchanged', 0)} unchanged, {counts.get('modified', 0)} modified, "
        f"{counts.get('added', 0)} added, {counts.get('removed', 0)} removed</b>. "
        f"{ch.get('validated', '?')} passed automated validation; "
        f"{ch.get('review_required', '?')} need analyst review.", S["body"]))

    rows = [["Policy area", "Change", "What changed", "Confidence / status"]]
    for c in changes:
        if c["change_type"] == "unchanged":
            continue
        status = c.get("validation_status", "").replace("_", " ")
        rows.append([t(c["policy_area"], 110), t(c["change_type"]),
                     t(c.get("what_changed"), 330),
                     f"{t(c.get('confidence'))}<br/>{t(status)}"])
    out.append(Spacer(1, 4))
    out.append(table(rows, [1.8 * inch, 0.65 * inch, 3.55 * inch, 1.0 * inch], S))
    out.append(Paragraph(
        "\"Added\" results are review candidates: the language may exist elsewhere in the "
        "proposed rule. Source: policy_html_extraction/output/policy_changes_validated.json "
        "(evidence quotes from both Federal Register versions).", S["small"]))
    return out


def section_comments(repo, S):
    out = [Paragraph("3. Public comments", S["h1"])]
    cm, tp = load(repo, "comments"), load(repo, "topics")
    if cm is None:
        return out + [Paragraph("Comment analysis output not found.", S["small"])]
    n = len(cm)
    sent = cm["sentiment_label"].value_counts()
    emo = cm["emotion_label"].value_counts()
    out.append(Paragraph(
        f"<b>{n:,}</b> text comments analyzed out of <b>{TOTAL_COMMENTS_ON_DOCKET:,}</b> listed "
        f"on Regulations.gov ({pct(n, TOTAL_COMMENTS_ON_DOCKET)}). Attachment-only comments "
        "(often from organizations) are not in the text analysis.", S["body"]))

    rows = [["Sentiment", "Comments", "Share", ""]]
    for lab in ["negative", "neutral", "positive"]:
        v = int(sent.get(lab, 0))
        rows.append([lab.capitalize(), f"{v:,}", pct(v, n), bar(v, n, width=1.0 * inch)])
    rows2 = [["Top emotions", "Comments", "Share", ""]]
    for lab, v in emo.head(5).items():
        rows2.append([t(lab).capitalize(), f"{int(v):,}", pct(v, n), bar(v, n, width=1.0 * inch)])
    pair = Table([[table(rows, [0.95 * inch, 0.75 * inch, 0.5 * inch, 1.15 * inch], S),
                   table(rows2, [0.95 * inch, 0.75 * inch, 0.5 * inch, 1.15 * inch], S)]],
                 colWidths=[3.5 * inch, 3.5 * inch])
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    out += [Spacer(1, 4), pair]

    if tp is not None:
        label_col = "Primary_Concern" if "Primary_Concern" in tp.columns else "Name"
        tp = tp.sort_values("Count", ascending=False)
        total = int(tp["Count"].sum())
        out.append(Paragraph("Main concerns (topic clusters)", S["h2"]))
        rows = [["Concern", "Comments", "Share", ""]]
        for _, r in tp.iterrows():
            rows.append([t(r[label_col]), f"{int(r['Count']):,}", pct(r["Count"], total),
                         bar(r["Count"], tp["Count"].max())])
        out.append(table(rows, [3.9 * inch, 0.8 * inch, 0.6 * inch, 1.7 * inch], S))
        named = tp[~tp["Topic"].isin([-1, 7])]
        if len(named) > 2:
            small = named.nsmallest(3, "Count")
            out.append(Paragraph(
                "<b>Minority viewpoints to keep visible:</b> "
                + "; ".join(f"{t(r[label_col])} ({int(r['Count'])} comments)"
                            for _, r in small.iterrows())
                + ". Small clusters can carry distinct arguments, including support for the "
                  "rule, that a majority-only summary would hide.", S["body"]))
    out.append(Paragraph(
        "Sentiment, emotion and topic labels are model estimates on de-identified text, "
        "not measures of public opinion overall; commenters are self-selected.", S["small"]))
    return out


def section_news(repo, S):
    out = [Paragraph("4. News coverage", S["h1"])]
    a = load(repo, "news_articles")
    cl, ev, vp = load(repo, "news_claims"), load(repo, "news_evidence"), load(repo, "news_viewpoints")
    if a is None:
        return out + [Paragraph("News outputs not found.", S["small"])]
    scored = a[a["analysis_status"] == "scored"]
    stages = a["policy_stage"].value_counts()
    out.append(Paragraph(
        f"<b>{len(a)}</b> articles in the briefing sample ({len(scored)} scored), from "
        f"{a['publisher_domain'].nunique()} publishers in "
        f"{a['publisher_country'].nunique()} countries. Average grounding score of scored "
        f"articles: <b>{scored['grounding_score'].mean():.0f}/100</b> "
        f"(range {scored['grounding_score'].min():.0f} to {scored['grounding_score'].max():.0f}). "
        "Stages covered: " + ", ".join(f"{t(k).replace('_', ' ')} ({v})"
                                       for k, v in stages.items()) + ".", S["body"]))
    if cl is not None:
        vc = cl["verdict"].value_counts()
        rows = [["Claim verdict", "Claims", "Share", ""]]
        for v in ["supported", "partially_supported", "contradicted", "unresolved"]:
            k = int(vc.get(v, 0))
            rows.append([verdict_label(v), str(k), pct(k, len(cl)), bar(k, len(cl), width=1.5 * inch)])
        out += [Spacer(1, 4), table(rows, [1.6 * inch, 0.7 * inch, 0.6 * inch, 1.7 * inch], S)]

        contra = cl[cl["verdict"] == "contradicted"]
        if len(contra):
            out.append(Paragraph("Claims contradicted by the rule text or court record "
                                 "(AI verdicts, pending analyst review)", S["h2"]))
            rows = [["Outlet", "Claim", "Why", "Evidence"]]
            for _, r in contra.iterrows():
                art = a[a["article_id"] == r["article_id"]]
                outlet = art["publisher_domain"].iloc[0] if len(art) else ""
                cites = ""
                if ev is not None:
                    e = ev[ev["claim_id"] == r["claim_id"]].sort_values("evidence_rank").head(2)
                    cites = "<br/>".join(t(c) for c in e["citation"])
                rows.append([t(outlet), t(r["claim_text"], 200),
                             t(r["verdict_explanation"], 260), cites])
            out.append(table(rows, [1.0 * inch, 2.2 * inch, 2.6 * inch, 1.2 * inch], S))

    if vp is not None and len(vp):
        out.append(Paragraph("Stakeholder viewpoints reported in the news", S["h2"]))
        st = vp["stance"].value_counts()
        out.append(Paragraph(
            ", ".join(f"<b>{t(k)}</b>: {v}" for k, v in st.items())
            + ". A balanced selection of both sides is listed below by organization; "
              "individuals quoted in the press are not named in this briefing.", S["body"]))
        rows = [["Stance", "Organization", "Provision addressed", "Reason given"]]
        pick = pd.concat([vp[vp["stance"] == "oppose"].head(6), vp[vp["stance"] == "support"].head(6),
                          vp[~vp["stance"].isin(["oppose", "support"])].head(2)])
        for _, r in pick.iterrows():
            org = r["organization"] if isinstance(r["organization"], str) and r["organization"].strip() \
                else "Quoted individual"
            rows.append([t(r["stance"]).capitalize(), t(org, 70),
                         t(r["affected_provision"], 110), t(r["reason"], 170)])
        out.append(table(rows, [0.7 * inch, 1.5 * inch, 2.1 * inch, 2.7 * inch], S))
    out.append(Paragraph(
        "Source: news_analysis/output/fact_check/*_ui.csv (20-article display sample of 88 "
        "fact-checked articles). Coverage reflects GDELT indexing and search terms, not all "
        "media.", S["small"]))
    return out


def section_video(repo, S):
    out = [Paragraph("5. Video coverage", S["h1"])]
    v = load(repo, "video")
    if v is None:
        return out + [Paragraph("Video output not found.", S["small"])]
    vids = v.drop_duplicates("video_key")
    reach = pd.to_numeric(vids["view_count"], errors="coerce")
    vc = v["verdict_at_publish"].value_counts()
    checkable = sum(int(vc.get(k, 0)) for k in ["Supported", "Partly supported", "Contradicted"])
    outdated = int((v.get("currency_status") == "Outdated").sum()) if "currency_status" in v else 0
    out.append(Paragraph(
        f"<b>{len(vids)}</b> videos ({', '.join(f'{k} {c}' for k, c in vids['platform'].value_counts().items())}), "
        f"<b>{len(v)}</b> claims. Of the {checkable} claims the rule text can judge, "
        f"<b>{pct(int(vc.get('Supported', 0)), checkable)} were supported</b> when published. "
        f"<b>{outdated}</b> accurate claims are flagged as possibly outdated since publication "
        f"(AI suggestion, pending analyst review). Known reach: "
        f"<b>{int(reach.sum()):,}</b> views across {int(reach.notna().sum())} videos with public "
        "view counts.", S["body"]))

    rows = [["Verdict (at publish)", "Claims", "Share", ""]]
    for k in ["Supported", "Partly supported", "Contradicted", "Not in rule text",
              "Different rule", "Not checked"]:
        c = int(vc.get(k, 0))
        if c:
            rows.append([verdict_label(k), str(c), pct(c, len(v)), bar(c, len(v), width=1.5 * inch)])
    out += [Spacer(1, 4), table(rows, [1.6 * inch, 0.7 * inch, 0.6 * inch, 1.7 * inch], S)]

    rows = [["Speaker", "Type", "Published", "Views", "Claims", "Supported", "Maybe outdated"]]
    for _, r in vids.sort_values("published_date").iterrows():
        g = v[v["video_key"] == r["video_key"]]
        views = pd.to_numeric(r["view_count"], errors="coerce")
        rows.append([t(r["speaker"], 40), t(r["speaker_type"]).replace("_", " "),
                     t(r["published_date"]), f"{int(views):,}" if pd.notna(views) else "-",
                     str(len(g)), str(int((g["verdict_at_publish"] == "Supported").sum())),
                     str(int((g.get("currency_status") == "Outdated").sum()))])
    out += [Spacer(1, 6), table(rows, [1.8 * inch, 0.9 * inch, 0.85 * inch, 0.75 * inch,
                                       0.55 * inch, 0.75 * inch, 0.9 * inch], S)]

    contra = v[v["verdict_at_publish"] == "Contradicted"]
    if len(contra):
        out.append(Paragraph("Claims contradicted by the rule text "
                             "(AI verdicts, pending analyst review)", S["h2"]))
        rows = [["Speaker", "Claim", "Rule text says", "Citation"]]
        for _, r in contra.iterrows():
            rows.append([f"{t(r['speaker'], 40)}<br/>{t(r['published_date'])} at {t(r['timestamp'])}",
                         t(r["claim"], 200), t(r["ai_explanation"], 260),
                         t(r.get("evidence_1_citation"))])
        out.append(table(rows, [1.3 * inch, 2.1 * inch, 2.7 * inch, 0.9 * inch], S))
    out.append(Paragraph(
        "Source: video_analysis/video_claims_for_ui.csv. Claims about what the court said are "
        "marked \"not in rule text\" until the court order is added as evidence.", S["small"]))
    return out


def section_takeaways(repo, S):
    """Key points computed from the data (no model-generated text)."""
    pts = []
    ch = load(repo, "policy_changes")
    if ch:
        types = pd.Series([c["change_type"] for c in ch["changes"]]).value_counts()
        pts.append(f"<b>Policy:</b> {types.get('modified', 0)} of {len(ch['changes'])} policy "
                   f"areas were modified from the proposed to the final rule and "
                   f"{types.get('added', 0)} appear new (pending review).")
    cm, tp = load(repo, "comments"), load(repo, "topics")
    if cm is not None:
        neg = (cm["sentiment_label"] == "negative").sum()
        top = ""
        if tp is not None:
            lc = "Primary_Concern" if "Primary_Concern" in tp.columns else "Name"
            r = tp[tp["Topic"] != -1].sort_values("Count", ascending=False).iloc[0]
            top = (f" The largest concern is \"{t(r[lc])}\" "
                   f"({pct(r['Count'], tp['Count'].sum())} of comments).")
        pts.append(f"<b>Comments:</b> {pct(neg, len(cm))} of {len(cm):,} analyzed comments are "
                   f"negative.{top}")
    a, cl = load(repo, "news_articles"), load(repo, "news_claims")
    if a is not None and cl is not None:
        sc = a[a["analysis_status"] == "scored"]["grounding_score"]
        pts.append(f"<b>News:</b> sampled articles average {sc.mean():.0f}/100 on grounding; "
                   f"{int((cl['verdict'] == 'contradicted').sum())} of {len(cl)} featured claims "
                   f"are contradicted by the rule or court record.")
    v = load(repo, "video")
    if v is not None:
        outd = int((v.get("currency_status") == "Outdated").sum()) if "currency_status" in v else 0
        pts.append(f"<b>Video:</b> {int((v['verdict_at_publish'] == 'Supported').sum())} of "
                   f"{len(v)} claims were supported when published, "
                   f"{int((v['verdict_at_publish'] == 'Contradicted').sum())} contradicted; "
                   f"{outd} may be outdated after the court ruling.")
    court = load(repo, "court")
    if court:
        pts.append("<b>Status:</b> the final rule was postponed by the court on "
                   f"{fmt_date(court['filed_date'])}; public-facing information published "
                   "before that date may now be outdated.")
    return [Paragraph("Key points", S["h1"])] + [Paragraph(p, S["bullet"], bulletText="-")
                                                  for p in pts]


def section_method(S):
    return [
        Paragraph("6. Method, sources and limits", S["h1"]),
        Paragraph(
            "Built with PolicyLens on Microsoft Foundry (Azure OpenAI, gpt-4.1-mini) for "
            "policy extraction and claim checking, with local models for comment sentiment, "
            "emotion, toxicity and topics, and Whisper for video transcription. Claims are "
            "checked against the official Federal Register text of the proposed rule "
            "(FR 2025-16554) and final rule (FR 2026-14439); news claims also against the "
            "court order (D. Mass. No. 1:26-cv-13799).", S["body"]),
        Paragraph(
            "<b>Limits:</b> verdicts, labels and topics are AI-assisted and need analyst "
            "review; samples are English-only and not representative of all coverage or "
            "public opinion; attachment-only comments are excluded from the text analysis; "
            "\"added\" policy changes and \"outdated\" flags are review candidates. This "
            "briefing is not legal advice or an official agency finding; the Federal Register "
            "text is authoritative.", S["body"]),
    ]



# ============================================================== one-page briefing
def build_one_page(repo, out):
    S = styles()
    small = ParagraphStyle("sm", parent=S["body"], fontSize=8.3, leading=10.6)
    tiny = ParagraphStyle("tn", parent=S["small"], fontSize=7.2, leading=9)
    head = ParagraphStyle("hd", parent=S["h2"], fontSize=10, leading=12, spaceBefore=0,
                          spaceAfter=3)
    bullet = ParagraphStyle("bl", parent=S["bullet"], fontSize=8.3, leading=10.3, spaceAfter=1)
    title = ParagraphStyle("t1", parent=S["title"], fontSize=17, leading=20, spaceAfter=1)
    note = ParagraphStyle("n1", parent=S["note"], fontSize=8, leading=10.2, spaceBefore=3,
                          spaceAfter=3, borderPadding=4)
    generated = datetime.now().strftime("%B %d, %Y")

    def mini(rows, widths):
        tb = Table([[c if not isinstance(c, str) else Paragraph(c, small) for c in r]
                    for r in rows], colWidths=widths)
        tb.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("TOPPADDING", (0, 0), (-1, -1), 1),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        return tb

    W = 3.35 * inch                     # width of one panel
    bw = 1.0 * inch                     # bar width inside a panel
    panels = {}

    # ---- policy
    ch = load(repo, "policy_changes")
    p = [Paragraph("What changed: proposed (V1) vs final (V2)", head)]
    if ch:
        types = pd.Series([c["change_type"] for c in ch["changes"]]).value_counts()
        n = len(ch["changes"])
        p.append(mini([[f"{k.capitalize()}", str(int(types.get(k, 0))),
                        bar(types.get(k, 0), n, width=bw)]
                       for k in ["unchanged", "modified", "added"]],
                      [0.9 * inch, 0.35 * inch, bw]))
        mods = [c for c in ch["changes"] if c["change_type"] == "modified"][:2]
        p.append(Paragraph("<b>Key modifications:</b> " + "; ".join(
            t(c["policy_area"], 95) for c in mods) + ".", small))
        p.append(Paragraph(f"{ch.get('review_required', 0)} of {n} areas (the new additions) "
                           "need analyst review.", tiny))
    panels["policy"] = p

    # ---- comments
    cm, tp = load(repo, "comments"), load(repo, "topics")
    p = [Paragraph("Public comments", head)]
    if cm is not None:
        n = len(cm)
        sent = cm["sentiment_label"].value_counts()
        p.append(Paragraph(f"<b>{n:,}</b> text comments analyzed of "
                           f"<b>{TOTAL_COMMENTS_ON_DOCKET:,}</b> on the docket.", small))
        p.append(mini([[k.capitalize(), pct(sent.get(k, 0), n), bar(sent.get(k, 0), n, width=bw)]
                       for k in ["negative", "neutral", "positive"]],
                      [0.9 * inch, 0.35 * inch, bw]))
        if tp is not None:
            lc = "Primary_Concern" if "Primary_Concern" in tp.columns else "Name"
            tot = tp["Count"].sum()
            top = tp[~tp["Topic"].isin([-1, 7])].sort_values("Count", ascending=False)
            p.append(Paragraph("<b>Top concerns:</b> " + "; ".join(
                f"{t(r[lc], 85)} ({pct(r['Count'], tot)})" for _, r in top.head(3).iterrows())
                + ".", small))
            sm = top.nsmallest(2, "Count")
            p.append(Paragraph("<b>Minority views:</b> " + "; ".join(
                f"{t(r[lc], 60)} ({int(r['Count'])})" for _, r in sm.iterrows()) + ".", tiny))
    panels["comments"] = p

    # ---- news
    a, cl, vp = load(repo, "news_articles"), load(repo, "news_claims"), load(repo, "news_viewpoints")
    p = [Paragraph("News articles", head)]
    if a is not None and cl is not None:
        sc = a[a["analysis_status"] == "scored"]["grounding_score"]
        p.append(Paragraph(f"<b>{len(a)}</b> articles, {a['publisher_domain'].nunique()} "
                           f"publishers. Average grounding: <b>{sc.mean():.0f}/100</b>.", small))
        vc = cl["verdict"].value_counts()
        p.append(mini([[verdict_label(k), str(int(vc.get(k, 0))),
                        bar(vc.get(k, 0), len(cl), width=bw)]
                       for k in ["supported", "partially_supported", "contradicted", "unresolved"]],
                      [1.2 * inch, 0.35 * inch, bw]))
        if vp is not None and len(vp):
            st = vp["stance"].value_counts()
            p.append(Paragraph("<b>Stakeholder viewpoints:</b> " + ", ".join(
                f"{int(v)} {t(k)}" for k, v in st.items()) + ".", small))
    panels["news"] = p

    # ---- video
    v = load(repo, "video")
    p = [Paragraph("News videos", head)]
    if v is not None:
        vids = v.drop_duplicates("video_key")
        views = pd.to_numeric(vids["view_count"], errors="coerce")
        vc = v["verdict_at_publish"].value_counts()
        outd = int((v.get("currency_status") == "Outdated").sum()) if "currency_status" in v else 0
        p.append(Paragraph(f"<b>{len(vids)}</b> videos, <b>{len(v)}</b> claims, "
                           f"<b>{int(views.sum()):,}</b> known views.", small))
        p.append(mini([[verdict_label(k), str(int(vc.get(k, 0))),
                        bar(vc.get(k, 0), len(v), width=bw)]
                       for k in ["Supported", "Partly supported", "Contradicted", "Not in rule text"]],
                      [1.2 * inch, 0.35 * inch, bw]))
        top = vids.assign(_v=views).sort_values("_v", ascending=False).iloc[0]
        g = v[v["video_key"] == top["video_key"]]
        p.append(Paragraph(
            f"<b>{outd}</b> accurate claims may be outdated since the court ruling. "
            f"Most-watched: {t(top['speaker'], 40)} ({int(top['_v']):,} views), "
            f"{int((g['verdict_at_publish'] == 'Supported').sum())} of {len(g)} claims supported, "
            f"{int((g['verdict_at_publish'] == 'Contradicted').sum())} contradicted.", small))
    panels["video"] = p

    def box(flow):
        tb = Table([[flow]], colWidths=[W])
        tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, RULE_LINE),
                                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                                ("TOPPADDING", (0, 0), (-1, -1), 4),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return tb

    grid = Table([[box(panels["policy"]), box(panels["comments"])],
                  [box(panels["news"]), box(panels["video"])]],
                 colWidths=[W + 0.15 * inch, W + 0.15 * inch])
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))

    court, rv = load(repo, "court"), load(repo, "rule_versions")
    status = "<b>Status:</b> "
    if rv:
        status += (f"proposed {fmt_date(rv['proposed']['postedDate'])} "
                   f"(FR {rv['proposed']['frDocNum']}); final rule published "
                   f"{fmt_date(rv['final']['postedDate'])} (FR {rv['final']['frDocNum']}), "
                   f"effective {fmt_date(rv['final']['effectiveDate'])}")
    if court:
        status += (f"; <b>postponed by the court on {fmt_date(court['filed_date'])}</b> "
                   f"({t(court['court'])}, No. {t(court['case_number'])}). "
                   "Duration of status continues while the case proceeds.")

    story = [
        Paragraph("Analyst briefing: duration of status rule", title),
        Paragraph(f"Docket {DOCKET} (DHS/ICE) | F, J and I nonimmigrants | {generated}",
                  S["subtitle"]),
        Spacer(1, 3),
        Paragraph(status, note),
        Paragraph("Key points", head),
    ]
    story += [Paragraph(x.text, bullet, bulletText="-")
              for x in section_takeaways(repo, S)[1:] if "Status:" not in x.text]
    # ---- analyst follow-ups (computed)
    follow = []
    if ch:
        follow.append(f"<b>Policy:</b> confirm the {ch.get('review_required', 0)} policy areas "
                      "flagged as added against the proposed rule text before reporting them "
                      "as new.")
    if cl is not None or v is not None:
        nc = int((cl["verdict"] == "contradicted").sum()) if cl is not None else 0
        vcn = int((v["verdict_at_publish"] == "Contradicted").sum()) if v is not None else 0
        line = (f"<b>Accuracy:</b> review the {nc} contradicted news claims and {vcn} "
                "contradicted video claims against their citations")
        if v is not None and vcn:
            vv = v[v["verdict_at_publish"] == "Contradicted"].copy()
            vv["_v"] = pd.to_numeric(vv["view_count"], errors="coerce").fillna(0)
            r = vv.sort_values("_v", ascending=False).iloc[0]
            line += (f"; highest reach: {t(r['speaker'], 40)} ({int(r['_v']):,} views): "
                     f"\"{t(r['claim'], 110)}\" ({t(r.get('evidence_1_citation'))})")
        follow.append(line + ".")
    if v is not None:
        outd = int((v.get("currency_status") == "Outdated").sum()) if "currency_status" in v else 0
        follow.append(f"<b>Currency:</b> decide Still current / Outdated for the {outd} video "
                      "claims the AI flags as possibly outdated since the court ruling.")
    if court:
        un = int((cl["verdict"] == "unresolved").sum()) if cl is not None else 0
        nir = int((v["verdict_at_publish"] == "Not in rule text").sum()) if v is not None else 0
        follow.append(f"<b>Court record:</b> {un} news claims are unresolved and {nir} video "
                      "claims (mostly about the ruling) cannot be judged from the rule text; "
                      "check them against the court order (No. "
                      f"{t(court['case_number'])}).")
    if tp is not None:
        follow.append("<b>Representativeness:</b> attachment-only comments (often from "
                      "organizations) are excluded from the text analysis; review them before "
                      "citing comment shares as the public's view.")

    page_w = letter[0] - 1.3 * inch
    story += [Spacer(1, 2), grid, review_table(follow, S, page_w, font=7.8), Spacer(1, 6),
              signoff_block(S, page_w), Spacer(1, 4)]
    story += [Paragraph(
                  "<b>How this was made:</b> PolicyLens on Microsoft Foundry (Azure OpenAI "
                  "gpt-4.1-mini) for policy comparison and claim checks against the official "
                  "Federal Register text; local models for comment sentiment and topics; "
                  "Whisper for video transcripts. <b>Limits:</b> AI-assisted draft for analyst "
                  "review, not legal advice; English-only samples, not representative of all "
                  "coverage or opinion; attachment-only comments excluded; \"added\" and "
                  "\"outdated\" results are review candidates. The Federal Register text is "
                  "authoritative.", tiny)]

    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.65 * inch,
                            rightMargin=0.65 * inch, topMargin=0.45 * inch,
                            bottomMargin=0.4 * inch, title=f"PolicyLens one-page briefing {DOCKET}",
                            author="PolicyLens", subject=RULE_TITLE)
    doc.build(story)
    return out

# ============================================================== build
def build(repo, out):
    S = styles()
    generated = datetime.now().strftime("%B %d, %Y %H:%M")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(0.75 * inch, 0.5 * inch,
                          f"PolicyLens briefing | Docket {DOCKET} | AI-assisted draft for "
                          f"analyst review, not legal advice | Generated {generated}")
        canvas.drawRightString(7.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.setStrokeColor(RULE_LINE)
        canvas.line(0.75 * inch, 0.65 * inch, 7.75 * inch, 0.65 * inch)
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=letter, leftMargin=0.75 * inch,
                            rightMargin=0.75 * inch, topMargin=0.7 * inch,
                            bottomMargin=0.85 * inch, title=f"PolicyLens briefing {DOCKET}",
                            author="PolicyLens", subject=RULE_TITLE)
    story = [
        Paragraph("Analyst briefing: duration of status rule", S["title"]),
        Paragraph(f"Docket {DOCKET} (DHS/ICE) | {t(RULE_TITLE)}", S["subtitle"]),
        Paragraph(f"Generated {generated} from committed PolicyLens outputs", S["subtitle"]),
        Paragraph("AI-assisted draft for analyst review. Every figure below is computed from "
                  "the project's output files; verdicts and labels are model-generated and "
                  "should be confirmed against the cited sources.", S["note"]),
    ]
    story += section_takeaways(repo, S)
    for sec in (section_status, section_changes, section_comments, section_news, section_video):
        story += sec(repo, S)
    story += section_method(S)
    story += [Spacer(1, 12), KeepTogether([signoff_block(S, letter[0] - 1.5 * inch)])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out


def build_briefing_bytes(repo=None, full=False):
    """PDF as bytes, for a download button in the app. One page by default."""
    repo = repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    buf = io.BytesIO()
    (build if full else build_one_page)(repo, buf)
    return buf.getvalue()


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="Build the PolicyLens analyst briefing PDF.")
    p.add_argument("--repo", default=os.path.dirname(here),
                   help="repository root (default: the folder above this script)")
    p.add_argument("--out", help="output PDF path")
    p.add_argument("--full", action="store_true",
                   help="build the detailed multi-page briefing instead of the one-pager")
    args = p.parse_args()
    if not args.out:
        name = f"PolicyLens_briefing_{DOCKET}_full.pdf" if args.full else f"PolicyLens_briefing_{DOCKET}.pdf"
        args.out = os.path.join(here, name)
    missing = [k for k, f in FILES.items() if not os.path.exists(os.path.join(args.repo, f))]
    if missing:
        print("Not found (those sections will be skipped or shortened):", ", ".join(missing))
    (build if args.full else build_one_page)(args.repo, args.out)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
