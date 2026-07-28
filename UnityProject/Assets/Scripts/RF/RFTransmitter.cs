using System;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public sealed class RFTransmitter : MonoBehaviour
    {
        [SerializeField] private string emitterId = "TX-A";
        [SerializeField] private string displayName = "ALPHA";
        [SerializeField] private bool isRadiating = true;
        [SerializeField] private ModulationType modulation = ModulationType.Bpsk;
        [SerializeField, Min(1f)] private float carrierFrequencyHz = 2.4e9f;
        [SerializeField, Min(1f)] private float symbolRateBaud = 1000f;
        [SerializeField, Min(4)] private int samplesPerSymbol = 16;
        [SerializeField, Min(0.0001f)] private float powerWatts = 1f;

        private bool[] payload = Array.Empty<bool>();
        private Vector3 anchorPosition;
        private ScenarioMotion motion = new ScenarioMotion();

        public string EmitterId => emitterId;
        public string DisplayName => displayName;
        public bool IsRadiating => isRadiating;
        public ModulationType Modulation
        {
            get => modulation;
            set => modulation = value;
        }

        public float CarrierFrequencyHz => carrierFrequencyHz;
        public float SymbolRateBaud => symbolRateBaud;
        public int SamplesPerSymbol => samplesPerSymbol;
        public float PowerWatts => powerWatts;
        public bool[] Payload => (bool[])payload.Clone();

        public void SetEmitterId(string value)
        {
            emitterId = string.IsNullOrWhiteSpace(value) ? "TX" : value;
        }

        public void Configure(ScenarioTransmitter definition)
        {
            if (definition == null)
            {
                throw new ArgumentNullException(nameof(definition));
            }

            emitterId = definition.id;
            displayName = definition.displayName;
            isRadiating = definition.enabled;
            modulation = ParseModulation(definition.modulation);
            carrierFrequencyHz = definition.carrierFrequencyHz;
            symbolRateBaud = definition.symbolRateBaud;
            samplesPerSymbol = definition.samplesPerSymbol;
            powerWatts = definition.powerWatts;
            anchorPosition = definition.positionMeters.ToVector3();
            motion = definition.motion ?? new ScenarioMotion();
            transform.position = anchorPosition;
        }

        public void SetRadiating(bool value)
        {
            isRadiating = value;
        }

        public void SetPowerWatts(float value)
        {
            if (value <= 0f)
            {
                throw new ArgumentOutOfRangeException(nameof(value), "Transmitter power must be positive.");
            }

            powerWatts = value;
        }

        public void SetPayload(bool[] bits)
        {
            payload = bits == null ? throw new ArgumentNullException(nameof(bits)) : (bool[])bits.Clone();
        }

        public void UpdateSpatialState(double simulationTimeSeconds)
        {
            transform.position = EvaluatePosition(anchorPosition, motion, simulationTimeSeconds);
        }

        public ComplexSample[] GenerateIq()
        {
            return RFModem.Modulate(
                payload,
                modulation,
                samplesPerSymbol,
                symbolRateBaud * samplesPerSymbol,
                symbolRateBaud);
        }

        public static ModulationType ParseModulation(string value)
        {
            if (Enum.TryParse(value, true, out ModulationType parsed))
            {
                return parsed;
            }

            throw new ArgumentException($"Unsupported modulation {value}.", nameof(value));
        }

        public static Vector3 EvaluatePosition(
            Vector3 anchor,
            ScenarioMotion motionDefinition,
            double simulationTimeSeconds)
        {
            if (motionDefinition == null
                || !string.Equals(
                    motionDefinition.type,
                    "pingPong",
                    StringComparison.OrdinalIgnoreCase))
            {
                return anchor;
            }

            Vector3 direction = motionDefinition.direction.ToVector3().normalized;
            float offset = Mathf.PingPong(
                (float)simulationTimeSeconds * motionDefinition.speedMetersPerSecond,
                motionDefinition.extentMeters);
            return anchor + direction * offset;
        }
    }
}
