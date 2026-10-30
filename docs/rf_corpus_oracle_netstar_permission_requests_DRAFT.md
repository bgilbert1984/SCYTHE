# ORACLE / NetSTAR LoRa permission-clarification requests — DRAFT (not sent)

**Status:** drafts only. Nothing contacted, downloaded, or filed.
**Context:** intake worklist P1/P2. WiSig (CC BY-NC-SA 4.0) is the only
paper-cited corpus that currently passes the license gate; ORACLE and NetSTAR
are held in the blocked registry. These requests seek the evidence that
would let them through — they do not weaken the gate.

## Verified state (2026-10-30)

**ORACLE** (Sankhe et al., INFOCOM 2019; Genesys Lab, Northeastern)
- `https://genesys-lab.org/oracle` states no license or terms; asks only that
  publications cite the paper.
- Datasets: `hdl.handle.net/2047/D20324547` (raw IQ, 16 X310) and
  `…/D20324548` (demodulated IQ symbols). Both redirect to
  `repository.library.northeastern.edu/files/neu:m044q520q` and
  `…/neu:m044q5228`; both returned **HTTP 403** to an automated fetch. The
  repository rights field is unread — unresolved, not evidence of no license.
- A 2026 arXiv paper labels it "CC BY 4.0 (inferred)". Not provider-stated;
  not counted.
- No verified contact address. Do not guess one; use the contact the
  repository record or the Genesys Lab site exposes, or the corresponding
  author of the paper.

**NetSTAR LoRa** (Elmaghbub & Hamdaoui, IEEE Access 2021; Oregon State)
- Release notes (2021; Oct 2023 v2) say the data "can be downloaded and used
  for research", with a request to cite the IEEE Access paper. No named
  license, no redistribution, commercial-use, or model terms.
- Hosted at `research.engr.oregonstate.edu/hamdaoui/datasets`.
- Contact `hamdaoui@oregonstate.edu` appeared in a web-search summary only;
  confirm it on the lab site before use.

## Acceptance criteria (same as the ModForge request)

- A **named, written license**, or a **written grant explicit enough to
  document as a separate exception** to the gate, covering the four items
  asked below. The registry's rights fields must be citable.
- Insufficient: informal "sure, go ahead" with nothing committed or citable;
  a statement covering research use only when other items stay silent
  (record those as `unverified`, fail-closed).

## Draft request — ORACLE

**Subject:** Terms of use for the ORACLE RF fingerprinting datasets

Hello — I'm building a research program that fingerprints public RF datasets
(metrics and reports; I do not redistribute corpora). ORACLE is a strong
candidate for cross-distance evaluation, and I'd like to use it correctly.

The download page asks for citation but states no license, and I couldn't read
a rights field on the Northeastern repository records for the two datasets
(`2047/D20324547`, `2047/D20324548`). Could you tell me, ideally as a named
license or a short written statement:

1. **Research use:** is non-commercial research use permitted?
2. **Commercial use:** is use in a commercial product or service permitted,
   or does it need separate permission?
3. **Redistribution:** may derived files (resampled, windowed, or
   feature-extracted data) be shared, or only the original archives from the
   repository?
4. **Trained models:** may models trained on the data, and fingerprint assets
   derived from them, be released, and under what terms?

A license line on the download page or repository record would settle it for
everyone. I will cite exactly as you specify. No urgency, and I won't use the
data until I hear back.

## Draft request — NetSTAR LoRa

**Subject:** Terms of use for the LoRa device-fingerprinting datasets

Hello — I'm building a research program that fingerprints public RF datasets
(metrics and reports; I do not redistribute corpora). Your LoRa datasets
("LoRa Device Fingerprinting in the Wild") are a good fit for cross-day
evaluation.

The release notes say the data "can be downloaded and used for research" and
ask for citation of the IEEE Access paper, which is clear for that purpose.
Because my registry needs an explicit rights statement per corpus, could you
confirm, ideally as a named license (e.g. a Creative Commons license) or a
short written statement:

1. **Research use:** non-commercial research use, as in the release notes.
2. **Commercial use:** whether use in a commercial product or service is
   permitted or needs separate permission.
3. **Redistribution:** whether derived files may be shared, or only the
   originals from your site.
4. **Trained models:** whether models trained on the data, and derived
   fingerprint assets, may be released, and under what terms.

A license note on the datasets page would settle it. I'll cite as you
specify, and I won't use the data until I hear back.

## Notes

- Keep each message to licensing only. No complaints about missing metadata,
  broken links, or format; those read as grievances next to a rights ask.
- Items 2–4 are the new ones relative to the ModForge request. If only item 1
  is answered, record 2–4 as `unverified` (fail-closed) per the
  `rights_assessment` sidecar; do not infer them.
- No reply is not a finding about the corpus. Admit-or-drop on silence is
  the owner's call.
- Sending is a separate decision: these drafts authorize nothing.
