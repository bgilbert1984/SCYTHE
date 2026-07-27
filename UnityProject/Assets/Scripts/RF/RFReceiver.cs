using System;
using UnityEngine;

namespace SCYTHE.RF
{
    public sealed class RFReceiver : MonoBehaviour
    {
        [SerializeField, Min(0f)] private float noiseStdDev = 0.00002f;

        public float NoiseStdDev => noiseStdDev;

        public void Configure(float channelNoiseStdDev)
        {
            noiseStdDev = Mathf.Max(0f, channelNoiseStdDev);
        }

        public bool[] Demodulate(
            ChannelResult channel,
            ModulationType modulation,
            int samplesPerSymbol,
            float symbolRate,
            int expectedBitCount)
        {
            if (channel.Samples == null)
            {
                throw new ArgumentException("Receiver requires channel samples.", nameof(channel));
            }

            var equalized = new ComplexSample[channel.Samples.Length];
            float inverseGain = 1f / Math.Max(channel.AmplitudeGain, 1e-12f);
            for (int index = 0; index < equalized.Length; index++)
            {
                equalized[index] = channel.Samples[index] * inverseGain;
            }

            return RFModem.Demodulate(
                equalized,
                modulation,
                samplesPerSymbol,
                symbolRate * samplesPerSymbol,
                symbolRate,
                expectedBitCount);
        }
    }
}
