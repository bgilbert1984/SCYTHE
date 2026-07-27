using SCYTHE.RF;
using UnityEngine;

namespace SCYTHE.Presentation
{
    /// <summary>
    /// Scientific overlay for the explicitly approximate isotropic free-space
    /// power-density model. This is not a full-wave electromagnetic solver.
    /// </summary>
    public sealed class RFFieldVisualizer : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;
        [SerializeField, Range(64, 512)] private int textureWidth = 256;
        [SerializeField, Range(32, 256)] private int textureHeight = 128;
        [SerializeField, Min(1f)] private float extentMeters = 15f;
        [SerializeField, Min(0.05f)] private float refreshIntervalSeconds = 0.2f;

        private Texture2D fieldTexture;
        private Color[] pixels;
        private float nextRefresh;

        public Texture2D FieldTexture => fieldTexture;
        public float ExtentMeters => extentMeters;

        public void Bind(RFSimulationController controller)
        {
            simulation = controller;
        }

        private void Awake()
        {
            fieldTexture = new Texture2D(textureWidth, textureHeight, TextureFormat.RGBA32, false, true)
            {
                name = "SCYTHE Approximate RF Power Density",
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp,
            };
            pixels = new Color[textureWidth * textureHeight];
        }

        private void Update()
        {
            if (simulation == null || UnityEngine.Time.unscaledTime < nextRefresh)
            {
                return;
            }

            nextRefresh = UnityEngine.Time.unscaledTime + refreshIntervalSeconds;
            RenderField();
        }

        public void Draw(Rect rectangle)
        {
            if (fieldTexture != null)
            {
                GUI.DrawTexture(rectangle, fieldTexture, ScaleMode.StretchToFill, false);
            }

            if (simulation?.Transmitter != null)
            {
                DrawMapMarker(rectangle, simulation.Transmitter.transform.position, new Color(1f, 0.25f, 0.06f), "TX");
            }

            if (simulation?.Receiver != null)
            {
                DrawMapMarker(rectangle, simulation.Receiver.transform.position, new Color(0.1f, 1f, 0.65f), "YOU");
            }
        }

        private void RenderField()
        {
            Transform transmitter = simulation.Transmitter.transform;
            float powerWatts = simulation.Transmitter.PowerWatts;

            for (int y = 0; y < textureHeight; y++)
            {
                float worldZ = Mathf.Lerp(-extentMeters, extentMeters, y / (float)(textureHeight - 1));
                for (int x = 0; x < textureWidth; x++)
                {
                    float worldX = Mathf.Lerp(-extentMeters, extentMeters, x / (float)(textureWidth - 1));
                    float dx = worldX - transmitter.position.x;
                    float dz = worldZ - transmitter.position.z;
                    float distance = Mathf.Sqrt(dx * dx + dz * dz);
                    float density = RFChannel.PowerDensityWattsPerSquareMeter(powerWatts, distance);
                    float dbmPerSquareMeter = 10f * Mathf.Log10(Mathf.Max(density * 1000f, 1e-12f));
                    float normalized = Mathf.InverseLerp(-55f, 5f, dbmPerSquareMeter);
                    pixels[y * textureWidth + x] = ColorRamp(normalized);
                }
            }

            fieldTexture.SetPixels(pixels);
            fieldTexture.Apply(false, false);
        }

        private static Color ColorRamp(float value)
        {
            value = Mathf.Clamp01(value);
            if (value < 0.33f)
            {
                return Color.Lerp(new Color(0.005f, 0.01f, 0.04f, 0.82f), new Color(0f, 0.25f, 0.7f, 0.88f), value / 0.33f);
            }

            if (value < 0.66f)
            {
                return Color.Lerp(new Color(0f, 0.25f, 0.7f, 0.88f), new Color(0.05f, 1f, 0.58f, 0.9f), (value - 0.33f) / 0.33f);
            }

            return Color.Lerp(new Color(0.05f, 1f, 0.58f, 0.9f), new Color(1f, 0.22f, 0.04f, 0.94f), (value - 0.66f) / 0.34f);
        }

        private void DrawMapMarker(Rect rectangle, Vector3 worldPosition, Color color, string label)
        {
            float u = Mathf.InverseLerp(-extentMeters, extentMeters, worldPosition.x);
            float v = Mathf.InverseLerp(-extentMeters, extentMeters, worldPosition.z);
            float x = Mathf.Lerp(rectangle.xMin, rectangle.xMax, u);
            float y = Mathf.Lerp(rectangle.yMax, rectangle.yMin, v);

            GUI.color = color;
            GUI.Box(new Rect(x - 4f, y - 4f, 8f, 8f), GUIContent.none);
            GUI.color = Color.white;
            GUI.Label(new Rect(x + 7f, y - 11f, 46f, 22f), label);
        }

        private void OnDestroy()
        {
            if (fieldTexture != null)
            {
                Destroy(fieldTexture);
            }
        }
    }
}
