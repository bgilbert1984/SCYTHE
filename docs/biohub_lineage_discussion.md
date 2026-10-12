> **Archived 2026-10-12.** This is a Kaggle discussion-post draft written during the
> Biohub "Cell Tracking During Development" competition, which has ended. The
> notebook, image and build script it referenced (`kaggle/biohub-lineage/`) were
> never committed to this repository and were deleted uncommitted alongside the
> rest of `kaggle/` (removed in the companion BioHub CellOps cleanup; see
> `docs/biohub-2026.md`). This document is kept as the one piece of that
> directory worth a permanent record. Links to Kaggle kernels below are external
> and may no longer resolve.

**Title:** A 0.953 tracker keeps only about half of labelled lineages intact

---

The leaderboard scores each frame-to-frame link on its own. But tracking in developmental biology is used to read **lineages**, which cell came from which across the whole movie, and one wrong link corrupts a lineage from that point on. So I measured how a public 0.953 pipeline ([Biohub 0.953 LB | ORIGINAL](https://www.kaggle.com/code/anvithpothula/biohub-0-953-lb-original)) does per lineage rather than per link.

**Setup.** The 4 visible `test/` movies are byte copies of training samples, so their labels are available. I matched predictions to labels with a re-implementation of the metric (after [sleepymegacat's notebook](https://www.kaggle.com/code/sleepymegacat/the-metric-decides-your-architecture-8-measured), which verified it against the official scorer), and it reproduces the link counts exactly: 2045 TP / 105 FP / 82 FN.

### Findings

**1. 96% of labelled links, 48% of labelled lineages.** The pipeline reproduces 2045 of 2127 labelled links, but only 32 of 66 labelled lineages come out with no error at all.

**2. Errors cluster; they don't compound.** Of all chains of k consecutive labelled links, here is the fraction reproduced perfectly, next to what independent errors would give (0.96^k):

| chain length k | 1 | 5 | 10 | 20 | 50 |
|---|---|---|---|---|---|
| observed | 0.96 | 0.87 | 0.81 | 0.70 | 0.64 |
| if errors were independent | 0.96 | 0.82 | 0.67 | 0.44 | 0.13 |

(A `chain-fidelity.png` plot accompanied this table in the original draft; the image was not retained.)

The curve levels off at about 0.62–0.65. Most lineages are tracked perfectly, and a minority absorb nearly all the errors.

**3. Many broken lineages are one link from perfect.** 16 of the 34 broken lineages have exactly one wrong link. Flagging the least certain link in each lineage for a person to check, which is how tracking is actually used in the lab, could recover many of them cheaply.

**4. The misses are mostly association errors, not detection.** Of the 82 missed labelled links:
- 41: both cells were detected, but each was linked to a different partner
- 22: a cell was not detected
- 12: the source continues along one path while the labelled target starts a new track, the signature of a missed division
- 7: the source track ends while its labelled target is taken by another track

**5. Divisions and dense regions are the weak spots.** All 6 links out of dividing cells are wrong, against 4% of links overall. That is only 3 divisions, so read it as a signal, not a rate. In the densest movie (~700 predicted cells per frame) 39% of lineages are intact, against 75% in a sparse one (~60 per frame).

### Four things that did not help

Consistent with finding 4, everything aimed at detection or localisation came out flat:
- **Refining node centroids** to the local intensity centre of mass (12 variants): best +0.004 link Jaccard, from one movie, and only with decimal coordinates. Rounded back to integer voxels, every variant scored zero or worse. The matched centres sit a median 0.8–2.6 µm from the labels, and it barely matters, because sparse labels are rarely stolen by a neighbouring prediction.
- **Gap-filling** with a second detector (DoG): the pipeline already closes 2-frame gaps, so there was almost nothing to bridge. Adding the second detector's tracks where the pipeline had none scored between −0.0024 and +0.0006, with the sign flipping as the settings changed.
- **The notebook's own post-processing sweep** (7 variants): all within 0.001 of the default.
- **Detection-threshold tuning**, on a simpler DoG + Trackastra pipeline: +0.012 on the public leaderboard (0.601 to 0.613). Useful there, but nowhere near the gap to 0.953.

### Caveats

- Small sample: 4 movies, 66 labelled lineages, 3 divisions.
- These movies are training samples, and the pipeline's models may have been trained on them, so its numbers here are probably optimistic.
- Only ~2.8% of cells are labelled, and those may be easier to follow than average.

**Questions for others:** has anyone measured lineage-level accuracy on their own pipeline? And does the clustering hold for yours, a few hard lineages taking most of the errors? If it does, association and division handling in hard regions is where the remaining gains are, not detection.
