using System;
using System.Collections.Generic;
using System.IO;
using SCYTHE.Core;
using SCYTHE.Global;
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
            ValidateScenarioContract();
            ValidateRfRoundTrips();
            ValidateDeterminism();
            ValidateSpatialRfModel();
            ValidateOcclusionApproximation();
            ValidateGlobalContractBoundary();
            ValidateWrappedPhaseGradient();
            ValidateOpticalMetadataContract();
            ValidateDeclaredOpticalDataset();
            Debug.Log(
                "[SCYTHE VALIDATION] PASS: scenario v3, multi-emitter RF round trips, "
                + "deterministic channel and motion, spatial attenuation, one-way Doppler, "
                + "explicit geometric occlusion loss, global evidence isolation, deterministic "
                + "geospatial sampling, wrapped optical gradient, and optical dataset contract.");
        }

        private static void ValidateScenarioContract()
        {
            ScenarioManifest manifest = ScenarioManifest.Load(
                "Scenarios/rf_milestone_01.json");
            Require(manifest.transmitters.Count >= 3, "Multi-emitter scenario requires at least three emitters.");
            Require(manifest.events.Count >= 1, "Scenario v3 requires at least one scripted event.");
            Require(manifest.globalSettings.enabled, "Scenario v3 must enable its Cesium georeference.");
            Require(
                string.Equals(
                    manifest.globalSettings.originEvidenceClass,
                    "ILLUSTRATIVE",
                    StringComparison.Ordinal),
                "The unregistered local-lab origin must remain ILLUSTRATIVE.");
            Require(
                !manifest.globalSettings.globalDatasetRequired
                    && manifest.globalSettings.datasets.Count == 0,
                "The first global scenario must not invent a bundled propagation dataset.");

            ScenarioTransmitter moving = manifest.transmitters.Find(
                transmitter => string.Equals(
                    transmitter.motion.type,
                    "pingPong",
                    StringComparison.OrdinalIgnoreCase));
            Require(moving != null, "Scenario does not declare deterministic transmitter motion.");
            Vector3 anchor = moving.positionMeters.ToVector3();
            Vector3 first = RFTransmitter.EvaluatePosition(anchor, moving.motion, 2.5d);
            Vector3 second = RFTransmitter.EvaluatePosition(anchor, moving.motion, 2.5d);
            Require(first == second, "Scripted transmitter motion is not deterministic.");
            Require(first != anchor, "Scripted moving transmitter remained at its anchor.");
        }

        private static void ValidateGlobalContractBoundary()
        {
            EvidenceStyle measured = EvidenceStyleRouter.Get(EvidenceClass.Measured);
            EvidenceStyle solver = EvidenceStyleRouter.Get(EvidenceClass.SolverOutput);
            EvidenceStyle illustrative =
                EvidenceStyleRouter.Get(EvidenceClass.Illustrative);
            Require(measured.Pattern == "SOLID", "MEASURED style must be solid.");
            Require(solver.Pattern == "HASHED", "SOLVER_OUTPUT style must be hashed.");
            Require(
                illustrative.Pattern == "DASHED",
                "ILLUSTRATIVE style must be dashed.");

            double[] grid =
            {
                0d, 10d,
                20d, 30d,
            };
            bool sampled = GlobalScalarGridSampler.TrySampleBilinear(
                grid,
                2,
                2,
                westDegrees: -1d,
                southDegrees: -1d,
                eastDegrees: 1d,
                northDegrees: 1d,
                longitudeDegrees: 0d,
                latitudeDegrees: 0d,
                out double center);
            Require(sampled && Math.Abs(center - 15d) < 1e-12d,
                "Global bilinear sampling is incorrect.");
            Require(
                !GlobalScalarGridSampler.TrySampleBilinear(
                    grid,
                    2,
                    2,
                    -1d,
                    -1d,
                    1d,
                    1d,
                    2d,
                    0d,
                    out _),
                "Global sampler accepted an out-of-bounds query.");

            GeodeticPosition referenceOrigin =
                Wgs84Reference.LocalEastUpNorthToLongitudeLatitudeHeight(
                    0d,
                    0d,
                    0d,
                    0d,
                    0d,
                    0d);
            Require(
                Math.Abs(referenceOrigin.LongitudeDegrees) < 1e-12d
                    && Math.Abs(referenceOrigin.LatitudeDegrees) < 1e-12d
                    && Math.Abs(referenceOrigin.HeightMeters) < 1e-7d,
                "WGS84 reference transform does not preserve its origin.");

            var manifest = new GlobalPropagationManifest
            {
                schemaVersion = "1.0",
                datasetId = "validation-global-rf-grid",
                title = "Validation-only global RF grid",
                description = "In-memory contract test; no physical dataset.",
                evidenceClass = "SYNTHETIC",
                visualizationIsAuthoritative = false,
                authority = new GlobalAuthorityMetadata
                {
                    solverName = "contract-test",
                    solverVersion = "1.0",
                    modelName = "in-memory fixture",
                    sourceRevision = "validation-command",
                    provenanceStatus = "COMPLETE",
                    runId = "validation-global-grid",
                    deterministic = true,
                },
                spatialReference = new GlobalSpatialReferenceMetadata
                {
                    type = "GEODETIC_GRID",
                    horizontalCrs = "EPSG:4326",
                    verticalDatum = "WGS84_ELLIPSOID",
                    coordinateOrder = "longitude,latitude,height",
                    heightUnits = "m",
                    ecefCompatible = true,
                    boundsDegrees = new[] { -1d, -1d, 1d, 1d },
                    crossesAntimeridian = false,
                },
                physics = new GlobalPhysicsMetadata
                {
                    domain = "RF",
                    rf = new GlobalRfMetadata
                    {
                        frequencyHz = 2.4e9d,
                        bandwidthHz = 1e6d,
                        polarization = "unspecified-test-only",
                    },
                },
                quantity = new GlobalQuantityMetadata
                {
                    name = "path loss",
                    definition = "Validation-only scalar.",
                    units = "dB",
                    valueSemantics = "PATH_LOSS",
                    uncertainty = new GlobalUncertaintyMetadata
                    {
                        kind = "NOT_QUANTIFIED",
                        description = "Validation fixture.",
                        assetPath = null,
                    },
                },
                grid = new GlobalGridMetadata
                {
                    representation = "CUSTOM_BINARY",
                    dimensions = new[] { 2, 2 },
                    resolution = new[] { 2d, 2d },
                    interpolation = "BILINEAR",
                    authoritativeAssetPath = "values.f64le",
                },
                assets = new[]
                {
                    new GlobalDatasetAsset
                    {
                        path = "values.f64le",
                        role = "AUTHORITATIVE_VALUES",
                        mediaType = "application/octet-stream",
                        sha256 = new string('a', 64),
                        sizeBytes = 32,
                    },
                },
            };
            manifest.ValidateForUnityConsumer();

            manifest.visualizationIsAuthoritative = true;
            bool rejectedAuthorityClaim = false;
            try
            {
                manifest.ValidateForUnityConsumer();
            }
            catch (InvalidDataException)
            {
                rejectedAuthorityClaim = true;
            }

            Require(
                rejectedAuthorityClaim,
                "Unity accepted an authoritative visualization claim.");
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

        private static void ValidateOcclusionApproximation()
        {
            float loss = RFOcclusionModel.AttenuationDbForBlockers(2, 9f);
            Require(Mathf.Abs(loss - 18f) < 0.0001f, "Blocker attenuation did not add in dB.");

            float amplitude = RFOcclusionModel.AmplitudeMultiplierFromLossDb(20f);
            Require(Mathf.Abs(amplitude - 0.1f) < 0.0001f, "20 dB loss did not produce 0.1 amplitude.");

            RFLinkResult clear = RFLinkPipeline.Run(
                KnownBits,
                ModulationType.Ask,
                16,
                1000f,
                8.5f,
                2.4e9f,
                0f,
                91,
                0f,
                0f);
            RFLinkResult blocked = RFLinkPipeline.Run(
                KnownBits,
                ModulationType.Ask,
                16,
                1000f,
                8.5f,
                2.4e9f,
                0f,
                91,
                0f,
                18f);
            float measuredRatio = blocked.AmplitudeGain / clear.AmplitudeGain;
            float expectedRatio = RFOcclusionModel.AmplitudeMultiplierFromLossDb(18f);
            Require(
                Mathf.Abs(measuredRatio - expectedRatio) < 0.000001f,
                "RF pipeline did not carry configured occlusion loss.");
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

        private static void ValidateDeclaredOpticalDataset()
        {
            ScenarioManifest manifest = ScenarioManifest.Load(
                "Scenarios/rf_milestone_01.json");
            OpticalDataset dataset = BuildCommand.LoadDeclaredOpticalDataset(manifest);
            if (manifest.opticalDatasetRequired)
            {
                Require(dataset != null, "Required optical dataset was not loaded.");
            }
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
