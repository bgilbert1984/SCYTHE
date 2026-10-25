import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

/** The source of a function declaration in the page, from its `function name(` to its closing brace at the same indent. */
function functionSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${name} is in the page`);
  const indent = page.lastIndexOf("\n", start) + 1;
  const pad = page.slice(indent, start);
  const end = page.indexOf(`\n${pad}}\n`, start);
  assert.ok(end > start);
  return page.slice(start, end + pad.length + 2);
}

/** A member of the page's OperatorSession object, from `name: function(` to the member's closing `},`. */
function sessionMember(name) {
  const header = `            ${name}: function(`;
  assert.equal(page.split(header).length - 1, 1, `${name} is defined once`);
  const start = page.indexOf(header);
  const end = page.indexOf("\n            },", start);
  return page.slice(start, end + "\n            }".length).trim().replace(new RegExp(`^${name}: `), "");
}

const pageEscape = new Function(`const _HTML_ESC = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};\n${functionSource("escapeHtml")}\nreturn escapeHtml;`)();

/** The status badge and the session panel, run against fake elements and a fake OperatorSession. */
function panels(session) {
  const els = {
    "operator-status": {innerHTML: ""}, "operator-login-btn": {style: {}}, "operator-logout-btn": {style: {}}, "operator-session-info": {innerHTML: ""},
  };
  const source = `${functionSource("updateOperatorStatusUI")}\n${functionSource("updateSessionInfoPanel")}\nreturn {updateOperatorStatusUI, updateSessionInfoPanel};`;
  const run = new Function("document", "OperatorSession", "escapeHtml", source)({getElementById: (id) => els[id] || null}, session, pageEscape);
  return {...run, els};
}

const EVIL = "<img src=x onerror=alert(1)>";
const ESCAPED = "&lt;img src=x onerror=alert(1)&gt;";
const ME = {isAuthenticated: true, callsign: "VIPER", role: "admin", operatorName: "viper@example.org", teamId: "t1"};

test("the status badge shows the callsign as text, whatever it contains", () => {
  const {updateOperatorStatusUI, els} = panels({...ME, callsign: EVIL});
  updateOperatorStatusUI();
  assert.doesNotMatch(els["operator-status"].innerHTML, /<img/);
  assert.equal(els["operator-status"].innerHTML.split(ESCAPED).length - 1, 1);
});

test("the status badge's class cannot be broken out of by a role", () => {
  const role = 'admin" onmouseover="window.pwned=1" x="';
  const {updateOperatorStatusUI, els} = panels({...ME, role});
  updateOperatorStatusUI();
  const html = els["operator-status"].innerHTML;
  assert.doesNotMatch(html, /onmouseover="window/);
  const attribute = html.match(/class="([^"]*)"/)[1];
  assert.equal(attribute, "operator-badge role-admin&quot; onmouseover=&quot;window.pwned=1&quot; x=&quot;");
});

test("the status badge and the buttons are as they were for an ordinary operator, and for none", () => {
  const {updateOperatorStatusUI, els} = panels(ME);
  updateOperatorStatusUI();
  assert.match(els["operator-status"].innerHTML, /<span class="operator-badge role-admin">\s*VIPER\s*<\/span>/);
  assert.deepEqual([els["operator-login-btn"].style.display, els["operator-logout-btn"].style.display], ["none", "inline-block"]);
  const none = panels({isAuthenticated: false});
  none.updateOperatorStatusUI();
  assert.equal(none.els["operator-status"].innerHTML, '<span class="not-logged-in">Not logged in</span>');
  assert.deepEqual([none.els["operator-login-btn"].style.display, none.els["operator-logout-btn"].style.display], ["inline-block", "none"]);
});

test("the status badge copes with elements the page does not have", () => {
  const {updateOperatorStatusUI, els} = panels(ME);
  delete els["operator-status"]; delete els["operator-login-btn"]; delete els["operator-logout-btn"];
  updateOperatorStatusUI();
});

test("the session panel shows the callsign, role, email and team as text, whatever they contain", () => {
  const {updateSessionInfoPanel, els} = panels({isAuthenticated: true, callsign: EVIL, role: EVIL, operatorName: EVIL, teamId: EVIL});
  updateSessionInfoPanel();
  const html = els["operator-session-info"].innerHTML;
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 5, "the callsign, the role in its class and its text, the email and the team");
});

test("the session panel's class cannot be broken out of by a role", () => {
  const {updateSessionInfoPanel, els} = panels({...ME, role: 'x" onclick="window.pwned=1'});
  updateSessionInfoPanel();
  assert.doesNotMatch(els["operator-session-info"].innerHTML, /onclick="window/);
});

test("the session panel is as it was for an ordinary operator, with and without a team, and for none", () => {
  const {updateSessionInfoPanel, els} = panels(ME);
  updateSessionInfoPanel();
  const html = els["operator-session-info"].innerHTML;
  assert.match(html, /<span class="operator-badge role-admin">VIPER<\/span>/);
  assert.match(html, /<div>Role: admin<\/div>/);
  assert.match(html, /<div>Email: viper@example\.org<\/div>/);
  assert.match(html, /<div>Team: t1<\/div>/);
  const noTeam = panels({...ME, teamId: null});
  noTeam.updateSessionInfoPanel();
  assert.doesNotMatch(noTeam.els["operator-session-info"].innerHTML, /Team:/);
  const numeric = panels({...ME, teamId: 7});
  numeric.updateSessionInfoPanel();
  assert.match(numeric.els["operator-session-info"].innerHTML, /<div>Team: 7<\/div>/);
  const none = panels({isAuthenticated: false});
  none.updateSessionInfoPanel();
  assert.match(none.els["operator-session-info"].innerHTML, /Not logged in[\s\S]*Login \/ Register/);
});

test("the session panel does nothing if the page has no such element", () => {
  const {updateSessionInfoPanel, els} = panels(ME);
  delete els["operator-session-info"];
  updateSessionInfoPanel();
});

/** One of the session's login callbacks, run against fakes that record the console and the calls. */
function callback(name, session) {
  const messages = [], calls = [];
  const scope = {
    addConsoleMessage: (text, kind) => messages.push([text, kind]), updateOperatorStatusUI: () => calls.push("status"), escapeHtml: pageEscape,
    window: {}, populateRooms: undefined,
  };
  const keys = Object.keys(scope);
  const fn = new Function(...keys, `return (${sessionMember(name)});`)(...keys.map((k) => scope[k]));
  fn.call(session);
  return {messages, calls};
}

test("the login and restore messages show the callsign as text, whatever it contains", () => {
  for (const name of ["onLoginSuccess", "onSessionRestored"]) {
    const {messages, calls} = callback(name, {callsign: EVIL});
    assert.equal(messages.length, 1, name);
    assert.doesNotMatch(messages[0][0], /<img/, name);
    assert.ok(messages[0][0].includes(ESCAPED), name);
    assert.equal(messages[0][1], "info");
    assert.deepEqual(calls, ["status"]);
  }
});

test("the login and restore messages are as they were for an ordinary callsign", () => {
  assert.deepEqual(callback("onLoginSuccess", {callsign: "VIPER"}).messages, [["👤 Operator VIPER logged in", "info"]]);
  assert.deepEqual(callback("onSessionRestored", {callsign: "VIPER"}).messages, [["👤 Session restored for VIPER", "info"]]);
});

test("no place in the page puts the operator's callsign, role, email or team into HTML unescaped", () => {
  const bare = /\$\{\s*(?:this|OperatorSession)\.(?:callsign|role|operatorName|teamId)\s*\}/g;
  const hits = [...page.matchAll(bare)].map((m) => page.slice(0, m.index).split("\n").length);
  const logLines = hits.filter((n) => /console\.log/.test(page.split("\n")[n - 1]));
  assert.deepEqual(hits.filter((n) => !logLines.includes(n)), [], "only console.log lines may use them bare");
});
