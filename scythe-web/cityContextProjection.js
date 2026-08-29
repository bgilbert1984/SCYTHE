function finite(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }

function cityKey(geo) {
  const city = String(geo?.city ?? "").trim(); if (!city) return null;
  const region = String(geo?.region ?? "").trim();
  const country = String(geo?.country ?? geo?.countryCode ?? "").trim();
  return {key: [city, region, country].join("|"), city, region, country};
}

function stableId(value) {
  let hash = 2166136261;
  for (const char of value) { hash ^= char.charCodeAt(0); hash = Math.imul(hash, 16777619); }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/**
 * Add bounded, display-only city context to a graph snapshot. These nodes are
 * derived from INFERRED GeoIP sidecars and are intentionally non-selectable by
 * server GraphOps workflows.
 */
export function projectCityContext(graph, {cityLimit = 24, membershipLimit = 200} = {}) {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const groups = new Map();
  for (const node of nodes) {
    const geo = node?.enrichment?.geo; const identity = cityKey(geo);
    const latitude = finite(geo?.latitude); const longitude = finite(geo?.longitude);
    if (!identity || latitude === null || longitude === null) continue;
    if (!groups.has(identity.key)) groups.set(identity.key, {...identity, hosts:[], latitudes:[], longitudes:[]});
    const group = groups.get(identity.key); group.hosts.push(node.id);
    group.latitudes.push(latitude); group.longitudes.push(longitude);
  }
  const selected = [...groups.values()].sort((a,b) => b.hosts.length-a.hosts.length || a.key.localeCompare(b.key))
    .slice(0, Math.max(0, Math.min(64, cityLimit)));
  const cityNodes = []; const membershipEdges = [];
  for (const group of selected) {
    const id = `city:${stableId(group.key)}`;
    const latitude = group.latitudes.reduce((sum,value)=>sum+value,0)/group.latitudes.length;
    const longitude = group.longitudes.reduce((sum,value)=>sum+value,0)/group.longitudes.length;
    cityNodes.push({id, kind:"geographic_city_context", evidenceClass:"INFERRED", position:null,
      labels:{name:group.city,city:group.city,region:group.region,country:group.country,
        host_count:String(group.hosts.length)},
      enrichment:{scope:"GEOGRAPHIC_CONTEXT",geo:{city:group.city,region:group.region,
        country:group.country,latitude,longitude,evidenceClass:"INFERRED",
        authority:"DERIVED_FROM_HOST_GEOIP_ESTIMATES"}},
      display:{selectionPurpose:"GEOGRAPHIC_CONTEXT",activityScore:Math.log1p(group.hosts.length),
        displayDerived:true,selectionDisabled:true,memberIds:group.hosts.slice(0,20),
        membersOmitted:Math.max(0,group.hosts.length-20)}});
    for (const hostId of group.hosts) {
      if (membershipEdges.length >= membershipLimit) break;
      membershipEdges.push({id:`city-membership:${stableId(`${id}|${hostId}`)}`,
        kind:"geoip_city_membership",nodes:[id,hostId],evidenceClass:"INFERRED",
        labels:{relation:"GEOIP_CITY_CONTEXT"},display:{directional:false,displayDerived:true,
          selectionDisabled:true}});
    }
  }
  const sensor = graph?.rfSensorContext;
  const sensorId = String(sensor?.sensorId ?? "").slice(0,128);
  const latitude = finite(sensor?.latitude); const longitude = finite(sensor?.longitude);
  const sensorNodes = sensorId ? [{id:`sensor:${sensorId}`,kind:"rf_receiver_sensor",evidenceClass:"OBSERVED",
    position:null, observedAt:sensor.latestFrameAt ?? sensor.capturedAt ?? null,
    labels:{name:sensorId,receiver_model:sensor.model ?? "Nooelec NESDR SMArt",
      bridge_state:String(sensor.bridgeState ?? "unknown").toUpperCase(),
      iq_connected:String(Boolean(sensor.iqConnected)),center_frequency_hz:String(sensor.centerFrequencyHz ?? ""),
      sample_rate_hz:String(sensor.sampleRateHz ?? "")},
    metadata:{capture_owner:sensor.captureOwner ?? "unknown",raw_iq_exposed:false,
      latest_frame_at:sensor.latestFrameAt ?? null,device_presence:"CONFIGURED_NOT_USB_ATTESTED"},
    enrichment:{scope:"LOCAL_RF_SENSOR",geo:latitude !== null && longitude !== null ? {
      latitude,longitude,uncertaintyRadiusKm:Math.max(0,finite(sensor.accuracyMeters) ?? 0)/1000,
      evidenceClass:sensor.locationEvidenceClass ?? "MEASURED",
      authority:sensor.locationAuthority ?? "BROWSER_GEOLOCATION_WITH_OPERATOR_CONSENT"}:null},
    display:{selectionPurpose:"RF_SENSOR_CONTEXT",displayDerived:true,selectionDisabled:false,
      interactionEvent:"scythe-web:rf-sensor-selection"}}] : [];
  return {...graph, nodes:[...nodes,...cityNodes,...sensorNodes], edges:[...(graph?.edges ?? []),...membershipEdges],
    cityContext:{nodeCount:cityNodes.length,edgeCount:membershipEdges.length,
      authority:"INFERRED_GEOIP_DISPLAY_CONTEXT",bounded:true,cityLimit,membershipLimit},
    rfSensorDisplayContext:{nodeCount:sensorNodes.length,edgeCount:0,
      authority:"RF_BRIDGE_RUNTIME_STATUS_PLUS_CONSENTED_BROWSER_LOCATION",bounded:true}};
}
