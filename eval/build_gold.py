"""Regenerate eval/gold.jsonl from SciFact plus a set of hand-written perturbations.

Usage: python eval/build_gold.py /path/to/scifact/data > eval/gold.jsonl

SciFact (Wadden et al., 2020; CC BY-NC 2.0) gives each claim a cited abstract and a verdict. Those map
onto the hop format as SUPPORT -> FAITHFUL, CONTRADICT -> DIRECTION_CONTRADICTION, and no evidence ->
UNSUPPORTED_ATTRIBUTION. SciFact has no examples of the subtler kinds of drift, so PERTURBATIONS below
rewrite real SciFact evidence sentences to introduce one specific change each. Those were written by
the DriftTrace developers and have not been reviewed by domain experts. Replace them with
expert-labeled hops as Review mode accumulates them.
"""

import json
import sys
from pathlib import Path

PER_CLASS = {"SUPPORT": 20, "CONTRADICT": 20, "NEI": 8}

# (SciFact doc_id, claim, label, note)
PERTURBATIONS = [
    (4388470, "Male and female avian somatic cells have an inherent sex identity.", "HEDGE_LOSS", "'may have' dropped"),
    (5573975, "The authors demonstrate that Alk3-mediated signaling plays a protective role in the kidney.", "HEDGE_LOSS", "'suggesting' dropped"),
    (25079962, "Decreased bioavailability of nitric oxide causes cerebral vasospasm.", "CAUSAL_INFLATION", "'associated with' to 'causes'"),
    (18872233, "Bariatric surgery directly causes a decrease in depression.", "CAUSAL_INFLATION", "association to causation"),
    (43334921, "Regular use of aspirin and/or NSAIDs prevents colorectal cancer.", "CAUSAL_INFLATION", "lower risk association to 'prevents'"),
    (3355397, "Ever use of pioglitazone causes pancreatic cancer.", "CAUSAL_INFLATION", "HR association to causation"),
    (44265107, "Policies requiring discontinuation of methadone were found in 52% of all programs.", "MAGNITUDE_DISTORTION", "32% to 52%"),
    (7098463, "Remission of type 2 diabetes was achieved by 93% of the surgical group.", "MAGNITUDE_DISTORTION", "73% to 93%"),
    (6477536, "About half of surgical randomized trials were discontinued early.", "MAGNITUDE_DISTORTION", "21% to about half"),
    (12258338, "The rate of preventable ordering adverse drug events fell by more than 90% after the intervention.", "MAGNITUDE_DISTORTION", "66% to more than 90%"),
    (9315213, "FGF21 deficiency causes a marked exacerbation of atherosclerotic plaque formation in humans.", "SCOPE_GENERALIZATION", "mice to humans"),
    (5468807, "Inhibition of HDAC6 activity significantly improves survival in patients with ARID1A-mutated cancers.", "SCOPE_GENERALIZATION", "mice to patients"),
    (7433668, "Infection with intestinal helminths impairs resistance to tuberculosis in people.", "SCOPE_GENERALIZATION", "mice to people"),
    (1780819, "Loss of Hand2 in the endometrium leads to precancerous lesions in women.", "SCOPE_GENERALIZATION", "mice to women"),
    (6490571, "76.3% to 85.4% of serious cases received no treatment.", "CONDITION_DROPPED", "less-developed countries and 12-month window dropped"),
    (12206390, "The residual lifetime risk of developing hypertension is 90%.", "CONDITION_DROPPED", "ages 55 and 65 dropped"),
    (5850219, "The prevalence of chlamydia was 3.1% in women.", "CONDITION_DROPPED", "ages 16-24 dropped"),
    (24338780, "IFN-gamma receptor-deficient mice develop severe myocarditis with high mortality.", "CONDITION_DROPPED", "immunization and BALB/c background dropped"),
]

# Passages that attribute the finding to other work. These are invented for the eval.
SECONDHAND = [
    ("Exercise lowers fasting glucose in adults with prediabetes.", "Previous studies have shown that exercise lowers fasting glucose in adults with prediabetes (Harper et al., 2016; Osei et al., 2018). We did not measure glucose in the present cohort."),
    ("Vitamin D supplementation reduces fracture risk in older women.", "Earlier trials reported that vitamin D supplementation reduced fracture risk in older women [14, 15]. Our study instead examined bone turnover markers."),
    ("Early antibiotic exposure is associated with childhood asthma.", "As reviewed elsewhere [22], early antibiotic exposure has been associated with childhood asthma. Here we focus on gut microbial composition at age two."),
    ("Sleep restriction impairs working memory in young adults.", "It is well established that sleep restriction impairs working memory in young adults (Lim and Dinges, 2010). The current analysis concerns reaction time only."),
]


def main(data_dir: str) -> None:
    data = Path(data_dir)
    corpus = {}
    for line in (data / "corpus.jsonl").open():
        doc = json.loads(line)
        corpus[doc["doc_id"]] = doc

    counts = dict.fromkeys(PER_CLASS, 0)
    labels = {"SUPPORT": "FAITHFUL", "CONTRADICT": "DIRECTION_CONTRADICTION"}
    rows = []
    for line in (data / "claims_dev.jsonl").open():
        claim = json.loads(line)
        evidence = claim.get("evidence") or {}
        if evidence:
            doc_id, sets = next(iter(evidence.items()))
            kind = sets[0]["label"]
            gold = labels[kind]
        elif claim.get("cited_doc_ids"):
            doc_id, kind, gold = claim["cited_doc_ids"][0], "NEI", "UNSUPPORTED_ATTRIBUTION"
        else:
            continue
        if counts[kind] >= PER_CLASS[kind]:
            continue
        counts[kind] += 1
        doc = corpus[int(doc_id)]
        rows.append({
            "id": f"scifact-{claim['id']}", "citing_sentence": claim["claim"], "cited_doi": None, "scifact_doc_id": int(doc_id),
            "source_title": doc["title"], "source_text": " ".join(s.strip() for s in doc["abstract"]),
            "gold_label": gold, "provenance": "scifact",
        })

    for index, (doc_id, claim, gold, note) in enumerate(PERTURBATIONS):
        doc = corpus[doc_id]
        rows.append({
            "id": f"perturbed-{index}", "citing_sentence": claim, "cited_doi": None, "scifact_doc_id": doc_id, "source_title": doc["title"],
            "source_text": " ".join(s.strip() for s in doc["abstract"]), "gold_label": gold, "provenance": "authored-perturbation", "note": note,
        })
    for index, (claim, passage) in enumerate(SECONDHAND):
        rows.append({
            "id": f"secondhand-{index}", "citing_sentence": claim, "cited_doi": None, "source_title": "Invented passage", "source_text": passage,
            "gold_label": "SECONDHAND_CITATION", "provenance": "authored-synthetic",
        })

    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
