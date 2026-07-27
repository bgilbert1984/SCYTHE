using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public readonly struct SpatialRFReading
    {
        public SpatialRFReading(
            Vector3 probePosition,
            Vector3 directionToTransmitter,
            float distanceMeters,
            float bearingDegrees,
            float powerDensityWattsPerSquareMeter,
            float receivedPowerDbm,
            float radialVelocityMetersPerSecond,
            float dopplerHz,
            float snrDb,
            float bitErrorRate)
        {
            ProbePosition = probePosition;
            DirectionToTransmitter = directionToTransmitter;
            DistanceMeters = distanceMeters;
            BearingDegrees = bearingDegrees;
            PowerDensityWattsPerSquareMeter = powerDensityWattsPerSquareMeter;
            ReceivedPowerDbm = receivedPowerDbm;
            RadialVelocityMetersPerSecond = radialVelocityMetersPerSecond;
            DopplerHz = dopplerHz;
            SnrDb = snrDb;
            BitErrorRate = bitErrorRate;
        }

        public Vector3 ProbePosition { get; }
        public Vector3 DirectionToTransmitter { get; }
        public float DistanceMeters { get; }
        public float BearingDegrees { get; }
        public float PowerDensityWattsPerSquareMeter { get; }
        public float ReceivedPowerDbm { get; }
        public float RadialVelocityMetersPerSecond { get; }
        public float DopplerHz { get; }
        public float SnrDb { get; }
        public float BitErrorRate { get; }
    }

    /// <summary>
    /// Samples the declared reduced-order RF model at the mobile probe position.
    /// It makes no claim to include multipath, polarization, antenna patterns, or
    /// full-wave behavior.
    /// </summary>
    public sealed class RFFieldSampler : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;
        [SerializeField] private Camera bearingCamera;

        public SpatialRFReading Current { get; private set; }
        public EvidenceLevel Evidence => EvidenceLevel.Simulated;

        public void Bind(RFSimulationController controller, Camera viewCamera)
        {
            simulation = controller;
            bearingCamera = viewCamera;
        }

        private void LateUpdate()
        {
            SampleNow();
        }

        public SpatialRFReading SampleNow()
        {
            if (simulation == null || simulation.Transmitter == null)
            {
                return Current;
            }

            Vector3 probePosition = transform.position;
            Vector3 offset = simulation.Transmitter.transform.position - probePosition;
            offset.y = 0f;
            float distance = Mathf.Max(0.1f, offset.magnitude);
            Vector3 direction = offset / distance;

            Vector3 referenceForward = bearingCamera != null ? bearingCamera.transform.forward : transform.forward;
            referenceForward.y = 0f;
            referenceForward.Normalize();
            float bearing = Vector3.SignedAngle(referenceForward, direction, Vector3.up);

            float powerDensity = RFChannel.PowerDensityWattsPerSquareMeter(
                simulation.Transmitter.PowerWatts,
                distance);
            float amplitudeGain = RFChannel.FreeSpaceAmplitudeGain(
                distance,
                simulation.Transmitter.CarrierFrequencyHz);
            float receivedPowerWatts = simulation.Transmitter.PowerWatts * amplitudeGain * amplitudeGain;
            float receivedPowerDbm = 10f * Mathf.Log10(Mathf.Max(receivedPowerWatts * 1000f, 1e-15f));
            float radialVelocity = simulation.RadialVelocityTowardTransmitter;
            float dopplerHz = RFChannel.DopplerShiftHz(
                radialVelocity,
                simulation.Transmitter.CarrierFrequencyHz);
            RFLinkResult result = simulation.LastResult;

            Current = new SpatialRFReading(
                probePosition,
                direction,
                distance,
                bearing,
                powerDensity,
                receivedPowerDbm,
                radialVelocity,
                dopplerHz,
                result?.SnrDb ?? float.NaN,
                result?.BitErrorRate ?? 0f);
            return Current;
        }
    }
}
