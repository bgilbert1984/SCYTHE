using System;
using UnityEngine;

namespace SCYTHE.Optics
{
    public static class OpticalCPUReference
    {
        /// <summary>
        /// Computes a forward phase gradient in radians per meter while respecting
        /// the phase wrap at +/- pi. This is the reference for future GPU kernels.
        /// </summary>
        public static Vector2[] ComputeWrappedPhaseGradient(
            float[] phaseRadians,
            int width,
            int height,
            float sampleSpacingMeters)
        {
            if (phaseRadians == null || phaseRadians.Length != width * height)
            {
                throw new ArgumentException("Phase array dimensions do not match.", nameof(phaseRadians));
            }

            if (width < 2 || height < 2 || sampleSpacingMeters <= 0f)
            {
                throw new ArgumentOutOfRangeException(nameof(sampleSpacingMeters));
            }

            var gradient = new Vector2[phaseRadians.Length];
            for (int y = 0; y < height; y++)
            {
                int nextY = Math.Min(y + 1, height - 1);
                for (int x = 0; x < width; x++)
                {
                    int nextX = Math.Min(x + 1, width - 1);
                    int index = y * width + x;
                    float phase = phaseRadians[index];
                    float deltaX = WrappedDelta(phaseRadians[y * width + nextX] - phase);
                    float deltaY = WrappedDelta(phaseRadians[nextY * width + x] - phase);
                    gradient[index] = new Vector2(
                        deltaX / sampleSpacingMeters,
                        deltaY / sampleSpacingMeters);
                }
            }

            return gradient;
        }

        public static float WrappedDelta(float deltaRadians)
        {
            return Mathf.Atan2(Mathf.Sin(deltaRadians), Mathf.Cos(deltaRadians));
        }
    }
}
