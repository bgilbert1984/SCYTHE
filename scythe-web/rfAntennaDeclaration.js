/**
 * Antenna declaration for the NESDR SMArt v5.
 *
 * THE ANTENNA CANNOT BE AUTO-DETECTED. This is a property of the hardware, not
 * a gap in this software, and the reasons are enumerated in NO_AUTODETECT below
 * so the question resolves in the interface rather than being asked again.
 *
 * What the receiver can honestly do is:
 *   1. record what the OPERATOR declares is attached  (OPERATOR_DECLARED)
 *   2. carry the vendor's own description of that part (VENDOR_DECLARED)
 *   3. test whether the antenna PORT is delivering RF energy at all (MEASURED)
 *
 * (3) is a corroboration, never an identification. Two different masts that both
 * pass energy at the tuned frequency are indistinguishable to a receive-only
 * path, so corroboration can support or contradict a declaration but can never
 * produce one.
 */

export const AUTHORITY = Object.freeze({
  OPERATOR: "OPERATOR_DECLARED",
  VENDOR: "VENDOR_DECLARED",
  MEASURED: "MEASURED",
  UNDECLARED: "UNDECLARED",
});

/**
 * Why no amount of software can read the antenna off this receiver. Each entry
 * is a physical fact about the signal path, ordered from connector outward.
 */
export const NO_AUTODETECT = Object.freeze({
  possible: false,
  headline: "ANTENNA AUTO-DETECTION // NOT PHYSICALLY AVAILABLE ON THIS RECEIVER",
  reasons: Object.freeze([
    "SMA IS A TWO-CONDUCTOR COAXIAL INTERFACE — CENTRE AND SHIELD. IT CARRIES NO " +
      "IDENTITY PIN, NO EEPROM, NO 1-WIRE AND NO I2C FOR A PART TO ANNOUNCE ITSELF.",
    "NO BIAS TEE IS FITTED TO THE SMArt v5, SO THERE IS NO DC PATH TO THE ANTENNA " +
      "PORT AND NO DC LOAD TO MEASURE. ACTIVE-VERSUS-PASSIVE CANNOT BE SENSED.",
    "THERE IS NO DIRECTIONAL COUPLER OR VSWR BRIDGE IN THE PATH. WITHOUT REFLECTED " +
      "POWER THERE IS NO RETURN LOSS, AND WITHOUT RETURN LOSS THERE IS NO RESONANCE " +
      "ESTIMATE. THIS IS THE DECISIVE LIMIT: THE PATH IS RECEIVE-ONLY.",
    "THE USB DESCRIPTORS IDENTIFY THE RTL2832U, THE R820T2 TUNER AND THE DONGLE " +
      "SERIAL. THEY DESCRIBE NOTHING BEYOND THE CONNECTOR.",
    "THE THREE BUNDLED MASTS ALL PASS ENERGY AT OVERLAPPING FREQUENCIES, SO EVEN A " +
      "PERFECT SPECTRUM MEASUREMENT WOULD NOT DISCRIMINATE BETWEEN THEM.",
  ]),
  instead: "DECLARE THE ANTENNA. THE RECEIVER WILL THEN TEST WHETHER THE PORT IS " +
    "RESPONSIVE AND REPORT AGREEMENT OR CONTRADICTION — NOT AN IDENTIFICATION.",
});

/**
 * The masts shipped in the Nooelec NESDR SMArt v5 bundle, as the vendor
 * describes them. Where the vendor publishes no resonance, resonanceHz is null
 * and stays null: "UHF antenna mast (fixed frequency)" names a band and withholds
 * the number, which is an omission to preserve, not a gap to fill in.
 */
export const BUNDLE_ANTENNAS = Object.freeze([
  Object.freeze({
    id: "nesdr-smart-telescopic",
    label: "TELESCOPIC MAST",
    vendorDescription: "Telescopic antenna mast (variable frequency)",
    connector: "SMA MALE",
    resonanceHz: null,
    resonanceAuthority: AUTHORITY.UNDECLARED,
    resonanceNote: "VENDOR STATES 'VARIABLE FREQUENCY' AND PUBLISHES NO RANGE. " +
      "RESONANCE DEPENDS ON THE EXTENSION THE OPERATOR SET AND IS NOT REPORTED BY ANY PART.",
    lengthMm: null,
    adjustable: true,
  }),
  Object.freeze({
    id: "nesdr-smart-433-ism",
    label: "433 MHz ISM MAST",
    vendorDescription: "433MHz (ISM) antenna mast (fixed frequency)",
    connector: "SMA MALE",
    resonanceHz: 433e6,
    resonanceAuthority: AUTHORITY.VENDOR,
    resonanceNote: "VENDOR-LABELLED CENTRE. NOT A MEASURED RESONANCE OF THIS PART.",
    lengthMm: null,
    adjustable: false,
  }),
  Object.freeze({
    id: "nesdr-smart-uhf",
    label: "UHF MAST",
    vendorDescription: "UHF antenna mast (fixed frequency)",
    connector: "SMA MALE",
    resonanceHz: null,
    resonanceAuthority: AUTHORITY.UNDECLARED,
    resonanceNote: "VENDOR STATES 'FIXED FREQUENCY' BUT PUBLISHES NO VALUE. THE BAND " +
      "IS NAMED; THE FREQUENCY IS NOT DECLARED.",
    lengthMm: null,
    adjustable: false,
  }),
  Object.freeze({
    id: "no-antenna",
    label: "NO ANTENNA / PORT TERMINATED",
    vendorDescription: "Nothing attached, or a 50 Ω termination on the SMA port",
    connector: "NONE",
    resonanceHz: null,
    resonanceAuthority: AUTHORITY.UNDECLARED,
    resonanceNote: "A DELIBERATE NULL DECLARATION. SPECTRUM OBSERVED IN THIS STATE IS " +
      "RECEIVER AND ENVIRONMENT NOISE, NOT AN ANTENNA MEASUREMENT.",
    lengthMm: null,
    adjustable: false,
  }),
  Object.freeze({
    id: "other",
    label: "OTHER (OPERATOR DESCRIBES)",
    vendorDescription: "An antenna outside the bundle, described by the operator",
    connector: "OPERATOR_DECLARED",
    resonanceHz: null,
    resonanceAuthority: AUTHORITY.UNDECLARED,
    resonanceNote: "OPERATOR-SUPPLIED DESCRIPTION. NO VENDOR RECORD BACKS IT.",
    lengthMm: null,
    adjustable: false,
  }),
]);

/**
 * The bundle's feedline is a separate declaration because it is a separate part
 * with its own loss. A mast on 2 m of RG58 is not the same signal chain as the
 * same mast screwed directly onto the dongle.
 */
export const FEEDLINES = Object.freeze([
  Object.freeze({
    // The default, and deliberately not "direct": nothing in a receive-only path
    // can tell a direct connection from 2 m of RG58, so defaulting to "direct"
    // would print configuration convenience as physical evidence.
    id: "undeclared",
    label: "FEEDLINE UNDECLARED",
    vendorDescription: "The operator has not stated how the antenna is connected",
    lengthM: null,
    lossAuthority: AUTHORITY.UNDECLARED,
  }),
  Object.freeze({
    id: "direct",
    label: "DIRECT TO SMA",
    vendorDescription: "Mast connected directly to the receiver, no feedline",
    lengthM: 0,
    lossAuthority: AUTHORITY.UNDECLARED,
  }),
  Object.freeze({
    id: "nesdr-magnetic-base-rg58-2m",
    label: "MAGNETIC BASE · 2 m RG58",
    vendorDescription: "Antenna base w/ 2m RG58 cable, magnetic mount",
    lengthM: 2,
    lossAuthority: AUTHORITY.UNDECLARED,
  }),
]);

const finite = (value) => {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    return null;
  }
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
};

export const antennaById = (id) => BUNDLE_ANTENNAS.find((entry) => entry.id === id) ?? null;
export const feedlineById = (id) => FEEDLINES.find((entry) => entry.id === id) ?? null;

/**
 * Build a declaration. Every field is OPERATOR_DECLARED: the operator is the
 * only instrument that can see the connector.
 */
export function declareAntenna({antennaId, feedlineId = "undeclared", extensionMm = null,
                                note = "", declaredAt = Date.now() / 1000} = {}) {
  const antenna = antennaById(antennaId);
  if (!antenna) {
    return {valid: false, reason: "NO ANTENNA SELECTED — DECLARATION REFUSED", declaration: null};
  }
  const feedline = feedlineById(feedlineId);
  if (!feedline) {
    return {valid: false, reason: "UNKNOWN FEEDLINE — DECLARATION REFUSED", declaration: null};
  }
  const extension = finite(extensionMm);
  if (extension !== null && (extension <= 0 || extension > 2000)) {
    return {valid: false, reason: "EXTENSION MUST BE BETWEEN 0 AND 2000 mm", declaration: null};
  }
  // A telescopic mast's resonance follows its extension, and only the operator
  // can see how far it is pulled out. An unextended declaration stays undeclared
  // rather than adopting the vendor's silent default.
  const quarterWaveHz = antenna.adjustable && extension !== null
    ? Math.round(299_792_458 / (4 * (extension / 1000)))
    : null;
  return {
    valid: true,
    reason: null,
    declaration: Object.freeze({
      antennaId: antenna.id,
      label: antenna.label,
      vendorDescription: antenna.vendorDescription,
      connector: antenna.connector,
      feedlineId: feedline.id,
      feedlineLabel: feedline.label,
      feedlineLengthM: feedline.lengthM,
      extensionMm: extension,
      quarterWaveHz,
      quarterWaveAuthority: quarterWaveHz === null ? AUTHORITY.UNDECLARED : "DERIVED_INFERENCE",
      quarterWaveNote: quarterWaveHz === null
        ? "NO EXTENSION DECLARED — NO RESONANCE DERIVED"
        : "IDEAL FREE-SPACE QUARTER WAVE FROM THE DECLARED LENGTH. IGNORES GROUND PLANE, " +
          "END EFFECT AND MOUNTING. IT IS AN ESTIMATE FROM A DECLARATION, NOT A MEASUREMENT.",
      resonanceHz: antenna.resonanceHz,
      resonanceAuthority: antenna.resonanceAuthority,
      resonanceNote: antenna.resonanceNote,
      note: String(note ?? "").slice(0, 256),
      declaredAt: finite(declaredAt),
      authority: AUTHORITY.OPERATOR,
      autoDetected: false,
      detectionNote: NO_AUTODETECT.headline,
    }),
  };
}

export const CORROBORATION = Object.freeze({
  RESPONSIVE: "PORT RESPONSIVE",
  QUIET: "PORT QUIET",
  INSUFFICIENT: "INSUFFICIENT EVIDENCE",
});

/** Energy above the floor that counts as the port delivering signal. */
export const RESPONSIVE_MARGIN_DB = 10;

/**
 * Test the antenna PORT against observed spectrum.
 *
 * This answers one question — is RF energy arriving at the ADC above the noise
 * floor? — and refuses the question it cannot answer, which is which antenna
 * delivered it. A responsive port corroborates that *something* is connected. A
 * quiet port is compatible with a disconnected antenna AND with a genuinely
 * quiet band, so it never becomes an accusation.
 */
export function corroborateAntenna(declaration, {frame = null, centerHz = null} = {}) {
  const discrimination =
    "CANNOT DISCRIMINATE BETWEEN THE BUNDLED MASTS — A RECEIVE-ONLY PATH HAS NO REFLECTOMETER";
  if (!declaration) {
    return {outcome: CORROBORATION.INSUFFICIENT, agreement: null, discrimination,
            reason: "NO ANTENNA DECLARED — THERE IS NOTHING TO CORROBORATE",
            authority: AUTHORITY.UNDECLARED};
  }
  if (!frame?.available) {
    return {outcome: CORROBORATION.INSUFFICIENT, agreement: null, discrimination,
            reason: "NO SPECTRUM FRAME RETAINED — THE PORT WAS NOT OBSERVED",
            authority: AUTHORITY.UNDECLARED};
  }
  const floor = finite(frame.noiseFloorDbfs);
  const peak = finite(frame.peakDbfs);
  if (floor === null || peak === null) {
    return {outcome: CORROBORATION.INSUFFICIENT, agreement: null, discrimination,
            reason: "FRAME REPORTS NO FLOOR OR PEAK LEVEL",
            authority: AUTHORITY.UNDECLARED};
  }
  const marginDb = peak - floor;
  const responsive = marginDb >= RESPONSIVE_MARGIN_DB;
  const terminated = declaration.antennaId === "no-antenna";

  // Agreement is only claimable where the declaration makes a testable claim.
  // "No antenna" predicts a quiet port; a responsive port contradicts it. Every
  // other declaration predicts nothing specific enough to be contradicted.
  let agreement = null;
  let reason;
  if (terminated && responsive) {
    agreement = "CONTRADICTS DECLARATION";
    reason = `PORT DECLARED TERMINATED, BUT A CARRIER SITS ${marginDb.toFixed(1)} dB ABOVE ` +
      "THE FLOOR. SOMETHING IS COUPLING ENERGY INTO THE RECEIVER.";
  } else if (terminated) {
    agreement = "CONSISTENT WITH DECLARATION";
    reason = `NO ENERGY ABOVE ${RESPONSIVE_MARGIN_DB} dB OVER THE FLOOR, AS A TERMINATED ` +
      "PORT PREDICTS.";
  } else if (responsive) {
    agreement = "CONSISTENT WITH A CONNECTED ANTENNA";
    reason = `A CARRIER SITS ${marginDb.toFixed(1)} dB ABOVE THE FLOOR, SO THE PORT IS ` +
      "DELIVERING RF. THIS DOES NOT IDENTIFY WHICH ANTENNA DELIVERED IT.";
  } else {
    agreement = null;
    reason = `NOTHING EXCEEDS THE FLOOR BY ${RESPONSIVE_MARGIN_DB} dB. THIS IS COMPATIBLE ` +
      "WITH A DISCONNECTED ANTENNA AND EQUALLY WITH A QUIET BAND. NO CONCLUSION IS DRAWN.";
  }
  return {
    outcome: responsive ? CORROBORATION.RESPONSIVE : CORROBORATION.QUIET,
    marginDb, agreement, reason, discrimination,
    observedAtHz: finite(centerHz),
    authority: AUTHORITY.MEASURED,
    identifiesAntenna: false,
  };
}

/** The hardware-health row for the antenna, declared or not. */
export function antennaHealthRow(declaration) {
  if (!declaration) {
    return {label: "ANTENNA", value: "UNDECLARED",
            detail: "OPERATOR HAS NOT DECLARED AN ANTENNA · NOT AUTO-DETECTABLE ON THIS RECEIVER"};
  }
  const parts = [declaration.label];
  if (declaration.feedlineId !== "direct") parts.push(declaration.feedlineLabel);
  if (declaration.extensionMm !== null) parts.push(`${declaration.extensionMm} mm EXTENDED`);
  return {label: "ANTENNA", value: parts.join(" · "),
          detail: `${AUTHORITY.OPERATOR} · NOT MEASURED BY THE RECEIVER`};
}
