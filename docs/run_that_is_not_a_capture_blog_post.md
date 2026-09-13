# The Run That Is Not a Capture: Building Evidence That Cannot Flatter Itself

**Date:** September 13, 2026
**Author:** SCYTHE Core Engineering Team
**Category:** Evidence-Centered Computing, RF, GraphOps, Promotion Discipline

---

An SDR is plugged into this workstation. A `Realtek RTL2838` sits on
`Bus 001 Device 026`, tuned to nothing, doing nothing. Over the last several
weeks SCYTHE built the entire path that would let a live walk produce evidence
good enough to promote into GraphOps — the durable ledger, the ownership lock,
reconciliation, ceilings, the execution boundary, the derived-evidence reader
and its producer.

And then, at the moment the run was scoped, the answer came back: **do not open
the radio.**

Not because the radio is dangerous. Because a capture would have changed
nothing, and running one anyway would have been authorization theater with a
USB cable attached.

That decision, and the six slices of code that followed from it, are the most
interesting thing SCYTHE has built this quarter. The engineering story is not
"we captured RF." It is "we established exactly what our evidence is allowed to
claim, and then wrote code that cannot claim more."

## The Finding That Made the Run Small

The question was simple: what does a walk verdict actually depend on?

SCYTHE's walk checker compares two position fixes against a kinematic budget.
Did the operator move further than a person walking could have moved in the
elapsed time? Two hashes travel with each fix:

```text
signal_chain_hash          sensor identity, sample type, rate, antenna,
                           feedline, extension, gain  -- all DECLARED
receiver_state_chain_hash  a manifest of position, speed and orientation
                           authorities
```

Neither touches a sample. `check_walk_step` never sees one either. A walk
verdict is a statement about displacement, and displacement is computed from
latitude, longitude and time.

So we asked what a capture would buy. It would activate the volatile IQ buffer,
`rtl_tcp`'s socket binding, the ring's invalidation rules, and the recovery
subsystem's posture — the entire Q1 surface — and produce an artefact
**identical in every field the verdict depends on**. A live observation does not
become more meaningful because a receiver was consuming samples nobody looks at.

The act was classified instead:

```text
LIVE_POSITION_ATTESTATION      a real bounded observation over real
                               attested positions
INSTRUMENT_CONFIGURED_IDLE     the instrument is declared, and idle
RF_MEASUREMENT_NOT_PERFORMED   no sample was taken, and none was needed
```

The RTL2838's presence may be *declared*. The run must not open, tune, reset,
claim, or otherwise communicate with it.

## The Schema Could Not Express That, So It Changed

Here is where a contract stops being prose. The artefact provenance in
`scythe_derived_evidence` is a **closed** field set — a name that is not in the
set is refused rather than ignored — and it carried no measurement status at
all. An artefact could describe a sample rate and a gain, and nothing in the
record distinguished *configured* from *exercised*.

Adding a required field to a closed set changes what the old schema name means,
so the name changed with it:

```text
scythe.derived-evidence-artefact.v1  ->  .v2
```

A v1 artefact is now refused as foreign — carrying the new fields or not,
because the version is not a hint to be overridden by whatever happens to be
present. There is **no migration and no default**, and none is needed: no v1
artefact was ever produced. Writing a compatibility path for a population of
zero would mean inventing exactly the defaults the amendment forbids, for a
reader that would never meet one.

The two new fields are closed to a single value each:

```python
MEASUREMENT_STATUSES = (RF_MEASUREMENT_NOT_PERFORMED,)
INSTRUMENT_STATES    = (INSTRUMENT_CONFIGURED_IDLE,)
```

One member is the honest size. This tree can produce exactly one kind of
artefact, and a second value would name a capability that does not exist, read
by a reader that has never seen one produce anything. When a measurement path
is contracted, its amendment adds its value.

## Configuration Is Not Measurement

The subtler rule is the one a well-meaning classifier would get wrong.

An artefact may legitimately carry the configuration it did not exercise —
`sample_rate_hz = 2_400_000`, `gain_db = 40.2`, `device_id = "rtl2838-0bda:2838"`
— and it is still a run where nothing was measured. Every populated setting
travels beside its own label:

```text
sample_rate_hz             2400000
sample_rate_hz_exercise    CONFIGURED_NOT_EXERCISED
gain_db                    40.2
gain_db_exercise           CONFIGURED_NOT_EXERCISED
```

A setting without its label, a label without its setting, and any other label
are each refused — and neither the reader nor the producer supplies the missing
claim, because labelling on the caller's behalf would make the label unable to
be wrong.

The reader's status function reads one field and looks at nothing else:

```python
def declared_measurement_status(provenance):
    """What the artefact says, never what the reader would guess."""
    return provenance["measurement_status"]
```

Not `device_id`: an RTL2838 in the manifest is an instrument that was *named*,
and naming one is not using one. Not the settings: a populated rate and gain
are a configuration, and the whole rule is that a declared configuration must
not imply it was exercised.

The important design choice is what happens to that configured-idle artefact:
it is **accepted, with its status preserved**, not refused. Refusing it would
teach a producer to omit the configuration entirely, after which the reader
knows *less* about what was attached than it does now. That is the refusal that
makes the record worse.

## The Observer Cannot Say It Either

The artefact is only half the record. The observation record — the thing that
says what this run *was* — also has to carry the declaration, and here the
property is an absence rather than a feature.

The observer has:

- no constructor field for a measurement status
- no `run` parameter for one
- and, enforced by an AST test over the entire module, **no occurrence of
  `RF_MEASUREMENT_NOT_PERFORMED` or `INSTRUMENT_CONFIGURED_IDLE` as a name or
  inside any string**

Containment, not equality — a token spliced into a longer literal would reach a
record just as surely as one standing alone.

The only route is an `Artefact`, taken nominally:

```python
InstrumentDeclaration.from_artefact(artefact)   # type(artefact) is Artefact
```

The observer never met an instrument and neither did the producer. The only
party with a claim to make is the artefact, and the artefact makes it in
writing. So a run carries a declaration or says it has none:

| run | authority | status |
| --- | --- | --- |
| derived artefact | `CARRIED_FROM_ARTEFACT` | the artefact's own values |
| constructed | `NO_INSTRUMENT_DECLARATION` | `NO_INSTRUMENT_DECLARATION` |

A constructed run has **no instrument** — not an idle one, not a configured
one, none. A constructed run handed a carried declaration, and a live run
handed one from nowhere, both publish *no record at all*: the mismatch is
refused before the first verdict rather than recorded beside the numbers it
would undermine.

## One Read, Or The Record Describes A Different File

Verdicts and the declaration must come from the same bytes. Reading the
artefact path once for the verdicts and again for the declaration would attest
an instrument belonging to whatever the second open found.

So the verdict source became a type rather than two arguments:

```python
VerdictSource(declaration=..., verdicts=...)   # one artefact, one read
```

A declaration passed *beside* an iterator is a declaration about whatever the
caller says. One test counts the opens; one negative control performs the
second one and fails that test.

The same rule surfaced again a directory down. The record reports whether the
promotion lineage changed during the observation, by digesting every published
generation before and after. An empty digest map turned out to have **three**
causes:

```text
{}   the namespace does not exist
{}   it exists and holds no generation
{}   the listing failed
```

A record publishing only the map flattens all three into one — and the
flattened version reads as the most reassuring of them: *a valid empty
generation was observed and did not change*. Nobody made that claim.

The fix is a single snapshot from a single directory listing:

```python
LineageSnapshot(presence, digest)
#   NO_LINEAGE_NAMESPACE | LINEAGE_HOLDS_NO_GENERATION | LINEAGE_PRESENT
```

Two listings can describe two filesystem instants, so presence answered by one
and digests taken from the other is a record about no single moment. And a
listing that fails for any reason other than a missing namespace raises
`LINEAGE_INSPECTION_REFUSED` and publishes nothing — *we could not look* is not
an answer to *what was there*.

An earlier draft of that record also carried a `lineage_present` boolean beside
the enum. It was removed. A serialized copy is a second answer that can
disagree with the one next to it, and a reader has no way to tell which one the
writer meant. The convenience survives as a derived property; the evidence does
not store it twice.

## Names Are Checked By Machine, And Rejected Rather Than Judged

SCYTHE keeps two disjoint verdict vocabularies: **merit** codes say something
about the subject, **executability** codes say whether a verdict could be
reached at all. They are named disjointly and counted separately, and a name
that reads like a member of the other set is a bug even when the code is
correct.

A test enforces this mechanically. It parses every non-test module in the tree
via AST — never raw text — and discovers the universe rather than trusting a
hand-maintained list:

```text
discovered tokens          395
declared merit codes        25
declared executability      50
```

The check is component-level containment in both directions, plus negation-pair
detection for `UN`/`NON`/`NOT` prefixes. Every hit is either a real collision or
a recorded judgement with a reason. There is no third state.

The interesting part is the names it has killed. In the last three slices alone:

| candidate | collided with | outcome |
| --- | --- | --- |
| `DECLARED_NOT_EXERCISED` | `LEDGER_GENERATION_UNDECLARED` | renamed `CONFIGURED_NOT_EXERCISED` |
| `ARTEFACT_DECLARED` | `LEDGER_GENERATION_UNDECLARED` | renamed `CARRIED_FROM_ARTEFACT` |
| `ARTEFACT_ATTESTED` | `LOCK_EXCLUSION_UNATTESTED` | rejected |
| `LINEAGE_ABSENT` | `ABSENT` (a coordinate kind) | renamed `NO_LINEAGE_NAMESPACE` |
| `LINEAGE_INSPECTION_FAILED` | `FAILED` | renamed `LINEAGE_INSPECTION_REFUSED` |

Each rejection is itself a test. `DECLARED_NOT_EXERCISED` *would have* collided,
and a test asserts it — so the reason survives after the prose explaining it has
been forgotten.

## Every Property Has A Mutation That Breaks It

A passing test suite proves that code runs. It does not prove the tests are
watching anything. SCYTHE's answer is a negative control per property: a
deliberate mutation that must make **exactly** that property's tests fail and no
others.

The last three slices added 36, on a set that now numbers 115. A representative
sample:

```text
status inferred from populated sample rate and gain    -> 4/4 broken
status inferred from device identity                   -> 4/4 broken
the observer writes the status into the record itself  -> 1/1 broken
a measurement value spliced into the observer's source -> 1/1 broken
the artefact opened twice, declaration and verdicts apart -> 1/1 broken
presence inferred from an empty digest map             -> 2/2 broken
an unreadable namespace reported as an empty lineage   -> 2/2 broken
a second answer serialized beside the enum             -> 2/2 broken
```

Each control also runs every *other* test in its slice, to catch a mutation
that breaks half the suite and therefore proves nothing about which property
the tests are watching.

Controls earn their keep by finding real weaknesses, and three did:

- the closed-set test read its constants at import and never saw the sets open;
- the AST test compared strings by equality, so a token spliced into a longer
  literal passed it;
- an undeclared-name control reached only the reader's copy of a set the
  producer binds separately.

All three were test bugs, found by the mutation rather than by review.

## Merge Is Not Acceptance

One process lesson is worth recording because it cost three repairs.

The governing discipline here is **proposal → acceptance → implementation**, each
a separate commit, merged before any code depends on it. Three times, an
amendment was merged while the document still described itself as `PROPOSED`.
Each was repaired by a forward acceptance commit rather than a rewritten merge:
treating the conversation as acceptance while the document said otherwise would
leave a contract contradicting itself, which is worse than a visible two-step.

The tempting generalization — *a merge instruction implies acceptance* — was
drafted, reviewed, and thrown out. It deletes a decision the contract exists to
require. A proposal may be merged precisely to preserve it as a proposal
*without* authorizing what it describes. What landed instead is a gate:

> A proposed amendment requires an explicit acceptance decision and an
> acceptance commit before implementation or execution. Merge approval alone
> does not supply acceptance unless it expressly says that the amendment's
> substance is accepted.

## Where It Stands

```text
Promotion Execution Contract    3,596 lines, amendments A-M
Test suite                      1,445 tests, OK (skipped=1)
Negative controls               115, all discriminating
Slices landed                   3, 4, 6, 6b, 7, 8, 9, 10a-10f
```

The directories for the bounded run are pinned, resolved against the host, and
created at mode `0700`:

```text
lineage root   /home/spectrcyde/scythe-ledger/promotion             absent
derived        /home/spectrcyde/scythe-live-observation/derived     empty
records        /home/spectrcyde/scythe-live-observation/records     empty
```

The namespace-refusal check was run against the real function, not by eye —
a detail worth stating, because the rule is wider than it looks. The lineage
root is a *filename prefix*, not a directory, so the forbidden namespace is its
parent. Placing the lineage root directly inside the observation tree refuses
**both** output directories, and that is the arrangement anyone tidying paths
would reach for first.

## The Last Blocker Is A Person

Everything mechanical is done. What remains is not mechanical.

The run needs twelve **operator-declared** fixes: real positions, observed
during a walk, read off a screen and typed in by a person, roughly fifteen
seconds apart, each stamped with the host's `monotonic_ns` at the instant the
runner accepts it. That is the host's *ingestion* time, not the receiver's fix
time, and the artefact says so — the two differ by however long the
transcription took, and no elapsed time may be computed across a phone's wall
clock and a host monotonic value.

The authority chain is recorded in three fields rather than one, because
collapsing them would lose the step where the authority actually degrades:

```text
position source       PHONE_DISPLAYED_FIX
transfer              HAND_TRANSCRIPTION
accepted authority    OPERATOR_DECLARED
```

The phone may hold a genuine GNSS fix. What reaches the artefact is what a
person read and retyped. **A fix read off a screen and typed in is a typed
fix**, and it is not elevated to device-attested GNSS merely because a phone
displayed it.

So the run waits. Not on a missing feature, and not on a device — on somebody
walking. The code will not interpolate the gap, will not accept a batch of
coordinates entered at once (that would stamp them nearly simultaneously and
falsely represent the cadence), and will not substitute constructed evidence if
a fix is unavailable. A run short of fixes ends on its bounds with what it has,
or does not run.

That last prohibition is the one that would be tempting at the moment it
mattered, which is exactly why it is written down.

---

*The Promotion Execution Contract lives at `docs/PROMOTION_EXECUTION_CONTRACT.md`.
The vocabulary rules it conforms to are in `SCYTHE_VERDICT_VOCABULARIES.md`.
Nothing described here has promoted anything to GraphOps: promotion authority
remains a separate explicit act, and the ARMED path stays blocked until a
completed observation record is reviewed.*
