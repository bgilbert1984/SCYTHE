#!/usr/bin/env python3
"""Convert a local PCAPNG file into a SCYTHE Hypergraph Bundle + standalone HTML viewer.

Default target:
    /workspaces/codespaces-blank/ftp_server/pcapng/06252026_207_pm_PST_MintWAN.pcapng

Usage:
    python3 /workspaces/codespaces-blank/ftp_server/pcapng_to_hypergraph_bundle.py \
      /workspaces/codespaces-blank/ftp_server/pcapng/06252026_207_pm_PST_MintWAN.pcapng

Outputs, in the PCAPNG directory by default:
    <pcap_stem>.hypergraph.json   # canonical engine snapshot / bundle payload
    <pcap_stem>.hypergraph.html   # self-contained offline viewer
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import logging
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

# The script normally lives in /workspaces/codespaces-blank/ftp_server/.
# Make repository imports work even when called from ftp_server/pcapng or cron/systemd.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hypergraph_engine import HypergraphEngine  # noqa: E402
from inference_exhaustion_ledger import InferenceExhaustionLedger  # noqa: E402
from pcap_ingest import IngestConfig, PcapIngestPipeline  # noqa: E402

DEFAULT_PCAP = Path("/workspaces/codespaces-blank/ftp_server/pcapng/06252026_207_pm_PST_MintWAN.pcapng")

KIND_COLORS = {
    "pcap_session": "#4a9eff",
    "pcap_artifact": "#e74c3c",
    "pcap_activity": "#f39c12",
    "session": "#4a9eff",
    "protocol_event": "#f39c12",
    "host": "#2ecc71",
    "geo_point": "#9b59b6",
    "flow": "#1abc9c",
    "flow_aggregate": "#1abc9c",
    "port_hub": "#e67e22",
    "service": "#00cec9",
    "asn": "#fd79a8",
    "org": "#636e72",
    "dns_name": "#a29bfe",
    "tls_sni": "#6c5ce7",
    "tls_cert": "#d63031",
    "http_host": "#00b894",
    "ja3": "#fdcb6e",
    "ja3s": "#fab1a0",
    "behavior_group": "#e67e22",
    "SESSION_HAS_ARTIFACT": "#ff6b6b",
    "SESSION_OBSERVED_HOST": "#4ecdc4",
    "HOST_GEO_ESTIMATE": "#a29bfe",
    "SESSION_ACTIVITY": "#ffeaa7",
    "SESSION_OBSERVED_FLOW": "#74b9ff",
    "FLOW_SRC": "#fd79a8",
    "FLOW_DST": "#e17055",
    "flow_observed": "#00cec9",
    "FLOW_DST_PORT": "#e67e22",
    "HOST_OFFERS_PORT": "#e67e22",
    "PORT_IMPLIED_SERVICE": "#00cec9",
    "HOST_IN_ASN": "#fd79a8",
    "ASN_IN_ORG": "#636e72",
    "FLOW_SNI": "#6c5ce7",
    "FLOW_DNS": "#a29bfe",
    "FLOW_HTTP_HOST": "#00b894",
    "SESSION_DERIVED_FROM_PCAP": "#e74c3c",
    "SESSION_CONTAINS_HOST": "#2ecc71",
    "SESSION_HAS_PROTOCOL_EVENT": "#f39c12",
    "HOST_COMMUNICATED_WITH": "#74b9ff",
    "SESSION_HAD_FLOW": "#1abc9c",
    "FLOW_QUERIED_DNS": "#a29bfe",
    "FLOW_TLS_SNI": "#6c5ce7",
    "FLOW_FROM_HOST": "#fd79a8",
    "FLOW_TO_HOST": "#e17055",
    "SESSION_BETWEEN_HOSTS": "#74b9ff",
    "SESSION_CONTAINS_EVENT": "#f39c12",
    "SESSION_MEMBER_OF_BEHAVIOR_GROUP": "#e67e22",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pcapng_to_hypergraph_bundle")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_path, path)
    logger.info("Wrote: %s", path)


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
    logger.info("Wrote: %s", path)


def sanitize_for_json(value: Any, *, _seen: set[int] | None = None, _depth: int = 0) -> Any:
    """Return a JSON-serializable structure without exploding on odd metadata objects.

    Hypergraph snapshots usually already serialize cleanly. This guard catches sets,
    Path objects, dataclasses-ish objects, and accidental circular references.
    """
    if _seen is None:
        _seen = set()
    if _depth > 20:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    obj_id = id(value)
    if obj_id in _seen:
        return "__circular_ref__"
    if isinstance(value, dict):
        _seen.add(obj_id)
        out = {str(k): sanitize_for_json(v, _seen=_seen, _depth=_depth + 1) for k, v in value.items()}
        _seen.discard(obj_id)
        return out
    if isinstance(value, (list, tuple, set)):
        _seen.add(obj_id)
        out = [sanitize_for_json(v, _seen=_seen, _depth=_depth + 1) for v in value]
        _seen.discard(obj_id)
        return out
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def values_from_snapshot_collection(obj: Any) -> list[dict[str, Any]]:
    """Normalize engine snapshot node/edge containers to a list of dicts."""
    if obj is None:
        return []
    if isinstance(obj, dict):
        vals = []
        for key, val in obj.items():
            if isinstance(val, dict):
                if "id" not in val:
                    val = {"id": key, **val}
                vals.append(val)
            else:
                vals.append({"id": key, "value": sanitize_for_json(val)})
        return vals
    if isinstance(obj, list):
        return [x if isinstance(x, dict) else {"value": sanitize_for_json(x)} for x in obj]
    return []


def first_present(*vals: Any) -> Any:
    for val in vals:
        if val is not None and val != "":
            return val
    return None


def stable_position(node_id: str, kind: str, index: int, total: int) -> dict[str, float]:
    """Deterministic starting position for the browser force layout."""
    digest = hashlib.sha1(f"{kind}:{node_id}".encode("utf-8", "ignore")).digest()
    seed = int.from_bytes(digest[:8], "big")
    angle = (seed / float(2**64)) * math.tau
    kind_band = {
        "pcap_artifact": 0.15,
        "pcap_session": 0.24,
        "session": 0.30,
        "host": 0.55,
        "flow": 0.72,
        "protocol_event": 0.82,
        "dns_name": 0.92,
        "tls_sni": 0.96,
        "port_hub": 0.68,
        "service": 0.62,
        "asn": 1.05,
        "org": 1.12,
        "geo_point": 1.18,
        "behavior_group": 1.25,
    }.get(kind, 1.0)
    jitter = ((seed >> 16) & 0xFFFF) / 0xFFFF
    radius = (90 + math.sqrt(max(total, 1)) * 7) * kind_band + jitter * 25
    return {
        "x": round(math.cos(angle) * radius, 4),
        "y": round(math.sin(angle) * radius, 4),
    }


def normalize_node(raw: dict[str, Any], index: int, total: int) -> dict[str, Any]:
    node_id = str(first_present(raw.get("id"), raw.get("node_id"), raw.get("key"), f"node:{index}"))
    kind = str(first_present(raw.get("kind"), raw.get("type"), raw.get("node_type"), "node"))
    labels = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    label = first_present(
        labels.get("ip"),
        labels.get("name"),
        labels.get("hostname"),
        labels.get("domain"),
        labels.get("port"),
        raw.get("label"),
        metadata.get("ip"),
        metadata.get("hostname"),
        node_id,
    )
    out = dict(raw)
    out["id"] = node_id
    out["kind"] = kind
    out["labels"] = {**labels, "display": str(label)}
    out.setdefault("metadata", metadata)
    out.setdefault("__pos", stable_position(node_id, kind, index, total))
    return sanitize_for_json(out)


def edge_nodes(raw: dict[str, Any]) -> list[str]:
    candidates = raw.get("nodes") or raw.get("members") or raw.get("endpoints")
    if isinstance(candidates, list):
        nodes: list[str] = []
        for item in candidates:
            if isinstance(item, dict):
                nid = first_present(item.get("id"), item.get("node"), item.get("node_id"))
            else:
                nid = item
            if nid is not None:
                nodes.append(str(nid))
        if nodes:
            return nodes
    src = first_present(raw.get("src"), raw.get("source"), raw.get("from"), raw.get("u"))
    dst = first_present(raw.get("dst"), raw.get("target"), raw.get("to"), raw.get("v"))
    nodes = []
    for item in (src, dst):
        if isinstance(item, dict):
            item = first_present(item.get("id"), item.get("node"), item.get("node_id"))
        if item is not None:
            nodes.append(str(item))
    return nodes


def normalize_edge(raw: dict[str, Any], index: int) -> dict[str, Any]:
    nodes = edge_nodes(raw)
    kind = str(first_present(raw.get("kind"), raw.get("type"), raw.get("edge_type"), "edge"))
    edge_id = str(first_present(raw.get("id"), raw.get("edge_id"), f"edge:{index}:{':'.join(nodes[:3])}"))
    out = dict(raw)
    out["id"] = edge_id
    out["kind"] = kind
    out["nodes"] = nodes
    out.setdefault("labels", raw.get("labels") if isinstance(raw.get("labels"), dict) else {})
    out.setdefault("metadata", raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {})
    out.setdefault("weight", first_present(raw.get("weight"), raw.get("score"), 1))
    return sanitize_for_json(out)


def pick_session_id(nodes: Iterable[dict[str, Any]], pcap_path: Path) -> str:
    for n in nodes:
        nid = str(n.get("id", ""))
        if nid.startswith("SESSION-"):
            return nid
    digest = hashlib.sha1(str(pcap_path).encode("utf-8", "ignore")).hexdigest()[:16]
    return f"PCAP-{digest}"


def make_viewer_payload(snapshot: dict[str, Any], pcap_path: Path, result: Any) -> dict[str, Any]:
    raw_nodes = values_from_snapshot_collection(snapshot.get("nodes"))
    raw_edges = values_from_snapshot_collection(snapshot.get("edges"))

    nodes = [normalize_node(n, i, len(raw_nodes)) for i, n in enumerate(raw_nodes)]
    node_ids = {str(n.get("id")) for n in nodes}
    edges = [normalize_edge(e, i) for i, e in enumerate(raw_edges)]
    edges = [e for e in edges if len(e.get("nodes") or []) >= 2 and all(n in node_ids for n in e.get("nodes")[:2])]

    kind_counts = Counter(str(n.get("kind", "node")) for n in nodes)
    edge_kind_counts = Counter(str(e.get("kind", "edge")) for e in edges)
    session_id = pick_session_id(nodes, pcap_path)
    exported_at = _dt.datetime.now(_dt.UTC).isoformat().replace("+00:00", "Z")

    stats = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "session_count": sum(1 for n in nodes if str(n.get("id", "")).startswith("SESSION-") or n.get("kind") in {"session", "pcap_session"}),
        "behavior_group_count": sum(1 for n in nodes if n.get("kind") == "behavior_group" or str(n.get("id", "")).startswith("BSG-")),
        "sessions_created": getattr(result, "sessions_created", None),
        "nodes_emitted": getattr(result, "nodes_emitted", None),
        "edges_emitted": getattr(result, "edges_emitted", None),
        "kind_counts": dict(kind_counts.most_common()),
        "edge_kind_counts": dict(edge_kind_counts.most_common()),
    }

    return {
        "schema": "scythe.hypergraph.bundle.viewer.v1",
        "sessionId": session_id,
        "title": f"PCAP Hypergraph: {pcap_path.name}",
        "pcap": {
            "path": str(pcap_path),
            "file": pcap_path.name,
            "stem": pcap_path.stem,
            "sha256": sha256_file(pcap_path),
        },
        "exported_at": exported_at,
        "nodes": nodes,
        "edges": edges,
        "kindColors": KIND_COLORS,
        "stats": stats,
        "ingest_result": {
            "ok": getattr(result, "ok", None),
            "sessions_created": getattr(result, "sessions_created", None),
            "nodes_emitted": getattr(result, "nodes_emitted", None),
            "edges_emitted": getattr(result, "edges_emitted", None),
            "errors": getattr(result, "errors", []),
        },
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_script_json(payload: Any) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("</script", "<\\/script")
        .replace("<!--", "<\\!--")
    )


def build_html_document(payload: dict[str, Any]) -> str:
    title = html.escape(str(payload.get("title") or "SCYTHE Hypergraph Bundle"))
    badge = html.escape(str(payload.get("sessionId") or "PCAP"))[:48]
    exported = html.escape(str(payload.get("exported_at") or ""))
    data_json = safe_script_json(payload)

    template = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%}
body{background:#080810;color:#ccc;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;display:flex;flex-direction:column;overflow:hidden}
#hdr{display:flex;align-items:center;gap:12px;padding:8px 14px;background:rgba(5,5,20,.96);border-bottom:1px solid #1a2a4a;flex-shrink:0;flex-wrap:wrap}
#hdr h1{font-size:13px;color:#4a9eff;font-weight:700;white-space:nowrap}.badge{font-size:10px;color:#4a7;background:rgba(0,60,30,.40);padding:2px 6px;border-radius:3px;border:1px solid #1a4a2a;font-weight:700}.meta{font-size:10px;color:#666}.stats{font-size:11px;color:#aaa;margin-left:auto}.ctrl{font-size:9px;color:#4a7;display:flex;align-items:center;gap:4px;white-space:nowrap}.ctrl input{background:#0a1020;border:1px solid #1a4a2a;color:#4af;font-size:10px;font-family:inherit;border-radius:2px;padding:2px 4px}.ctrl input[type=number]{width:44px}.btn{cursor:pointer;background:#0a1328;border:1px solid #1a4a6a;color:#8bd7ff;border-radius:3px;padding:3px 7px;font-size:10px;font-family:inherit}.btn:hover{background:#102044;color:#fff}
#main{display:flex;flex:1;overflow:hidden}#graph-wrap{flex:1;position:relative;overflow:hidden;background:radial-gradient(circle at 50% 45%,#101632 0,#080810 62%,#050509 100%)}#graph{width:100%;height:100%;display:block}.watermark{position:absolute;left:12px;bottom:10px;color:#344;font-size:10px;pointer-events:none}.hint{position:absolute;left:12px;top:12px;color:#6b7c96;font-size:10px;pointer-events:none;background:rgba(3,5,15,.46);border:1px solid rgba(74,158,255,.12);border-radius:4px;padding:5px 7px}
#sidebar{width:360px;background:rgba(5,5,20,.96);border-left:1px solid #1a2a4a;display:flex;flex-direction:column;overflow:hidden}.tabs{display:flex;border-bottom:1px solid #1a2a4a;flex-shrink:0}.tab{padding:7px 14px;font-size:10px;cursor:pointer;color:#666;border-bottom:2px solid transparent}.tab.active{color:#4a9eff;border-bottom-color:#4a9eff}.panel{flex:1;overflow:auto}.search{padding:8px;border-bottom:1px solid #121c33}.search input{width:100%;background:#090d1b;border:1px solid #22375d;color:#d7ecff;font:11px inherit;border-radius:4px;padding:7px}table{width:100%;border-collapse:collapse;font-size:10px}th{position:sticky;top:0;background:#0d0d22;color:#68758d;padding:5px 8px;text-align:left;z-index:1}td{padding:4px 8px;border-bottom:1px solid #111827;vertical-align:top}tr{cursor:pointer}tr:hover td{background:rgba(74,158,255,.07)}tr.selected td{background:rgba(74,158,255,.16)!important}.idcell{color:#888;font-size:10px;max-width:175px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kindpill{font-weight:700}.labelcell{max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#d6e8ff}
#sel-panel{display:none;position:fixed;top:56px;right:374px;width:300px;max-height:72vh;overflow:auto;background:rgba(5,5,25,.97);border:1px solid #1a4a6a;border-radius:6px;padding:10px;font-size:11px;z-index:9;box-shadow:0 4px 22px rgba(0,0,0,.65)}#sel-panel h2{font-size:12px;color:#4a9eff;margin-bottom:7px;padding-right:16px}#sel-close{float:right;cursor:pointer;color:#777;font-size:14px;line-height:1}.row{display:flex;justify-content:space-between;gap:8px;padding:3px 0;border-bottom:1px solid #1a2a3a}.row:last-child{border-bottom:none}.key{color:#8a94aa}.val{color:#fff;text-align:right;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#offline-note{padding:6px 14px;font-size:9px;color:#46556a;background:#090912;border-top:1px solid #111;flex-shrink:0}.legend{display:flex;flex-wrap:wrap;gap:5px;padding:7px 8px;border-bottom:1px solid #111827}.leg{font-size:9px;color:#aab;padding:2px 5px;border:1px solid #1a2a4a;border-radius:3px;background:#090d1b}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}.empty{padding:18px;color:#666;text-align:center}
</style>
</head>
<body>
<div id="hdr">
  <h1>🕸 SCYTHE Hypergraph Bundle</h1>
  <span class="badge">__BADGE__</span>
  <span class="meta">exported __EXPORTED__</span>
  <span class="stats" id="hdr-stats">Loading…</span>
  <label class="ctrl">Hops <input id="cfg-hop" type="number" min="1" max="4" value="1"></label>
  <label class="ctrl">Labels <input id="cfg-max" type="number" min="1" max="100" value="24"></label>
  <button class="btn" id="fit-btn">Fit</button>
  <button class="btn" id="json-btn">JSON</button>
</div>
<div id="sel-panel"><span id="sel-close" title="Close">✕</span><h2 id="sel-kind">—</h2><div id="sel-rows"></div></div>
<div id="main">
  <div id="graph-wrap">
    <canvas id="graph"></canvas>
    <div class="hint">drag nodes · wheel zoom · click selects · hyperedges draw as amber centroid spokes</div>
    <div class="watermark">fully self-contained canvas renderer — no CDN, no server, no excuses</div>
  </div>
  <div id="sidebar">
    <div class="tabs"><div class="tab active" id="tab-nodes">Nodes</div><div class="tab" id="tab-edges">Edges</div><div class="tab" id="tab-kinds">Kinds</div></div>
    <div class="search"><input id="q" placeholder="filter nodes / edges / labels"></div>
    <div class="legend" id="legend"></div>
    <div class="panel" id="nodes-panel"><table><thead><tr><th>Kind</th><th>Label</th><th>ID</th></tr></thead><tbody id="nodes-body"></tbody></table></div>
    <div class="panel" id="edges-panel" style="display:none"><table><thead><tr><th>Kind</th><th>Nodes</th><th>ID</th></tr></thead><tbody id="edges-body"></tbody></table></div>
    <div class="panel" id="kinds-panel" style="display:none"><table><thead><tr><th>Kind</th><th>Count</th><th>Color</th></tr></thead><tbody id="kinds-body"></tbody></table></div>
  </div>
</div>
<div id="offline-note">🟢 Embedded graph payload in <code>#g-data</code>. Open this file directly from the PCAPNG directory or ship it as evidence collateral.</div>
<script type="application/json" id="g-data">__DATA_JSON__</script>
<script>
(function(){
'use strict';
const GDATA = JSON.parse(document.getElementById('g-data').textContent);
const colors = GDATA.kindColors || {};
const nodes = (GDATA.nodes || []).map((n,i)=>({ ...n, _i:i, x:(n.__pos&&n.__pos.x)||Math.cos(i)*80, y:(n.__pos&&n.__pos.y)||Math.sin(i)*80, vx:0, vy:0, fixed:false }));
const byId = Object.fromEntries(nodes.map(n=>[n.id,n]));
const edges = (GDATA.edges || []).filter(e=>(e.nodes||[]).length>=2);
const links = edges.map(e=>({ ...e, refs:(e.nodes||[]).map(id=>byId[id]).filter(Boolean) })).filter(e=>e.refs.length>=2);
const adj = new Map(); nodes.forEach(n=>adj.set(n.id,new Set())); links.forEach(e=>{ for(let i=0;i<e.refs.length;i++) for(let j=i+1;j<e.refs.length;j++){ adj.get(e.refs[i].id)?.add(e.refs[j].id); adj.get(e.refs[j].id)?.add(e.refs[i].id); }});
let selected=null, hover=null, drag=null, pan={x:0,y:0}, scale=1, running=true, tick=0;
const canvas=document.getElementById('graph'), ctx=canvas.getContext('2d');
const q=document.getElementById('q');
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function label(n){const l=n.labels||{};return l.display||l.ip||l.name||l.hostname||l.domain||l.port||n.label||n.id;}
function col(k){return colors[k]||'#7f8fa6';}
function resize(){const r=canvas.parentElement.getBoundingClientRect();canvas.width=Math.max(300,Math.floor(r.width*devicePixelRatio));canvas.height=Math.max(220,Math.floor(r.height*devicePixelRatio));canvas.style.width=r.width+'px';canvas.style.height=r.height+'px';ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);fit(false);} window.addEventListener('resize',resize);
function worldToScreen(n){return {x:canvas.clientWidth/2+(n.x+pan.x)*scale,y:canvas.clientHeight/2+(n.y+pan.y)*scale};}
function screenToWorld(x,y){return {x:(x-canvas.clientWidth/2)/scale-pan.x,y:(y-canvas.clientHeight/2)/scale-pan.y};}
function radius(n){return ({pcap_artifact:8,pcap_session:7,session:6,host:5.5,flow:3.3,protocol_event:4,behavior_group:7,geo_point:5,port_hub:4.8,service:4.5,dns_name:4,tls_sni:4,asn:4,org:4}[n.kind]||3.5);}
function hopDistances(id, maxHop){const d={}; if(!id) return d; d[id]=0; let frontier=[id]; for(let h=1;h<=maxHop;h++){const next=[]; frontier.forEach(v=>(adj.get(v)||[]).forEach(nb=>{if(d[nb]===undefined){d[nb]=h;next.push(nb);}})); frontier=next;} return d;}
function physics(){ if(!running || drag) return; tick++; const n=nodes.length; const charge=Math.min(2600,500+Math.sqrt(n)*90); for(let i=0;i<n;i++){ for(let j=i+1;j<n;j++){ const a=nodes[i],b=nodes[j]; let dx=b.x-a.x,dy=b.y-a.y,dd=dx*dx+dy*dy+0.01; if(dd>90000) continue; let f=charge/dd; let inv=1/Math.sqrt(dd); dx*=inv;dy*=inv; a.vx-=dx*f; a.vy-=dy*f; b.vx+=dx*f; b.vy+=dy*f; }} links.forEach(e=>{ for(let i=1;i<e.refs.length;i++){ const a=e.refs[0], b=e.refs[i]; let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1; let target=e.refs.length>2?72:46; let f=(d-target)*0.006*(Math.min(4,e.weight||1)); dx/=d;dy/=d; a.vx+=dx*f; a.vy+=dy*f; b.vx-=dx*f; b.vy-=dy*f; }}); nodes.forEach(n=>{ n.vx+=(-n.x)*0.0009; n.vy+=(-n.y)*0.0009; n.vx*=0.86; n.vy*=0.86; if(!n.fixed){n.x+=n.vx;n.y+=n.vy;} }); }
function draw(){physics(); ctx.clearRect(0,0,canvas.clientWidth,canvas.clientHeight); const hop=parseInt(document.getElementById('cfg-hop').value||'1',10); const maxLabels=parseInt(document.getElementById('cfg-max').value||'24',10); const dist=hopDistances(selected&&selected.id, hop); ctx.save();
  links.forEach(e=>{ const refs=e.refs; if(refs.length===2){const a=worldToScreen(refs[0]),b=worldToScreen(refs[1]); const near=selected? (dist[refs[0].id]!==undefined||dist[refs[1].id]!==undefined):true; ctx.globalAlpha=near?0.42:0.08; ctx.strokeStyle=col(e.kind); ctx.lineWidth=Math.max(0.6, Math.min(2.8, (e.weight||1)*0.55))*scale; ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();} else {let cx=0,cy=0; refs.forEach(r=>{const s=worldToScreen(r);cx+=s.x;cy+=s.y;});cx/=refs.length;cy/=refs.length; const near=selected?refs.some(r=>dist[r.id]!==undefined):true; ctx.globalAlpha=near?0.32:0.06; ctx.strokeStyle=col(e.kind)||'#f39c12'; refs.forEach(r=>{const s=worldToScreen(r);ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(s.x,s.y);ctx.stroke();}); ctx.globalAlpha=near?0.48:0.08; ctx.fillStyle=col(e.kind); ctx.beginPath(); ctx.arc(cx,cy,3.5,0,Math.PI*2); ctx.fill(); }});
  ctx.globalAlpha=1; nodes.forEach(n=>{ const s=worldToScreen(n); const r=radius(n)*scale; const isSel=selected&&selected.id===n.id, isHover=hover&&hover.id===n.id; const reachable=!selected||dist[n.id]!==undefined; ctx.globalAlpha=reachable?1:0.18; ctx.fillStyle=col(n.kind); ctx.shadowColor=col(n.kind); ctx.shadowBlur=isSel?18:(isHover?10:4); ctx.beginPath(); ctx.arc(s.x,s.y,isSel?r*1.7:(isHover?r*1.35:r),0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0; if(isSel){ctx.lineWidth=2;ctx.strokeStyle='#fff';ctx.stroke();} });
  if(selected){ let count=0; ctx.font='10px ui-monospace,monospace'; Object.keys(dist).sort((a,b)=>dist[a]-dist[b]).forEach(id=>{ if(count>=maxLabels) return; const n=byId[id]; if(!n) return; const s=worldToScreen(n); ctx.globalAlpha=0.9; ctx.fillStyle='rgba(5,8,18,.72)'; const text=(dist[id]?('+'+dist[id]+' '):'')+String(label(n)).slice(0,42); const w=ctx.measureText(text).width+8; ctx.fillRect(s.x+8,s.y-16,w,15); ctx.strokeStyle=col(n.kind); ctx.strokeRect(s.x+8,s.y-16,w,15); ctx.fillStyle='#dbeafe'; ctx.fillText(text,s.x+12,s.y-5); count++; }); }
  ctx.restore(); requestAnimationFrame(draw); }
function hit(x,y){let best=null,bd=Infinity; nodes.forEach(n=>{const s=worldToScreen(n); const d=(s.x-x)**2+(s.y-y)**2; const rr=(radius(n)*scale+7)**2; if(d<rr&&d<bd){best=n;bd=d;}}); return best;}
function select(n){selected=n||null; document.querySelectorAll('tr[data-id]').forEach(r=>r.classList.toggle('selected', selected&&r.dataset.id===selected.id)); const p=document.getElementById('sel-panel'); if(!selected){p.style.display='none';return;} const kc=col(selected.kind); document.getElementById('sel-kind').innerHTML='<span style="color:'+kc+'">'+esc(selected.kind||'node')+'</span>'; const rows=[]; const labels=selected.labels||{}; Object.keys(labels).slice(0,18).forEach(k=>rows.push('<div class="row"><span class="key">'+esc(k)+'</span><span class="val" title="'+esc(labels[k])+'">'+esc(labels[k])+'</span></div>')); rows.push('<div class="row"><span class="key">id</span><span class="val" title="'+esc(selected.id)+'">'+esc(String(selected.id).slice(0,34))+'</span></div>'); rows.push('<div class="row"><span class="key">degree</span><span class="val">'+((adj.get(selected.id)||new Set()).size)+'</span></div>'); p.querySelector('#sel-rows').innerHTML=rows.join(''); p.style.display='block'; }
canvas.addEventListener('mousemove',ev=>{const r=canvas.getBoundingClientRect(); const x=ev.clientX-r.left,y=ev.clientY-r.top; if(drag){const w=screenToWorld(x,y); drag.x=w.x; drag.y=w.y; drag.vx=drag.vy=0; return;} hover=hit(x,y); canvas.style.cursor=hover?'pointer':'grab';});
canvas.addEventListener('mousedown',ev=>{const r=canvas.getBoundingClientRect(); const n=hit(ev.clientX-r.left,ev.clientY-r.top); if(n){drag=n; drag.fixed=true; select(n);} else {drag={pan:true,x:ev.clientX,y:ev.clientY,px:pan.x,py:pan.y};}});
window.addEventListener('mouseup',()=>{if(drag&&!drag.pan) drag.fixed=false; drag=null;});
window.addEventListener('mousemove',ev=>{if(drag&&drag.pan){pan.x=drag.px+(ev.clientX-drag.x)/scale; pan.y=drag.py+(ev.clientY-drag.y)/scale;}});
canvas.addEventListener('click',ev=>{const r=canvas.getBoundingClientRect(); select(hit(ev.clientX-r.left,ev.clientY-r.top));});
canvas.addEventListener('wheel',ev=>{ev.preventDefault(); const old=scale; scale*=ev.deltaY>0?0.9:1.1; scale=Math.max(.15,Math.min(6,scale)); const r=canvas.getBoundingClientRect(); const mx=ev.clientX-r.left,my=ev.clientY-r.top; pan.x+=(mx-canvas.clientWidth/2)*(1/scale-1/old); pan.y+=(my-canvas.clientHeight/2)*(1/scale-1/old);},{passive:false});
function fit(doCenter=true){ if(!nodes.length)return; const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y); const minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys); const w=Math.max(1,maxx-minx),h=Math.max(1,maxy-miny); scale=Math.max(.25,Math.min(2.5,Math.min(canvas.clientWidth/(w+100), canvas.clientHeight/(h+100)))); if(doCenter){pan.x=-(minx+maxx)/2;pan.y=-(miny+maxy)/2;} }
document.getElementById('fit-btn').onclick=()=>fit(true); document.getElementById('json-btn').onclick=()=>{const blob=new Blob([JSON.stringify(GDATA,null,2)],{type:'application/json'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=(GDATA.pcap?.stem||'scythe')+'.hypergraph.viewer.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);}; document.getElementById('sel-close').onclick=()=>select(null);
function switchTab(tab){['nodes','edges','kinds'].forEach(t=>{document.getElementById(t+'-panel').style.display=t===tab?'':'none';document.getElementById('tab-'+t).classList.toggle('active',t===tab);});} window.switchTab=switchTab; document.getElementById('tab-nodes').onclick=()=>switchTab('nodes'); document.getElementById('tab-edges').onclick=()=>switchTab('edges'); document.getElementById('tab-kinds').onclick=()=>switchTab('kinds');
function renderTables(){const term=(q.value||'').toLowerCase(); const nb=document.getElementById('nodes-body'), eb=document.getElementById('edges-body'), kb=document.getElementById('kinds-body'); const kindCounts={}; nodes.forEach(n=>kindCounts[n.kind]=(kindCounts[n.kind]||0)+1); const nodeRows=nodes.filter(n=>(n.id+' '+n.kind+' '+label(n)).toLowerCase().includes(term)).slice(0,2000).map(n=>'<tr class="n-row" data-id="'+esc(n.id)+'"><td><span class="kindpill" style="color:'+col(n.kind)+'">'+esc(n.kind)+'</span></td><td class="labelcell" title="'+esc(label(n))+'">'+esc(label(n))+'</td><td class="idcell" title="'+esc(n.id)+'">'+esc(n.id)+'</td></tr>').join(''); nb.innerHTML=nodeRows||'<tr><td colspan="3" class="empty">No matching nodes</td></tr>'; const edgeRows=links.filter(e=>(e.id+' '+e.kind+' '+(e.nodes||[]).join(' ')).toLowerCase().includes(term)).slice(0,2000).map(e=>'<tr data-edge="'+esc(e.id)+'"><td><span class="kindpill" style="color:'+col(e.kind)+'">'+esc(e.kind)+'</span></td><td class="labelcell" title="'+esc((e.nodes||[]).join(' → '))+'">'+esc((e.nodes||[]).slice(0,3).join(' → '))+(e.nodes.length>3?' …':'')+'</td><td class="idcell" title="'+esc(e.id)+'">'+esc(e.id)+'</td></tr>').join(''); eb.innerHTML=edgeRows||'<tr><td colspan="3" class="empty">No matching edges</td></tr>'; kb.innerHTML=Object.entries(kindCounts).sort((a,b)=>b[1]-a[1]).map(([k,v])=>'<tr><td><span class="kindpill" style="color:'+col(k)+'">'+esc(k)+'</span></td><td>'+v+'</td><td><span class="dot" style="background:'+col(k)+'"></span>'+col(k)+'</td></tr>').join(''); document.querySelectorAll('.n-row').forEach(r=>r.onclick=()=>select(byId[r.dataset.id])); document.getElementById('tab-nodes').textContent='Nodes ('+nodes.length+')'; document.getElementById('tab-edges').textContent='Edges ('+links.length+')'; document.getElementById('tab-kinds').textContent='Kinds ('+Object.keys(kindCounts).length+')'; document.getElementById('legend').innerHTML=Object.entries(kindCounts).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([k,v])=>'<span class="leg"><span class="dot" style="background:'+col(k)+'"></span>'+esc(k)+' '+v+'</span>').join('');}
q.addEventListener('input',renderTables);
const st=GDATA.stats||{}; document.getElementById('hdr-stats').textContent=nodes.length+' nodes · '+links.length+' edges'+(st.session_count!=null?' · '+st.session_count+' sessions':'')+(st.behavior_group_count?' · '+st.behavior_group_count+' BSG':'');
renderTables(); resize(); fit(true); for(let i=0;i<90;i++) physics(); requestAnimationFrame(draw);
}());
</script>
</body>
</html>
'''
    return (
        template
        .replace("__TITLE__", title)
        .replace("__BADGE__", badge)
        .replace("__EXPORTED__", exported)
        .replace("__DATA_JSON__", data_json)
    )


def ingest_pcap(pcap_path: Path) -> tuple[dict[str, Any], Any]:
    engine = HypergraphEngine()
    ledger = InferenceExhaustionLedger()
    config = IngestConfig(
        ftp_url="",  # unused for direct local ingestion
        staging_dir=str(pcap_path.parent),
        register_ledger=False,
        skip_existing=False,
    )
    pipeline = PcapIngestPipeline(engine, ledger, config)
    result = pipeline.ingest_file(pcap_path)
    snapshot = engine.snapshot(include_traces=True)
    return sanitize_for_json(snapshot), result


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Emit SCYTHE Hypergraph JSON + standalone HTML from a PCAPNG file.")
    p.add_argument("pcapng_path", nargs="?", default=str(DEFAULT_PCAP), help="Path to .pcapng/.pcap input")
    p.add_argument("--json-out", type=str, default=None, help="Override JSON bundle output path")
    p.add_argument("--html-out", type=str, default=None, help="Override HTML viewer output path")
    p.add_argument("--no-html", action="store_true", help="Only write the JSON bundle")
    p.add_argument("--compact-json", action="store_true", help="Write compact JSON instead of pretty JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    pcap_path = Path(args.pcapng_path).expanduser().resolve()
    if not pcap_path.exists():
        logger.error("PCAPNG file not found: %s", pcap_path)
        return 2
    if not pcap_path.is_file():
        logger.error("PCAPNG path is not a file: %s", pcap_path)
        return 2

    try:
        snapshot, result = ingest_pcap(pcap_path)
    except Exception as exc:
        logger.exception("Failed to ingest PCAPNG: %s", exc)
        return 3

    if not getattr(result, "ok", False):
        logger.warning("Ingest completed with errors: %s", getattr(result, "errors", []))

    json_path = Path(args.json_out).expanduser().resolve() if args.json_out else pcap_path.parent / f"{pcap_path.stem}.hypergraph.json"
    atomic_write_json(json_path, snapshot, indent=None if args.compact_json else 2)

    html_path = None
    if not args.no_html:
        payload = make_viewer_payload(snapshot, pcap_path, result)
        html_path = Path(args.html_out).expanduser().resolve() if args.html_out else pcap_path.parent / f"{pcap_path.stem}.hypergraph.html"
        atomic_write_text(html_path, build_html_document(payload))

    response = {
        "status": "ok" if getattr(result, "ok", False) else "warning",
        "pcap_file": str(pcap_path),
        "bundle_file": str(json_path),
        "html_file": str(html_path) if html_path else None,
        "sessions_created": getattr(result, "sessions_created", None),
        "nodes_emitted": getattr(result, "nodes_emitted", None),
        "edges_emitted": getattr(result, "edges_emitted", None),
        "errors": getattr(result, "errors", []),
    }
    print(json.dumps(response, indent=2))
    return 0 if getattr(result, "ok", False) else 4


if __name__ == "__main__":
    raise SystemExit(main())
