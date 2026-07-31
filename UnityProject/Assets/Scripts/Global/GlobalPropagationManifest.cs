using System;
using System.IO;
using UnityEngine;

namespace SCYTHE.Global
{
    [Serializable]
    public sealed class GlobalAuthorityMetadata
    {
        public string solverName;
        public string solverVersion;
        public string modelName;
        public string sourceRevision;
        public string provenanceStatus;
        public string runId;
        public bool deterministic;
    }

    [Serializable]
    public sealed class GlobalSpatialReferenceMetadata
    {
        public string type;
        public string horizontalCrs;
        public string verticalDatum;
        public string coordinateOrder;
        public string heightUnits;
        public bool ecefCompatible;
        public double[] boundsDegrees;
        public bool crossesAntimeridian;
    }

    [Serializable]
    public sealed class GlobalRfMetadata
    {
        public double frequencyHz;
        public double bandwidthHz;
        public string polarization;
    }

    [Serializable]
    public sealed class GlobalPhysicsMetadata
    {
        public string domain;
        public GlobalRfMetadata rf;
    }

    [Serializable]
    public sealed class GlobalUncertaintyMetadata
    {
        public string kind;
        public string description;
        public string assetPath;
    }

    [Serializable]
    public sealed class GlobalQuantityMetadata
    {
        public string name;
        public string definition;
        public string units;
        public string valueSemantics;
        public GlobalUncertaintyMetadata uncertainty;
    }

    [Serializable]
    public sealed class GlobalGridMetadata
    {
        public string representation;
        public int[] dimensions;
        public double[] resolution;
        public string interpolation;
        public string authoritativeAssetPath;
    }

    [Serializable]
    public sealed class GlobalDatasetAsset
    {
        public string path;
        public string role;
        public string mediaType;
        public string sha256;
        public long sizeBytes;
    }

    [Serializable]
    public sealed class GlobalPropagationManifest
    {
        public string schemaVersion;
        public string datasetId;
        public string title;
        public string description;
        public string evidenceClass;
        public GlobalAuthorityMetadata authority;
        public GlobalSpatialReferenceMetadata spatialReference;
        public GlobalPhysicsMetadata physics;
        public GlobalQuantityMetadata quantity;
        public GlobalGridMetadata grid;
        public GlobalDatasetAsset[] assets;
        public bool visualizationIsAuthoritative;

        public EvidenceClass ParsedEvidenceClass =>
            EvidenceStyleRouter.Parse(evidenceClass);

        public static GlobalPropagationManifest Parse(string json)
        {
            GlobalPropagationManifest manifest =
                JsonUtility.FromJson<GlobalPropagationManifest>(json);
            if (manifest == null)
            {
                throw new InvalidDataException(
                    "Global propagation manifest could not be parsed.");
            }

            manifest.ValidateForUnityConsumer();
            return manifest;
        }

        public void ValidateForUnityConsumer()
        {
            if (!string.Equals(schemaVersion, "1.0", StringComparison.Ordinal)
                || string.IsNullOrWhiteSpace(datasetId)
                || string.IsNullOrWhiteSpace(title))
            {
                throw new InvalidDataException(
                    "Global dataset identity or schema version is invalid.");
            }

            _ = ParsedEvidenceClass;
            if (visualizationIsAuthoritative)
            {
                throw new InvalidDataException(
                    "Unity refuses manifests that claim visualization is authoritative.");
            }

            if (authority == null
                || string.IsNullOrWhiteSpace(authority.solverName)
                || string.IsNullOrWhiteSpace(authority.solverVersion)
                || string.IsNullOrWhiteSpace(authority.sourceRevision)
                || string.IsNullOrWhiteSpace(authority.runId))
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} has incomplete solver provenance.");
            }

            if (spatialReference == null
                || !string.Equals(
                    spatialReference.type,
                    "GEODETIC_GRID",
                    StringComparison.Ordinal)
                || !string.Equals(
                    spatialReference.horizontalCrs,
                    "EPSG:4326",
                    StringComparison.Ordinal)
                || !string.Equals(
                    spatialReference.coordinateOrder,
                    "longitude,latitude,height",
                    StringComparison.Ordinal)
                || !string.Equals(
                    spatialReference.heightUnits,
                    "m",
                    StringComparison.Ordinal)
                || !string.Equals(
                    spatialReference.verticalDatum,
                    "WGS84_ELLIPSOID",
                    StringComparison.Ordinal)
                || !spatialReference.ecefCompatible)
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} is not an ECEF-compatible EPSG:4326 grid.");
            }

            if (spatialReference.boundsDegrees == null
                || spatialReference.boundsDegrees.Length != 4)
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} requires west/south/east/north bounds.");
            }

            if (spatialReference.crossesAntimeridian)
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} crosses the antimeridian; "
                    + "the first Unity adapter requires pre-split tiles.");
            }

            if (physics == null
                || (physics.domain != "RF" && physics.domain != "RF_AND_OPTICAL")
                || physics.rf == null
                || physics.rf.frequencyHz <= 0d)
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} does not declare an RF quantity.");
            }

            if (quantity == null
                || string.IsNullOrWhiteSpace(quantity.name)
                || string.IsNullOrWhiteSpace(quantity.units)
                || quantity.uncertainty == null
                || string.IsNullOrWhiteSpace(quantity.uncertainty.kind))
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} has incomplete quantity or uncertainty metadata.");
            }

            if (grid == null
                || grid.dimensions == null
                || grid.dimensions.Length < 2
                || grid.dimensions[0] < 1
                || grid.dimensions[1] < 1
                || string.IsNullOrWhiteSpace(grid.authoritativeAssetPath)
                || assets == null
                || assets.Length == 0)
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} has incomplete grid metadata.");
            }

            GlobalDatasetAsset authorityAsset =
                FindAsset(grid.authoritativeAssetPath);
            if (authorityAsset == null
                || !string.Equals(
                    authorityAsset.role,
                    "AUTHORITATIVE_VALUES",
                    StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    $"Global dataset {datasetId} does not identify its authoritative values.");
            }

            foreach (GlobalDatasetAsset asset in assets)
            {
                if (asset == null
                    || !IsSafeRelativePath(asset.path)
                    || asset.sizeBytes < 0
                    || !IsLowerHexSha256(asset.sha256))
                {
                    throw new InvalidDataException(
                        $"Global dataset {datasetId} contains invalid asset metadata.");
                }
            }
        }

        public GlobalDatasetAsset FindAsset(string relativePath)
        {
            if (assets == null)
            {
                return null;
            }

            foreach (GlobalDatasetAsset asset in assets)
            {
                if (asset != null
                    && string.Equals(
                        asset.path,
                        relativePath,
                        StringComparison.Ordinal))
                {
                    return asset;
                }
            }

            return null;
        }

        public static bool IsSafeRelativePath(string value)
        {
            return !string.IsNullOrWhiteSpace(value)
                && !Path.IsPathRooted(value)
                && !value.Contains("..")
                && !value.Contains("\\");
        }

        private static bool IsLowerHexSha256(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.Length != 64)
            {
                return false;
            }

            foreach (char character in value)
            {
                bool isHex = (character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f');
                if (!isHex)
                {
                    return false;
                }
            }

            return true;
        }
    }
}
