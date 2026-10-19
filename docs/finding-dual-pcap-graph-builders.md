# Finding: PCAP ingest has two graph builders with divergent identity schemes

Status: observed, no code changed. Decision required.
Method: offline harness, real `HypergraphEngine` + real `WriteBus` (`strict_no_bypass=True`),
fresh state per run, capture `eth0_60s.pcapng` (431,096 bytes, 1,811 IP packets, sha256 `c5cbe0bf...`).
One capture, one run per path. The registry's `_resolve_pcap_path` was stubbed to return the file
(the real one needs a live operator room snapshot); parsing and emission code ran unmodified.

## 1. Two builders, not one builder with several transports

| | `PcapIngestPipeline.ingest_file` | `PcapRegistry.ingest_pcap_session` |
|---|---|---|
| Reached from | `/api/ingest/ftp`, `/api/pcap/batch_ingest`, CLI, tak-gpt shortcut, `docs/pcapng_to_hypergraph_bundle.py` | browser upload: `/api/pcap/upload` then `/api/pcap/<id>/ingest` |
| Parser | `decode_pcap` | its own scapy loop (dpkt, then simulation, as fallbacks) |
| Unit | session (5-tuple + 30 s bucket, `min_session_packets=2`) | flow aggregated over the whole file |
| Result on capture | 185 nodes / 418 edges; 37 sessions, 34 hosts, 37 flows | 345 nodes / 521 edges; 167 flows, 73 hosts, 102 port hubs |
| Adds | PCAP artifact node, ASN/org, geo, protocol events, behavior groups, ledger, `mission_id`, evidence manifest | activity nodes, port hubs |
| Host id | `host:<ip>` (`pcap_ingest._host_id`) | `host:ip:<ip>` (`graph_ids.host_id`) |

Only 1 node id is shared between the two graphs; host ids share none.
Part of the host/flow gap is the `min_session_packets=2` filter (193 sessions / 73 hosts at 1; 37 / 34 at 2).
The session model, enrichment and id scheme differ regardless.

`mcp_registry._ingest_pcap` calls `engine.ingest_pcap`, which is not defined anywhere in the repo.

## 2. The registry graph is a product surface, not dead code

- `command-ops-visualization.html` reads `ingestData.result.host_count` and `result.geo_points`
  from the registry response to create recon entities and fly the camera.
- The UI styles and sizes nodes by `kind`, including `port_hub`, `service`, `pcap_activity`:
  port hubs are an intended view.
- Server code accepts both `session` and `pcap_session` kinds.
- Nothing in the repo tests `PcapRegistry`.

## 3. Two identity domains

`graph_ids.py` states the goal: deterministic ids so re-ingest upserts instead of duplicating.
The registry, `infer_rules_v0_1.py`, `graphop_to_rdf.py` and `owlrl_materializer.py` use it.
`pcap_ingest.py` does not import it and defines its own `host:<ip>`.

## 4. Measured effect on inference (`InferenceEngine.run_all`, same capture)

| Graph | Inferred edges | Kinds | Dangling endpoints |
|---|---|---|---|
| pipeline | 39 | 27 `INFERRED_HOST_IN_ORG`, 12 `INFERRED_FLOW_IN_SERVICE` | 0 |
| registry | 8 | 7 `INFERRED_FLOW_IN_SERVICE`, 1 `INFERRED_HOST_OFFERS_SERVICE` | 0 |

- No duplicate host entities and no dangling edges were produced. The failure mode here is **silent
  skipping**, not fragmentation: rules that look hosts up through `graph_ids.host_id` find nothing in a
  pipeline graph. `_rule_svc_002` ends in `if not self.idx.node(host_nid): continue`, so the pipeline graph
  gets no `INFERRED_HOST_OFFERS_SERVICE` (the registry graph gets one).
- The rules keyed on edges already in the graph (`R-ORG-001`, `R-SVC-001`) work on both.
- **Not covered:** the TLS/HTTP/DNS/GEO/CORR rules did not fire on either graph (this capture produced no
  `FLOW_TLS_SNI` / `FLOW_HTTP_HOST` / `FLOW_QUERIED_DNS` edges that they consume). They also resolve hosts
  by `graph_ids.host_id`, so the same skipping is expected but unverified. The RDF/OWL materializers were not run.

## 5. Decisions (independent; do not bundle)

- **A. Product model:** one graph model, or two intentional projections (interactive preview graph vs.
  evidence graph). The UI contract in section 2 argues against simply deleting the registry path.
- **B. Identity:** one id function for host/session/artifact across both builders. Changing the pipeline's
  `host:<ip>` to `host:ip:<ip>` is a graph identity migration (stored graphs, inference, RDF/OWL, cached UI
  state), not a refactor; it needs its own issue/PR series.
- **C. Manifest ownership:** the component that performs the ingest emits the manifest. Today that is only
  the pipeline; `handle_mcp_pcap_ingest` also does not pass `manifest_dir`.

## 6. Open items

- [ ] Document registry vs pipeline graph semantics
- [ ] Define preview graph vs evidence graph relationship
- [ ] Audit graph identity generation across `graph_ids.py`, `pcap_ingest.py`, `pcap_registry.py`
- [ ] Re-run the inference experiment on a capture that contains TLS SNI, HTTP Host and DNS
- [ ] Run the RDF/OWL materializers on pipeline output
- [ ] Decide canonical identity migration strategy
