using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.Global
{
    public readonly struct GlobalFieldSample
    {
        public GlobalFieldSample(
            bool isAvailable,
            string status,
            string datasetId,
            EvidenceClass evidenceClass,
            string quantityName,
            string units,
            double value)
        {
            IsAvailable = isAvailable;
            Status = status;
            DatasetId = datasetId;
            EvidenceClass = evidenceClass;
            QuantityName = quantityName;
            Units = units;
            Value = value;
        }

        public bool IsAvailable { get; }
        public string Status { get; }
        public string DatasetId { get; }
        public EvidenceClass EvidenceClass { get; }
        public string QuantityName { get; }
        public string Units { get; }
        public double Value { get; }
    }

    public sealed class GlobalDatasetManager : MonoBehaviour
    {
        [SerializeField] private CesiumGeospatialAdapter geospatialAdapter;
        [SerializeField] private string status = "NOT INITIALIZED";
        [SerializeField] private int validatedDatasetCount;

        private readonly List<GlobalPropagationManifest> datasets =
            new List<GlobalPropagationManifest>();
        private ScenarioGlobalSettings settings;
        private DateTimeOffset utcEpoch;

        public string Status => status;
        public int ValidatedDatasetCount => validatedDatasetCount;
        public IReadOnlyList<GlobalPropagationManifest> Datasets => datasets;
        public GeodeticPosition OperatorPosition { get; private set; }
        public bool HasOperatorPosition { get; private set; }
        public GlobalFieldSample LastSample { get; private set; }

        public void Bind(
            ScenarioGlobalSettings globalSettings,
            CesiumGeospatialAdapter adapter)
        {
            settings = globalSettings;
            geospatialAdapter = adapter;
            InitializeDatasets();
        }

        private void Update()
        {
            GeodeticPosition position = default;
            HasOperatorPosition = geospatialAdapter != null
                && geospatialAdapter.TryGetOperatorPosition(out position);
            if (HasOperatorPosition)
            {
                OperatorPosition = position;
            }
        }

        public DateTimeOffset CurrentUtc()
        {
            return utcEpoch.AddSeconds(SimulationClock.TimeSeconds);
        }

        public GlobalFieldSample SampleRFField(
            double latitudeDegrees,
            double longitudeDegrees,
            double heightMeters,
            DateTimeOffset utc,
            double frequencyHz)
        {
            foreach (GlobalPropagationManifest dataset in datasets)
            {
                double declaredFrequency = dataset.physics.rf.frequencyHz;
                double bandwidth = Math.Max(dataset.physics.rf.bandwidthHz, 0d);
                double tolerance = Math.Max(0.5d * bandwidth, 1d);
                if (Math.Abs(frequencyHz - declaredFrequency) > tolerance)
                {
                    continue;
                }

                LastSample = new GlobalFieldSample(
                    false,
                    $"VALIDATED {dataset.grid.representation} AUTHORITY; "
                        + "NO UNITY TILE ADAPTER REGISTERED",
                    dataset.datasetId,
                    dataset.ParsedEvidenceClass,
                    dataset.quantity.name,
                    dataset.quantity.units,
                    double.NaN);
                return LastSample;
            }

            LastSample = new GlobalFieldSample(
                false,
                datasets.Count == 0
                    ? "NO REGISTERED GLOBAL DATASET"
                    : "NO DATASET MATCHES QUERY",
                string.Empty,
                EvidenceClass.Illustrative,
                string.Empty,
                string.Empty,
                double.NaN);
            return LastSample;
        }

        private void InitializeDatasets()
        {
            datasets.Clear();
            validatedDatasetCount = 0;
            LastSample = default;
            if (settings == null || !settings.enabled)
            {
                utcEpoch = DateTimeOffset.UnixEpoch;
                status = "GLOBAL MODE DISABLED";
                return;
            }

            utcEpoch = DateTimeOffset.Parse(
                settings.utcEpoch,
                CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);
            foreach (ScenarioGlobalDatasetReference reference in settings.datasets)
            {
                try
                {
                    datasets.Add(
                        LoadAndVerify(
                            reference.manifestRelativePath,
                            reference.manifestSha256));
                }
                catch (Exception error)
                {
                    if (reference.required)
                    {
                        throw;
                    }

                    Debug.LogWarning(
                        $"[SCYTHE GLOBAL] Optional dataset rejected: {error.Message}");
                }
            }

            validatedDatasetCount = datasets.Count;
            if (validatedDatasetCount == 0)
            {
                status = "NO REGISTERED GLOBAL DATASET // NO FALLBACK";
                if (settings.globalDatasetRequired)
                {
                    throw new InvalidDataException(
                        "Scenario requires global data but no dataset passed validation.");
                }
            }
            else
            {
                status = $"{validatedDatasetCount} CONTRACT-VALIDATED DATASET(S)";
            }
        }

        public static GlobalPropagationManifest LoadAndVerify(
            string manifestRelativePath,
            string expectedManifestSha256)
        {
            if (!GlobalPropagationManifest.IsSafeRelativePath(manifestRelativePath))
            {
                throw new InvalidDataException(
                    $"Unsafe global manifest path {manifestRelativePath}.");
            }

            string streamingRoot = Path.GetFullPath(Application.streamingAssetsPath);
            string manifestPath = Path.GetFullPath(
                Path.Combine(streamingRoot, manifestRelativePath));
            RequireInside(streamingRoot, manifestPath);
            if (!File.Exists(manifestPath))
            {
                throw new FileNotFoundException(
                    "Global dataset manifest is missing.",
                    manifestPath);
            }

            string manifestChecksum = Sha256File(manifestPath);
            if (!string.Equals(
                    manifestChecksum,
                    expectedManifestSha256,
                    StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    $"Global manifest checksum mismatch for {manifestRelativePath}.");
            }

            GlobalPropagationManifest manifest = GlobalPropagationManifest.Parse(
                File.ReadAllText(manifestPath));
            string datasetRoot = Path.GetDirectoryName(manifestPath)
                ?? throw new InvalidDataException("Global manifest has no parent directory.");
            foreach (GlobalDatasetAsset asset in manifest.assets)
            {
                string assetPath = Path.GetFullPath(Path.Combine(datasetRoot, asset.path));
                RequireInside(datasetRoot, assetPath);
                if (!File.Exists(assetPath))
                {
                    throw new FileNotFoundException(
                        $"Global dataset asset {asset.path} is missing.",
                        assetPath);
                }

                var file = new FileInfo(assetPath);
                if (file.Length != asset.sizeBytes)
                {
                    throw new InvalidDataException(
                        $"Global dataset asset {asset.path} size mismatch.");
                }

                string checksum = Sha256File(assetPath);
                if (!string.Equals(
                        checksum,
                        asset.sha256,
                        StringComparison.Ordinal))
                {
                    throw new InvalidDataException(
                        $"Global dataset asset {asset.path} checksum mismatch.");
                }
            }

            return manifest;
        }

        private static string Sha256File(string path)
        {
            using (SHA256 sha256 = SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
            {
                byte[] digest = sha256.ComputeHash(stream);
                return BitConverter.ToString(digest)
                    .Replace("-", string.Empty)
                    .ToLowerInvariant();
            }
        }

        private static void RequireInside(string root, string path)
        {
            string normalizedRoot = Path.GetFullPath(root)
                .TrimEnd(Path.DirectorySeparatorChar)
                + Path.DirectorySeparatorChar;
            if (!path.StartsWith(normalizedRoot, StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    $"Dataset path escapes its declared root: {path}");
            }
        }
    }
}
