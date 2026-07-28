using System;
using System.Collections.Generic;
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
    public sealed class ScenarioMotion
    {
        public string type = "static";
        public ScenarioVector3 direction = new ScenarioVector3(1f, 0f, 0f);
        public float speedMetersPerSecond;
        public float extentMeters;

        public void Validate(string transmitterId)
        {
            bool isStatic = string.Equals(type, "static", StringComparison.OrdinalIgnoreCase);
            bool isPingPong = string.Equals(type, "pingPong", StringComparison.OrdinalIgnoreCase);
            if (!isStatic && !isPingPong)
            {
                throw new InvalidDataException(
                    $"Transmitter {transmitterId} motion type must be static or pingPong.");
            }

            if (direction == null || speedMetersPerSecond < 0f || extentMeters < 0f)
            {
                throw new InvalidDataException($"Transmitter {transmitterId} motion is invalid.");
            }

            if (isPingPong
                && (speedMetersPerSecond <= 0f
                    || extentMeters <= 0f
                    || direction.ToVector3().sqrMagnitude < 0.000001f))
            {
                throw new InvalidDataException(
                    $"Transmitter {transmitterId} pingPong motion requires direction, speed, and extent.");
            }
        }
    }

    [Serializable]
    public sealed class ScenarioTransmitter
    {
        public string id = "TX-A";
        public string displayName = "Emitter Alpha";
        public bool enabled = true;
        public string modulation = "Bpsk";
        public float carrierFrequencyHz = 2.4e9f;
        public float symbolRateBaud = 1000f;
        public int samplesPerSymbol = 16;
        public float powerWatts = 1f;
        public ScenarioVector3 positionMeters = new ScenarioVector3(-4f, 0.25f, 1.5f);
        public ScenarioMotion motion = new ScenarioMotion();

        public void Validate()
        {
            if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(displayName))
            {
                throw new InvalidDataException("Every transmitter requires an id and displayName.");
            }

            if (carrierFrequencyHz <= 0f
                || symbolRateBaud <= 0f
                || samplesPerSymbol < 4
                || powerWatts <= 0f
                || positionMeters == null)
            {
                throw new InvalidDataException($"Transmitter {id} has invalid RF or spatial parameters.");
            }

            string normalizedModulation = modulation?.Trim().ToLowerInvariant();
            if (normalizedModulation != "ask"
                && normalizedModulation != "fsk"
                && normalizedModulation != "bpsk"
                && normalizedModulation != "qpsk")
            {
                throw new InvalidDataException($"Transmitter {id} has unsupported modulation {modulation}.");
            }

            (motion ?? throw new InvalidDataException($"Transmitter {id} requires motion metadata."))
                .Validate(id);
        }
    }

    [Serializable]
    public sealed class ScenarioOcclusion
    {
        public bool enabled = true;
        public float lossDbPerBlocker = 18f;
        public int maximumBlockers = 4;

        public void Validate()
        {
            if (lossDbPerBlocker < 0f || maximumBlockers < 1)
            {
                throw new InvalidDataException("RF occlusion loss and blocker limit are invalid.");
            }
        }
    }

    [Serializable]
    public sealed class ScenarioEvent
    {
        public float timeSeconds;
        public string action;
        public string transmitterId;
        public float numericValue;
        public string textValue;

        public void Validate(float previousTime)
        {
            if (timeSeconds < 0f || timeSeconds < previousTime)
            {
                throw new InvalidDataException("Scenario events must be non-negative and time ordered.");
            }

            switch (action?.Trim().ToLowerInvariant())
            {
                case "setenabled":
                case "setpowerwatts":
                case "setmodulation":
                case "selectactive":
                    break;
                default:
                    throw new InvalidDataException($"Unsupported scenario event action {action}.");
            }

            if (string.IsNullOrWhiteSpace(transmitterId))
            {
                throw new InvalidDataException("Every scenario event requires transmitterId.");
            }

            string normalizedAction = action.Trim().ToLowerInvariant();
            if (normalizedAction == "setpowerwatts" && numericValue <= 0f)
            {
                throw new InvalidDataException("setPowerWatts requires a positive numericValue.");
            }

            if (normalizedAction == "setenabled"
                && numericValue != 0f
                && numericValue != 1f)
            {
                throw new InvalidDataException("setEnabled numericValue must be exactly 0 or 1.");
            }

            if (normalizedAction == "setmodulation")
            {
                string normalizedModulation = textValue?.Trim().ToLowerInvariant();
                if (normalizedModulation != "ask"
                    && normalizedModulation != "fsk"
                    && normalizedModulation != "bpsk"
                    && normalizedModulation != "qpsk")
                {
                    throw new InvalidDataException(
                        "setModulation textValue must be ASK, FSK, BPSK, or QPSK.");
                }
            }
        }
    }

    [Serializable]
    public sealed class ScenarioManifest
    {
        public string schemaVersion = "2.0";
        public string scenarioId = "rf-milestone-02";
        public string displayName = "Multi-Emitter RF Environment";
        public string description =
            "Deterministic multi-emitter baseband links with explicit geometric occlusion approximation.";
        public int seed = 424242;
        public float fixedStepSeconds = 1f / 60f;
        public float channelNoiseStdDev = 0.00002f;
        public float linkFrameIntervalSeconds = 0.5f;
        public ScenarioVector3 probeStartPositionMeters = new ScenarioVector3(4f, 0.05f, -2f);
        public float probeWalkSpeedMetersPerSecond = 3.5f;
        public float probeSprintSpeedMetersPerSecond = 6f;
        public List<ScenarioTransmitter> transmitters = new List<ScenarioTransmitter>();
        public ScenarioOcclusion occlusion = new ScenarioOcclusion();
        public List<ScenarioEvent> events = new List<ScenarioEvent>();
        public string opticalDatasetRelativeDirectory = "";
        public bool opticalDatasetRequired;

        public static ScenarioManifest Load(string relativePath)
        {
            string path = Path.Combine(Application.streamingAssetsPath, relativePath);
            if (!File.Exists(path))
            {
                ScytheDiagnostics.Warn($"Scenario manifest not found at {path}; using explicit built-in defaults.");
                ScenarioManifest fallback = CreateFallback();
                fallback.Validate();
                return fallback;
            }

            ScenarioManifest manifest = JsonUtility.FromJson<ScenarioManifest>(File.ReadAllText(path));
            if (manifest == null)
            {
                throw new InvalidDataException($"Scenario manifest at {path} could not be parsed.");
            }

            manifest.Validate();
            return manifest;
        }

        public void Validate()
        {
            if (string.IsNullOrWhiteSpace(scenarioId))
            {
                throw new InvalidDataException("Scenario manifest requires scenarioId.");
            }

            if (!string.Equals(schemaVersion, "2.0", StringComparison.Ordinal))
            {
                throw new InvalidDataException($"Unsupported scenario schemaVersion {schemaVersion}; expected 2.0.");
            }

            if (channelNoiseStdDev < 0f
                || fixedStepSeconds <= 0f
                || linkFrameIntervalSeconds <= 0f
                || probeWalkSpeedMetersPerSecond <= 0f
                || probeSprintSpeedMetersPerSecond < probeWalkSpeedMetersPerSecond
                || probeStartPositionMeters == null)
            {
                throw new InvalidDataException("Scenario timing, noise, position, or movement settings are invalid.");
            }

            if (transmitters == null || transmitters.Count < 1)
            {
                throw new InvalidDataException("A scenario requires at least one transmitter.");
            }

            var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (ScenarioTransmitter transmitter in transmitters)
            {
                transmitter?.Validate();
                if (transmitter == null || !ids.Add(transmitter.id))
                {
                    throw new InvalidDataException("Transmitter ids must be non-empty and unique.");
                }
            }

            (occlusion ?? throw new InvalidDataException("Scenario requires occlusion metadata.")).Validate();

            float previousTime = -1f;
            if (events == null)
            {
                events = new List<ScenarioEvent>();
            }

            foreach (ScenarioEvent scenarioEvent in events)
            {
                scenarioEvent?.Validate(previousTime);
                if (scenarioEvent == null || !ids.Contains(scenarioEvent.transmitterId))
                {
                    throw new InvalidDataException("Scenario event references an unknown transmitter.");
                }

                previousTime = scenarioEvent.timeSeconds;
            }

            if (!string.IsNullOrEmpty(opticalDatasetRelativeDirectory))
            {
                if (Path.IsPathRooted(opticalDatasetRelativeDirectory)
                    || opticalDatasetRelativeDirectory.Contains(".."))
                {
                    throw new InvalidDataException("Optical dataset directory must be a safe relative path.");
                }
            }
            else if (opticalDatasetRequired)
            {
                throw new InvalidDataException("A required optical dataset must declare its relative directory.");
            }
        }

        private static ScenarioManifest CreateFallback()
        {
            var manifest = new ScenarioManifest();
            manifest.transmitters.Add(new ScenarioTransmitter());
            return manifest;
        }
    }
}
