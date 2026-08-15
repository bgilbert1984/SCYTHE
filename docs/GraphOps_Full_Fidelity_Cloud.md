# Operation Full-Fidelity Evidence Capsule

GraphOps can send exact, bounded operational evidence to Ollama Cloud when an operator explicitly chooses **ASK CLOUD // FULL FIDELITY**. The normal **ASK OLLAMA** route remains local and unchanged.

## Evidence path

```text
selected live-graph host
  → server-resolved immutable graph revision
  → bounded RTT and traceroute measurement
  → retained server-owned trace evidence ID
  → operator disclosure acknowledgement
  → full-fidelity evidence capsule
  → https://ollama.com/api/chat
  → schema-validated interpretive report
  → disclosure receipt in the GraphOps panel
```

The browser submits only the question, pinned selection reference, trace evidence ID, and acknowledgement. It cannot supply or modify the capsule evidence.

## Included exactly

- Selected host IP and graph identity
- Traceroute hop IPs, RTT values, coordinates, GeoIP records, anomalies, and tool status
- Exact observation and graph timestamps
- Selected entity labels and enrichment
- Up to 24 incident edges and 24 hyperedge members
- Evidence classes and measurement boundaries

Exact geography remains `INFERRED`; exact coordinates do not convert GeoIP into physical device location. Graph adjacency remains non-causal.

## Always excluded

- Credentials, authorization headers, cookies, and process environment
- Secret-bearing fields and recognizable credential values
- Raw packet payloads
- Engine-internal metadata
- Unrelated files and unbounded graph state
- Directive execution authority

The Cloud model is interpretive only. It cannot run probes, mutate the graph, execute directives, or promote an inference into an observation.

## Deterministic epistemic validation

Cloud prose is validated after generation. This layer does not rely on prompt compliance alone:

- Interface GeoIP cannot be promoted into a physical route itinerary.
- Differential RTT between independent ICMP hop responses is not segment propagation time.
- Single-trace timing cannot establish congestion, load balancing, or a route change.
- Uncorroborated GeoIP caps confidence at `0.60`; a derived physics warning caps it at `0.50`.
- Unsupported physical-route claims and timing-cause attributions are replaced and capped at `0.25` or `0.35`.
- RTT magnitude cannot be promoted into local/nearby/short-haul/long-haul distance claims.
- A traceroute's last responding TTL cannot be promoted into a claim that the packet path ended there.
- Failure to observe a VPN, relay, tunnel, distant leg, or route cannot be promoted into evidence of absence.
- A non-actionable direction such as `analysis` is replaced by a concrete repeated-measurement instruction.

Traceroute timing and physics flags are labelled `DERIVED_INFERENCE`. Physics flags are mapped back to the original TTL-bearing hop after processing the compact geolocated subset; missing GeoIP hops therefore cannot shift an anomaly onto another router.

## Investigation workspace

Each selected hypergraph entity opens or reactivates a bounded GraphOps tab. Its question, trace evidence, output, and status remain associated with that entity while other investigations proceed. Twelve tabs are retained per page session. The dialog closes only through its explicit close control or Escape—not by clicking elsewhere—and has an independently persisted keyboard/pointer resize handle.

## Retention and binding

Host-trace evidence is retained in orchestrator memory for 30 minutes and is bound to its exact entity ID and graph revision. A stale, missing, expired, or mismatched evidence reference is refused. Restarting the orchestrator clears retained capsules and requires a new trace.

## Receipt

Every successful response reports the capsule ID, capsule SHA-256, destination, model, exact-IP/location counts, graph scope, infrastructure/control-plane counts, unresolved evidence tensions, observed changes, withheld tests, exclusions, route, and authority boundary. The capsule itself and Cloud credential are not returned to the browser.

## Configuration

```ini
Environment=OLLAMA_API_KEY_FILE=/home/spectrcyde/SCYTHE/.ollama
Environment=OLLAMA_CLOUD_MODEL=gpt-oss:20b
```

The API credential is sent only to the fixed HTTPS origin `https://ollama.com`. Do not place the credential in browser JavaScript, request bodies, command-line arguments, or logs.
# Infrastructure evidence and compatibility

Full-Fidelity capsules may include a bounded `graphops.infrastructure.v1` snapshot. Observed traffic, inferred ASN/GeoIP, modeled AS-path candidates, and display-only geometry retain separate authority labels. Disclosure receipts count each infrastructure class.

Capsules also carry `evidenceCompatibility`. Full fidelity means exact disclosed evidence, not universally sufficient evidence. When a question requires unavailable temporal freshness, sensor-negative-evidence conditions, quantization provenance, interpolation provenance, or infrastructure evidence, deterministic validation refuses the unsupported conclusion and identifies the observations required before re-querying. See [GraphOps_InfraFlow.md](GraphOps_InfraFlow.md).

PeeringDB declarations and RIS Live messages are disclosed as separate evidence layers. PeeringDB remains self-reported; RIS remains a timestamped collector-vantage control-plane observation. Neither is promoted into the measured traceroute or used to assert a physical/data-plane route. Questions naming BGP/RIS, PeeringDB/IX/facilities, or CAIDA relationships require their corresponding evidence class; unavailable layers produce an explicit question-evidence refusal.

Persisted, time-windowed `infrastructureContradictions` may also be disclosed. They are deterministic unresolved source disagreements and tensions, not model-generated verdicts. The Cloud system contract requires the model to preserve alternatives, falsifiers, window and source revisions, and withheld tests. It explicitly forbids translating origin disagreement into hijacking or a collector withdrawal into global unreachability.

## Selection-focused capsule projection

The live infrastructure snapshot can be substantially larger than a Cloud model's context window. Full-Fidelity Cloud therefore does not serialize the entire unrelated environment. Before disclosure, the server deterministically projects exact records around the selected host's network domain and its observed domain flows:

- at most 16 relevant domains and 32 observed flows;
- at most 16 PeeringDB networks, 32 IX memberships, 32 facility presences, and their 32 referenced facilities/exchanges;
- at most 32 selection-relevant RIS observations;
- at most 32 relevant contradiction findings and control-plane changes.

Included records retain their exact values and evidence classes. `capsuleProjection` reports source, included, and omitted counts and binds the projection to the sanitized complete source snapshot with SHA-256. Omitted records are neither summarized nor used for inference. The disclosure receipt displays included versus omitted/hash-bound record totals.

There is deliberately no global fallback when the selected host has no prefix- or origin-relevant RIS observation. The focused capsule reports zero control-plane observations and omits unrelated collector records. Evidence compatibility is evaluated after projection, so a question requiring BGP/RIS evidence is refused when no relevant record was actually disclosed.

Provider HTTP errors are classified without returning provider bodies or capsule data. In particular, context exhaustion is reported as `request exceeded the model context window` instead of an opaque HTTP 400.
