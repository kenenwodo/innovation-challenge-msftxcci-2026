"""
Search the evidence index built by build_evidence_index.py.

BM25 keyword retrieval over the proposed rule, final rule, and court ruling.
Filters restrict results by document, evidence tier, voice, and date, so a
claim about what the rule does can be matched against primary evidence only.

  .venv/bin/python news_analysis/src/evidence_index.py "grace period cut to 30 days"
  .venv/bin/python news_analysis/src/evidence_index.py "4 year cap" --docs final --tiers A -k 5
  .venv/bin/python news_analysis/src/evidence_index.py "students can extend their stay" --facts

From Python:
  from evidence_index import EvidenceIndex
  index = EvidenceIndex.load()
  hits = index.search("grace period cut to 30 days", docs=["final"], tiers=["A", "B"])
"""

import argparse
import json
import re
import textwrap

from rank_bm25 import BM25Okapi

from build_evidence_index import EVIDENCE_DIR, TIERS, search_text

# Evidence that may support a claim about what the rule says or does
POLICY_TIERS = ("A", "B")

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*")


def tokenize(text):
    """Lowercase word tokens. Compounds like "4-year" or "f-1" also yield their parts."""
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        tokens.append(token)
        parts = re.split(r"[-./]", token)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p)
    return tokens


class BM25Index:
    """Small BM25 wrapper that supports record filters."""

    def __init__(self, texts):
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])

    def rank(self, query, allowed, k):
        """Return the top allowed positions with their BM25 scores and ranks."""
        candidates = [position for position, is_allowed in enumerate(allowed) if is_allowed]
        if not candidates:
            return []

        scores = self.bm25.get_scores(tokenize(query))
        best = sorted(candidates, key=lambda position: -scores[position])[:k]
        return [
            (position, {"bm25": round(float(scores[position]), 4), "bm25_rank": rank})
            for rank, position in enumerate(best, start=1)
        ]


class EvidenceIndex:

    def __init__(self, records, key_facts=None):
        self.records = records
        self.by_id = {r["evidence_id"]: r for r in records}
        self.paragraphs = BM25Index([search_text(r) for r in records])
        self.key_facts = key_facts or []
        self._facts_index = None

    @classmethod
    def load(cls, evidence_dir=EVIDENCE_DIR):
        with (evidence_dir / "evidence_paragraphs.jsonl").open(encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]

        facts_path = evidence_dir / "key_facts_verified.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))["facts"] if facts_path.exists() else []
        return cls(records, facts)

    def get(self, evidence_id):
        return self.by_id[evidence_id]

    def search(self, query, docs=("proposed", "final"), tiers=POLICY_TIERS, voices=None, k=8,
               footnotes=False, as_of=None):
        """
        Top-k evidence paragraphs for query. Each hit is the evidence record
        plus a "scores" dict containing its BM25 score and rank.

        Footnotes are left out unless asked for: they are mostly DHS's source
        citations (press releases, reports), not statements about the rule.
        """
        allowed = [
            r["doc"] in docs and r["tier"] in tiers and (voices is None or r["voice"] in voices)
            and (footnotes or r["kind"] != "footnote")
            and (as_of is None or not r.get("available_date") or r["available_date"] <= as_of)
            for r in self.records
        ]
        return [{**self.records[position], "scores": scores}
                for position, scores in self.paragraphs.rank(query, allowed, k)]

    def search_facts(self, query, docs=("proposed", "final"), k=5):
        """
        Top-k key facts for query, matched on each fact's plain-English
        statement plus its quotes from the rule (the quotes carry the
        official wording, e.g. "4 years" where the statement says "4-year").
        """
        if self._facts_index is None:
            texts = [f["statement"] + " " + " ".join(e["quote"] for e in f["evidence"])
                     for f in self.key_facts]
            self._facts_index = BM25Index(texts)

        allowed = [any(d in docs for d in f["applies_to"]) for f in self.key_facts]
        return [{**self.key_facts[position], "scores": scores}
                for position, scores in self._facts_index.rank(query, allowed, k)]


# --------------------------------------------------
# COMMAND LINE
# --------------------------------------------------

def print_hit(n, hit):
    scores = hit["scores"]
    where = " · ".join(
        x
        for x in (
            hit.get("court_citation"),
            hit.get("cfr_citation"),
            hit.get("fr_cite"),
            hit["heading"][:60],
        )
        if x
    )
    print(
        f"{n}. {hit['evidence_id']}  [tier {hit['tier']}, {hit['voice']}]  "
        f"(BM25 {scores['bm25']})"
    )
    print(f"   {where}")
    print(textwrap.indent(textwrap.fill(hit["text"][:400], 100), "   "))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("query")
    parser.add_argument(
        "--docs",
        default="proposed,final",
        help="comma-separated: proposed,final,court_memo,court_order",
    )
    parser.add_argument("--tiers", default=",".join(POLICY_TIERS),
                        help="comma-separated evidence tiers: " + "; ".join(f"{t}={d.split(':')[0]}" for t, d in TIERS.items()))
    parser.add_argument("--voices", help="comma-separated: regulation,agency,commenters,court")
    parser.add_argument("--as-of", help="exclude evidence filed or published after YYYY-MM-DD")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--footnotes", action="store_true", help="include footnotes (DHS's source citations)")
    parser.add_argument("--facts", action="store_true", help="search the key facts instead of paragraphs")
    args = parser.parse_args()

    index = EvidenceIndex.load()
    docs = args.docs.split(",")

    if args.facts:
        for n, fact in enumerate(index.search_facts(args.query, docs=docs, k=args.k), start=1):
            cites = sorted({m["cfr_citation"] or m["evidence_id"] for e in fact["evidence"] for m in e["matches"][:1]})
            print(f"{n}. {fact['fact_id']} ({fact['topic']}, applies to {'/'.join(fact['applies_to'])})")
            print(textwrap.indent(textwrap.fill(fact["statement"], 100), "   "))
            print(f"   evidence: {', '.join(cites)}\n")
        return

    hits = index.search(args.query, docs=docs, tiers=args.tiers.split(","),
                        voices=args.voices.split(",") if args.voices else None, k=args.k,
                        footnotes=args.footnotes, as_of=args.as_of)
    for n, hit in enumerate(hits, start=1):
        print_hit(n, hit)


if __name__ == "__main__":
    main()
