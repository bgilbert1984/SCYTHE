/**
 * ADS-B aircraft as a workspace tool (the pilot for the lifecycle contract in lifecycle.js).
 *
 * This is the behaviour that used to sit inside AISStreamManager.init and handleAircraftUpdate, moved behind
 * activate/deactivate: while active it listens for `adsb_update` and `adsb_snapshot` on the realtime socket and seeds from
 * `/api/adsb/tracks`; when deactivated it stops listening, drops late fetch results, and removes every aircraft it drew.
 *
 * QUARANTINE: aircraft are viz content shaped as recon entities so the existing renderer draws them; never measurement
 * evidence.
 *
 * Services it needs (see `requires`):
 *   entityMarkers   {upsert(entity), remove(entityId)}  the renderer
 *   realtimeSocket  () => socket | null                  the socket.io client, if connected yet
 *   fetchJson       (url) => Promise<json>               REST with the page's instance-relative rewriting
 */

export const ADSB_ID = "adsb";
export const TRACKS_URL = "/api/adsb/tracks";
const FT_TO_M = 0.3048;

/** A recon entity for one track, or null when the track cannot be placed. */
export function trackToEntity(track) {
  if (!track || !track.icao) return null;
  const lat = Number(track.lat), lon = Number(track.lon);
  if (track.lat == null || track.lon == null || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return {
    entity_id: `adsb_${track.icao}`,
    name: track.callsign || track.icao,
    location: {lat, lon, altitude_m: (Number(track.alt_ft) || 0) * FT_TO_M},
    ontology: ["aircraft", "ads-b", "safari"],
    disposition: "NEUTRAL",
    threat_level: "NONE",   // the renderer's description reads this; a track carries no threat assessment
    source: "adsb-live",
  };
}

export function createAdsbPlugin({log = console} = {}) {
  return {
    id: ADSB_ID,
    title: "ADS-B",
    requires: ["entityMarkers", "realtimeSocket", "fetchJson"],
    activate(ctx) {
      const sink = ctx.services.get("entityMarkers");
      const socket = ctx.services.get("realtimeSocket")();
      const fetchJson = ctx.services.get("fetchJson");
      const drawn = new Set();

      const ingest = ctx.guard((track) => {
        const entity = trackToEntity(track);
        if (!entity) return;
        drawn.add(entity.entity_id);
        sink.upsert(entity);
      });
      const onSnapshot = (data) => { for (const track of (data && data.tracks) || []) ingest(track); };

      ctx.onCleanup(() => { for (const id of drawn) sink.remove(id); drawn.clear(); });
      if (socket) { ctx.listen(socket, "adsb_update", ingest); ctx.listen(socket, "adsb_snapshot", onSnapshot); }
      else log.debug?.("[ADS-B] no realtime socket yet; seeding from REST only");

      Promise.resolve().then(() => fetchJson(TRACKS_URL))
        .then(ctx.guard((body) => { for (const track of (body && body.tracks) || []) ingest(track); }))
        .catch((error) => log.debug?.("[ADS-B] tracks seed failed:", error));
    },
  };
}
