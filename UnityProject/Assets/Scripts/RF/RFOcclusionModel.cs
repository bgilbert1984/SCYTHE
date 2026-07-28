using System.Collections.Generic;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public readonly struct RFOcclusionReading
    {
        public RFOcclusionReading(int blockerCount, float attenuationDb)
        {
            BlockerCount = blockerCount;
            AttenuationDb = attenuationDb;
        }

        public int BlockerCount { get; }
        public float AttenuationDb { get; }
        public bool IsOccluded => BlockerCount > 0;
    }

    /// <summary>
    /// Marker for static scene geometry that participates in the documented,
    /// reduced-order line-of-sight attenuation approximation.
    /// </summary>
    public sealed class RFOccluder : MonoBehaviour
    {
    }

    /// <summary>
    /// Counts explicitly marked colliders intersecting the transmitter-to-probe
    /// segment. Every unique blocker contributes a configured scalar dB loss.
    /// This is not diffraction, material EM response, or full-wave propagation.
    /// </summary>
    public sealed class RFOcclusionModel : MonoBehaviour
    {
        [SerializeField] private bool modelEnabled = true;
        [SerializeField, Min(0f)] private float lossDbPerBlocker = 18f;
        [SerializeField, Min(1)] private int maximumBlockers = 4;

        public bool ModelEnabled => modelEnabled;
        public float LossDbPerBlocker => lossDbPerBlocker;

        public void Configure(ScenarioOcclusion settings)
        {
            modelEnabled = settings.enabled;
            lossDbPerBlocker = settings.lossDbPerBlocker;
            maximumBlockers = settings.maximumBlockers;
        }

        public RFOcclusionReading Sample(Vector3 transmitterPosition, Vector3 probePosition)
        {
            if (!modelEnabled)
            {
                return new RFOcclusionReading(0, 0f);
            }

            Vector3 origin = transmitterPosition + Vector3.up * 1.2f;
            Vector3 destination = probePosition + Vector3.up * 1.2f;
            Vector3 offset = destination - origin;
            float distance = offset.magnitude;
            if (distance <= 0.001f)
            {
                return new RFOcclusionReading(0, 0f);
            }

            RaycastHit[] hits = Physics.RaycastAll(
                origin,
                offset / distance,
                distance,
                Physics.DefaultRaycastLayers,
                QueryTriggerInteraction.Ignore);
            var blockerIds = new HashSet<int>();
            foreach (RaycastHit hit in hits)
            {
                RFOccluder blocker = hit.collider.GetComponentInParent<RFOccluder>();
                if (blocker != null)
                {
                    blockerIds.Add(blocker.GetInstanceID());
                }
            }

            int blockerCount = Mathf.Min(blockerIds.Count, maximumBlockers);
            return new RFOcclusionReading(
                blockerCount,
                AttenuationDbForBlockers(blockerCount, lossDbPerBlocker));
        }

        public static float AttenuationDbForBlockers(int blockerCount, float perBlockerDb)
        {
            return Mathf.Max(0, blockerCount) * Mathf.Max(0f, perBlockerDb);
        }

        public static float AmplitudeMultiplierFromLossDb(float lossDb)
        {
            return Mathf.Pow(10f, -Mathf.Max(0f, lossDb) / 20f);
        }
    }
}
