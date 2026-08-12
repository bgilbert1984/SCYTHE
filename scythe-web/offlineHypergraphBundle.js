const NODE_LIMIT = 500;
const EDGE_LIMIT = 1000;

function boundedSnapshot(graph) {
  if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new TypeError("a live hypergraph snapshot is required");
  }
  return JSON.parse(JSON.stringify({
    graphRevision: String(graph.graphRevision ?? "graph-unknown"),
    capturedAt: Number(graph.capturedAt) || null,
    snapshotAuthority: String(graph.snapshotAuthority ?? "BOUNDED_LIVE_GRAPH_VIEW"),
    bounded: true,
    nodeLimit: NODE_LIMIT,
    edgeLimit: EDGE_LIMIT,
    nodes: graph.nodes.slice(0, NODE_LIMIT),
    edges: graph.edges.slice(0, EDGE_LIMIT),
  }));
}

async function sha256(value, cryptoImpl = globalThis.crypto) {
  if (!cryptoImpl?.subtle) throw new Error("Web Crypto SHA-256 is unavailable");
  const digest = await cryptoImpl.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function safeJson(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

function safeFilename(value) {
  return String(value || "graph-unknown").replace(/[^a-zA-Z0-9._-]+/g, "-").slice(0, 80);
}

export async function buildOfflineHypergraphBundle(graph, {
  exportedAt = new Date().toISOString(), cryptoImpl = globalThis.crypto,
} = {}) {
  const snapshot = boundedSnapshot(graph);
  const snapshotJson = JSON.stringify(snapshot);
  const payload = {
    manifest: {
      schema: "scythe.offline-live-hypergraph.v1",
      exportedAt,
      graphRevision: snapshot.graphRevision,
      nodeCount: snapshot.nodes.length,
      edgeCount: snapshot.edges.length,
      digestAlgorithm: "SHA-256",
      snapshotSha256: await sha256(snapshotJson, cryptoImpl),
      topologyAuthority: "NON_GEOGRAPHIC_DETERMINISTIC_LAYOUT",
      evidenceBoundary: "EMBEDDED GRAPH CLAIMS RETAIN THEIR EVIDENCE CLASS; ADJACENCY IS NOT CAUSALITY",
      rawPacketsExposed: false,
      offline: true,
    },
    snapshot,
  };
  const data = safeJson(payload);
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCYTHE Offline Hypergraph // ${safeFilename(snapshot.graphRevision)}</title>
<style>
*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;background:#030914;color:#ccefff;font:11px/1.4 ui-monospace,monospace}body{display:grid;grid-template-rows:auto auto 1fr auto;overflow:hidden}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #24506a;background:#071422}h1{margin:0;color:#66ddff;font-size:13px;letter-spacing:.08em}.badge{padding:2px 6px;border:1px solid #24506a;color:#9eb6c7}.ok{color:#63ffd1;border-color:#26765f}.bad{color:#ff7890;border-color:#8b3142}nav{display:flex;gap:5px;padding:6px 10px;border-bottom:1px dashed #24506a}button{color:#ccefff;background:#071422;border:1px solid #24506a;padding:4px 8px;font:10px ui-monospace,monospace;cursor:pointer}button[aria-pressed=true]{border-color:#00d4ff;color:#fff}#main{position:relative;min-height:0}section{position:absolute;inset:0}section[hidden]{display:none}canvas{display:block;width:100%;height:100%;cursor:grab}canvas.drag{cursor:grabbing}#tip{position:absolute;z-index:3;max-width:320px;padding:6px 8px;border:1px solid #00d4ff;background:rgba(3,9,20,.96);white-space:pre-wrap;pointer-events:none}#tip[hidden]{display:none}#inspector{position:absolute;top:8px;right:8px;width:min(330px,45vw);max-height:calc(100% - 16px);overflow:auto;padding:8px;border:1px solid #f7d154;background:rgba(3,9,20,.94);white-space:pre-wrap;color:#f7d154}#inspector[hidden]{display:none}#table-view{overflow:auto;padding:8px}table{width:100%;border-collapse:collapse}th{position:sticky;top:0;background:#071422;color:#66ddff;text-align:left}th,td{padding:5px 7px;border-bottom:1px solid #183349;vertical-align:top}tr{cursor:pointer}tr:hover{background:rgba(0,212,255,.08)}code{color:#b7ffdc;overflow-wrap:anywhere}footer{padding:6px 10px;border-top:1px dashed #24506a;color:#7f9bad}.boundary{color:#ff7890}
</style></head><body>
<header><h1>SCYTHE // OFFLINE LIVE HYPERGRAPH</h1><span class="badge">REVISION // ${safeFilename(snapshot.graphRevision)}</span><span class="badge">${snapshot.nodes.length} NODES // ${snapshot.edges.length} EDGES</span><span id="verify" class="badge">SHA-256 // VERIFYING…</span></header>
<nav><button id="mode-3d" aria-pressed="true">3D TOPOLOGY</button><button id="mode-table" aria-pressed="false">2D ACCESSIBLE</button><button id="save-json">SAVE JSON</button></nav>
<main id="main"><section id="canvas-view"><canvas aria-label="Offline non-geographic hypergraph topology"></canvas><pre id="tip" hidden></pre><pre id="inspector" hidden></pre></section>
<section id="table-view" hidden><table><thead><tr><th>TYPE</th><th>KIND</th><th>ID / MEMBERS</th><th>EVIDENCE</th><th>OBSERVED</th></tr></thead><tbody id="rows"></tbody></table></section></main>
<footer>OFFLINE // SELF-CONTAINED // RAW PACKETS NOT EXPOSED <span class="boundary">BOUNDARY // TOPOLOGY IS NOT GEOLOCATION · ADJACENCY IS NOT CAUSALITY</span></footer>
<script type="application/json" id="bundle-data">${data}</script>
<script>
(()=>{'use strict';
const B=JSON.parse(document.getElementById('bundle-data').textContent),G=B.snapshot,M=B.manifest;
const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d'),tip=document.getElementById('tip'),inspector=document.getElementById('inspector');
const colors={OBSERVED:'#4a9eff',MEASURED:'#63ffd1',INFERRED:'#f7d154',SYNTHETIC:'#b784ff',CONTRADICTED:'#ff4f64'};
function hash(s){let h=2166136261;for(const c of String(s)){h^=c.charCodeAt(0);h=Math.imul(h,16777619)}return h>>>0}
const pos=new Map(G.nodes.map(n=>{const a=hash(n.id),b=hash(n.id+':z'),lon=(a%3600)/3600*Math.PI*2,v=(b%2001)/1000-1,p=Math.sqrt(Math.max(0,1-v*v)),r=70+((a>>>12)%61);return[n.id,{x:Math.cos(lon)*p*r,y:v*r,z:Math.sin(lon)*p*r}]}));
const nodeById=new Map(G.nodes.map(n=>[n.id,n]));let rx=-.28,ry=.52,zoom=2.1,drag=false,last=null,screen=[];
function project(p,w,h){const cy=Math.cos(ry),sy=Math.sin(ry),cx=Math.cos(rx),sx=Math.sin(rx),x=p.x*cy-p.z*sy,z=p.x*sy+p.z*cy,y=p.y*cx-z*sx,z2=p.y*sx+z*cx,k=zoom*260/(360+z2);return{x:w/2+x*k,y:h/2+y*k,z:z2,k}}
function fit(){const d=devicePixelRatio||1,r=canvas.getBoundingClientRect();canvas.width=Math.max(1,r.width*d);canvas.height=Math.max(1,r.height*d);ctx.setTransform(d,0,0,d,0,0);draw()}
function draw(){const r=canvas.getBoundingClientRect(),w=r.width,h=r.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#030914';ctx.fillRect(0,0,w,h);const projected=new Map([...pos].map(([id,p])=>[id,project(p,w,h)]));
for(const e of G.edges){const members=(e.nodes||[]).filter(id=>projected.has(id));if(members.length<2)continue;ctx.strokeStyle=colors[e.evidenceClass]||'#456';ctx.globalAlpha=e.evidenceClass==='INFERRED'?.42:.68;ctx.lineWidth=e.evidenceClass==='OBSERVED'?1.6:1;ctx.setLineDash(e.evidenceClass==='INFERRED'?[5,5]:[]);const a=projected.get(members[0]);for(const id of members.slice(1)){const b=projected.get(id);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}}
ctx.setLineDash([]);ctx.globalAlpha=1;screen=G.nodes.map(n=>({n,p:projected.get(n.id)})).sort((a,b)=>a.p.z-b.p.z);for(const o of screen){const radius=nRadius(o.n)*Math.max(.7,o.p.k);ctx.beginPath();ctx.arc(o.p.x,o.p.y,radius,0,Math.PI*2);ctx.fillStyle=colors[o.n.evidenceClass]||'#8aa';ctx.fill();ctx.strokeStyle='#071422';ctx.lineWidth=2;ctx.stroke()}}
function nRadius(n){return n.kind==='network_host'?4.6:3.5}function nearest(ev){const r=canvas.getBoundingClientRect(),x=ev.clientX-r.left,y=ev.clientY-r.top;let best=null,dist=14;for(const o of screen){const d=Math.hypot(o.p.x-x,o.p.y-y);if(d<dist){best=o;dist=d}}return best}
function summary(n){const e=n.enrichment||{},net=e.network||{},geo=e.geo||{},labels=n.labels||{};return[n.kind||'ENTITY',n.id,'EVIDENCE // '+(n.evidenceClass||'INFERRED'),labels.ip?'IP // '+labels.ip:'',net.asn?'NETWORK // AS'+net.asn+' · '+(net.organization||'UNKNOWN ORG'):'',geo.city?'PLACE ESTIMATE // '+[geo.city,geo.region,geo.country||geo.countryCode].filter(Boolean).join(', '):'',n.observedAt?'OBSERVED // '+new Date(n.observedAt*1000).toISOString():'','BOUNDARY // ENRICHMENT IS INFERRED; TOPOLOGY IS NOT GEOLOCATION'].filter(Boolean).join('\\n')}
canvas.addEventListener('pointerdown',e=>{drag=true;last=e;canvas.classList.add('drag');canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointerup',e=>{drag=false;last=null;canvas.classList.remove('drag');canvas.releasePointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(drag&&last){ry+=(e.clientX-last.clientX)*.008;rx+=(e.clientY-last.clientY)*.008;last=e;draw();tip.hidden=true;return}const o=nearest(e);tip.hidden=!o;if(o){tip.textContent=summary(o.n);const r=canvas.getBoundingClientRect();tip.style.left=(e.clientX-r.left+12)+'px';tip.style.top=(e.clientY-r.top+12)+'px'}});canvas.addEventListener('mouseleave',()=>tip.hidden=true);canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.5,Math.min(7,zoom*(e.deltaY>0?.9:1.1)));draw()},{passive:false});canvas.addEventListener('click',e=>{const o=nearest(e);inspector.hidden=!o;if(o)inspector.textContent=summary(o.n)+'\\n\\nMETADATA // '+JSON.stringify(o.n.metadata||{},null,2)});
const rows=document.getElementById('rows');function addRow(type,item,members){const tr=document.createElement('tr');for(const value of [type,item.kind||'',members,item.evidenceClass||'INFERRED',item.observedAt?new Date(item.observedAt*1000).toISOString():'']){const td=document.createElement('td');const code=document.createElement('code');code.textContent=String(value||'');td.appendChild(code);tr.appendChild(td)}if(type==='NODE')tr.onclick=()=>{document.getElementById('mode-3d').click();inspector.hidden=false;inspector.textContent=summary(item)+'\\n\\nMETADATA // '+JSON.stringify(item.metadata||{},null,2)};rows.appendChild(tr)}
for(const n of G.nodes)addRow('NODE',n,n.id);for(const e of G.edges)addRow('EDGE',e,(e.nodes||[]).join(' → '));
function mode(table){document.getElementById('canvas-view').hidden=table;document.getElementById('table-view').hidden=!table;document.getElementById('mode-3d').setAttribute('aria-pressed',String(!table));document.getElementById('mode-table').setAttribute('aria-pressed',String(table));if(!table)fit()}document.getElementById('mode-3d').onclick=()=>mode(false);document.getElementById('mode-table').onclick=()=>mode(true);
document.getElementById('save-json').onclick=()=>{const u=URL.createObjectURL(new Blob([JSON.stringify(B,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='scythe-'+G.graphRevision+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};
crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(G))).then(d=>{const h=[...new Uint8Array(d)].map(b=>b.toString(16).padStart(2,'0')).join(''),v=document.getElementById('verify');v.textContent='SHA-256 // '+(h===M.snapshotSha256?'VERIFIED':'MISMATCH')+' // '+h;v.classList.add(h===M.snapshotSha256?'ok':'bad')}).catch(()=>{const v=document.getElementById('verify');v.textContent='SHA-256 // VERIFICATION UNAVAILABLE';v.classList.add('bad')});
addEventListener('resize',fit);fit();
})();
</script></body></html>`;
}

export async function downloadOfflineHypergraphBundle(graph, {
  documentImpl = globalThis.document, urlImpl = globalThis.URL, cryptoImpl = globalThis.crypto,
  exportedAt,
} = {}) {
  const html = await buildOfflineHypergraphBundle(graph, {exportedAt, cryptoImpl});
  const blob = new Blob([html], {type: "text/html;charset=utf-8"});
  const url = urlImpl.createObjectURL(blob);
  const anchor = documentImpl.createElement("a");
  anchor.href = url;
  anchor.download = `scythe-live-hypergraph-${safeFilename(graph.graphRevision)}.html`;
  anchor.click();
  setTimeout(() => urlImpl.revokeObjectURL(url), 5000);
  return {filename: anchor.download, bytes: blob.size, graphRevision: graph.graphRevision};
}
