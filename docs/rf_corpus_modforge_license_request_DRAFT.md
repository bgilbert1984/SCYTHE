# ModForge / CorteXforge license-clarification request — DRAFT (not sent)

**Status:** draft for Spectrcyde's call. Nothing contacted.
**Context:** `rf_corpus_observatory_modforge_intake_DRAFT.md` — intake decision (1):
pursue license clarification with the author, or drop ModForge.
**Verified 2026-10-29 ~19:36 UTC:**
- `https://raw.githubusercontent.com/Andreaj42/CorteXforge/main/LICENSE` —
  still "Copyright (c) 2026, Andréa Joly / All rights reserved." (2 lines).
- GitHub repo page (Andreaj42/CorteXforge) reports license: **Other (NOASSERTION)**.
- Author: Andréa Joly, PhD student, Inria (2024–present);
  paper co-authors Leonardo S Cardoso (CITI), Jean-Marie Gorce;
  arXiv 2610.26999v1, submitted 2026-10-19.
- Dataset (ModForge) license: unstated anywhere (registry.json has no license
  field; README silent). Hosted only at cortexforge.andrea-joly.fr.
- Contact channels found: GitHub repo (issue/discussion — standard channel);
  author's portfolio andrea-joly.fr (no contact details verified on the
  rendered page). No email address verified — do not guess one.

## Suggested channel

GitHub issue on Andreaj42/CorteXforge (public, the normal channel for an
open-source licensing question). Alternative: whatever contact the author's
site exposes, from his own account. Nothing has been opened or sent.

## Draft issue

**Title:** License status of the code and of the ModForge dataset

**Body:**

Hi — I'm working on a research program that fingerprints public RF datasets
(non-commercial, metrics-and-reports only, no redistribution; EXTERNAL provenance,
results cited per your terms), and CorteXforge/ModForge is a natural candidate
because it's a real-receiver corpus with the same 24 classes as RadioML2018.

Two questions before I can touch it — our registry requires a stated license
field for every corpus, and I couldn't find one:

1. **Code:** the repo LICENSE is "Copyright (c) 2026, Andréa Joly / All
   rights reserved", but the v1.0.0 release notes mention "open-source
   licensing". Is the all-rights-reserved LICENSE the intended one, or is
   an open license (e.g. Apache-2.0 / MIT) planned?

2. **Data:** the ModForge dataset (and awgn_single) have no stated license
   anywhere I could find — not in the README, not in registry.json. Is the
   dataset available for non-commercial research use, and under what terms?
   A one-line license note in the README (e.g. CC-BY-4.0) would settle it.

No urgency — but until (2) is clarified I won't download the archive. Happy
to cite exactly as you specify.

## What a sufficient answer looks like (acceptance criteria)

- A **named, written license** on the dataset (e.g. CC-BY-4.0) stated in the
  README or a LICENSE-DATA file — not a reply-only "sure, use it". The
  registry's license field must be citable.
- The code question reconciled: either the release notes match the LICENSE
  or the LICENSE changes.
- Insufficient: verbal permission in an email/issue with no license file
  committed; a license covering code only, leaving the data unstated.

## Notes

- Keep the issue to licensing only. The HDF5-vs-SigMF format discrepancy and
  the `modfore` README typo are separate, non-blocking observations — do not
  bundle them in; they read as complaints next to a license ask.
- If the author clarifies, the intake plan stands: awgn_single (1.59 GB,
  SigMF) as the cheap probe first, ModForge (10.4 GB, HDF5) held for later.
- If no reply within a reasonable window: that's not a finding about the
  corpus — decision (2) of the intake draft (admit or drop) is then his to
  make on silence.
