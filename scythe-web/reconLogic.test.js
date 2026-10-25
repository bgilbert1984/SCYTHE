import test from "node:test";
import assert from "node:assert/strict";
import {
  dispositionCssColor, threatCssColor, describeEntity, alertListHtml, reconRowHtml, normalizeLoaded, normalizeEntityData,
  planSyncEvent, streamEvent, mldcLabel, runningAverage, NO_ALERTS,
} from "./reconLogic.js";

const NOW = 1700000000000;
const ENTITY = {
  entity_id: "ent-1", name: "Bogey", disposition: "HOSTILE", threat_level: "HIGH", ontology: "air.fighter",
  location: {lat: 36.1, lon: -86.2}, distance_nm: 12.34, bearing_deg: 45, velocity: {speed_kts: 450, heading_deg: 90},
};

test("colours are looked up for a known name and are grey for any other, including names every object has", () => {
  assert.equal(dispositionCssColor("HOSTILE"), "#FF0000");
  assert.equal(dispositionCssColor("FAKER"), "#800080");
  assert.equal(dispositionCssColor("nonesuch"), "#AAAAAA");
  assert.equal(dispositionCssColor(undefined), "#AAAAAA");
  assert.equal(dispositionCssColor("constructor"), "#AAAAAA");
  assert.equal(threatCssColor("CRITICAL"), "#FF0000");
  assert.equal(threatCssColor("NONE"), "#00FFFF");
  assert.equal(threatCssColor("toString"), "#AAAAAA");
  assert.equal(threatCssColor(), "#AAAAAA");
});

test("every disposition and threat level has its colour", () => {
  const dispositions = {UNKNOWN: "#FFFF00", PENDING: "#FFFF00", ASSUMED_FRIEND: "#4444FF", FRIEND: "#0000FF", NEUTRAL: "#00FF00", SUSPICIOUS: "#FFA500", HOSTILE: "#FF0000", ELIMINATED: "#888888", JOKER: "#FF00FF", FAKER: "#800080"};
  for (const [name, color] of Object.entries(dispositions)) assert.equal(dispositionCssColor(name), color, name);
  const threats = {CRITICAL: "#FF0000", HIGH: "#FF4500", MEDIUM: "#FFA500", LOW: "#FFFF00", MINIMAL: "#00FF00", NONE: "#00FFFF"};
  for (const [name, color] of Object.entries(threats)) assert.equal(threatCssColor(name), color, name);
});

test("the description throws for an entity with no threat level or disposition, as it did", () => {
  assert.throws(() => describeEntity({...ENTITY, threat_level: undefined}), TypeError);
  assert.throws(() => describeEntity({...ENTITY, disposition: undefined}), TypeError);
});

test("the description's numbers default to zero and its flags to none", () => {
  const html = describeEntity({entity_id: "e", name: "n", disposition: "D", threat_level: "T", ontology: "o"});
  assert.match(html, /0\.00000°, 0\.00000°/);
  assert.match(html, /0\.00 NM/);
  assert.match(html, /Bearing:<\/td><td style="padding: 3px;">0\.0°<\/td>/);
  assert.match(html, /Speed:<\/td><td style="padding: 3px;">0\.0 kts<\/td>/);
  assert.match(html, /Heading:<\/td><td style="padding: 3px;">0\.0°<\/td>/);
  assert.match(html, /RF Emissions:<\/td><td style="padding: 3px;">None</);
  assert.match(html, /IFF Response:<\/td><td style="padding: 3px;">❌ None</);
  assert.match(describeEntity({...ENTITY, rf_emissions: true, iff_response: true}), /📡 Detected[\s\S]*✅ Valid/);
});

test("the description colours a disposition that is not a colour name grey", () => {
  assert.match(describeEntity({...ENTITY, disposition: "constructor"}), /color: #AAAAAA;">constructor</);
});

test("the alert list says so when empty, and an alert with no numbers throws, as before", () => {
  assert.equal(alertListHtml([]), NO_ALERTS);
  assert.throws(() => alertListHtml([{entity_id: "e", name: "n", threat_level: "LOW", disposition: "d"}]), TypeError);
  assert.throws(() => alertListHtml([{entity_id: "e", name: "n", disposition: "d", distance_nm: 1, bearing_deg: 1}]), TypeError);
});

test("the alert list has one row per alert, in order", () => {
  const alerts = [1, 2, 3].map((i) => ({entity_id: `e${i}`, name: `n${i}`, threat_level: "LOW", disposition: "d", distance_nm: i, bearing_deg: i}));
  const html = alertListHtml(alerts);
  assert.equal(html.split('class="alert-item').length - 1, 3);
  assert.match(html, /^\n {24}<div class="alert-item/, "each row starts on its own line, indented as the page's template had it");
  assert.match(html, /<\/div>\n {20}$/);
  assert.ok(html.indexOf("n1") < html.indexOf("n2") && html.indexOf("n2") < html.indexOf("n3"));
});

test("a list row marks a hostile entity red and any other blue, and its three buttons carry the id", () => {
  const e = {...ENTITY, location: {lat: 36.1239, lon: -86.4999}};
  const hostile = reconRowHtml(e);
  assert.match(hostile, /background-color:#ff4d4d/);
  assert.match(hostile, /HOSTILE \| HIGH \| 36\.124, -86\.500/);
  assert.match(reconRowHtml({...e, disposition: "FRIEND"}), /background-color:#4a9eff/);
  assert.deepEqual([...hostile.matchAll(/data-action="(\w+)"/g)].map((m) => m[1]), ["track", "task", "info"]);
  assert.equal(hostile.split('data-entity-id="ent-1"').length - 1, 3);
  assert.throws(() => reconRowHtml({...e, location: undefined}), TypeError);
});

test("an entity from the full list is kept as it is, or lifted out of `data` keeping an id", () => {
  const plain = {entity_id: "a", location: {lat: 1, lon: 2}};
  assert.equal(normalizeLoaded(plain), plain);
  assert.deepEqual(normalizeLoaded({id: "b", data: {name: "n", location: {lat: 1, lon: 2}}}), {name: "n", location: {lat: 1, lon: 2}, entity_id: "b"});
  assert.equal(normalizeLoaded({entity_id: "c", id: "x", data: {entity_id: "d", location: {lat: 1, lon: 2}}}).entity_id, "d");
  assert.equal(normalizeLoaded({entity_id: "c", data: {location: {lat: 1, lon: 2}}}).entity_id, "c");
  assert.equal(normalizeLoaded({entity_id: "c", id: "x", data: {location: {lat: 1, lon: 2}}}).entity_id, "x");
});

test("an entity without a numeric location is not loaded", () => {
  assert.equal(normalizeLoaded({entity_id: "a"}), null);
  assert.equal(normalizeLoaded({entity_id: "a", location: {lat: "1", lon: 2}}), null);
  assert.equal(normalizeLoaded({entity_id: "a", location: {lat: 1, lon: "2"}}), null);
  assert.equal(normalizeLoaded({entity_id: "a", location: {lat: 1}}), null);
  assert.equal(normalizeLoaded({id: "b", data: {name: "n"}}), null);
  assert.equal(normalizeLoaded({entity_id: "a", data: "text", location: {lat: 1, lon: 2}}).entity_id, "a");
});

test("normalising fills every field the page reads", () => {
  const e = normalizeEntityData({}, NOW);
  assert.deepEqual(e, {
    entity_id: `ENTITY-${NOW}`, location: {lat: 0, lon: 0, altitude_m: 0}, name: `ENTITY-${NOW}`, disposition: "UNKNOWN", threat_level: "NONE",
    velocity: {speed_kts: 0, heading_deg: 0}, ontology: "unknown", distance_nm: 0, bearing_deg: 0, last_update: NOW / 1000,
  });
});

test("normalising keeps what the data has and finds ids and coordinates under their other names", () => {
  assert.equal(normalizeEntityData({id: "x"}, NOW).entity_id, "x");
  assert.equal(normalizeEntityData({entity_id: "e", id: "x"}, NOW).entity_id, "e");
  assert.equal(normalizeEntityData({entity_id: "e", name: "N"}, NOW).name, "N");
  const located = normalizeEntityData({entity_id: "e", lat: "3.5", lon: 4}, NOW);
  assert.deepEqual(located.location, {lat: 3.5, lon: 4, altitude_m: 0});
  assert.deepEqual(normalizeEntityData({entity_id: "e", location: {lat: "1", lon: "2", alt: "7"}}, NOW).location, {lat: 1, lon: 2, alt: "7", altitude_m: 7});
  assert.equal(normalizeEntityData({entity_id: "e", location: {lat: 1, lon: 2, altitude_m: 9, alt: 7}}, NOW).location.altitude_m, 9);
  const full = {entity_id: "e", name: "N", disposition: "HOSTILE", threat_level: "HIGH", velocity: {speed_kts: 5, heading_deg: 6}, ontology: "o", distance_nm: 1, bearing_deg: 2, last_update: 99};
  assert.deepEqual(normalizeEntityData(full, NOW), {...full, location: {lat: 0, lon: 0, altitude_m: 0}});
});

test("normalising shares the location object with its data (the writes land on it too), and nothing else", () => {
  const data = {entity_id: "e", location: {lat: "1", lon: "2"}};
  const e = normalizeEntityData(data, NOW);
  assert.equal(e.location, data.location);
  assert.equal(data.location.lat, 1);
  assert.equal("name" in data, false);
  assert.notEqual(e, data);
});

const sync = (type, id, data, entityType = "RECON_ENTITY") => ({entity_id: id, entity_type: entityType, event_type: type, entity_data: data});

test("an event for something other than a recon entity does nothing", () => {
  assert.equal(planSyncEvent(null, {}, NOW), null);
  assert.equal(planSyncEvent(undefined, {}, NOW), null);
  assert.equal(planSyncEvent(sync("CREATE", "x", {}, "SENSOR"), {}, NOW), null);
  assert.equal(planSyncEvent(sync("CREATE", "x", {}, null), {}, NOW), null);
  assert.equal(planSyncEvent({entity_id: "x", event_type: "CREATE"}, {}, NOW), null);
  assert.equal(planSyncEvent(sync("CREATE", undefined, {}, "SENSOR"), {}, NOW), null);
});

test("a recon entity, an nmap target, and an id that starts AIS- or ENTITY- are all handled", () => {
  for (const [type, id] of [["RECON_ENTITY", "x"], ["NMAP_TARGET", "x"], ["SENSOR", "AIS-1"], ["", "ENTITY-1"]]) {
    assert.equal(planSyncEvent(sync("CREATE", id, {entity_id: id}, type), {}, NOW).kind, "upsert", `${type} ${id}`);
  }
  assert.equal(planSyncEvent(sync("CREATE", "XAIS-1", {}, "SENSOR"), {}, NOW), null);
});

test("CREATE and PREEXISTING keep the normalised data under the event's id, in any case", () => {
  for (const type of ["CREATE", "PREEXISTING", "create", "Preexisting"]) {
    const plan = planSyncEvent(sync(type, "a", {entity_id: "a", name: "N"}), {a: {name: "old", extra: 1}}, NOW);
    assert.equal(plan.kind, "upsert");
    assert.equal(plan.id, "a");
    assert.equal(plan.entity.name, "N");
    assert.equal("extra" in plan.entity, false, "a create replaces what was held");
  }
});

test("the event's id is the key, even when the data's own id differs", () => {
  const plan = planSyncEvent(sync("CREATE", "from-event", {entity_id: "from-data"}), {}, NOW);
  assert.equal(plan.id, "from-event");
  assert.equal(plan.entity.entity_id, "from-data");
});

test("UPDATE merges over the held entity and keeps the event's id; with none held it creates one", () => {
  const held = {entity_id: "a", name: "old", disposition: "HOSTILE", mine: 1, location: {lat: 1, lon: 1, altitude_m: 0}};
  const plan = planSyncEvent(sync("UPDATE", "a", {entity_id: "zzz", name: "new"}), {a: held}, NOW);
  assert.equal(plan.entity.name, "new");
  assert.equal(plan.entity.mine, 1);
  assert.equal(plan.entity.entity_id, "a");
  assert.equal(plan.entity.disposition, "UNKNOWN", "a normalised default overrides the held value: the old merge did the same");
  const created = planSyncEvent(sync("UPDATE", "b", {entity_id: "b", name: "new"}), {}, NOW);
  assert.equal(created.kind, "upsert");
  assert.equal(created.entity.name, "new");
  assert.equal(created.entity.entity_id, "b");
  const elsewhere = planSyncEvent(sync("UPDATE", "b", {entity_id: "other"}), {}, NOW);
  assert.equal(elsewhere.entity.entity_id, "other", "only a merge forces the event's id into the entity; a creation keeps the data's");
});

test("an event with no data creates an entity with made-up fields, and nothing throws", () => {
  const plan = planSyncEvent({entity_id: "a", entity_type: "RECON_ENTITY", event_type: "CREATE"}, {}, NOW);
  assert.equal(plan.id, "a");
  assert.equal(plan.entity.entity_id, `ENTITY-${NOW}`);
  assert.equal(planSyncEvent({entity_id: "a", entity_type: "RECON_ENTITY", event_type: "UPDATE"}, {}, NOW).kind, "upsert");
});

test("an event with no type is an UPDATE (it merges into what is held), and `eventType` is read before `event_type`", () => {
  const merged = planSyncEvent({entity_id: "a", entity_type: "RECON_ENTITY", entity_data: {entity_id: "a", name: "n"}}, {a: {entity_id: "a", mine: 1}}, NOW);
  assert.equal(merged.kind, "upsert");
  assert.equal(merged.entity.mine, 1);
  assert.equal(merged.entity.name, "n");
  const both = {entity_id: "a", entity_type: "RECON_ENTITY", eventType: "DELETE", event_type: "CREATE"};
  assert.equal(planSyncEvent(both, {a: {}}, NOW).kind, "delete");
});

test("DELETE says whether the entity was held", () => {
  assert.deepEqual(planSyncEvent(sync("DELETE", "a", {}), {a: {entity_id: "a"}}, NOW), {id: "a", kind: "delete", existed: true});
  assert.deepEqual(planSyncEvent(sync("delete", "a", {}), {}, NOW), {id: "a", kind: "delete", existed: false});
});

test("an event type it does not handle does nothing", () => {
  assert.equal(planSyncEvent(sync("MOVE", "a", {}), {a: {}}, NOW), null);
  assert.equal(planSyncEvent(sync("", "a", {}), {a: {}}, NOW).kind, "upsert", "an empty type is the UPDATE default");
});

test("only the entities actually held count: an inherited name is not one", () => {
  assert.equal(planSyncEvent(sync("DELETE", "toString", {}), {}, NOW).existed, false);
  const plan = planSyncEvent(sync("UPDATE", "constructor", {name: "n"}), {}, NOW);
  assert.equal(plan.entity.name, "n");
  assert.equal(typeof plan.entity.entity_id, "string");
});

test("a stream message becomes a sync event: CREATE for a new entity, UPDATE for a held one, DELETE when deleted", () => {
  const held = {a: {entity_id: "a"}};
  assert.deepEqual(streamEvent({entity: {entity_id: "b", name: "n"}}, held), {event_type: "CREATE", entity_id: "b", entity_type: "RECON_ENTITY", entity_data: {entity_id: "b", name: "n"}});
  assert.equal(streamEvent({entity: {entity_id: "a"}}, held).event_type, "UPDATE");
  assert.equal(streamEvent({type: "entity_deleted", entity: {entity_id: "a"}}, held).event_type, "DELETE");
  assert.equal(streamEvent({type: "entity_deleted", entity: {entity_id: "zz"}}, held).event_type, "DELETE");
});

test("a stream message may be the entity itself, and may name its own entity type", () => {
  const evt = streamEvent({entity_id: "n", entity_type: "NMAP_TARGET", name: "x"}, {});
  assert.equal(evt.entity_type, "NMAP_TARGET");
  assert.equal(evt.entity_data.name, "x");
  assert.equal(evt.entity_id, "n");
});

test("the greeting and a message with no entity id are skipped", () => {
  assert.equal(streamEvent({type: "connected"}, {}), null);
  assert.equal(streamEvent({type: "connected", entity_id: "x"}, {}), null, "even if the greeting names something");
  assert.equal(streamEvent({entity: {name: "n"}}, {}), null);
  assert.equal(streamEvent({entity: null, name: "n"}, {}), null);
  assert.equal(streamEvent({type: "entity_deleted"}, {}), null);
});

test("a stream message for an entity named like an inherited property is a CREATE", () => {
  assert.equal(streamEvent({entity: {entity_id: "toString"}}, {}).event_type, "CREATE");
  assert.equal(streamEvent({entity: {entity_id: "a"}}, {a: null}).event_type, "CREATE", "a held slot with nothing in it is not an entity");
});

const label = (extra = {}) => mldcLabel({entity_id: "e", name: "N", location: {lat: 36, lon: -86}, ...extra}, NOW);

test("an MLDC label carries where the entity is, its name and id", () => {
  assert.deepEqual([label().id, label().coordinates, label().label], ["e", [-86, 36], "N"]);
  assert.equal(label({name: ""}).label, "e");
});

test("an MLDC label's priority is the threat level's if it has one, else the disposition's, else a half", () => {
  assert.equal(label({threat_level: "CRITICAL", disposition: "NEUTRAL"}).priority, 0.95);
  assert.equal(label({threat_level: "HIGH"}).priority, 0.85);
  assert.equal(label({threat_level: "SUSPICIOUS"}).priority, 0.70);
  assert.equal(label({threat_level: "NONE", disposition: "HOSTILE"}).priority, 0.30);
  assert.equal(label({threat_level: "LOW", disposition: "HOSTILE"}).priority, 0.90);
  assert.equal(label({threat_level: "LOW", disposition: "SUSPICIOUS"}).priority, 0.75);
  assert.equal(label({threat_level: "LOW", disposition: "UNKNOWN"}).priority, 0.50);
  assert.equal(label({threat_level: "LOW", disposition: "FRIENDLY"}).priority, 0.40);
  assert.equal(label({threat_level: "LOW", disposition: "NEUTRAL"}).priority, 0.35);
  assert.equal(label({threat_level: "LOW", disposition: "FRIEND"}).priority, 0.5);
  assert.equal(label().priority, 0.30, "no threat level is NONE");
  assert.equal(label({threat_level: "toString", disposition: "constructor"}).priority, 0.5);
});

test("an MLDC label's trust is the classification confidence if a number, else the confidence, else 0.8", () => {
  assert.equal(label({classification_confidence: 0.9, confidence: 0.1}).trust, 0.9);
  assert.equal(label({classification_confidence: 0, confidence: 0.1}).trust, 0);
  assert.equal(label({classification_confidence: "0.9", confidence: 0.1}).trust, 0.1);
  assert.equal(label({confidence: 0.2}).trust, 0.2);
  assert.equal(label().trust, 0.8);
});

test("an MLDC label's recency is one when just seen or unseen and decays over ten minutes", () => {
  assert.equal(label().recency, 1);
  assert.equal(label({last_seen: new Date(NOW).toISOString()}).recency, 1);
  assert.ok(Math.abs(label({last_seen: new Date(NOW - 600000).toISOString()}).recency - Math.exp(-1)) < 1e-9);
  assert.equal(label({last_seen: new Date(NOW + 60000).toISOString()}).recency, 1, "a time in the future is not older than now");
  assert.ok(Number.isNaN(label({last_seen: "junk"}).recency));
});

test("an MLDC label's RF confidence and frequency come from the entity's fields, with defaults", () => {
  assert.equal(label({rf_confidence: 0.1, signal_confidence: 0.2}).rfConfidence, 0.1);
  assert.equal(label({signal_confidence: 0.2}).rfConfidence, 0.2);
  assert.equal(label({rf_emissions: true}).rfConfidence, 0.85);
  assert.equal(label().rfConfidence, 0.5);
  assert.equal(label({frequency_hz: 5, rf_emissions: {frequency_mhz: 1}}).frequencyHz, 5);
  assert.equal(label({rf_emissions: {frequency_mhz: 433}}).frequencyHz, 433e6);
  assert.equal(label({rf_emissions: true}).frequencyHz, undefined);
  assert.equal(label().frequencyHz, undefined);
});

test("an MLDC label's colour follows the disposition", () => {
  assert.deepEqual(label({disposition: "HOSTILE"}).color, [255, 60, 60]);
  assert.deepEqual(label({disposition: "SUSPICIOUS"}).color, [255, 165, 0]);
  assert.deepEqual(label({disposition: "FRIENDLY"}).color, [0, 220, 100]);
  assert.deepEqual(label({disposition: "FRIEND"}).color, [180, 210, 255]);
  assert.deepEqual(label().color, [180, 210, 255]);
});

test("the running average folds in the newest sample", () => {
  assert.equal(runningAverage(0, 1, 10), 10);
  assert.equal(runningAverage(10, 2, 20), 15);
  assert.equal(runningAverage(15, 3, 30), 20);
});
