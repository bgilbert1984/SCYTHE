/**
 * RECEIVE PRESETS — US region.
 *
 * These are places the receiver can be pointed. They are NOT "authorized bands":
 * a receiver's ability to tune somewhere says nothing about transmission
 * authorization or the legal status of handling what is received. The label is
 * deliberate and is asserted in tests.
 */

export const REGION = "US";

export const RECEIVE_PRESETS = Object.freeze([
  Object.freeze({label: "FM BROADCAST", startHz: 88e6, endHz: 108e6, mode: "WFM",
                 note: "WIDEBAND BROADCAST SPAN"}),
  Object.freeze({label: "NOAA WEATHER", mode: "NFM",
                 channelsHz: Object.freeze([162.400e6, 162.425e6, 162.450e6, 162.475e6,
                                            162.500e6, 162.525e6, 162.550e6]),
                 note: "SEVEN DISCRETE CHANNELS, NOT A CONTINUOUS SPAN"}),
  Object.freeze({label: "ISM 433", centerHz: 433.920e6, mode: "RAW",
                 note: "SHARED UNLICENSED SPECTRUM"}),
  Object.freeze({label: "ISM 915", centerHz: 915e6, mode: "RAW",
                 note: "SHARED UNLICENSED SPECTRUM"}),
  Object.freeze({label: "ADS-B", centerHz: 1090e6, mode: "RAW",
                 note: "ABOVE THE CONFIGURED 2.048 MS/s SPAN; ONE CENTRE ONLY"}),
]);

export const PRESET_BOUNDARY =
  "RECEIVE PRESETS DESCRIBE WHERE THIS RECEIVER CAN BE POINTED. "
  + "TUNABILITY IS NOT TRANSMISSION AUTHORIZATION.";

/** Centre frequencies a preset implies, given the span one capture can observe. */
export function presetCentres(preset, sampleRateHz) {
  const span = Number(sampleRateHz);
  if (Array.isArray(preset?.channelsHz)) return [...preset.channelsHz];
  if (Number.isFinite(preset?.centerHz)) return [preset.centerHz];
  const start = Number(preset?.startHz); const end = Number(preset?.endHz);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
  if (!Number.isFinite(span) || span <= 0) return [];
  // One instantaneous span at a time: a wide preset needs several visits.
  const centres = [];
  for (let centre = start + span / 2; centre - span / 2 < end; centre += span) {
    centres.push(Math.round(centre));
  }
  return centres;
}
