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
    ranking: graph.ranking ?? null,
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
      locationAuthority: "INFERRED_GEOIP_ESTIMATE",
      displayLens: snapshot.ranking?.lens ?? "SOURCE_ORDER",
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
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #24506a;background:#071422}h1{margin:0;color:#66ddff;font-size:13px;letter-spacing:.08em}.badge{padding:2px 6px;border:1px solid #24506a;color:#9eb6c7}.ok{color:#63ffd1;border-color:#26765f}.bad{color:#ff7890;border-color:#8b3142}nav{display:flex;flex-wrap:wrap;gap:5px;align-items:center;padding:6px 10px;border-bottom:1px dashed #24506a}button{color:#ccefff;background:#071422;border:1px solid #24506a;padding:4px 8px;font:10px ui-monospace,monospace;cursor:pointer}button[aria-pressed=true]{border-color:#00d4ff;color:#fff}.cfg{display:flex;align-items:center;gap:4px;margin-left:5px;color:#63ffd1;font-size:9px;white-space:nowrap}.cfg input{width:44px;padding:3px;color:#ccefff;background:#071422;border:1px solid #26765f;font:10px ui-monospace,monospace}#label-status{color:#7f9bad;font-size:9px}#main{position:relative;min-height:0}section{position:absolute;inset:0}section[hidden]{display:none}canvas{display:block;width:100%;height:100%;cursor:grab}canvas.drag{cursor:grabbing}#location-canvas{cursor:crosshair}#location-status{position:absolute;left:9px;bottom:8px;z-index:2;padding:5px 7px;border:1px solid rgba(247,209,84,.42);background:rgba(3,9,20,.9);color:#f7d154;white-space:pre-wrap;font-size:9px;pointer-events:none}#neighbor-labels{position:absolute;inset:0;overflow:hidden;pointer-events:none}.neighbor-label{position:absolute;z-index:2;max-width:180px;transform:translate(-50%,-115%);padding:2px 5px;border:1px solid rgba(0,212,255,.48);border-radius:3px;background:rgba(3,9,20,.92);color:#ccefff;font-size:9px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.neighbor-label b{color:#66ddff}.neighbor-label[data-hop="2"]{border-color:rgba(247,209,84,.55)}.neighbor-label[data-hop="3"],.neighbor-label[data-hop="4"]{opacity:.72}#tip,#location-tip{position:absolute;z-index:3;max-width:320px;padding:6px 8px;border:1px solid #00d4ff;background:rgba(3,9,20,.96);white-space:pre-wrap;pointer-events:none}#tip[hidden],#location-tip[hidden]{display:none}#inspector{position:absolute;top:8px;right:8px;width:min(330px,45vw);max-height:calc(100% - 16px);overflow:auto;padding:8px;border:1px solid #f7d154;background:rgba(3,9,20,.94);white-space:pre-wrap;color:#f7d154}#inspector[hidden]{display:none}#table-view{overflow:auto;padding:8px}table{width:100%;border-collapse:collapse}th{position:sticky;top:0;background:#071422;color:#66ddff;text-align:left}th,td{padding:5px 7px;border-bottom:1px solid #183349;vertical-align:top}tr{cursor:pointer}tr:hover{background:rgba(0,212,255,.08)}code{color:#b7ffdc;overflow-wrap:anywhere}footer{padding:6px 10px;border-top:1px dashed #24506a;color:#7f9bad}.boundary{color:#ff7890}
</style></head><body>
<header><h1>SCYTHE // OFFLINE LIVE HYPERGRAPH</h1><span class="badge">REVISION // ${safeFilename(snapshot.graphRevision)}</span><span class="badge">${snapshot.nodes.length} NODES // ${snapshot.edges.length} EDGES</span><span id="verify" class="badge">SHA-256 // VERIFYING…</span></header>
<nav><button id="mode-3d" aria-pressed="true">3D TOPOLOGY</button><button id="mode-table" aria-pressed="false">2D ACCESSIBLE</button><button id="mode-location" aria-pressed="false">LOCATION ESTIMATES</button><button id="save-json">SAVE JSON</button><label class="cfg" title="Graph-distance depth for persistent labels">HOPS <input id="cfg-hop" type="number" min="1" max="4" value="1"></label><label class="cfg" title="Maximum persistent neighbor labels">MAX LABELS <input id="cfg-max" type="number" min="1" max="100" value="24"></label><span id="label-status">LABELS // SELECT A NODE</span></nav>
<main id="main"><section id="canvas-view"><canvas id="topology-canvas" aria-label="Offline non-geographic hypergraph topology"></canvas><div id="neighbor-labels" aria-live="polite"></div><pre id="tip" hidden></pre><pre id="inspector" hidden></pre></section>
<section id="location-view" hidden><canvas id="location-canvas" aria-label="Offline coordinate projection of inferred host GeoIP estimates"></canvas><pre id="location-status"></pre><pre id="location-tip" hidden></pre></section>
<section id="table-view" hidden><table><thead><tr><th>TYPE</th><th>KIND</th><th>ID / MEMBERS</th><th>EVIDENCE</th><th>LOCATION ESTIMATE</th><th>OBSERVED</th></tr></thead><tbody id="rows"></tbody></table></section></main>
<footer>PURPOSE // <span style="color:#fff">● SELECTED</span> · <span style="color:#00d4ff">● ACTIVE TRAFFIC</span> · <span style="color:#ff8c42">● SIGNAL</span> · <span style="color:#bb83ff">● NEW</span> · <span style="color:#f7d154">● DIVERSITY</span> · <span style="color:#7890a8">● CONTEXT</span> · STATUS BADGE <span style="color:#38f28f">● ACTIVE</span> / <span style="color:#ff4f64">● INACTIVE</span><br>OFFLINE // SELF-CONTAINED // RAW PACKETS NOT EXPOSED <span class="boundary">BOUNDARY // TOPOLOGY IS NOT GEOLOCATION · ADJACENCY IS NOT CAUSALITY</span></footer>
<script type="application/json" id="bundle-data">${data}</script>
<script>
(()=>{'use strict';
const B=JSON.parse(document.getElementById('bundle-data').textContent),G=B.snapshot,M=B.manifest;
const canvas=document.getElementById('topology-canvas'),ctx=canvas.getContext('2d'),locCanvas=document.getElementById('location-canvas'),locCtx=locCanvas.getContext('2d'),locationStatus=document.getElementById('location-status'),locationTip=document.getElementById('location-tip'),tip=document.getElementById('tip'),inspector=document.getElementById('inspector'),labelRoot=document.getElementById('neighbor-labels'),labelStatus=document.getElementById('label-status');
const colors={OBSERVED:'#4a9eff',MEASURED:'#63ffd1',INFERRED:'#f7d154',SYNTHETIC:'#b784ff',CONTRADICTED:'#ff4f64'};
const purposeColors={SELECTED_CONTEXT:'#ffffff',MOST_ACTIVE:'#00d4ff',EXPLICIT_SIGNAL:'#ff8c42',NEW_ARRIVAL:'#bb83ff',NETWORK_DIVERSITY:'#f7d154',STABLE_CONTEXT:'#7890a8'};
function hash(s){let h=2166136261;for(const c of String(s)){h^=c.charCodeAt(0);h=Math.imul(h,16777619)}return h>>>0}
const pos=new Map(G.nodes.map(n=>{const a=hash(n.id),b=hash(n.id+':z'),lon=(a%3600)/3600*Math.PI*2,v=(b%2001)/1000-1,p=Math.sqrt(Math.max(0,1-v*v)),r=70+((a>>>12)%61);return[n.id,{x:Math.cos(lon)*p*r,y:v*r,z:Math.sin(lon)*p*r}]}));
const nodeById=new Map(G.nodes.map(n=>[n.id,n])),adj=new Map(G.nodes.map(n=>[n.id,new Set()]));for(const e of G.edges){const ids=(e.nodes||[]).filter(id=>adj.has(id));for(const a of ids)for(const b of ids)if(a!==b)adj.get(a).add(b)}let rx=-.28,ry=.52,zoom=2.1,drag=false,last=null,screen=[],selectedId=null,distances=new Map(),labelIds=[];
function project(p,w,h){const cy=Math.cos(ry),sy=Math.sin(ry),cx=Math.cos(rx),sx=Math.sin(rx),x=p.x*cy-p.z*sy,z=p.x*sy+p.z*cy,y=p.y*cx-z*sx,z2=p.y*sx+z*cx,k=zoom*260/(360+z2);return{x:w/2+x*k,y:h/2+y*k,z:z2,k}}
function sizeCanvas(target,context){const d=devicePixelRatio||1,r=target.getBoundingClientRect();target.width=Math.max(1,r.width*d);target.height=Math.max(1,r.height*d);context.setTransform(d,0,0,d,0,0)}function fit(){sizeCanvas(canvas,ctx);sizeCanvas(locCanvas,locCtx);draw();drawLocation()}
function draw(){const r=canvas.getBoundingClientRect(),w=r.width,h=r.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#030914';ctx.fillRect(0,0,w,h);const projected=new Map([...pos].map(([id,p])=>[id,project(p,w,h)]));
for(const e of G.edges){const members=(e.nodes||[]).filter(id=>projected.has(id));if(members.length<2)continue;ctx.strokeStyle=colors[e.evidenceClass]||'#456';ctx.globalAlpha=e.evidenceClass==='INFERRED'?.42:.68;ctx.lineWidth=e.evidenceClass==='OBSERVED'?1.6:1;ctx.setLineDash(e.evidenceClass==='INFERRED'?[5,5]:[]);const a=projected.get(members[0]);for(const id of members.slice(1)){const b=projected.get(id);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}}
ctx.setLineDash([]);ctx.globalAlpha=1;screen=G.nodes.map(n=>({n,p:projected.get(n.id)})).sort((a,b)=>a.p.z-b.p.z);for(const o of screen){const radius=nRadius(o.n)*Math.max(.7,o.p.k)*(o.n.id===selectedId?1.5:1);ctx.beginPath();ctx.arc(o.p.x,o.p.y,radius,0,Math.PI*2);ctx.fillStyle=purposeColors[o.n.display&&o.n.display.selectionPurpose]||colors[o.n.evidenceClass]||'#8aa';ctx.fill();ctx.strokeStyle=o.n.id===selectedId?'#fff':'#071422';ctx.lineWidth=o.n.id===selectedId?3:2;ctx.stroke();const state=o.n.liveness&&o.n.liveness.state;if(state==='active'||state==='inactive'){ctx.beginPath();ctx.arc(o.p.x,o.p.y-radius-4,2.8,0,Math.PI*2);ctx.fillStyle=state==='active'?'#38f28f':'#ff4f64';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke()}}positionLabels(projected,w,h)}
function nRadius(n){return n.kind==='network_host'?4.6:3.5}function nearest(ev){const r=canvas.getBoundingClientRect(),x=ev.clientX-r.left,y=ev.clientY-r.top;let best=null,dist=14;for(const o of screen){const d=Math.hypot(o.p.x-x,o.p.y-y);if(d<dist){best=o;dist=d}}return best}
function summary(n){const e=n.enrichment||{},net=e.network||{},geo=e.geo||{},labels=n.labels||{},lat=Number(geo.latitude),lon=Number(geo.longitude),unc=Number(geo.uncertaintyRadiusKm),purpose=n.display&&n.display.selectionPurpose;return[n.kind||'ENTITY',n.id,purpose?'DISPLAY PURPOSE // '+purpose.replaceAll('_',' '):'','EVIDENCE // '+(n.evidenceClass||'INFERRED'),labels.ip?'IP // '+labels.ip:'',net.asn?'NETWORK // AS'+net.asn+' · '+(net.organization||'UNKNOWN ORG'):'',geo.city?'PLACE ESTIMATE // '+[geo.city,geo.region,geo.country||geo.countryCode].filter(Boolean).join(', '):'',Number.isFinite(lat)&&Number.isFinite(lon)?'COORDINATES // '+lat.toFixed(3)+'°, '+lon.toFixed(3)+'°'+(Number.isFinite(unc)?' · ±'+unc+' km':''):'',n.observedAt?'OBSERVED // '+new Date(n.observedAt*1000).toISOString():'','BOUNDARY // GEOIP IS INFERRED; TOPOLOGY IS NOT GEOLOCATION; ESTIMATE IS NOT PHYSICAL DEVICE LOCATION'].filter(Boolean).join('\\n')}
const located=G.nodes.map(n=>{const g=n.enrichment&&n.enrichment.geo,lat=g&&g.latitude!=null?Number(g.latitude):NaN,lon=g&&g.longitude!=null?Number(g.longitude):NaN;return Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180?{n,lat,lon,unc:Number(g.uncertaintyRadiusKm)}:null}).filter(Boolean);let locScreen=[];function locProject(lat,lon,w,h){return{x:18+(lon+180)/360*(w-36),y:18+(90-lat)/180*(h-36)}}function drawLocation(){const r=locCanvas.getBoundingClientRect(),w=r.width,h=r.height;if(!w||!h)return;locCtx.clearRect(0,0,w,h);locCtx.fillStyle='#030914';locCtx.fillRect(0,0,w,h);locCtx.strokeStyle='#24506a';locCtx.strokeRect(18,18,w-36,h-36);locCtx.strokeStyle='#183349';locCtx.setLineDash([2,4]);for(let lon=-120;lon<=120;lon+=60){const a=locProject(-90,lon,w,h),b=locProject(90,lon,w,h);locCtx.beginPath();locCtx.moveTo(a.x,a.y);locCtx.lineTo(b.x,b.y);locCtx.stroke()}for(let lat=-60;lat<=60;lat+=30){const a=locProject(lat,-180,w,h),b=locProject(lat,180,w,h);locCtx.beginPath();locCtx.moveTo(a.x,a.y);locCtx.lineTo(b.x,b.y);locCtx.stroke()}locCtx.setLineDash([]);locScreen=located.map(o=>({...o,p:locProject(o.lat,o.lon,w,h)}));for(const o of locScreen){if(Number.isFinite(o.unc)&&o.unc>0){locCtx.beginPath();locCtx.arc(o.p.x,o.p.y,Math.max(4,Math.min(32,o.unc/45)),0,Math.PI*2);locCtx.fillStyle='rgba(247,209,84,.07)';locCtx.fill();locCtx.strokeStyle='rgba(247,209,84,.42)';locCtx.setLineDash([3,3]);locCtx.stroke();locCtx.setLineDash([])}locCtx.beginPath();locCtx.arc(o.p.x,o.p.y,5,0,Math.PI*2);locCtx.fillStyle='#f7d154';locCtx.fill();locCtx.strokeStyle='#fff';locCtx.stroke()}locationStatus.textContent='LOCATION ESTIMATES // '+located.length+' GEOIP-PLOTTED // '+Math.max(0,G.nodes.length-located.length)+' UNLOCATED\\nAUTHORITY // INFERRED · LOCAL GEOIP DATABASE\\nBOUNDARY // IP NETWORK LOCATION ESTIMATE; NOT PHYSICAL DEVICE LOCATION'}function nearestLocation(ev){const r=locCanvas.getBoundingClientRect(),x=ev.clientX-r.left,y=ev.clientY-r.top;let best=null,distance=14;for(const o of locScreen){const d=Math.hypot(o.p.x-x,o.p.y-y);if(d<distance){best=o;distance=d}}return best}
function bounded(id,fallback,min,max){const el=document.getElementById(id),value=Math.max(min,Math.min(max,parseInt(el.value)||fallback));el.value=String(value);return value}function nodeLabel(n){const l=n.labels||{},e=n.enrichment||{},net=e.network||{};return l.ip||l.name||net.organization||n.id.slice(0,24)}
function selectNode(id){const candidate=id&&nodeById.has(id)?id:null;selectedId=candidate===selectedId?null:candidate;distances=new Map();if(selectedId){distances.set(selectedId,0);const queue=[selectedId];while(queue.length){const current=queue.shift(),depth=distances.get(current);if(depth>=4)continue;for(const next of adj.get(current)||[])if(!distances.has(next)){distances.set(next,depth+1);queue.push(next)}}const n=nodeById.get(selectedId);inspector.hidden=false;inspector.textContent=summary(n)+'\\n\\nMETADATA // '+JSON.stringify(n.metadata||{},null,2)}else inspector.hidden=true;refreshLabels();draw()}
function refreshLabels(){labelRoot.replaceChildren();labelIds=[];if(!selectedId){labelStatus.textContent='LABELS // SELECT A NODE';return}const hops=bounded('cfg-hop',1,1,4),max=bounded('cfg-max',24,1,100);labelIds=[...distances].filter(([id,d])=>id!==selectedId&&d>=1&&d<=hops).sort((a,b)=>a[1]-b[1]||a[0].localeCompare(b[0])).slice(0,max).map(([id])=>id);for(const id of labelIds){const n=nodeById.get(id),el=document.createElement('div');el.className='neighbor-label';el.dataset.entityId=id;el.dataset.hop=String(distances.get(id));const kind=document.createElement('b');kind.textContent=(n.kind||'ENTITY')+' · HOP '+distances.get(id);el.append(kind,document.createElement('br'),document.createTextNode(nodeLabel(n)));labelRoot.appendChild(el)}labelStatus.textContent='LABELS // '+labelIds.length+' SHOWN // WITHIN '+hops+' HOP'+(hops===1?'':'S')+' // CAP '+max;draw()}
function positionLabels(projected,w,h){for(const el of labelRoot.children){const p=projected.get(el.dataset.entityId),visible=p&&p.z<340&&p.x>0&&p.x<w&&p.y>0&&p.y<h;el.hidden=!visible;if(visible){el.style.left=p.x+'px';el.style.top=p.y+'px'}}}
canvas.addEventListener('pointerdown',e=>{drag=true;last=e;canvas.classList.add('drag');canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointerup',e=>{drag=false;last=null;canvas.classList.remove('drag');canvas.releasePointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(drag&&last){ry+=(e.clientX-last.clientX)*.008;rx+=(e.clientY-last.clientY)*.008;last=e;draw();tip.hidden=true;return}const o=nearest(e);tip.hidden=!o;if(o){tip.textContent=summary(o.n);const r=canvas.getBoundingClientRect();tip.style.left=(e.clientX-r.left+12)+'px';tip.style.top=(e.clientY-r.top+12)+'px'}});canvas.addEventListener('mouseleave',()=>tip.hidden=true);canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(.5,Math.min(7,zoom*(e.deltaY>0?.9:1.1)));draw()},{passive:false});canvas.addEventListener('click',e=>{if(!drag){const o=nearest(e);selectNode(o?.n.id||null)}});for(const id of ['cfg-hop','cfg-max'])document.getElementById(id).addEventListener('input',refreshLabels);
locCanvas.addEventListener('pointermove',e=>{const o=nearestLocation(e);locationTip.hidden=!o;if(o){locationTip.textContent=summary(o.n);const r=locCanvas.getBoundingClientRect();locationTip.style.left=(e.clientX-r.left+12)+'px';locationTip.style.top=(e.clientY-r.top+12)+'px'}});locCanvas.addEventListener('mouseleave',()=>locationTip.hidden=true);locCanvas.addEventListener('click',e=>{const o=nearestLocation(e);if(o){mode('topology');selectNode(o.n.id)}});
const rows=document.getElementById('rows');function addRow(type,item,members){const tr=document.createElement('tr');tr.dataset.entityId=item.id;const g=item.enrichment&&item.enrichment.geo,location=g&&g.latitude!=null&&g.longitude!=null?[g.city,g.region,g.country||g.countryCode].filter(Boolean).join(', ')+' · '+Number(g.latitude).toFixed(3)+'°, '+Number(g.longitude).toFixed(3)+'°':'';for(const value of [type,item.kind||'',members,item.evidenceClass||'INFERRED',location,item.observedAt?new Date(item.observedAt*1000).toISOString():'']){const td=document.createElement('td');const code=document.createElement('code');code.textContent=String(value||'');td.appendChild(code);tr.appendChild(td)}if(type==='NODE')tr.onclick=()=>{mode('topology');selectNode(item.id)};rows.appendChild(tr)}
for(const n of G.nodes)addRow('NODE',n,n.id);for(const e of G.edges)addRow('EDGE',e,(e.nodes||[]).join(' → '));
function mode(name){for(const [view,button] of [['topology','mode-3d'],['table','mode-table'],['location','mode-location']]){document.getElementById(view==='topology'?'canvas-view':view+'-view').hidden=view!==name;document.getElementById(button).setAttribute('aria-pressed',String(view===name))}if(name!=='table')fit()}document.getElementById('mode-3d').onclick=()=>mode('topology');document.getElementById('mode-table').onclick=()=>mode('table');document.getElementById('mode-location').onclick=()=>mode('location');
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
