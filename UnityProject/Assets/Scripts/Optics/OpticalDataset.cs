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

            if (wavelengthNm <= 0f || sampleSpacingMeters <= 0f)
            {
                throw new InvalidDataException("Optical wavelength and sample spacing must be positive.");
            }

            if (!string.Equals(phaseUnits, "radians", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("SCYTHE optical phase assets must use radians.");
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
    }
}
