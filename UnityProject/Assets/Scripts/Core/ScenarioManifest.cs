using System;
using System.IO;
using UnityEngine;

namespace SCYTHE.Core
{
    [Serializable]
    public sealed class ScenarioVector3
    {
        public float x;
        public float y;
        public float z;

        public ScenarioVector3()
        {
        }

        public ScenarioVector3(float x, float y, float z)
        {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        public Vector3 ToVector3()
        {
            return new Vector3(x, y, z);
        }
    }

    [Serializable]
    public sealed class ScenarioManifest
    {
        public string schemaVersion = "1.0";
        public string scenarioId = "rf-milestone-01";
        public string displayName = "Single RF Link";
        public string description = "Deterministic baseband link with an approximate free-space channel.";
        public int seed = 424242;
        public float fixedStepSeconds = 1f / 60f;
        public float carrierFrequencyHz = 2.4e9f;
        public float symbolRateBaud = 1000f;
        public int samplesPerSymbol = 16;
        public float transmitterPowerWatts = 1f;
        public float channelNoiseStdDev = 0.00002f;
        public float linkFrameIntervalSeconds = 0.5f;
        public ScenarioVector3 transmitterPositionMeters = new ScenarioVector3(-4f, 0.25f, 1.5f);
        public ScenarioVector3 probeStartPositionMeters = new ScenarioVector3(4f, 0.05f, -2f);
        public float probeWalkSpeedMetersPerSecond = 3.5f;
        public float probeSprintSpeedMetersPerSecond = 6f;

        public static ScenarioManifest Load(string relativePath)
        {
            string path = Path.Combine(Application.streamingAssetsPath, relativePath);
            if (!File.Exists(path))
            {
                ScytheDiagnostics.Warn($"Scenario manifest not found at {path}; using explicit built-in defaults.");
                return new ScenarioManifest();
            }

            ScenarioManifest manifest = JsonUtility.FromJson<ScenarioManifest>(File.ReadAllText(path));
            manifest.Validate();
            return manifest;
        }

        public void Validate()
        {
            if (string.IsNullOrWhiteSpace(scenarioId))
            {
                throw new InvalidDataException("Scenario manifest requires scenarioId.");
            }

            if (carrierFrequencyHz <= 0f || symbolRateBaud <= 0f)
            {
                throw new InvalidDataException("Carrier frequency and symbol rate must be positive.");
            }

            if (samplesPerSymbol < 4)
            {
                throw new InvalidDataException("samplesPerSymbol must be at least 4.");
            }

            if (transmitterPowerWatts <= 0f || channelNoiseStdDev < 0f)
            {
                throw new InvalidDataException("Transmitter power must be positive and noise cannot be negative.");
            }

            if (linkFrameIntervalSeconds <= 0f
                || probeWalkSpeedMetersPerSecond <= 0f
                || probeSprintSpeedMetersPerSecond < probeWalkSpeedMetersPerSecond
                || transmitterPositionMeters == null
                || probeStartPositionMeters == null)
            {
                throw new InvalidDataException("Spatial scenario timing, positions, and movement speeds are invalid.");
            }
        }
    }
}
