using System;
using System.Collections.Generic;
using UnityEngine;

namespace SCYTHE.RF
{
    /// <summary>
    /// Deterministic complex-baseband modem. Carrier upconversion is deliberately
    /// outside this first milestone; carrier frequency belongs to the channel model.
    /// </summary>
    public static class RFModem
    {
        private const float AskLowAmplitude = 0.25f;
        private const float AskHighAmplitude = 1f;

        public static ComplexSample[] Modulate(
            IReadOnlyList<bool> bits,
            ModulationType modulation,
            int samplesPerSymbol,
            float sampleRate,
            float symbolRate)
        {
            ValidateParameters(bits, samplesPerSymbol, sampleRate, symbolRate);

            int bitsPerSymbol = modulation == ModulationType.Qpsk ? 2 : 1;
            int symbolCount = Mathf.CeilToInt(bits.Count / (float)bitsPerSymbol);
            var output = new ComplexSample[symbolCount * samplesPerSymbol];

            for (int symbol = 0; symbol < symbolCount; symbol++)
            {
                int bitIndex = symbol * bitsPerSymbol;
                bool first = bitIndex < bits.Count && bits[bitIndex];
                bool second = bitIndex + 1 < bits.Count && bits[bitIndex + 1];

                for (int sample = 0; sample < samplesPerSymbol; sample++)
                {
                    int outputIndex = symbol * samplesPerSymbol + sample;
                    output[outputIndex] = modulation switch
                    {
                        ModulationType.Ask => new ComplexSample(first ? AskHighAmplitude : AskLowAmplitude, 0f),
                        ModulationType.Bpsk => new ComplexSample(first ? 1f : -1f, 0f),
                        ModulationType.Qpsk => new ComplexSample(first ? 0.70710678f : -0.70710678f, second ? 0.70710678f : -0.70710678f),
                        ModulationType.Fsk => ModulateFsk(first, sample, sampleRate, symbolRate),
                        _ => throw new ArgumentOutOfRangeException(nameof(modulation)),
                    };
                }
            }

            return output;
        }

        public static bool[] Demodulate(
            IReadOnlyList<ComplexSample> samples,
            ModulationType modulation,
            int samplesPerSymbol,
            float sampleRate,
            float symbolRate,
            int expectedBitCount)
        {
            if (samples == null || samples.Count == 0 || samples.Count % samplesPerSymbol != 0)
            {
                throw new ArgumentException("IQ sample count must be a non-zero multiple of samplesPerSymbol.", nameof(samples));
            }

            int symbolCount = samples.Count / samplesPerSymbol;
            var decoded = new List<bool>(modulation == ModulationType.Qpsk ? symbolCount * 2 : symbolCount);

            for (int symbol = 0; symbol < symbolCount; symbol++)
            {
                int offset = symbol * samplesPerSymbol;
                switch (modulation)
                {
                    case ModulationType.Ask:
                        decoded.Add(AverageMagnitude(samples, offset, samplesPerSymbol) > (AskLowAmplitude + AskHighAmplitude) * 0.5f);
                        break;
                    case ModulationType.Bpsk:
                        decoded.Add(AverageComponent(samples, offset, samplesPerSymbol, useQ: false) >= 0f);
                        break;
                    case ModulationType.Qpsk:
                        decoded.Add(AverageComponent(samples, offset, samplesPerSymbol, useQ: false) >= 0f);
                        decoded.Add(AverageComponent(samples, offset, samplesPerSymbol, useQ: true) >= 0f);
                        break;
                    case ModulationType.Fsk:
                        decoded.Add(DemodulateFsk(samples, offset, samplesPerSymbol, sampleRate, symbolRate));
                        break;
                    default:
                        throw new ArgumentOutOfRangeException(nameof(modulation));
                }
            }

            if (expectedBitCount < 0 || expectedBitCount > decoded.Count)
            {
                throw new ArgumentOutOfRangeException(nameof(expectedBitCount));
            }

            return decoded.GetRange(0, expectedBitCount).ToArray();
        }

        private static ComplexSample ModulateFsk(bool bit, int sample, float sampleRate, float symbolRate)
        {
            float frequency = bit ? symbolRate * 0.25f : -symbolRate * 0.25f;
            float phase = 2f * Mathf.PI * frequency * sample / sampleRate;
            return new ComplexSample(Mathf.Cos(phase), Mathf.Sin(phase));
        }

        private static bool DemodulateFsk(
            IReadOnlyList<ComplexSample> samples,
            int offset,
            int count,
            float sampleRate,
            float symbolRate)
        {
            float positiveEnergy = CorrelationEnergy(samples, offset, count, sampleRate, symbolRate * 0.25f);
            float negativeEnergy = CorrelationEnergy(samples, offset, count, sampleRate, -symbolRate * 0.25f);
            return positiveEnergy >= negativeEnergy;
        }

        private static float CorrelationEnergy(
            IReadOnlyList<ComplexSample> samples,
            int offset,
            int count,
            float sampleRate,
            float frequency)
        {
            float correlationI = 0f;
            float correlationQ = 0f;
            for (int sample = 0; sample < count; sample++)
            {
                float phase = -2f * Mathf.PI * frequency * sample / sampleRate;
                float cosine = Mathf.Cos(phase);
                float sine = Mathf.Sin(phase);
                ComplexSample value = samples[offset + sample];
                correlationI += value.I * cosine - value.Q * sine;
                correlationQ += value.I * sine + value.Q * cosine;
            }

            return correlationI * correlationI + correlationQ * correlationQ;
        }

        private static float AverageMagnitude(IReadOnlyList<ComplexSample> samples, int offset, int count)
        {
            float sum = 0f;
            for (int index = 0; index < count; index++)
            {
                sum += samples[offset + index].Magnitude;
            }

            return sum / count;
        }

        private static float AverageComponent(
            IReadOnlyList<ComplexSample> samples,
            int offset,
            int count,
            bool useQ)
        {
            float sum = 0f;
            for (int index = 0; index < count; index++)
            {
                ComplexSample sample = samples[offset + index];
                sum += useQ ? sample.Q : sample.I;
            }

            return sum / count;
        }

        private static void ValidateParameters(
            IReadOnlyList<bool> bits,
            int samplesPerSymbol,
            float sampleRate,
            float symbolRate)
        {
            if (bits == null || bits.Count == 0)
            {
                throw new ArgumentException("At least one input bit is required.", nameof(bits));
            }

            if (samplesPerSymbol < 4 || sampleRate <= 0f || symbolRate <= 0f)
            {
                throw new ArgumentOutOfRangeException(nameof(samplesPerSymbol), "Modem rates and sample count must be positive.");
            }
        }
    }
}
