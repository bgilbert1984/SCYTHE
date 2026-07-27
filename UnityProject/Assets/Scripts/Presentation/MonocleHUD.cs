using SCYTHE.Core;
using SCYTHE.RF;
using UnityEngine;

namespace SCYTHE.Presentation
{
    public sealed class MonocleHUD : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;
        [SerializeField] private RFFieldVisualizer fieldVisualizer;
        [SerializeField] private RFFieldSampler fieldSampler;

        private GUIStyle headingStyle;
        private GUIStyle labelStyle;
        private GUIStyle evidenceStyle;
        private GUIStyle passStyle;
        private GUIStyle failStyle;

        public void Bind(
            RFSimulationController controller,
            RFFieldVisualizer visualizer,
            RFFieldSampler sampler)
        {
            simulation = controller;
            fieldVisualizer = visualizer;
            fieldSampler = sampler;
        }

        private void OnGUI()
        {
            EnsureStyles();
            RFLinkResult result = simulation?.LastResult;

            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.94f);
            GUI.Box(new Rect(22f, 22f, 476f, 236f), GUIContent.none);
            GUI.color = Color.white;

            GUI.Label(new Rect(42f, 34f, 430f, 34f), "SCYTHE // RF LINK LAB", headingStyle);
            GUI.Label(new Rect(42f, 69f, 430f, 24f), "EVIDENCE: SIMULATED", evidenceStyle);
            GUI.Label(new Rect(42f, 92f, 430f, 23f), "MODEL: COMPLEX BASEBAND + FRIIS + AWGN", labelStyle);

            if (result != null)
            {
                string snr = float.IsPositiveInfinity(result.SnrDb) ? "∞" : $"{result.SnrDb:F1} dB";
                GUI.Label(new Rect(42f, 118f, 430f, 23f), $"MODULATION: {result.Modulation.ToString().ToUpperInvariant()}    SNR: {snr}", labelStyle);
                GUI.Label(new Rect(42f, 143f, 430f, 23f), $"DISTANCE: {simulation.LinkDistanceMeters:F2} m    BER: {result.BitErrorRate:F4}", labelStyle);
                GUI.Label(new Rect(42f, 168f, 430f, 23f), $"TX: {RFSimulationController.FormatBits(result.InputBits)}", labelStyle);
                GUI.Label(new Rect(42f, 193f, 430f, 23f), $"RX: {RFSimulationController.FormatBits(result.DecodedBits)}", labelStyle);
                GUI.Label(
                    new Rect(42f, 219f, 430f, 24f),
                    result.IsExactMatch ? "LINK CHECK: PASS" : $"LINK CHECK: FAIL ({result.ErrorCount} bit errors)",
                    result.IsExactMatch ? passStyle : failStyle);
            }

            float mapWidth = Mathf.Min(440f, Screen.width * 0.36f);
            float mapHeight = mapWidth * 0.5f;
            Rect mapRect = new Rect(22f, Screen.height - mapHeight - 48f, mapWidth, mapHeight);
            fieldVisualizer?.Draw(mapRect);
            GUI.Label(new Rect(mapRect.x + 10f, mapRect.y + 8f, mapRect.width - 20f, 24f), "APPROX. ISOTROPIC POWER DENSITY", evidenceStyle);
            GUI.Label(new Rect(mapRect.x, mapRect.yMax + 4f, mapRect.width + 260f, 24f), "WASD MOVE  SHIFT SPRINT  SPACE JUMP  CLICK/TAB LOOK  1–4 MODULATION  ESC EXIT", labelStyle);

            DrawWorldMarker(simulation?.Transmitter?.transform, "TX", new Color(1f, 0.28f, 0.08f));
            DrawSpatialInstrument();
        }

        private void DrawSpatialInstrument()
        {
            if (fieldSampler == null)
            {
                return;
            }

            SpatialRFReading reading = fieldSampler.Current;
            const float width = 340f;
            Rect panel = new Rect(Screen.width - width - 22f, 22f, width, 246f);
            GUI.color = new Color(0.02f, 0.055f, 0.075f, 0.94f);
            GUI.Box(panel, GUIContent.none);
            GUI.color = Color.white;

            GUI.Label(new Rect(panel.x + 18f, panel.y + 12f, width - 36f, 28f), "SCYTHE_AR // SPATIAL PROBE", headingStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 44f, width - 36f, 22f), "EVIDENCE: SIMULATED", evidenceStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 69f, width - 36f, 22f), $"RANGE       {reading.DistanceMeters,8:F2} m", labelStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 92f, width - 36f, 22f), $"BEARING     {reading.BearingDegrees,8:+0.0;-0.0;0.0}°", labelStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 115f, width - 36f, 22f), $"POWER       {reading.ReceivedPowerDbm,8:F1} dBm", labelStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 138f, width - 36f, 22f), $"RADIAL V    {reading.RadialVelocityMetersPerSecond,8:+0.00;-0.00;0.00} m/s", labelStyle);
            GUI.Label(new Rect(panel.x + 18f, panel.y + 161f, width - 36f, 22f), $"DOPPLER     {reading.DopplerHz,8:+0.00;-0.00;0.00} Hz", labelStyle);

            DrawSignalBars(new Rect(panel.x + 18f, panel.y + 194f, 148f, 30f), reading.ReceivedPowerDbm);
            DrawBearingArrow(new Vector2(panel.xMax - 67f, panel.yMax - 45f), reading.BearingDegrees);
        }

        private static void DrawSignalBars(Rect area, float receivedPowerDbm)
        {
            float normalized = Mathf.InverseLerp(-75f, -20f, receivedPowerDbm);
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
                fontSize = 22,
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
                normal = { textColor = new Color(1f, 0.25f, 0.12f) },
            };
        }
    }
}
