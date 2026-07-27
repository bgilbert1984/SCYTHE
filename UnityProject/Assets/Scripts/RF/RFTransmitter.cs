using System;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public sealed class RFTransmitter : MonoBehaviour
    {
        [SerializeField] private ModulationType modulation = ModulationType.Bpsk;
        [SerializeField, Min(1f)] private float carrierFrequencyHz = 2.4e9f;
        [SerializeField, Min(1f)] private float symbolRateBaud = 1000f;
        [SerializeField, Min(4)] private int samplesPerSymbol = 16;
        [SerializeField, Min(0.0001f)] private float powerWatts = 1f;

        private bool[] payload = Array.Empty<bool>();

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

        public void Configure(ScenarioManifest manifest)
        {
            carrierFrequencyHz = manifest.carrierFrequencyHz;
            symbolRateBaud = manifest.symbolRateBaud;
            samplesPerSymbol = manifest.samplesPerSymbol;
            powerWatts = manifest.transmitterPowerWatts;
        }

        public void SetPayload(bool[] bits)
        {
            payload = bits == null ? throw new ArgumentNullException(nameof(bits)) : (bool[])bits.Clone();
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
    }
}
