using SCYTHE.Core;
using SCYTHE.Global;
using SCYTHE.Optics;
using SCYTHE.RF;
using UnityEngine;

namespace SCYTHE.Presentation
{
    public sealed class MonocleHUD : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;
        [SerializeField] private RFFieldVisualizer fieldVisualizer;
        [SerializeField] private RFFieldSampler fieldSampler;
        [SerializeField] private OpticalDatasetLoader opticalLoader;
        [SerializeField] private ScenarioDirector scenarioDirector;
        [SerializeField] private CesiumGeospatialAdapter geospatialAdapter;
        [SerializeField] private GlobalDatasetManager globalDatasetManager;

        private GUIStyle headingStyle;
        private GUIStyle labelStyle;
        private GUIStyle evidenceStyle;
        private GUIStyle passStyle;
        private GUIStyle failStyle;
        private bool showOpticalFusion = true;

        public void Bind(
            RFSimulationController controller,
            RFFieldVisualizer visualizer,
            RFFieldSampler sampler,
            OpticalDatasetLoader optics,
            ScenarioDirector director,
            CesiumGeospatialAdapter geospatial,
            GlobalDatasetManager globalDatasets)
        {
            simulation = controller;
            fieldVisualizer = visualizer;
            fieldSampler = sampler;
            opticalLoader = optics;
            scenarioDirector = director;
            geospatialAdapter = geospatial;
            globalDatasetManager = globalDatasets;
        }

        private void Update()
        {
            if (Input.GetKeyDown(KeyCode.O))
            {
                showOpticalFusion = !showOpticalFusion;
            }
        }

        private void OnGUI()
        {
            EnsureStyles();
            DrawOpticalFusionLayer();
            DrawLinkPanel();
            DrawGlobalPanel();
            DrawFieldMap();
            DrawSpatialInstrument();
            DrawEmitterRoster();
            DrawOpticalPanel();
            DrawScenarioPanel();
            DrawWorldMarkers();
        }

        private void DrawGlobalPanel()
        {
            if (geospatialAdapter == null || globalDatasetManager == null)
            {
                return;
            }

            const float width = 360f;
            Rect panel = new Rect(538f, 22f, width, 164f);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.94f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;

            EvidenceClass originEvidence;
            try
            {
                originEvidence = EvidenceStyleRouter.Parse(
                    geospatialAdapter.OriginEvidenceClass);
            }
            catch
            {
                originEvidence = EvidenceClass.Illustrative;
            }

            EvidenceStyle originStyle = EvidenceStyleRouter.Get(originEvidence);
            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 10f, width - 32f, 28f),
                geospatialAdapter.IsCesiumBacked
                    ? "CESIUM // WGS84 MONOCLE"
                    : "WGS84 REFERENCE // CESIUM UNAVAILABLE",
                headingStyle);
            Color previousContentColor = GUI.contentColor;
            GUI.contentColor = originStyle.Color;
            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 40f, width - 32f, 22f),
                $"ORIGIN: {originStyle.Label} // {originStyle.Pattern}",
                evidenceStyle);
            GUI.contentColor = previousContentColor;

            if (!globalDatasetManager.HasOperatorPosition)
            {
                GUI.Label(
                    new Rect(panel.x + 16f, panel.y + 65f, width - 32f, 22f),
                    "GEODETIC POSITION: INITIALIZING",
                    labelStyle);
            }
            else
            {
                GeodeticPosition position = globalDatasetManager.OperatorPosition;
                GUI.Label(
                    new Rect(panel.x + 16f, panel.y + 65f, width - 32f, 22f),
                    $"LON {position.LongitudeDegrees:+000.000000;-000.000000;000.000000}°  "
                    + $"LAT {position.LatitudeDegrees:+00.000000;-00.000000;00.000000}°",
                    labelStyle);
                GUI.Label(
                    new Rect(panel.x + 16f, panel.y + 88f, width - 32f, 22f),
                    $"HEIGHT {position.HeightMeters:F2} m WGS84 ELLIPSOID",
                    labelStyle);

                RFTransmitter transmitter = simulation?.Transmitter;
                if (transmitter != null)
                {
                    GlobalFieldSample sample = globalDatasetManager.SampleRFField(
                        position.LatitudeDegrees,
                        position.LongitudeDegrees,
                        position.HeightMeters,
                        globalDatasetManager.CurrentUtc(),
                        transmitter.CarrierFrequencyHz);
                    GUI.Label(
                        new Rect(panel.x + 16f, panel.y + 111f, width - 32f, 22f),
                        $"GLOBAL SAMPLE: {sample.Status}",
                        sample.IsAvailable ? passStyle : labelStyle);
                }
            }

            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 136f, width - 32f, 22f),
                $"DATA: {globalDatasetManager.Status}",
                globalDatasetManager.ValidatedDatasetCount > 0
                    ? passStyle
                    : failStyle);
        }

        private void DrawLinkPanel()
        {
            RFTransmitter transmitter = simulation?.Transmitter;
            RFLinkResult result = simulation?.LastResult;

            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.94f);
            GUI.Box(new Rect(22f, 22f, 500f, 252f), GUIContent.none);
            GUI.color = Color.white;

            GUI.Label(new Rect(42f, 34f, 456f, 34f), "SCYTHE // MULTI-EMITTER LAB", headingStyle);
            GUI.Label(new Rect(42f, 69f, 456f, 24f), "EVIDENCE: SIMULATED", evidenceStyle);
            GUI.Label(
                new Rect(42f, 92f, 456f, 23f),
                "MODEL: COMPLEX BASEBAND + FRIIS + AWGN + LOS LOSS",
                labelStyle);

            if (transmitter == null)
            {
                return;
            }

            GUI.Label(
                new Rect(42f, 118f, 456f, 23f),
                $"ACTIVE: {transmitter.EmitterId} // {transmitter.DisplayName}    "
                + $"{transmitter.CarrierFrequencyHz / 1e6f:F1} MHz",
                labelStyle);

            if (!transmitter.IsRadiating || result == null)
            {
                GUI.Label(new Rect(42f, 146f, 456f, 25f), "LINK STATUS: EMITTER OFFLINE", failStyle);
                return;
            }

            string snr = float.IsPositiveInfinity(result.SnrDb) ? "∞" : $"{result.SnrDb:F1} dB";
            GUI.Label(
                new Rect(42f, 143f, 456f, 23f),
                $"MODULATION: {result.Modulation.ToString().ToUpperInvariant()}    SNR: {snr}",
                labelStyle);
            GUI.Label(
                new Rect(42f, 168f, 456f, 23f),
                $"DISTANCE: {simulation.LinkDistanceMeters:F2} m    BER: {result.BitErrorRate:F4}",
                labelStyle);
            GUI.Label(
                new Rect(42f, 193f, 456f, 23f),
                $"TX: {RFSimulationController.FormatBits(result.InputBits)}",
                labelStyle);
            GUI.Label(
                new Rect(42f, 216f, 456f, 23f),
                $"RX: {RFSimulationController.FormatBits(result.DecodedBits)}",
                labelStyle);
            GUI.Label(
                new Rect(42f, 239f, 456f, 24f),
                result.IsExactMatch
                    ? "LINK CHECK: PASS"
                    : $"LINK CHECK: FAIL ({result.ErrorCount} bit errors)",
                result.IsExactMatch ? passStyle : failStyle);
        }

        private void DrawFieldMap()
        {
            float mapWidth = Mathf.Min(440f, Screen.width * 0.36f);
            float mapHeight = mapWidth * 0.5f;
            Rect mapRect = new Rect(22f, Screen.height - mapHeight - 48f, mapWidth, mapHeight);
            fieldVisualizer?.Draw(mapRect);
            GUI.Label(
                new Rect(mapRect.x + 10f, mapRect.y + 8f, mapRect.width - 20f, 24f),
                "INCOHERENT FREE-SPACE POWER SUM",
                evidenceStyle);
            GUI.Label(
                new Rect(mapRect.x + 10f, mapRect.y + 29f, mapRect.width - 20f, 22f),
                "MAP EXCLUDES OCCLUSION + PHASE INTERFERENCE",
                labelStyle);
            GUI.Label(
                new Rect(mapRect.x, mapRect.yMax + 4f, mapRect.width + 500f, 24f),
                "WASD MOVE  SHIFT SPRINT  SPACE JUMP  CLICK/TAB LOOK  "
                + "T SELECT TX  1–4 MODULATION  O OPTICS  [ ] DEPTH  ESC EXIT",
                labelStyle);
        }

        private void DrawSpatialInstrument()
        {
            if (fieldSampler == null || fieldSampler.Current.Transmitter == null)
            {
                return;
            }

            SpatialRFReading reading = fieldSampler.Current;
            const float width = 350f;
            Rect panel = new Rect(Screen.width - width - 22f, 22f, width, 284f);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.94f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;

            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 12f, width - 36f, 28f),
                "SCYTHE_AR // SPATIAL PROBE",
                headingStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 44f, width - 36f, 22f),
                $"ACTIVE {reading.Transmitter.EmitterId} // EVIDENCE: SIMULATED",
                evidenceStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 69f, width - 36f, 22f),
                $"RANGE       {reading.DistanceMeters,8:F2} m",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 92f, width - 36f, 22f),
                $"BEARING     {reading.BearingDegrees,8:+0.0;-0.0;0.0}°",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 115f, width - 36f, 22f),
                $"POWER       {FormatPower(reading.ReceivedPowerDbm),12}",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 138f, width - 36f, 22f),
                $"RADIAL V    {reading.RadialVelocityMetersPerSecond,8:+0.00;-0.00;0.00} m/s",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 161f, width - 36f, 22f),
                $"DOPPLER     {reading.DopplerHz,8:+0.00;-0.00;0.00} Hz",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 18f, panel.y + 184f, width - 36f, 22f),
                reading.IsOccluded
                    ? $"LOS MODEL   BLOCKED ×{reading.BlockerCount}  −{reading.OcclusionAttenuationDb:F1} dB"
                    : "LOS MODEL   CLEAR  0.0 dB",
                reading.IsOccluded ? failStyle : passStyle);

            DrawSignalBars(
                new Rect(panel.x + 18f, panel.y + 229f, 148f, 30f),
                reading.ReceivedPowerDbm);
            DrawBearingArrow(
                new Vector2(panel.xMax - 67f, panel.yMax - 43f),
                reading.BearingDegrees);
        }

        private void DrawEmitterRoster()
        {
            if (fieldSampler == null || fieldSampler.CurrentReadings.Count == 0)
            {
                return;
            }

            const float width = 350f;
            float height = 42f + fieldSampler.CurrentReadings.Count * 24f;
            Rect panel = new Rect(Screen.width - width - 22f, 318f, width, height);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.92f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;
            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 8f, width - 32f, 24f),
                "EMITTER ENVIRONMENT",
                evidenceStyle);

            for (int index = 0; index < fieldSampler.CurrentReadings.Count; index++)
            {
                SpatialRFReading reading = fieldSampler.CurrentReadings[index];
                string prefix = reading.IsActive ? "▶" : " ";
                string status = reading.IsRadiating
                    ? $"{FormatPower(reading.ReceivedPowerDbm),10}  {reading.DistanceMeters,5:F1}m"
                    : "OFFLINE";
                GUI.Label(
                    new Rect(panel.x + 16f, panel.y + 34f + index * 24f, width - 32f, 22f),
                    $"{prefix} {reading.Transmitter.EmitterId,-5} "
                    + $"{reading.Transmitter.Modulation.ToString().ToUpperInvariant(),-4} {status}",
                    reading.IsActive ? passStyle : labelStyle);
            }
        }

        private void DrawScenarioPanel()
        {
            if (scenarioDirector == null)
            {
                return;
            }

            const float width = 350f;
            Rect panel = new Rect(Screen.width - width - 22f, 448f, width, 84f);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.9f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;
            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 8f, width - 32f, 22f),
                $"SCENARIO t={SimulationClock.TimeSeconds:F1}s  "
                + $"{scenarioDirector.ExecutedEventCount}/{scenarioDirector.TotalEventCount}",
                evidenceStyle);
            string next = float.IsPositiveInfinity(scenarioDirector.SecondsUntilNextEvent)
                ? "NEXT: COMPLETE"
                : $"NEXT EVENT IN {scenarioDirector.SecondsUntilNextEvent:F1}s";
            GUI.Label(new Rect(panel.x + 16f, panel.y + 32f, width - 32f, 22f), next, labelStyle);
            GUI.Label(
                new Rect(panel.x + 16f, panel.y + 54f, width - 32f, 22f),
                $"LAST: {scenarioDirector.LastEventLabel}",
                labelStyle);
        }

        private void DrawOpticalPanel()
        {
            if (opticalLoader == null)
            {
                return;
            }

            float width = 370f;
            float height = opticalLoader.IsLoaded ? 196f : 76f;
            Rect panel = new Rect(
                Mathf.Max(480f, Screen.width * 0.5f - width * 0.5f),
                Screen.height - height - 48f,
                width,
                height);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.9f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;
            GUI.Label(
                new Rect(panel.x + 14f, panel.y + 8f, width - 28f, 24f),
                "OPTICAL FUSION // DATASET SPACE // UNREGISTERED",
                evidenceStyle);
            GUI.Label(
                new Rect(panel.x + 14f, panel.y + 32f, width - 28f, 22f),
                opticalLoader.Status,
                opticalLoader.IsLoaded ? passStyle : labelStyle);

            if (!opticalLoader.IsLoaded)
            {
                return;
            }

            GUI.color = Color.white;
            GUI.DrawTexture(
                new Rect(panel.x + 14f, panel.y + 62f, 164f, 110f),
                opticalLoader.IntensityTexture,
                ScaleMode.ScaleToFit,
                false);
            GUI.DrawTexture(
                new Rect(panel.x + 192f, panel.y + 62f, 164f, 110f),
                opticalLoader.SelectedDepthTexture != null
                    ? opticalLoader.SelectedDepthTexture
                    : opticalLoader.PhaseTexture,
                ScaleMode.ScaleToFit,
                false);
            GUI.Label(
                new Rect(panel.x + 14f, panel.y + 172f, 164f, 20f),
                "INTENSITY",
                labelStyle);
            GUI.Label(
                new Rect(panel.x + 192f, panel.y + 172f, 164f, 20f),
                opticalLoader.SelectedDepthTexture != null
                    ? $"DEPTH {opticalLoader.SelectedDepthPlane + 1}/{opticalLoader.DepthPlaneCount}"
                    : "PHASE (RAD)",
                labelStyle);
        }

        private void DrawOpticalFusionLayer()
        {
            if (!showOpticalFusion || opticalLoader == null || !opticalLoader.IsLoaded)
            {
                return;
            }

            GUI.color = new Color(1f, 1f, 1f, 0.07f);
            GUI.DrawTexture(
                new Rect(0f, 0f, Screen.width, Screen.height),
                opticalLoader.IntensityTexture,
                ScaleMode.ScaleAndCrop,
                false);
            GUI.color = Color.white;
        }

        private void DrawWorldMarkers()
        {
            if (simulation == null)
            {
                return;
            }

            for (int index = 0; index < simulation.Transmitters.Count; index++)
            {
                RFTransmitter transmitter = simulation.Transmitters[index];
                Color color = transmitter == simulation.Transmitter
                    ? new Color(1f, 0.28f, 0.08f)
                    : new Color(1f, 0.75f, 0.1f);
                if (!transmitter.IsRadiating)
                {
                    color = Color.gray;
                }

                DrawWorldMarker(
                    transmitter.transform,
                    transmitter.EmitterId,
                    color);
            }
        }

        private static string FormatPower(float receivedPowerDbm)
        {
            return float.IsNegativeInfinity(receivedPowerDbm)
                ? "OFFLINE"
                : $"{receivedPowerDbm:F1} dBm";
        }

        private static void DrawSignalBars(Rect area, float receivedPowerDbm)
        {
            float normalized = float.IsNegativeInfinity(receivedPowerDbm)
                ? 0f
                : Mathf.InverseLerp(-95f, -20f, receivedPowerDbm);
            int active = Mathf.Clamp(Mathf.CeilToInt(normalized * 5f), 0, 5);
            for (int index = 0; index < 5; index++)
            {
                float height = 7f + index * 4.5f;
                Rect bar = new Rect(area.x + index * 25f, area.yMax - height, 17f, height);
                GUI.color = index < active
                    ? new Color(0.12f, 1f, 0.62f)
                    : new Color(0.12f, 0.25f, 0.28f);
                GUI.Box(bar, GUIContent.none);
            }
            GUI.color = Color.white;
        }

        private static void DrawBearingArrow(Vector2 center, float bearingDegrees)
        {
            Matrix4x4 previous = GUI.matrix;
            GUIUtility.RotateAroundPivot(bearingDegrees, center);
            GUI.color = new Color(0.15f, 1f, 0.7f);
            GUI.Box(new Rect(center.x - 2f, center.y - 27f, 4f, 44f), GUIContent.none);
            GUI.Box(new Rect(center.x - 8f, center.y - 27f, 16f, 7f), GUIContent.none);
            GUI.color = Color.white;
            GUI.matrix = previous;
        }

        private void DrawWorldMarker(Transform target, string label, Color color)
        {
            Camera camera = Camera.main;
            if (target == null || camera == null)
            {
                return;
            }

            Vector3 screen = camera.WorldToScreenPoint(target.position + Vector3.up * 1.5f);
            if (screen.z <= 0f)
            {
                return;
            }

            float y = Screen.height - screen.y;
            GUI.color = color;
            GUI.Box(new Rect(screen.x - 6f, y - 6f, 12f, 12f), GUIContent.none);
            GUI.color = Color.white;
            GUI.Label(new Rect(screen.x + 10f, y - 10f, 80f, 24f), label, labelStyle);
        }

        private void EnsureStyles()
        {
            if (headingStyle != null)
            {
                return;
            }

            headingStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 21,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.15f, 1f, 0.7f) },
            };
            labelStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 13,
                normal = { textColor = new Color(0.75f, 0.94f, 0.9f) },
            };
            evidenceStyle = new GUIStyle(labelStyle)
            {
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.26f, 0.72f, 1f) },
            };
            passStyle = new GUIStyle(labelStyle)
            {
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.15f, 1f, 0.52f) },
            };
            failStyle = new GUIStyle(labelStyle)
            {
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(1f, 0.42f, 0.12f) },
            };
        }
    }
}
