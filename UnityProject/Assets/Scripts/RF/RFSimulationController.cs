using System;
using System.Collections.Generic;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.RF
{
    public sealed class RFSimulationController : MonoBehaviour
    {
        private static readonly bool[] KnownPayload =
        {
            true, false, true, true, false, false, true, false,
            true, false, false, true, true, true, false, true,
        };

        [SerializeField] private List<RFTransmitter> transmitters = new List<RFTransmitter>();
        [SerializeField] private RFReceiver receiver;
        [SerializeField] private RFOcclusionModel occlusionModel;
        [SerializeField, Min(0.05f)] private float frameIntervalSeconds = 0.5f;

        private readonly Dictionary<string, RFLinkResult> lastResults =
            new Dictionary<string, RFLinkResult>(StringComparer.OrdinalIgnoreCase);
        private ScenarioManifest manifest;
        private double nextFrameTime;
        private int frameIndex;
        private int activeTransmitterIndex;

        public RFLinkResult LastResult => GetResult(Transmitter);
        public ScenarioManifest Manifest => manifest;
        public RFTransmitter Transmitter => transmitters.Count == 0
            ? null
            : transmitters[Mathf.Clamp(activeTransmitterIndex, 0, transmitters.Count - 1)];
        public IReadOnlyList<RFTransmitter> Transmitters => transmitters;
        public RFReceiver Receiver => receiver;
        public RFOcclusionModel OcclusionModel => occlusionModel;
        public int ActiveTransmitterIndex => activeTransmitterIndex;
        public float LinkDistanceMeters => DistanceTo(Transmitter);

        public Vector3 ReceiverVelocity
        {
            get
            {
                if (receiver == null)
                {
                    return Vector3.zero;
                }

                CharacterController character = receiver.GetComponent<CharacterController>();
                return character != null ? character.velocity : Vector3.zero;
            }
        }

        public float RadialVelocityTowardTransmitter =>
            RadialVelocityToward(Transmitter);

        public event Action<RFLinkResult> FrameCompleted;
        public event Action<RFTransmitter> ActiveTransmitterChanged;

        public void Bind(
            IReadOnlyList<RFTransmitter> sceneTransmitters,
            RFReceiver linkReceiver,
            RFOcclusionModel sceneOcclusionModel)
        {
            transmitters.Clear();
            if (sceneTransmitters != null)
            {
                for (int index = 0; index < sceneTransmitters.Count; index++)
                {
                    transmitters.Add(sceneTransmitters[index]);
                }
            }

            receiver = linkReceiver;
            occlusionModel = sceneOcclusionModel;
        }

        private void Awake()
        {
            manifest = ScenarioManifest.Load("Scenarios/rf_milestone_01.json");
            if (transmitters.Count != manifest.transmitters.Count)
            {
                throw new InvalidOperationException(
                    "Generated scene transmitter count does not match the scenario manifest.");
            }

            for (int index = 0; index < transmitters.Count; index++)
            {
                ScenarioTransmitter definition = manifest.transmitters[index];
                if (!string.Equals(
                    transmitters[index].EmitterId,
                    definition.id,
                    StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidOperationException(
                        $"Scene transmitter {transmitters[index].EmitterId} does not match manifest {definition.id}.");
                }

                transmitters[index].Configure(definition);
                transmitters[index].SetPayload(KnownPayload);
            }

            receiver.Configure(manifest.channelNoiseStdDev);
            occlusionModel.Configure(manifest.occlusion);
            frameIntervalSeconds = manifest.linkFrameIntervalSeconds;
        }

        private void Start()
        {
            UpdateTransmitterPositions();
            RunFrame();
            nextFrameTime = SimulationClock.TimeSeconds + frameIntervalSeconds;
            ScytheDiagnostics.Log(
                $"Loaded scenario {manifest.scenarioId} with seed {manifest.seed}, "
                + $"{transmitters.Count} transmitters, and occlusion "
                + $"{(occlusionModel.ModelEnabled ? "enabled" : "disabled")}.");
        }

        private void Update()
        {
            HandleInput();
            UpdateTransmitterPositions();

            if (SimulationClock.TimeSeconds >= nextFrameTime)
            {
                RunFrame();
                nextFrameTime += frameIntervalSeconds;
            }
        }

        public RFLinkResult GetResult(RFTransmitter transmitter)
        {
            if (transmitter == null)
            {
                return null;
            }

            return lastResults.TryGetValue(transmitter.EmitterId, out RFLinkResult result)
                ? result
                : null;
        }

        public RFTransmitter FindTransmitter(string emitterId)
        {
            return transmitters.Find(
                transmitter => string.Equals(
                    transmitter.EmitterId,
                    emitterId,
                    StringComparison.OrdinalIgnoreCase));
        }

        public float DistanceTo(RFTransmitter transmitter)
        {
            return transmitter == null || receiver == null
                ? 0f
                : Vector3.Distance(transmitter.transform.position, receiver.transform.position);
        }

        public float RadialVelocityToward(RFTransmitter transmitter)
        {
            if (transmitter == null || receiver == null)
            {
                return 0f;
            }

            Vector3 lineOfSight = transmitter.transform.position - receiver.transform.position;
            lineOfSight.y = 0f;
            return lineOfSight.sqrMagnitude < 0.000001f
                ? 0f
                : Vector3.Dot(ReceiverVelocity, lineOfSight.normalized);
        }

        public RFOcclusionReading SampleOcclusion(RFTransmitter transmitter)
        {
            return transmitter == null || receiver == null || occlusionModel == null
                ? new RFOcclusionReading(0, 0f)
                : occlusionModel.Sample(transmitter.transform.position, receiver.transform.position);
        }

        public void SelectModulation(ModulationType modulation)
        {
            if (Transmitter == null || Transmitter.Modulation == modulation)
            {
                return;
            }

            Transmitter.Modulation = modulation;
            RunFrame();
        }

        public void SelectActiveTransmitter(string emitterId)
        {
            int index = transmitters.FindIndex(
                transmitter => string.Equals(
                    transmitter.EmitterId,
                    emitterId,
                    StringComparison.OrdinalIgnoreCase));
            if (index < 0 || index == activeTransmitterIndex)
            {
                return;
            }

            activeTransmitterIndex = index;
            ActiveTransmitterChanged?.Invoke(Transmitter);
        }

        public void CycleActiveTransmitter()
        {
            if (transmitters.Count < 2)
            {
                return;
            }

            activeTransmitterIndex = (activeTransmitterIndex + 1) % transmitters.Count;
            ActiveTransmitterChanged?.Invoke(Transmitter);
        }

        public void RunFrame()
        {
            for (int index = 0; index < transmitters.Count; index++)
            {
                RFTransmitter transmitter = transmitters[index];
                if (!transmitter.IsRadiating)
                {
                    lastResults.Remove(transmitter.EmitterId);
                    continue;
                }

                RFOcclusionReading occlusion = SampleOcclusion(transmitter);
                RFLinkResult result = RFLinkPipeline.Run(
                    transmitter.Payload,
                    transmitter.Modulation,
                    transmitter.SamplesPerSymbol,
                    transmitter.SymbolRateBaud,
                    DistanceTo(transmitter),
                    transmitter.CarrierFrequencyHz,
                    receiver.NoiseStdDev,
                    manifest.seed + frameIndex * 97 + index * 1009,
                    RadialVelocityToward(transmitter),
                    occlusion.AttenuationDb);
                lastResults[transmitter.EmitterId] = result;
            }

            frameIndex++;
            FrameCompleted?.Invoke(LastResult);
        }

        public static string FormatBits(bool[] bits)
        {
            if (bits == null)
            {
                return "—";
            }

            var characters = new char[bits.Length];
            for (int index = 0; index < bits.Length; index++)
            {
                characters[index] = bits[index] ? '1' : '0';
            }

            return new string(characters);
        }

        private void UpdateTransmitterPositions()
        {
            for (int index = 0; index < transmitters.Count; index++)
            {
                transmitters[index].UpdateSpatialState(SimulationClock.TimeSeconds);
                transmitters[index].transform.Rotate(
                    0f,
                    20f * UnityEngine.Time.deltaTime,
                    0f,
                    Space.World);
            }
        }

        private void HandleInput()
        {
            if (Input.GetKeyDown(KeyCode.Alpha1))
            {
                SelectModulation(ModulationType.Ask);
            }
            else if (Input.GetKeyDown(KeyCode.Alpha2))
            {
                SelectModulation(ModulationType.Fsk);
            }
            else if (Input.GetKeyDown(KeyCode.Alpha3))
            {
                SelectModulation(ModulationType.Bpsk);
            }
            else if (Input.GetKeyDown(KeyCode.Alpha4))
            {
                SelectModulation(ModulationType.Qpsk);
            }
            else if (Input.GetKeyDown(KeyCode.T))
            {
                CycleActiveTransmitter();
            }

            if (Input.GetKeyDown(KeyCode.Escape))
            {
                Application.Quit();
            }
        }
    }
}
