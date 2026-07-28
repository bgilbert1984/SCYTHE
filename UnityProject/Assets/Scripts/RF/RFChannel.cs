using System;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public readonly struct ChannelResult
    {
        public ChannelResult(ComplexSample[] samples, float amplitudeGain, float snrDb)
        {
            Samples = samples;
            AmplitudeGain = amplitudeGain;
            SnrDb = snrDb;
        }

        public ComplexSample[] Samples { get; }
        public float AmplitudeGain { get; }
        public float SnrDb { get; }
    }

    public static class RFChannel
    {
        public static ChannelResult ApplyFreeSpaceAwgn(
            ComplexSample[] input,
            float distanceMeters,
            float carrierFrequencyHz,
            float noiseStdDev,
            int seed,
            float dopplerHz = 0f,
            float sampleRate = 1f,
            float additionalAttenuationDb = 0f)
        {
            if (input == null || input.Length == 0)
            {
                throw new ArgumentException("Channel input cannot be empty.", nameof(input));
            }

            if (sampleRate <= 0f)
            {
                throw new ArgumentOutOfRangeException(nameof(sampleRate));
            }

            float gain = FreeSpaceAmplitudeGain(distanceMeters, carrierFrequencyHz)
                * RFOcclusionModel.AmplitudeMultiplierFromLossDb(additionalAttenuationDb);
            var random = new System.Random(seed);
            var output = new ComplexSample[input.Length];
            double signalPower = 0d;
            double noisePower = 0d;

            for (int index = 0; index < input.Length; index++)
            {
                float phase = 2f * Mathf.PI * dopplerHz * index / sampleRate;
                float cosine = Mathf.Cos(phase);
                float sine = Mathf.Sin(phase);
                ComplexSample source = input[index];
                var shifted = new ComplexSample(
                    source.I * cosine - source.Q * sine,
                    source.I * sine + source.Q * cosine);
                ComplexSample signal = shifted * gain;
                float noiseI = NextGaussian(random) * noiseStdDev;
                float noiseQ = NextGaussian(random) * noiseStdDev;
                output[index] = new ComplexSample(signal.I + noiseI, signal.Q + noiseQ);
                signalPower += signal.MagnitudeSquared;
                noisePower += noiseI * noiseI + noiseQ * noiseQ;
            }

            float snrDb = noisePower <= double.Epsilon
                ? float.PositiveInfinity
                : 10f * Mathf.Log10((float)(signalPower / noisePower));
            return new ChannelResult(output, gain, snrDb);
        }

        public static float FreeSpaceAmplitudeGain(float distanceMeters, float carrierFrequencyHz)
        {
            float safeDistance = Mathf.Max(0.1f, distanceMeters);
            double wavelength = PhysicalConstants.SpeedOfLightMetersPerSecond / carrierFrequencyHz;
            return (float)(wavelength / (4d * Math.PI * safeDistance));
        }

        public static float PowerDensityWattsPerSquareMeter(float transmitterPowerWatts, float distanceMeters)
        {
            float safeDistance = Mathf.Max(0.25f, distanceMeters);
            return transmitterPowerWatts / (4f * Mathf.PI * safeDistance * safeDistance);
        }

        /// <summary>
        /// One-way Doppler. Positive radial velocity means the receiver is moving
        /// toward the transmitter and observes a higher carrier frequency.
        /// </summary>
        public static float DopplerShiftHz(float radialVelocityTowardTransmitter, float carrierFrequencyHz)
        {
            return (float)(radialVelocityTowardTransmitter
                * carrierFrequencyHz
                / PhysicalConstants.SpeedOfLightMetersPerSecond);
        }

        private static float NextGaussian(System.Random random)
        {
            double first = Math.Max(double.Epsilon, random.NextDouble());
            double second = random.NextDouble();
            return (float)(Math.Sqrt(-2d * Math.Log(first)) * Math.Cos(2d * Math.PI * second));
        }
    }
}
