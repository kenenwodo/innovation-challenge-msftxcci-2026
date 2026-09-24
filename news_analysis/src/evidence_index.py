"""
Search the evidence index built by build_evidence_index.py.

Hybrid retrieval: BM25 keyword scores (good at numbers, CFR citations and
form names) fused with embedding similarity (good at paraphrase) by
reciprocal rank fusion. Filters restrict results by rule version, evidence
tier and voice, so a claim about what the rule does can be matched against
binding text and agency statements only, never against commenters' views.

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

import numpy as np
from rank_bm25 import BM25Okapi

from build_evidence_index import EMBEDDING_MODEL, EVIDENCE_DIR, TIERS, search_text

# Evidence that may support a claim about what the rule says or does
POLICY_TIERS = ("A", "B")

# bge models expect this prefix on queries (not on passages)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

RRF_K = 60           # standard reciprocal-rank-fusion constant
CANDIDATES = 100     # how deep each ranker's list goes into the fusion

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


class HybridIndex:
    """BM25 plus optional dense vectors over a list of texts."""

    def __init__(self, texts, vectors=None):
        self.bm25 = BM25Okapi([tokenize(t) for t in texts])
        self.vectors = vectors
        self._model = None

    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def rank(self, query, allowed, k, mode="hybrid"):
        """
        Return [(position, scores)] for the top k allowed positions.
        allowed is a boolean mask over the indexed texts; mode is
        "hybrid", "bm25" or "dense".
        """
        candidates = np.flatnonzero(allowed)
        if not len(candidates):
            return []

        rankings = {}
        if mode in ("hybrid", "bm25"):
            bm25 = self.bm25.get_scores(tokenize(query))
            rankings["bm25"] = (bm25, candidates[np.argsort(-bm25[candidates])][:CANDIDATES])

        if mode in ("hybrid", "dense") and self.vectors is not None:
            query_vector = self.model().encode(QUERY_PREFIX + query, normalize_embeddings=True)
            cosine = self.vectors @ query_vector
            rankings["dense"] = (cosine, candidates[np.argsort(-cosine[candidates])][:CANDIDATES])

        fused = {}
        for name, (scores, order) in rankings.items():
            for rank, position in enumerate(order, start=1):
                entry = fused.setdefault(int(position), {"rrf": 0.0})
                entry["rrf"] += 1 / (RRF_K + rank)
                entry[f"{name}_rank"] = rank
                entry[name] = round(float(scores[position]), 4)

        best = sorted(fused.items(), key=lambda item: -item[1]["rrf"])[:k]
        return [(position, {**scores, "rrf": round(scores["rrf"], 5)}) for position, scores in best]


class EvidenceIndex:

    def __init__(self, records, vectors=None, key_facts=None):
        self.records = records
        self.by_id = {r["evidence_id"]: r for r in records}
        self.paragraphs = HybridIndex([search_text(r) for r in records], vectors)
        self.key_facts = key_facts or []
        self._facts_index = None

    @classmethod
    def load(cls, evidence_dir=EVIDENCE_DIR, use_embeddings=True):
        with (evidence_dir / "evidence_paragraphs.jsonl").open(encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]

        vectors = None
        meta_path = evidence_dir / "embeddings_meta.json"
        if use_embeddings and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["evidence_ids"] != [r["evidence_id"] for r in records]:
                raise RuntimeError("embeddings.npy is out of date: re-run build_evidence_index.py")
            vectors = np.load(evidence_dir / "embeddings.npy")

        facts_path = evidence_dir / "key_facts_verified.json"
        facts = json.loads(facts_path.read_text(encoding="utf-8"))["facts"] if facts_path.exists() else []
        return cls(records, vectors, facts)

    @property
    def has_embeddings(self):
        return self.paragraphs.vectors is not None

    def get(self, evidence_id):
        return self.by_id[evidence_id]

    def search(self, query, docs=("proposed", "final"), tiers=POLICY_TIERS, voices=None, k=8,
               mode="hybrid", footnotes=False):
        """
        Top-k evidence paragraphs for query. Each hit is the evidence record
        plus a "scores" dict (rrf, and bm25/dense scores and ranks).

        Footnotes are left out unless asked for: they are mostly DHS's source
        citations (press releases, reports), not statements about the rule.
        """
        allowed = np.array([
            r["doc"] in docs and r["tier"] in tiers and (voices is None or r["voice"] in voices)
            and (footnotes or r["kind"] != "footnote")
            for r in self.records
        ])
        return [{**self.records[position], "scores": scores}
                for position, scores in self.paragraphs.rank(query, allowed, k, mode)]

    def search_facts(self, query, docs=("proposed", "final"), k=5, mode="hybrid"):
        """
        Top-k key facts for query, matched on each fact's plain-English
        statement plus its quotes from the rule (the quotes carry the
        official wording, e.g. "4 years" where the statement says "4-year").
        """
        if self._facts_index is None:
            texts = [f["statement"] + " " + " ".join(e["quote"] for e in f["evidence"])
                     for f in self.key_facts]
            vectors = None
            if self.has_embeddings:
                vectors = self.paragraphs.model().encode(texts, normalize_embeddings=True)
            self._facts_index = HybridIndex(texts, vectors)
            self._facts_index._model = self.paragraphs._model

        allowed = np.array([any(d in docs for d in f["applies_to"]) for f in self.key_facts])
        return [{**self.key_facts[position], "scores": scores}
                for position, scores in self._facts_index.rank(query, allowed, k, mode)]


# --------------------------------------------------
# COMMAND LINE
# --------------------------------------------------

def print_hit(n, hit):
    scores = hit["scores"]
    ranks = " ".join(f"{name}#{scores[name + '_rank']}" for name in ("bm25", "dense") if name + "_rank" in scores)
    where = " · ".join(x for x in (hit.get("cfr_citation"), hit.get("fr_cite"), hit["heading"][:60]) if x)
    print(f"{n}. {hit['evidence_id']}  [tier {hit['tier']}, {hit['voice']}]  ({ranks})")
    print(f"   {where}")
    print(textwrap.indent(textwrap.fill(hit["text"][:400], 100), "   "))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("query")
    parser.add_argument("--docs", default="proposed,final", help="comma-separated: proposed,final")
    parser.add_argument("--tiers", default=",".join(POLICY_TIERS),
                        help="comma-separated evidence tiers: " + "; ".join(f"{t}={d.split(':')[0]}" for t, d in TIERS.items()))
    parser.add_argument("--voices", help="comma-separated: regulation,agency,commenters")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--footnotes", action="store_true", help="include footnotes (DHS's source citations)")
    parser.add_argument("--facts", action="store_true", help="search the key facts instead of paragraphs")
    parser.add_argument("--keyword-only", action="store_true", help="skip embeddings")
    args = parser.parse_args()

    index = EvidenceIndex.load(use_embeddings=not args.keyword_only)
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
                        footnotes=args.footnotes)
    for n, hit in enumerate(hits, start=1):
        print_hit(n, hit)


if __name__ == "__main__":
    main()
