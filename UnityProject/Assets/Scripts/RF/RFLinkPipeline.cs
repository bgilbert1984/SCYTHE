using System;
using System.Collections.Generic;

namespace SCYTHE.RF
{
    public sealed class RFLinkResult
    {
        public bool[] InputBits { get; internal set; }
        public bool[] DecodedBits { get; internal set; }
        public ComplexSample[] TransmittedIq { get; internal set; }
        public ComplexSample[] ReceivedIq { get; internal set; }
        public ModulationType Modulation { get; internal set; }
        public float SnrDb { get; internal set; }
        public float DopplerHz { get; internal set; }
        public float RadialVelocityMetersPerSecond { get; internal set; }
        public float OcclusionAttenuationDb { get; internal set; }
        public float AmplitudeGain { get; internal set; }
        public int ErrorCount { get; internal set; }
        public float BitErrorRate => InputBits.Length == 0 ? 0f : ErrorCount / (float)InputBits.Length;
        public bool IsExactMatch => ErrorCount == 0;
    }

    public static class RFLinkPipeline
    {
        public static RFLinkResult Run(
            IReadOnlyList<bool> inputBits,
            ModulationType modulation,
            int samplesPerSymbol,
            float symbolRate,
            float distanceMeters,
            float carrierFrequencyHz,
            float noiseStdDev,
            int seed,
            float radialVelocityTowardTransmitter = 0f,
            float occlusionAttenuationDb = 0f)
        {
            bool[] bits = CopyBits(inputBits);
            float sampleRate = symbolRate * samplesPerSymbol;
            ComplexSample[] transmitted = RFModem.Modulate(bits, modulation, samplesPerSymbol, sampleRate, symbolRate);
            float dopplerHz = RFChannel.DopplerShiftHz(radialVelocityTowardTransmitter, carrierFrequencyHz);
            ChannelResult channel = RFChannel.ApplyFreeSpaceAwgn(
                transmitted,
                distanceMeters,
                carrierFrequencyHz,
                noiseStdDev,
                seed,
                dopplerHz,
                sampleRate,
                occlusionAttenuationDb);

            var equalized = new ComplexSample[channel.Samples.Length];
            float inverseGain = 1f / Math.Max(channel.AmplitudeGain, 1e-12f);
            for (int index = 0; index < equalized.Length; index++)
            {
                equalized[index] = channel.Samples[index] * inverseGain;
            }

            bool[] decoded = RFModem.Demodulate(
                equalized,
                modulation,
                samplesPerSymbol,
                sampleRate,
                symbolRate,
                bits.Length);

            int errors = 0;
            for (int index = 0; index < bits.Length; index++)
            {
                if (bits[index] != decoded[index])
                {
                    errors++;
                }
            }

            return new RFLinkResult
            {
                InputBits = bits,
                DecodedBits = decoded,
                TransmittedIq = transmitted,
                ReceivedIq = channel.Samples,
                Modulation = modulation,
                SnrDb = channel.SnrDb,
                DopplerHz = dopplerHz,
                RadialVelocityMetersPerSecond = radialVelocityTowardTransmitter,
                OcclusionAttenuationDb = occlusionAttenuationDb,
                AmplitudeGain = channel.AmplitudeGain,
                ErrorCount = errors,
            };
        }

        private static bool[] CopyBits(IReadOnlyList<bool> bits)
        {
            if (bits == null || bits.Count == 0)
            {
                throw new ArgumentException("RF link requires a non-empty payload.", nameof(bits));
            }

            var copy = new bool[bits.Count];
            for (int index = 0; index < bits.Count; index++)
            {
                copy[index] = bits[index];
            }

            return copy;
        }
    }
}
