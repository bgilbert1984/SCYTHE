using System;
using System.Collections.Generic;
using System.IO;
using SCYTHE.Core;
using SCYTHE.Optics;
using SCYTHE.RF;
using UnityEngine;

namespace Scythe.Editor
{
    public static class ValidationCommand
    {
        private static readonly bool[] KnownBits =
        {
            true, false, true, true, false, false, true, false,
            true, false, false, true, true, true, false, true,
        };

        public static void Run()
        {
            ValidateCoreModels();
        }

        public static void ValidateCoreModels()
        {
            ValidateRfRoundTrips();
            ValidateDeterminism();
            ValidateSpatialRfModel();
            ValidateWrappedPhaseGradient();
            ValidateOpticalMetadataContract();
            Debug.Log("[SCYTHE VALIDATION] PASS: RF round trips, deterministic channel, spatial attenuation, one-way Doppler, wrapped optical gradient, and optical metadata contract.");
        }

        private static void ValidateRfRoundTrips()
        {
            foreach (ModulationType modulation in Enum.GetValues(typeof(ModulationType)))
            {
                RFLinkResult result = RFLinkPipeline.Run(
                    KnownBits,
                    modulation,
                    samplesPerSymbol: 16,
                    symbolRate: 1000f,
                    distanceMeters: 8.5f,
                    carrierFrequencyHz: 2.4e9f,
                    noiseStdDev: 0.00002f,
                    seed: 424242);

                Require(result.IsExactMatch, $"{modulation} round trip produced {result.ErrorCount} bit errors.");
                Require(result.TransmittedIq.Length > 0, $"{modulation} generated no IQ samples.");
                Require(!float.IsNaN(result.SnrDb), $"{modulation} produced invalid SNR.");
            }
        }

        private static void ValidateDeterminism()
        {
            RFLinkResult first = RFLinkPipeline.Run(
                KnownBits,
                ModulationType.Qpsk,
                16,
                1000f,
                8.5f,
                2.4e9f,
                0.00002f,
                77);
            RFLinkResult second = RFLinkPipeline.Run(
                KnownBits,
                ModulationType.Qpsk,
                16,
                1000f,
                8.5f,
                2.4e9f,
                0.00002f,
                77);

            Require(first.ReceivedIq.Length == second.ReceivedIq.Length, "Deterministic runs differ in length.");
            for (int index = 0; index < first.ReceivedIq.Length; index++)
            {
                Require(
                    first.ReceivedIq[index].I == second.ReceivedIq[index].I
                    && first.ReceivedIq[index].Q == second.ReceivedIq[index].Q,
                    $"Seeded channel diverged at IQ sample {index}.");
            }
        }

        private static void ValidateSpatialRfModel()
        {
            float gainAtFiveMeters = RFChannel.FreeSpaceAmplitudeGain(5f, 2.4e9f);
            float gainAtTenMeters = RFChannel.FreeSpaceAmplitudeGain(10f, 2.4e9f);
            Require(
                Mathf.Abs(gainAtFiveMeters / gainAtTenMeters - 2f) < 0.0001f,
                "Free-space amplitude did not follow inverse distance.");

            float densityAtFiveMeters = RFChannel.PowerDensityWattsPerSquareMeter(1f, 5f);
            float densityAtTenMeters = RFChannel.PowerDensityWattsPerSquareMeter(1f, 10f);
            Require(
                Mathf.Abs(densityAtFiveMeters / densityAtTenMeters - 4f) < 0.0001f,
                "Power density did not follow inverse-square distance.");

            float doppler = RFChannel.DopplerShiftHz(10f, 2.4e9f);
            float expected = (float)(10f * 2.4e9f / PhysicalConstants.SpeedOfLightMetersPerSecond);
            Require(Mathf.Abs(doppler - expected) < 0.0001f, "One-way Doppler calculation is incorrect.");
            Require(RFChannel.DopplerShiftHz(-10f, 2.4e9f) < 0f, "Receding motion must produce negative Doppler.");

            RFLinkResult moving = RFLinkPipeline.Run(
                KnownBits,
                ModulationType.Ask,
                16,
                1000f,
                8.5f,
                2.4e9f,
                0.00002f,
                55,
                radialVelocityTowardTransmitter: 3f);
            Require(moving.DopplerHz > 0f, "Moving RF pipeline did not carry Doppler metadata.");
            Require(moving.IsExactMatch, "ASK magnitude demodulation should tolerate the tested Doppler shift.");
        }

        private static void ValidateWrappedPhaseGradient()
        {
            float[] phase =
            {
                6.20f, 0.05f,
                6.20f, 0.05f,
            };
            Vector2[] gradient = OpticalCPUReference.ComputeWrappedPhaseGradient(
                phase,
                width: 2,
                height: 2,
                sampleSpacingMeters: 0.01f);

            float expected = OpticalCPUReference.WrappedDelta(0.05f - 6.20f) / 0.01f;
            Require(Mathf.Abs(gradient[0].x - expected) < 0.0001f, "Phase gradient did not wrap across 2π.");
            Require(Mathf.Abs(gradient[0].y) < 0.0001f, "Constant vertical phase produced a gradient.");
        }

        private static void ValidateOpticalMetadataContract()
        {
            var metadata = new OpticalMetadata
            {
                schemaVersion = "1.0",
                wavelengthNm = 650f,
                sampleSpacingMeters = 100e-9f,
                coordinateSystem = "right-handed, x-right, y-up, z-forward",
                phaseUnits = "radians",
                intensityUnits = "normalized",
                intensityNormalization = "contract-test-only; no physical dataset",
                polarizationRepresentation = "none",
                solver = "contract-validator",
                solverVersion = "1.0",
                provenance = "Generated inside ValidationCommand; not solver output.",
                generatedUtc = "2026-07-27T00:00:00Z",
                depthPlanePositionsMeters = new List<float>(),
                laneMaskSemantics = "none",
            };
            metadata.Validate();

            metadata.phaseUnits = "degrees";
            bool rejectedInvalidUnits = false;
            try
            {
                metadata.Validate();
            }
            catch (InvalidDataException)
            {
                rejectedInvalidUnits = true;
            }

            Require(rejectedInvalidUnits, "Optical metadata accepted non-radian phase units.");
        }

        private static void Require(bool condition, string message)
        {
            if (!condition)
            {
                throw new InvalidOperationException($"[SCYTHE VALIDATION] {message}");
            }
        }
    }
}
