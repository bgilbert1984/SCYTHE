using System.Collections.Generic;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public readonly struct SpatialRFReading
    {
        public SpatialRFReading(
            RFTransmitter transmitter,
            bool isActive,
            Vector3 probePosition,
            Vector3 directionToTransmitter,
            float distanceMeters,
            float bearingDegrees,
            float powerDensityWattsPerSquareMeter,
            float receivedPowerDbm,
            float radialVelocityMetersPerSecond,
            float dopplerHz,
            float snrDb,
            float bitErrorRate,
            int blockerCount,
            float occlusionAttenuationDb)
        {
            Transmitter = transmitter;
            IsActive = isActive;
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
            BlockerCount = blockerCount;
            OcclusionAttenuationDb = occlusionAttenuationDb;
        }

        public RFTransmitter Transmitter { get; }
        public bool IsActive { get; }
        public bool IsRadiating => Transmitter != null && Transmitter.IsRadiating;
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
        public int BlockerCount { get; }
        public float OcclusionAttenuationDb { get; }
        public bool IsOccluded => BlockerCount > 0;
    }

    /// <summary>
    /// Samples every declared reduced-order RF link at the mobile probe. No
    /// coherent emitter interference, diffraction, material response, antenna
    /// patterns, polarization, or full-wave behavior is claimed.
    /// </summary>
    public sealed class RFFieldSampler : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;
        [SerializeField] private Camera bearingCamera;

        private readonly List<SpatialRFReading> readings = new List<SpatialRFReading>();

        public SpatialRFReading Current { get; private set; }
        public SpatialRFReading Strongest { get; private set; }
        public IReadOnlyList<SpatialRFReading> CurrentReadings => readings;
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
            readings.Clear();
            if (simulation == null || simulation.Transmitters.Count == 0)
            {
                return Current;
            }

            bool strongestSet = false;
            Vector3 probePosition = transform.position;
            for (int index = 0; index < simulation.Transmitters.Count; index++)
            {
                RFTransmitter transmitter = simulation.Transmitters[index];
                SpatialRFReading reading = SampleTransmitter(transmitter, probePosition);
                readings.Add(reading);

                if (reading.IsActive)
                {
                    Current = reading;
                }

                if (reading.IsRadiating
                    && (!strongestSet || reading.ReceivedPowerDbm > Strongest.ReceivedPowerDbm))
                {
                    Strongest = reading;
                    strongestSet = true;
                }
            }

            return Current;
        }

        private SpatialRFReading SampleTransmitter(
            RFTransmitter transmitter,
            Vector3 probePosition)
        {
            Vector3 offset = transmitter.transform.position - probePosition;
            offset.y = 0f;
            float distance = Mathf.Max(0.1f, offset.magnitude);
            Vector3 direction = offset / distance;

            Vector3 referenceForward = bearingCamera != null
                ? bearingCamera.transform.forward
                : transform.forward;
            referenceForward.y = 0f;
            if (referenceForward.sqrMagnitude < 0.000001f)
            {
                referenceForward = Vector3.forward;
            }
            referenceForward.Normalize();
            float bearing = Vector3.SignedAngle(referenceForward, direction, Vector3.up);

            RFOcclusionReading occlusion = simulation.SampleOcclusion(transmitter);
            float powerDensity = RFChannel.PowerDensityWattsPerSquareMeter(
                transmitter.PowerWatts,
                distance)
                * Mathf.Pow(10f, -occlusion.AttenuationDb / 10f);
            float amplitudeGain = RFChannel.FreeSpaceAmplitudeGain(
                distance,
                transmitter.CarrierFrequencyHz)
                * RFOcclusionModel.AmplitudeMultiplierFromLossDb(occlusion.AttenuationDb);
            float receivedPowerWatts = transmitter.PowerWatts * amplitudeGain * amplitudeGain;
            float receivedPowerDbm = transmitter.IsRadiating
                ? 10f * Mathf.Log10(Mathf.Max(receivedPowerWatts * 1000f, 1e-15f))
                : float.NegativeInfinity;
            float radialVelocity = simulation.RadialVelocityToward(transmitter);
            float dopplerHz = RFChannel.DopplerShiftHz(
                radialVelocity,
                transmitter.CarrierFrequencyHz);
            RFLinkResult result = simulation.GetResult(transmitter);

            return new SpatialRFReading(
                transmitter,
                transmitter == simulation.Transmitter,
                probePosition,
                direction,
                distance,
                bearing,
                powerDensity,
                receivedPowerDbm,
                radialVelocity,
                dopplerHz,
                result?.SnrDb ?? float.NaN,
                result?.BitErrorRate ?? 0f,
                occlusion.BlockerCount,
                occlusion.AttenuationDb);
        }
    }
}
