"""
Parse the Federal Register HTML of a rule into a section tree.

Works on both versions of the rule (see DOCUMENTS below):
  python extract_html.py                        # proposed rule (default)
  python extract_html.py --document final       # final rule

content.html is the GPO text version of the rule inside a <pre> block.
Unlike extract_pdf.py, which flattens each PDF page into plain text, this
keeps the structure the GPO text carries:

  - section IDs from the outline headings (e.g. V.E.iii)
  - official Federal Register page numbers from the [[Page N]] markers
  - paragraph boundaries (GPO indents the first line of every paragraph)
  - footnotes, bulleted lists, and tables, kept apart from body text
  - a source type for every section (agency explanation, cost analysis,
    regulatory text, ...)
  - CFR citations for paragraphs of regulatory text, e.g. 8 CFR 214.2(f)(5)(v)

Outputs (proposed rule; the final rule's files start with final_rule_):
  output/rule_sections.jsonl   one JSON object per section, in document order
  output/rule_sections.md      the same content, for human review
"""

import argparse
import html
import json
import re
from pathlib import Path


# --------------------------------------------------
# 1. CONFIGURATION
# --------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PROJECT_DIR.parent
OUTPUT_DIR = PROJECT_DIR / "output"

# The two versions of the rule number their outlines differently and have
# different preamble sections, so each gets its own settings.
#
#   outline           token style of each heading level: the proposed rule
#                     nests V.E.iii.1, the final rule nests IV.B.2.a
#   acronyms_section  the section that lists one acronym per line
#   source_types      source type of each preamble section, matched by the
#                     longest section-ID prefix. Sections after the preamble
#                     get their type from their structure.
DOCUMENTS = {
    "proposed": {
        "title": "Proposed Rule",
        "html": PROJECT_DIR / "data" / "content.html",
        "output_stem": "rule_sections",
        "outline": ["upper_roman", "upper", "roman", "digit"],
        "acronyms_section": "II",
        "source_types": {
            "I": "procedural",            # Public Participation
            "II": "procedural",           # Acronyms and Abbreviations
            "III": "agency_explanation",  # Executive Summary
            "III.C": "cost_analysis",     # Summary of the Costs and Benefits
            "IV": "agency_explanation",   # Background and Purpose
            "V": "agency_explanation",    # Discussion of the Proposed Rule
            "VI": "procedural",           # Statutory and Regulatory Requirements
            "VI.A": "cost_analysis",      # E.O. 12866 regulatory impact analysis
            "VI.B": "cost_analysis",      # Regulatory Flexibility Act
            "VI.E": "cost_analysis",      # Paperwork Reduction Act burden
        },
    },
    "final": {
        "title": "Final Rule",
        "html": REPO_DIR / "data" / "ICEB-2025-0001-21962" / "document" / "content.html",
        "output_stem": "final_rule_sections",
        "outline": ["upper_roman", "upper", "digit", "lower"],
        "acronyms_section": "I",
        "source_types": {
            "I": "procedural",            # Acronyms and Abbreviations
            "II": "agency_explanation",   # Executive Summary
            "II.B": "comment_response",   # Public Participation--Overview of Comments
            "II.D": "cost_analysis",      # Summary of the Costs and Benefits
            "III": "agency_explanation",  # Background and Purpose
            "IV": "comment_response",     # Response to Public Comments on the Proposed Rule
            "V": "agency_explanation",    # Discussion of the Final Rule
            "VI": "procedural",           # Statutory and Regulatory Requirements
            "VI.A": "cost_analysis",      # E.O. 12866 regulatory impact analysis
            "VI.B": "cost_analysis",      # Final Regulatory Flexibility Act analysis
        },
    },
}

BULLET = "\u2022"  # GPO writes <bullet>
CIRCLE = "\u25e6"  # GPO writes [cir]

ROMAN = [
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
    "xxi", "xxii", "xxiii", "xxiv", "xxv",
]


# --------------------------------------------------
# 2. GPO TEXT PATTERNS
# --------------------------------------------------

PAGE_RE = re.compile(r"^\[\[Page (\d+)\]\]$")
VOLUME_RE = re.compile(r"Federal Register Volume (\d+)")

SEPARATOR_RE = re.compile(r"^-{10,}$")
FOOTNOTE_RULE_RE = re.compile(r"^-{75}$")   # a footnote block sits between two of these
FOOTNOTE_START_RE = re.compile(r"^\s+\\(\d+)\\\s?")
FOOTNOTE_REF_RE = re.compile(r"\\(\d+)\\")
TABLE_TITLE_RE = re.compile(r"^ +Table \d+--")
TABLE_RULE_RE = re.compile(r"^-{80,}$")
# The final rule lists what a CFR section contains in tables such as
# "Table 1 to Sec. 214.2--Section Contents", one "(5) Period of stay" row per line
CONTENTS_TABLE_RE = re.compile(r"^ +Table \d+ to .+--(?:Section|Paragraph) Contents$")
CONTENTS_ROW_RE = re.compile(r"^ +\(\w{1,5}\) [^.]{1,90}$")

FRONT_LABEL_RE = re.compile(
    r"^(AGENCY|ACTION|SUMMARY|DATES|ADDRESSES|"
    r"FOR FURTHER INFORMATION CONTACT|SUPPLEMENTARY INFORMATION): ?"
)
NUMBERED_HEADING_RE = re.compile(r"^([IVX]+|[A-Z]|[ivx]+|[a-z]|\d+)\. (\S.*)$")

PART_RE = re.compile(r"^PART (\w+)--(.+)$")
CFR_SECTION_RE = re.compile(r"^Sec\.\s+(\d+\w*\.\d+)\s+(.*)$")
INSTRUCTION_RE = re.compile(r"^(\d+|[a-z])\. ")
OMISSION_RE = re.compile(r"^\* \* \* \* \*$")
STUB_RE = re.compile(r"^\(\w+\) \* \* \*$")
DESIGNATOR_RE = re.compile(r"^\((\w{1,5})\)\s")
# A second designator after a short subject heading, as in
# "(5) Period of Stay--(i) General." or "(ii) Admission period. (A) J-1 ..."
INLINE_DESIGNATOR_RE = re.compile(r"^[^().]{1,200}?(?:\.|--)\s*\((\w{1,5})\)\s")


# --------------------------------------------------
# 3. CLEAN THE HTML INTO PAGE-TAGGED LINES
# --------------------------------------------------

def load_lines(html_path):
    """
    Return the rule as (line, fr_page) pairs, plus the Federal Register
    volume number.

    Trailing spaces are kept on purpose: GPO wraps long lines at a space
    and leaves that space at the end of the line, which tells us the next
    line continues the same paragraph or heading.
    """
    raw = html_path.read_text(encoding="utf-8")

    start = raw.find("<pre>") + len("<pre>")
    end = raw.rfind("</pre>")
    body = raw[start:end].replace("\x00", "")

    # <bullet> is a GPO pseudo-tag, not HTML: replace it before stripping
    # tags or every bullet is lost.
    body = body.replace("<bullet>", BULLET).replace("[cir]", CIRCLE)
    body = re.sub(r"</?(?:a|span)\b[^>]*>", "", body)  # keep link text
    body = html.unescape(body).replace("\xa0", " ")

    volume_match = VOLUME_RE.search(body)
    volume = int(volume_match.group(1)) if volume_match else None

    lines = []
    page = None
    after_marker = False

    for line in body.split("\n"):
        marker = PAGE_RE.match(line.strip())

        if marker:
            # Page markers can fall mid-sentence. Drop them and the blank
            # lines around them so the paragraph reads straight through.
            page = int(marker.group(1))
            while lines and not lines[-1][0].strip():
                lines.pop()
            after_marker = True
            continue

        if after_marker and not line.strip():
            continue

        after_marker = False
        lines.append((line, page))

    return lines, volume


# --------------------------------------------------
# 4. TEXT HELPERS
# --------------------------------------------------

def is_wrapped(raw):
    """True if GPO wrapped this line, so the next line continues it."""
    return raw.endswith((" ", "-", "/"))


def join_lines(raw_lines):
    """Join wrapped lines into one string, keeping hyphenated terms intact."""
    text = ""
    previous = ""

    for raw in raw_lines:
        piece = raw.strip()

        if not piece:
            continue

        if not text:
            text = piece
        elif previous.endswith(("-", "/")):
            text += piece       # Form I-\n94 -> Form I-94
        else:
            text += " " + piece

        previous = raw

    return text


def clean_text(text):
    text = FOOTNOTE_REF_RE.sub(lambda m: f"[^{m.group(1)}]", text)
    text = text.replace("``", '"').replace("''", '"')
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def next_roman(current):
    return ROMAN[ROMAN.index(current.lower()) + 1] if current else "i"


def expected_tokens(style, current):
    """The heading tokens that may follow current at a level of this style."""
    if style == "upper_roman":
        return [next_roman(current).upper()]
    if style == "roman":
        position = ROMAN.index(current) if current else -1
        return ROMAN[position + 1:position + 3]   # the proposed rule skips V.E.vi
    if style == "digit":
        return [str(int(current) + 1 if current else 1)]
    first = "A" if style == "upper" else "a"
    return [chr(ord(current) + 1) if current else first]


# --------------------------------------------------
# 5. CFR PARAGRAPH CITATIONS
# --------------------------------------------------
#
# CFR paragraphs nest as (a) > (1) > (i) > (A) > (1) > (i). Only amended
# paragraphs are printed, with "* * * * *" where text was skipped, and
# tokens like (i) can be a letter or a roman numeral. So each designator is
# placed by comparing it with the path of the paragraph before it.

CHILD_KIND = {None: "lower", "lower": "digit", "digit": "roman", "roman": "upper", "upper": "digit"}


def designator_kinds(token):
    if token.isdigit():
        return ["digit"]
    if token in ROMAN:
        return ["roman", "lower"] if len(token) == 1 else ["roman"]
    if re.fullmatch(r"[a-z]", token):
        return ["lower"]
    if re.fullmatch(r"[A-Z]", token):
        return ["upper"]
    return []


def ordinal(token, kind):
    if kind == "digit":
        return int(token)
    if kind == "roman":
        return ROMAN.index(token) + 1
    return ord(token.lower()) - ord("a") + 1


def place_designator(stack, token, after_gap, after_stub):
    """
    Return (new_stack, placed). stack is a list of (kind, token, confident)
    from the top-level paragraph down, e.g. (f)(5)(i).
    """
    kinds = designator_kinds(token)

    def next_sibling(depths):
        for depth in depths:
            kind, current, _ = stack[depth]
            if kind in kinds and ordinal(token, kind) == ordinal(current, kind) + 1:
                return stack[:depth] + [(kind, token, True)]
        return None

    # 1. Next sibling of the deepest paragraph: (ii) after (i)
    found = next_sibling([len(stack) - 1]) if stack else None
    if found:
        return found, True

    # 2. First child in standard CFR order. After "(a) * * *" the earlier
    #    children were omitted, so any value fits, even across "* * * * *".
    if after_stub or not after_gap:
        kind = CHILD_KIND[stack[-1][0] if stack else None]
        if kind in kinds and (after_stub or ordinal(token, kind) == 1):
            return stack + [(kind, token, True)], True

    # 3. Next sibling of a shallower paragraph: (b) after (a)(4)(iii)
    found = next_sibling(range(len(stack) - 2, -1, -1))
    if found:
        return found, True

    # 4. After omitted text, any later value at an existing level
    if after_gap or after_stub:
        for depth in range(len(stack) - 1, -1, -1):
            kind, current, _ = stack[depth]
            if kind in kinds and ordinal(token, kind) > ordinal(current, kind):
                return stack[:depth] + [(kind, token, True)], True
        if not stack and "lower" in kinds:
            return [("lower", token, True)], True
        # A child whose earlier siblings were omitted: (b) ... * * * * * (2)
        if stack and CHILD_KIND[stack[-1][0]] in kinds:
            return stack + [(CHILD_KIND[stack[-1][0]], token, True)], True

    # 5. Irregular nesting, e.g. (f)(7)(i) followed by (1): keep but flag it
    if stack and not after_gap:
        for kind in kinds:
            if kind != stack[-1][0] and ordinal(token, kind) == 1:
                return stack + [(kind, token, False)], True

    return stack, False


# --------------------------------------------------
# 6. PARSER
# --------------------------------------------------

class RuleParser:

    def __init__(self, lines, document):
        self.lines = lines
        self.outline_styles = document["outline"]
        self.acronyms_section = [document["acronyms_section"]]
        self.source_types = document["source_types"]
        self.sections = []
        self.section = None
        self.paragraph = None

        self.zone = "front_matter"   # then "preamble", then "regulatory"
        self.outline = []            # numbered heading tokens, e.g. ["V", "E", "iii"]
        self.prev_raw = ""
        self.prev_blank = False

        self.part_id = None
        self.list_of_subjects_id = None
        self.instruction_next = False

        self.cfr_stack = []
        self.after_gap = False
        self.after_stub = False
        self.unplaced_designators = []

    # ---------- sections and paragraphs ----------

    def new_section(self, section_id, label, heading, level, parent_id,
                    source_type, page, cfr_section=None):
        self.flush_paragraph()

        existing = {s["section_id"] for s in self.sections}
        base, n = section_id, 2
        while section_id in existing:
            section_id = f"{base}-{n}"
            n += 1

        self.section = {
            "section_id": section_id,
            "label": label,
            "heading": heading,
            "level": level,
            "parent_id": parent_id,
            "zone": self.zone,
            "source_type": source_type,
            "cfr_section": cfr_section,
            "fr_page_start": page,
            "fr_page_end": page,
            "paragraphs": [],
            "footnotes": [],
        }
        self.sections.append(self.section)

    def start_paragraph(self, raw, page, kind="text", marker=None):
        self.flush_paragraph()
        self.paragraph = {"kind": kind, "marker": marker, "raw": [raw], "pages": [page]}

    def continue_paragraph(self, raw, page):
        self.paragraph["raw"].append(raw)
        self.paragraph["pages"].append(page)

    def flush_paragraph(self):
        if not self.paragraph:
            return

        paragraph, self.paragraph = self.paragraph, None
        kind, marker = paragraph["kind"], paragraph["marker"]

        if kind == "table":
            text = clean_text_table(paragraph["raw"])
        else:
            text = clean_text(join_lines(paragraph["raw"]))
            if text[:1] in (BULLET, CIRCLE):
                kind, marker = "list_item", text[0]
                text = text[1:].strip()

        pages = [p for p in paragraph["pages"] if p is not None]
        number = len(self.section["paragraphs"]) + 1

        record = {
            "para_id": f"{self.section['section_id']}/p{number}",
            "kind": kind,
            "list_marker": marker,
            "text": text,
            "fr_page_start": min(pages) if pages else None,
            "fr_page_end": max(pages) if pages else None,
            "footnote_refs": [int(n) for n in re.findall(r"\[\^(\d+)\]", text)],
            "cfr_citation": None,
            "cfr_citation_confident": None,
        }

        if self.section["cfr_section"]:
            self.assign_cfr_citation(record)

        self.section["paragraphs"].append(record)

    def assign_cfr_citation(self, record):
        text = record["text"]

        if record["kind"] == "omitted":
            self.after_gap = True
            return

        match = DESIGNATOR_RE.match(text)

        if match:
            self.cfr_stack, placed = place_designator(
                self.cfr_stack, match.group(1), self.after_gap, self.after_stub
            )
            rest = text[match.end():]

            for _ in range(2):
                inline = INLINE_DESIGNATOR_RE.match(rest) if placed else None
                if not inline:
                    break
                self.cfr_stack, placed = place_designator(
                    self.cfr_stack, inline.group(1), False, False
                )
                rest = rest[inline.end():]

            self.after_gap = False
            # "(f) * * *" or, inline, "(j) Exchange visitors--(1) * * *"
            self.after_stub = text.endswith("* * *")

            if not placed:
                self.unplaced_designators.append(record["para_id"])
                return

        if self.cfr_stack:
            path = "".join(f"({token})" for _, token, _ in self.cfr_stack)
            record["cfr_citation"] = f"8 CFR {self.section['cfr_section']}{path}"
            record["cfr_citation_confident"] = all(c for _, _, c in self.cfr_stack)

    # ---------- blocks that span several lines ----------

    def read_footnotes(self, i):
        """Read a footnote block between two 75-dash rules."""
        self.flush_paragraph()
        end = i + 1
        while not FOOTNOTE_RULE_RE.match(self.lines[end][0]):
            end += 1

        notes = []
        for raw, page in self.lines[i + 1:end]:
            start = FOOTNOTE_START_RE.match(raw)
            if start:
                notes.append({"number": int(start.group(1)), "raw": [raw[start.end():]], "fr_page": page})
            elif notes and raw.strip():
                notes[-1]["raw"].append(raw)

        for note in notes:
            self.section["footnotes"].append({
                "number": note["number"],
                "text": clean_text(join_lines(note["raw"])),
                "fr_page": note["fr_page"],
            })

        return end + 1

    def read_table(self, i):
        """Keep a table verbatim: its layout is its meaning."""
        self.flush_paragraph()
        end = i
        while end + 1 < len(self.lines):
            closing = TABLE_RULE_RE.match(self.lines[end][0]) and not self.lines[end + 1][0].strip()
            if closing and end > i:
                break
            end += 1

        block = self.lines[i:end + 1]
        self.paragraph = {
            "kind": "table", "marker": None,
            "raw": [raw for raw, _ in block], "pages": [page for _, page in block],
        }
        self.flush_paragraph()
        return end + 1

    def read_contents_table(self, i):
        """
        Keep a CFR contents table as one table. Its rows look like paragraph
        designators and would otherwise be cited as regulatory text.
        """
        self.flush_paragraph()
        end = last_row = i
        while end + 1 < len(self.lines):
            raw = self.lines[end + 1][0]
            if OMISSION_RE.match(raw.strip()):
                end += 1
            elif CONTENTS_ROW_RE.match(raw) and not STUB_RE.match(raw.strip()):
                end = last_row = end + 1
            else:
                break

        # a trailing "* * * * *" belongs to the regulatory text, not the table
        block = self.lines[i:last_row + 1]
        self.paragraph = {
            "kind": "table", "marker": None,
            "raw": [raw for raw, _ in block], "pages": [page for _, page in block],
        }
        self.flush_paragraph()
        return last_row + 1

    def read_heading_lines(self, i):
        """A heading continues onto the next line when GPO wrapped it."""
        end = i
        while is_wrapped(self.lines[end][0]) and end + 1 < len(self.lines) and self.lines[end + 1][0].strip():
            end += 1
        return join_lines([raw for raw, _ in self.lines[i:end + 1]]), end + 1

    # ---------- headings ----------

    def numbered_heading_level(self, text):
        """
        Return the level if text is the next outline heading (I. / A. / i. / 1.).
        Checking the expected sequence tells letter I apart from roman I and
        avoids mistaking a wrapped line like "C. 1101" for a heading.
        """
        match = NUMBERED_HEADING_RE.match(text)
        if not match or not (match.group(2)[0].isupper() or match.group(2)[0].isdigit()):
            return None

        token = match.group(1)
        o = self.outline

        for level, style in enumerate(self.outline_styles, start=1):
            if len(o) < level - 1:
                break
            current = o[level - 1] if len(o) >= level else None
            if token in expected_tokens(style, current):
                return level
        return None

    def is_unnumbered_heading(self, i):
        """
        Short title-like lines such as "Costs" or "USCIS Form I-765" that
        stand alone between paragraphs.
        """
        raw = self.lines[i][0]
        text = raw.strip()

        if is_wrapped(raw) or len(text) > 90 or text[-1] in ".,;:":
            return False
        if not (text[0].isupper() or text[0].isdigit()):
            return False
        if not (self.prev_blank or self.prev_raw.rstrip().endswith((".", ":", ")", '"', "'", "]"))):
            return False

        following = self.lines[i + 1][0] if i + 1 < len(self.lines) else ""
        return not following.strip() or following.startswith("    ")

    def read_numbered_heading(self, i, level):
        text, next_i = self.read_heading_lines(i)
        token, heading = NUMBERED_HEADING_RE.match(text).groups()

        self.outline = self.outline[:level - 1] + [token]
        section_id = ".".join(self.outline)
        parent_id = ".".join(self.outline[:-1]) or None

        self.new_section(section_id, f"{token}. {heading}", heading, level, parent_id,
                         source_type_for(section_id, self.source_types), self.lines[i][1])
        return next_i

    def read_unnumbered_heading(self, i, parent_id, level, source_type):
        text = self.lines[i][0].strip()
        section_id = f"{parent_id}.{slugify(text)}" if parent_id else slugify(text)
        self.new_section(section_id, text, text, level, parent_id, source_type, self.lines[i][1])
        return i + 1

    # ---------- zone handlers: each reads line i and returns the next index ----------

    def handle_front_matter(self, i, raw, page, col0):
        label = FRONT_LABEL_RE.match(raw) if col0 else None

        if label:
            name = label.group(1)
            self.new_section(f"FRONT.{name.replace(' ', '_')}", name, name, 2, "FRONT",
                             "front_matter", page)
            self.start_paragraph(raw[label.end():], page)
        elif not col0:
            self.start_paragraph(raw, page)
        elif self.paragraph and is_wrapped(self.prev_raw):
            self.continue_paragraph(raw, page)
        else:
            self.start_paragraph(raw, page)   # cover-page lines stand alone

        return i + 1

    def handle_preamble(self, i, raw, page, col0):
        text = raw.strip()
        may_be_heading = col0 and (self.prev_blank or not is_wrapped(self.prev_raw))

        if may_be_heading:
            level = self.numbered_heading_level(text)
            if level:
                return self.read_numbered_heading(i, level)

            if self.outline != self.acronyms_section and self.is_unnumbered_heading(i):
                numbered_id = ".".join(self.outline)
                return self.read_unnumbered_heading(i, numbered_id, len(self.outline) + 1,
                                                    source_type_for(numbered_id, self.source_types))

        if not col0:
            self.start_paragraph(raw, page)
        elif may_be_heading and self.outline == self.acronyms_section:
            self.start_paragraph(raw, page, kind="list_item")   # one acronym per line
        elif self.paragraph and not self.prev_blank:
            self.continue_paragraph(raw, page)
        else:
            self.start_paragraph(raw, page)

        return i + 1

    def handle_regulatory(self, i, raw, page, col0):
        text = raw.strip()

        if col0 and text == "List of Subjects":
            self.list_of_subjects_id = "LIST_OF_SUBJECTS"
            self.new_section("LIST_OF_SUBJECTS", text, text, 1, None, "procedural", page)
            return i + 1

        if col0 and text == "Regulatory Amendments":
            self.list_of_subjects_id = None
            self.new_section("REGULATORY_AMENDMENTS", text, text, 1, None, "procedural", page)
            return i + 1

        part = PART_RE.match(text) if col0 else None
        if part:
            self.part_id = f"8 CFR Part {part.group(1)}"
            self.new_section(self.part_id, text, part.group(2),
                             1, None, "regulatory_text", page)
            return i + 1

        # GPO marks each amendatory instruction with a line holding only "0"
        if col0 and text == "0":
            self.flush_paragraph()
            self.instruction_next = True
            return i + 1

        if self.instruction_next and col0:
            self.instruction_next = False
            number = INSTRUCTION_RE.match(text)

            if number and number.group(1).isdigit():
                n = number.group(1)
                self.new_section(f"{self.part_id} / amendment {n}", f"Amendment {n}",
                                 f"Amendment {n}", 2, self.part_id, "amendatory_instruction", page)
                self.start_paragraph(raw, page)
            else:
                marker = number.group(0).strip() if number else None
                self.start_paragraph(raw[number.end():] if number else raw, page,
                                     kind="list_item", marker=marker)
            return i + 1

        # A wrapped cross-reference can also start a line with "Sec. 214.1"
        starts_line = col0 and (self.prev_blank or not is_wrapped(self.prev_raw))
        cfr_section = CFR_SECTION_RE.match(text) if starts_line else None
        if cfr_section:
            heading_text, next_i = self.read_heading_lines(i)
            number, heading = CFR_SECTION_RE.match(heading_text).groups()
            self.new_section(f"8 CFR {number}", f"\u00a7 {number} {heading}", heading, 2,
                             self.part_id, "regulatory_text", page, cfr_section=number)
            self.cfr_stack, self.after_gap, self.after_stub = [], True, False
            return next_i

        if col0 and OMISSION_RE.match(text):
            self.start_paragraph(raw, page, kind="omitted")
            self.flush_paragraph()
            return i + 1

        if not col0:
            self.start_paragraph(raw, page)
        elif self.prev_blank and self.section["cfr_section"]:
            self.new_section("SIGNATURE", "Signature", "Signature", 1, None, "procedural", page)
            self.start_paragraph(raw, page)
        elif self.prev_blank and self.list_of_subjects_id and self.is_unnumbered_heading(i):
            self.read_unnumbered_heading(i, self.list_of_subjects_id, 2, "procedural")
        elif self.paragraph and not self.prev_blank:
            self.continue_paragraph(raw, page)
        else:
            self.start_paragraph(raw, page)

        return i + 1

    # ---------- main loop ----------

    def run(self):
        self.new_section("FRONT", "Document header", "Document header", 1, None, "front_matter", None)
        handlers = {
            "front_matter": self.handle_front_matter,
            "preamble": self.handle_preamble,
            "regulatory": self.handle_regulatory,
        }

        i = 0
        while i < len(self.lines):
            raw, page = self.lines[i]
            text = raw.strip()
            col0 = bool(text) and not raw.startswith(" ")

            if not text:
                self.flush_paragraph()
                self.prev_blank = True
                i += 1
                continue

            if FOOTNOTE_RULE_RE.match(raw):
                i = self.read_footnotes(i)
                self.prev_raw, self.prev_blank = "", False
                continue

            if TABLE_TITLE_RE.match(raw):
                i = self.read_table(i)
                self.prev_raw, self.prev_blank = "", False
                continue

            if CONTENTS_TABLE_RE.match(raw):
                i = self.read_contents_table(i)
                self.prev_raw, self.prev_blank = "", False
                continue

            if SEPARATOR_RE.match(raw):
                self.flush_paragraph()
                i += 1
                continue

            if self.zone == "front_matter" and col0 and self.numbered_heading_level(text) == 1:
                self.zone = "preamble"
            elif self.zone == "preamble" and col0 and text == "List of Subjects":
                self.zone = "regulatory"

            next_i = handlers[self.zone](i, raw, page, col0)
            self.prev_raw, self.prev_blank = self.lines[next_i - 1][0], False
            i = next_i

        self.flush_paragraph()
        finalize(self.sections)
        return self.sections


def clean_text_table(raw_lines):
    lines = [raw.rstrip() for raw in raw_lines]
    return "\n".join(lines).replace("``", '"').replace("''", '"')


def source_type_for(section_id, source_types):
    parts = section_id.split(".")
    for n in range(len(parts), 0, -1):
        prefix = ".".join(parts[:n])
        if prefix in source_types:
            return source_types[prefix]
    return "agency_explanation"


def finalize(sections):
    """Fill in section paths, page ranges, word counts, and footnote links."""
    by_id = {s["section_id"]: s for s in sections}
    references = {}

    for section in sections:
        path, node = [], section
        while node:
            path.insert(0, node["label"])
            node = by_id.get(node["parent_id"])
        section["path"] = path

        pages = [section["fr_page_start"]] if section["fr_page_start"] else []
        for paragraph in section["paragraphs"]:
            pages += [p for p in (paragraph["fr_page_start"], paragraph["fr_page_end"]) if p]
            for number in paragraph["footnote_refs"]:
                references.setdefault(number, []).append(paragraph["para_id"])
        pages += [f["fr_page"] for f in section["footnotes"] if f["fr_page"]]

        section["fr_page_start"] = min(pages) if pages else None
        section["fr_page_end"] = max(pages) if pages else None
        section["word_count"] = sum(len(p["text"].split()) for p in section["paragraphs"])

    for section in sections:
        for footnote in section["footnotes"]:
            footnote["referenced_in"] = references.get(footnote["number"], [])

    # Put the derived fields next to the identifying ones in the output
    order = ["section_id", "label", "heading", "level", "parent_id", "path", "zone",
             "source_type", "cfr_section", "fr_page_start", "fr_page_end", "word_count",
             "paragraphs", "footnotes"]
    for n, section in enumerate(sections):
        sections[n] = {key: section[key] for key in order}


# --------------------------------------------------
# 7. SAVE OUTPUTS
# --------------------------------------------------

def format_pages(item, volume, start_key="fr_page_start", end_key="fr_page_end"):
    start, end = item.get(start_key), item.get(end_key)
    if not start:
        return ""
    prefix = f"{volume} FR " if volume else "FR page "
    return f"{prefix}{start}" if start == end else f"{prefix}{start}\u2013{end}"


def save_markdown(sections, volume, title, path):
    lines = [
        f"# {title}: Parsed Sections",
        "",
        f"> Parsed from the GPO HTML text of the {title.lower()} by extract_html.py.",
        "> Page numbers are official Federal Register pages.",
        "> Bracketed citations on regulatory paragraphs are computed by the parser;",
        "> a trailing ? means the paragraph nesting was irregular and should be checked.",
        "",
    ]

    for section in sections:
        depth = min(section["level"] + 1, 6)
        meta = [f"`{section['section_id']}`", section["source_type"]]
        pages = format_pages(section, volume)
        if pages:
            meta.append(pages)

        lines.extend([f"{'#' * depth} {section['label']}", "", " \u00b7 ".join(meta), ""])

        for paragraph in section["paragraphs"]:
            text = paragraph["text"]

            if paragraph["kind"] == "table":
                lines.extend(["```", text, "```", ""])
                continue

            if paragraph["kind"] == "omitted":
                # a bare "* * * * *" line would render as a horizontal rule
                lines.extend(["`* * * * *` *(unchanged text omitted)*", ""])
                continue

            if paragraph["cfr_citation"]:
                flag = "" if paragraph["cfr_citation_confident"] else "?"
                text = f"**[{paragraph['cfr_citation']}{flag}]** {text}"

            if paragraph["kind"] == "list_item":
                marker = paragraph["list_marker"] if paragraph["list_marker"] not in (BULLET, CIRCLE) else ""
                indent = "  " if paragraph["list_marker"] == CIRCLE else ""
                text = f"{indent}- {marker + ' ' if marker else ''}{text}"

            lines.extend([text, ""])

        for footnote in section["footnotes"]:
            lines.extend([f"[^{footnote['number']}]: {footnote['text']}", ""])

    path.write_text("\n".join(lines), encoding="utf-8")


def save_outputs(sections, volume, document, output_dir):
    """Write <stem>.jsonl and <stem>.md; return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{document['output_stem']}.jsonl"
    markdown_path = output_dir / f"{document['output_stem']}.md"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for section in sections:
            fh.write(json.dumps(section, ensure_ascii=False) + "\n")
    save_markdown(sections, volume, document["title"], markdown_path)
    return jsonl_path, markdown_path


# --------------------------------------------------
# 8. MAIN PROGRAM
# --------------------------------------------------

def parse_rule(html_path, document):
    """Parse one rule document. Returns (sections, volume, parser)."""
    lines, volume = load_lines(Path(html_path))
    parser = RuleParser(lines, document)
    return parser.run(), volume, parser


def print_report(sections, parser):
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    footnotes = [f for s in sections for f in s["footnotes"]]
    cited = [p for p in paragraphs if p["cfr_citation"]]
    irregular = [p for p in cited if not p["cfr_citation_confident"]]

    by_type = {}
    for section in sections:
        by_type[section["source_type"]] = by_type.get(section["source_type"], 0) + 1

    print(f"Sections:   {len(sections)} "
          f"({', '.join(f'{k} {v}' for k, v in sorted(by_type.items()))})")
    print(f"Paragraphs: {len(paragraphs):,} "
          f"(tables {sum(p['kind'] == 'table' for p in paragraphs)}, "
          f"list items {sum(p['kind'] == 'list_item' for p in paragraphs)})")
    print(f"Footnotes:  {len(footnotes)}")
    print(f"CFR citations: {len(cited)} paragraphs "
          f"({len(irregular)} with irregular nesting, "
          f"{len(parser.unplaced_designators)} could not be placed)")

    known = {f["number"] for f in footnotes}
    missing = sorted({n for p in paragraphs for n in p["footnote_refs"]} - known)
    unreferenced = sorted(f["number"] for f in footnotes if not f["referenced_in"])
    if missing:
        print(f"WARNING: footnote references without footnote text: {missing}")
    if unreferenced:
        print(f"WARNING: footnotes never referenced in the text: {unreferenced}")
    for para_id in parser.unplaced_designators:
        print(f"WARNING: could not place CFR designator in {para_id}")


def main():
    arg_parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    arg_parser.add_argument("--document", choices=DOCUMENTS, default="proposed",
                            help="which version of the rule to parse (default: proposed)")
    arg_parser.add_argument("--html", type=Path,
                            help="content.html to parse (default: set per document in DOCUMENTS)")
    arg_parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = arg_parser.parse_args()

    document = DOCUMENTS[args.document]
    print("Reading HTML...")

    sections, volume, parser = parse_rule(args.html or document["html"], document)
    jsonl_path, markdown_path = save_outputs(sections, volume, document, args.output_dir)
    print_report(sections, parser)

    print(f"Saved to {jsonl_path}")
    print(f"Saved to {markdown_path}")


if __name__ == "__main__":
    main()
