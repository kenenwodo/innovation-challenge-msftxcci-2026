"""
Check that the evidence index finds the right evidence for real news sentences.

Runs every case in news_analysis/retrieval_cases.json through keyword-only,
embedding-only and hybrid search, for both layers the fact-checker uses:
key facts (search_facts) and rule paragraphs (search). Reports hit@1,
hit@3, hit@5 and mean reciprocal rank (MRR).

  .venv/bin/python news_analysis/src/eval_retrieval.py
  .venv/bin/python news_analysis/src/eval_retrieval.py --show-misses
"""

import argparse
import json

from build_evidence_index import NEWS_DIR
from evidence_index import POLICY_TIERS, EvidenceIndex

CASES_PATH = NEWS_DIR / "retrieval_cases.json"
K = 5


def paragraph_hits(index, case, mode):
    hits = index.search(case["query"], docs=case.get("docs", ("proposed", "final")),
                        tiers=case.get("tiers", POLICY_TIERS), k=K, mode=mode)
    relevant = [hit["evidence_id"] in case["evidence_ids"]
                or any((hit["cfr_citation"] or "").startswith(p) for p in case["cfr_prefixes"])
                for hit in hits]
    return hits, relevant


def fact_hits(index, case, mode):
    hits = index.search_facts(case["query"], docs=case.get("docs", ("proposed", "final")), k=K, mode=mode)
    return hits, [hit["fact_id"] in case["key_facts"] for hit in hits]


def first_rank(relevant):
    return next((n for n, ok in enumerate(relevant, start=1) if ok), None)


def report(title, cases, results, modes):
    print(f"\n{title}: rank of first relevant result ({len(cases)} cases)\n")
    print(f"{'mode':<8}{'hit@1':>7}{'hit@3':>7}{'hit@5':>7}{'MRR':>7}")
    for mode in modes:
        ranks = [first_rank(relevant) for _, relevant in results[mode]]
        hit = lambda k: sum(1 for r in ranks if r and r <= k)
        mrr = sum(1 / r for r in ranks if r) / len(ranks)
        print(f"{mode:<8}{hit(1):>7}{hit(3):>7}{hit(5):>7}{mrr:>7.2f}")

    print()
    for n, case in enumerate(cases):
        row = "  ".join(f"{mode} {first_rank(results[mode][n][1]) or '-'}" for mode in modes)
        print(f"  {case['id']:<30}{row}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--show-misses", action="store_true",
                        help="print the top paragraphs for cases hybrid search missed")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    index = EvidenceIndex.load()
    modes = ["bm25", "dense", "hybrid"] if index.has_embeddings else ["bm25"]

    facts = {mode: [fact_hits(index, case, mode) for case in cases] for mode in modes}
    paragraphs = {mode: [paragraph_hits(index, case, mode) for case in cases] for mode in modes}

    report("Key facts", cases, facts, modes)
    report("Rule paragraphs (tiers A-B unless the case says otherwise)", cases, paragraphs, modes)

    if args.show_misses:
        for n, case in enumerate(cases):
            hits, relevant = paragraphs[modes[-1]][n]
            if not any(relevant):
                print(f"\nMISS {case['id']}: {case['query']}")
                for hit in hits:
                    print(f"   {hit['evidence_id']:<32}{hit['cfr_citation'] or '':<28}{hit['text'][:70]}")


if __name__ == "__main__":
    main()
