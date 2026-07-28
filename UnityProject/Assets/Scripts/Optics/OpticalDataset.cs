using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace SCYTHE.Optics
{
    [Serializable]
    public sealed class OpticalMetadata
    {
        public string schemaVersion;
        public float wavelengthNm;
        public float sampleSpacingMeters;
        public string coordinateSystem;
        public string phaseUnits;
        public string intensityUnits;
        public string intensityNormalization;
        public string polarizationRepresentation;
        public string solver;
        public string solverVersion;
        public string provenance;
        public string generatedUtc;
        public List<float> depthPlanePositionsMeters = new List<float>();
        public string laneMaskSemantics;

        public void Validate()
        {
            Require(schemaVersion, nameof(schemaVersion));
            Require(coordinateSystem, nameof(coordinateSystem));
            Require(phaseUnits, nameof(phaseUnits));
            Require(intensityUnits, nameof(intensityUnits));
            Require(intensityNormalization, nameof(intensityNormalization));
            Require(polarizationRepresentation, nameof(polarizationRepresentation));
            Require(solver, nameof(solver));
            Require(solverVersion, nameof(solverVersion));
            Require(provenance, nameof(provenance));
            Require(generatedUtc, nameof(generatedUtc));
            Require(laneMaskSemantics, nameof(laneMaskSemantics));

            if (wavelengthNm <= 0f || sampleSpacingMeters <= 0f)
            {
                throw new InvalidDataException("Optical wavelength and sample spacing must be positive.");
            }

            if (!string.Equals(phaseUnits, "radians", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("SCYTHE optical phase assets must use radians.");
            }

            if (!string.Equals(schemaVersion, "1.0", StringComparison.Ordinal))
            {
                throw new InvalidDataException($"Unsupported optical schemaVersion {schemaVersion}.");
            }

            if (!string.Equals(intensityUnits, "W/m^2", StringComparison.Ordinal)
                && !string.Equals(intensityUnits, "normalized", StringComparison.Ordinal))
            {
                throw new InvalidDataException("Optical intensity units must be W/m^2 or normalized.");
            }

            if (!string.Equals(polarizationRepresentation, "stokes-IQUV", StringComparison.Ordinal)
                && !string.Equals(polarizationRepresentation, "jones-ExEy-complex", StringComparison.Ordinal)
                && !string.Equals(polarizationRepresentation, "none", StringComparison.Ordinal))
            {
                throw new InvalidDataException("Unsupported optical polarization representation.");
            }

            if (!DateTime.TryParse(
                generatedUtc,
                null,
                System.Globalization.DateTimeStyles.AdjustToUniversal,
                out _))
            {
                throw new InvalidDataException("Optical generatedUtc must be an ISO-compatible timestamp.");
            }

            if (depthPlanePositionsMeters == null)
            {
                throw new InvalidDataException("Optical depthPlanePositionsMeters cannot be null.");
            }
        }

        private static void Require(string value, string field)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                throw new InvalidDataException($"Optical metadata requires {field}.");
            }
        }
    }

    [Serializable]
    public sealed class OpticalDataset
    {
        public TextAsset metadataJson;
        public Texture2D phaseRadians;
        public Texture2D intensity;
        public Texture2D polarization;
        public List<Texture2D> depthPlanes = new List<Texture2D>();
        public List<Texture2D> laneMasks = new List<Texture2D>();

        public OpticalMetadata ParseAndValidateMetadata()
        {
            if (metadataJson == null)
            {
                throw new InvalidDataException("An optical dataset requires metadata.json.");
            }

            OpticalMetadata metadata = JsonUtility.FromJson<OpticalMetadata>(metadataJson.text);
            metadata.Validate();
            return metadata;
        }

        public OpticalMetadata ValidateCompleteDataset()
        {
            OpticalMetadata metadata = ParseAndValidateMetadata();
            if (phaseRadians == null || intensity == null)
            {
                throw new InvalidDataException(
                    "A complete optical dataset requires phase.exr and intensity.exr.");
            }

            if (phaseRadians.width != intensity.width || phaseRadians.height != intensity.height)
            {
                throw new InvalidDataException(
                    "Optical phase and intensity textures must have matching dimensions.");
            }

            if (depthPlanes == null
                || depthPlanes.Count != metadata.depthPlanePositionsMeters.Count)
            {
                throw new InvalidDataException(
                    "Optical depth-plane texture count must match metadata positions.");
            }

            for (int index = 0; index < depthPlanes.Count; index++)
            {
                Texture2D plane = depthPlanes[index];
                if (plane == null
                    || plane.width != phaseRadians.width
                    || plane.height != phaseRadians.height)
                {
                    throw new InvalidDataException(
                        $"Optical depth plane {index} is missing or dimensionally inconsistent.");
                }
            }

            return metadata;
        }
    }
}
