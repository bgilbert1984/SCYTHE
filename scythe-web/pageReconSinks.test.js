import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

const _HTML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
// The page's own escapeHtml, from its early script.
const escapeHtml = new Function(`const _HTML_ESC = ${JSON.stringify(_HTML_ESC)};\n${(() => {
  const start = page.indexOf("function escapeHtml(s, _d)");
  assert.ok(start > 0, "escapeHtml is in the page");
  return page.slice(start, page.indexOf("\n    }\n", start) + "\n    }\n".length);
})()}\nreturn escapeHtml;`)();

/** A member of the page's AutoReconVisualization object, as a function: from `name: function(` to the member's closing brace. */
function reconMember(name) {
  const header = `            ${name}: function(`;
  assert.equal(page.split(header).length - 1, 1, `${name} is defined once`);
  const start = page.indexOf(header);
  const end = page.indexOf("\n            }", start);
  return page.slice(start, end + "\n            }".length).trim().replace(new RegExp(`^${name}: `), "");
}

/** The recon object's description and alert-list members, run against a fake document. */
function recon({alerts = []} = {}) {
  const els = {"recon-alert-list": {innerHTML: ""}};
  const members = ["createEntityDescription", "getDispositionCssColor", "getThreatCssColor", "updateAlertList"];
  const source = `return {${members.map((name) => `${name}: ${reconMember(name)}`).join(",\n")}};`;
  const object = new Function("document", "escapeHtml", source)({getElementById: (id) => els[id]}, escapeHtml);
  object.alerts = alerts;
  return {object, els};
}

/** The reconList panel function, run against a fake document and a fake AutoReconVisualization. */
function reconList(entities) {
  const start = page.indexOf("window.updateReconList = function() {");
  assert.ok(start > 0, "updateReconList is in the page");
  const end = page.indexOf("\n        };", start);
  const source = page.slice(start, end + "\n        };".length);
  const list = {innerHTML: "", children: [], appendChild(li) { this.children.push(li); }};
  const els = {reconList: list, "recon-panel-count": {}, "recon-entity-count": {}};
  const window = {AutoReconVisualization: {entities, createEntityDescription: undefined}};
  const document = {
    getElementById: (id) => els[id],
    createElement: () => ({className: "", innerHTML: "", querySelectorAll: () => []}),
  };
  new Function("window", "document", "escapeHtml", "console", `${source}\nwindow.updateReconList();`)(window, document, escapeHtml, {debug() {}});
  return {list, els};
}

const EVIL = "<img src=x onerror=alert(1)>";
const ESCAPED = "&lt;img src=x onerror=alert(1)&gt;";
const ENTITY = {
  entity_id: "ent-1", name: "Bogey", disposition: "HOSTILE", threat_level: "HIGH", ontology: "air.fighter",
  location: {lat: 36.1, lon: -86.2}, distance_nm: 12.34, bearing_deg: 45, velocity: {speed_kts: 450, heading_deg: 90},
  rf_emissions: true, iff_response: false,
};

/** What the browser does with `attr="..."`: decode the entities, then the value is script. */
const decodeAttribute = (value) => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

/** The argument each inline handler named `fn` is called with, once the browser has decoded the attribute. */
function handlerArguments(html, fn) {
  const calls = [];
  const pattern = new RegExp(`onclick="AutoReconVisualization\\.${fn}\\(([^"]*)\\)"`, "g");
  for (const match of html.matchAll(pattern)) {
    new Function(fn, `${fn}(${decodeAttribute(match[1])})`)((arg) => calls.push(arg));
  }
  return calls;
}

const HOSTILE_IDS = ["x');alert(1);//", 'x");alert(1);//', "a\\b", "a\nb", "</script><b>", "o'brien", EVIL];

test("the entity description shows every text field as text, whatever it contains", () => {
  const {object} = recon();
  const html = object.createEntityDescription({...ENTITY, name: EVIL, entity_id: EVIL, disposition: EVIL, threat_level: EVIL, ontology: EVIL});
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 7, "five text fields, and the id inside each of the two buttons");
});

test("the entity description is as it was for an ordinary entity", () => {
  const {object} = recon();
  const html = object.createEntityDescription(ENTITY);
  assert.match(html, /🎯 Bogey<\/h3>/);
  assert.match(html, /Entity ID:<\/td><td style="padding: 3px;">ent-1<\/td>/);
  assert.match(html, /color: #FF0000;">HOSTILE<\/td>/);
  assert.match(html, /color: #FF4500;">HIGH<\/td>/);
  assert.match(html, /36\.10000°, -86\.20000°/);
  assert.match(html, /12\.34 NM/);
  assert.match(html, /450\.0 kts/);
  assert.match(html, /air\.fighter/);
  assert.match(html, /📡 Detected/);
  assert.match(html, /❌ None/);
  assert.deepEqual(handlerArguments(html, "createTaskForEntity"), ["ent-1"]);
  assert.deepEqual(handlerArguments(html, "updateDisposition"), ["ent-1"]);
});

test("the description's buttons are given exactly the entity id, once the browser has decoded the attribute, whatever the id contains", () => {
  const {object} = recon();
  for (const id of ["ent-1", ...HOSTILE_IDS]) {
    const html = object.createEntityDescription({...ENTITY, entity_id: id});
    assert.deepEqual(handlerArguments(html, "createTaskForEntity"), [id], `create task: ${JSON.stringify(id)}`);
    assert.deepEqual(handlerArguments(html, "updateDisposition"), [id], `change disposition: ${JSON.stringify(id)}`);
  }
});

test("the alert list shows names, threat levels and dispositions as text, whatever they contain", () => {
  const alert = {entity_id: "ent-1", name: EVIL, threat_level: EVIL, disposition: EVIL, distance_nm: 1, bearing_deg: 2};
  const {object, els} = recon({alerts: [alert]});
  object.updateAlertList();
  assert.doesNotMatch(els["recon-alert-list"].innerHTML, /<img/);
  assert.equal(els["recon-alert-list"].innerHTML.split(ESCAPED).length - 1, 4, "name, threat level, disposition, and the threat level in the class attribute");
});

test("a threat level cannot break out of the alert's class attribute", () => {
  const alert = {entity_id: "e", name: "n", threat_level: 'HIGH" onmouseover="alert(1)', disposition: "d", distance_nm: 1, bearing_deg: 2};
  const {object, els} = recon({alerts: [alert]});
  object.updateAlertList();
  assert.doesNotMatch(els["recon-alert-list"].innerHTML, /class="alert-item threat-high" onmouseover/);
  assert.match(els["recon-alert-list"].innerHTML, /class="alert-item threat-high&quot; onmouseover=&quot;alert\(1\)"/);
});

test("the alert list is as it was for ordinary alerts, and an empty one says so", () => {
  const {object, els} = recon({alerts: [{entity_id: "ent-1", name: "Bogey", threat_level: "HIGH", disposition: "HOSTILE", distance_nm: 12.34, bearing_deg: 45.4}]});
  object.updateAlertList();
  const html = els["recon-alert-list"].innerHTML;
  assert.match(html, /class="alert-item threat-high"/);
  assert.match(html, /<span class="alert-name">Bogey<\/span>/);
  assert.match(html, /<span class="alert-threat">HIGH<\/span>/);
  assert.match(html, /<span>HOSTILE<\/span>/);
  assert.match(html, /12\.3 NM @ 45°/);
  assert.deepEqual(handlerArguments(html, "flyToEntity"), ["ent-1"]);
  object.alerts = [];
  object.updateAlertList();
  assert.equal(els["recon-alert-list"].innerHTML, '<div class="no-alerts">No active alerts</div>');
});

test("the alert's click handler is given exactly the entity id, whatever the id contains", () => {
  for (const id of HOSTILE_IDS) {
    const {object, els} = recon({alerts: [{entity_id: id, name: "n", threat_level: "LOW", disposition: "d", distance_nm: 1, bearing_deg: 2}]});
    object.updateAlertList();
    assert.deepEqual(handlerArguments(els["recon-alert-list"].innerHTML, "flyToEntity"), [id], JSON.stringify(id));
  }
});

test("the recon list shows names, dispositions and threat levels as text, whatever they contain", () => {
  const {list} = reconList({a: {...ENTITY, name: EVIL, disposition: EVIL, threat_level: EVIL}});
  const html = list.children[0].innerHTML;
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 3);
});

test("the recon list's buttons carry exactly the entity id, whatever it contains", () => {
  for (const id of ["ent-1", ...HOSTILE_IDS]) {
    const {list} = reconList({a: {...ENTITY, entity_id: id}});
    const attrs = [...list.children[0].innerHTML.matchAll(/data-entity-id="([^"]*)"/g)].map((m) => decodeAttribute(m[1]));
    assert.deepEqual(attrs, [id, id, id], JSON.stringify(id));
  }
});

test("the recon list is as it was for ordinary entities, and an empty one says so", () => {
  const {list, els} = reconList({a: ENTITY, b: {...ENTITY, entity_id: "ent-2", name: "Friendly", disposition: "FRIEND"}});
  assert.equal(list.children.length, 2);
  const html = list.children[0].innerHTML;
  assert.match(html, /<span class="list-item-title">Bogey<\/span>/);
  assert.match(html, /background-color:#ff4d4d/);
  assert.match(html, /HOSTILE \| HIGH \| 36\.100, -86\.200/);
  assert.match(list.children[1].innerHTML, /background-color:#4a9eff/);
  assert.equal(els["recon-panel-count"].textContent, 2);
  assert.equal(els["recon-entity-count"].textContent, 2);
  assert.match(reconList({}).list.innerHTML, /No recon entities available/);
});

test("the page no longer puts an entity's fields into these three pieces of HTML unescaped", () => {
  for (const name of ["createEntityDescription", "updateAlertList"]) {
    const source = reconMember(name);
    for (const field of ["name", "entity_id", "disposition", "threat_level", "ontology"]) {
      const bare = new RegExp(`\\$\\{(?:entity|alert)\\.${field}\\}`);
      assert.doesNotMatch(source, bare, `${name}: ${field}`);
    }
  }
  const start = page.indexOf("window.updateReconList = function() {");
  const body = page.slice(start, page.indexOf("\n        };", start));
  for (const field of ["name", "entity_id", "disposition", "threat_level"]) {
    assert.doesNotMatch(body, new RegExp(`\\$\\{e\\.${field}\\}`), `updateReconList: ${field}`);
  }
  assert.doesNotMatch(body, /<pre>\$\{JSON/);
});

test("an id that is a number, or missing, reaches the handlers as a string, as it did when the id sat inside quotes", () => {
  const {object, els} = recon({alerts: [{entity_id: 5, name: "n", threat_level: "LOW", disposition: "d", distance_nm: 1, bearing_deg: 2}, {name: "n", threat_level: "LOW", disposition: "d", distance_nm: 1, bearing_deg: 2}]});
  object.updateAlertList();
  assert.deepEqual(handlerArguments(els["recon-alert-list"].innerHTML, "flyToEntity"), ["5", "undefined"]);
  const html = object.createEntityDescription({...ENTITY, entity_id: 5}) + object.createEntityDescription({...ENTITY, entity_id: undefined});
  assert.deepEqual(handlerArguments(html, "createTaskForEntity"), ["5", "undefined"]);
  assert.deepEqual(handlerArguments(html, "updateDisposition"), ["5", "undefined"]);
});
