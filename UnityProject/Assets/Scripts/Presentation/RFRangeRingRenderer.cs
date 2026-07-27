using UnityEngine;

namespace SCYTHE.Presentation
{
    public sealed class RFRangeRingRenderer : MonoBehaviour
    {
        [SerializeField] private float[] radiiMeters = { 5f, 10f, 15f, 20f };
        [SerializeField, Range(16, 128)] private int segments = 64;
        [SerializeField, Min(0.005f)] private float lineWidth = 0.035f;

        private Material lineMaterial;

        private void Start()
        {
            Shader shader = Shader.Find("Sprites/Default");
            if (shader == null)
            {
                Debug.LogWarning("[SCYTHE] Range rings disabled: Sprites/Default shader not found.");
                return;
            }

            lineMaterial = new Material(shader)
            {
                name = "SCYTHE Runtime Range Ring",
            };

            for (int index = 0; index < radiiMeters.Length; index++)
            {
                CreateRing(radiiMeters[index], index);
            }
        }

        private void CreateRing(float radius, int index)
        {
            var ringObject = new GameObject($"Range Ring {radius:F0}m");
            ringObject.transform.SetParent(transform, false);
            ringObject.transform.localPosition = Vector3.up * (0.035f + index * 0.002f);

            LineRenderer line = ringObject.AddComponent<LineRenderer>();
            line.useWorldSpace = false;
            line.loop = true;
            line.positionCount = segments;
            line.startWidth = lineWidth;
            line.endWidth = lineWidth;
            line.sharedMaterial = lineMaterial;
            Color color = new Color(0.08f, 0.85f, 0.62f, Mathf.Lerp(0.7f, 0.25f, index / (float)radiiMeters.Length));
            line.startColor = color;
            line.endColor = color;

            for (int segment = 0; segment < segments; segment++)
            {
                float angle = segment / (float)segments * Mathf.PI * 2f;
                line.SetPosition(segment, new Vector3(Mathf.Cos(angle) * radius, 0f, Mathf.Sin(angle) * radius));
            }
        }

        private void OnDestroy()
        {
            if (lineMaterial != null)
            {
                Destroy(lineMaterial);
            }
        }
    }
}
