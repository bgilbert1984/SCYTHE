using System;
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

        [SerializeField] private RFTransmitter transmitter;
        [SerializeField] private RFReceiver receiver;
        [SerializeField, Min(0.05f)] private float frameIntervalSeconds = 0.5f;

        private ScenarioManifest manifest;
        private double nextFrameTime;
        private int frameIndex;

        public RFLinkResult LastResult { get; private set; }
        public ScenarioManifest Manifest => manifest;
        public RFTransmitter Transmitter => transmitter;
        public RFReceiver Receiver => receiver;
        public float LinkDistanceMeters => transmitter == null || receiver == null
            ? 0f
            : Vector3.Distance(transmitter.transform.position, receiver.transform.position);
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
        public float RadialVelocityTowardTransmitter
        {
            get
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
        }

        public event Action<RFLinkResult> FrameCompleted;

        public void Bind(RFTransmitter linkTransmitter, RFReceiver linkReceiver)
        {
            transmitter = linkTransmitter;
            receiver = linkReceiver;
        }

        private void Awake()
        {
            manifest = ScenarioManifest.Load("Scenarios/rf_milestone_01.json");
            transmitter.Configure(manifest);
            transmitter.SetPayload(KnownPayload);
            receiver.Configure(manifest.channelNoiseStdDev);
            frameIntervalSeconds = manifest.linkFrameIntervalSeconds;
            transmitter.Modulation = ModulationType.Bpsk;
        }

        private void Start()
        {
            RunFrame();
            nextFrameTime = SimulationClock.TimeSeconds + frameIntervalSeconds;
            ScytheDiagnostics.Log($"Loaded scenario {manifest.scenarioId} with seed {manifest.seed}.");
        }

        private void Update()
        {
            HandleModulationInput();
            transmitter.transform.Rotate(0f, 30f * UnityEngine.Time.deltaTime, 0f, Space.World);

            if (SimulationClock.TimeSeconds >= nextFrameTime)
            {
                RunFrame();
                nextFrameTime += frameIntervalSeconds;
            }
        }

        public void SelectModulation(ModulationType modulation)
        {
            if (transmitter.Modulation == modulation)
            {
                return;
            }

            transmitter.Modulation = modulation;
            RunFrame();
        }

        public void RunFrame()
        {
            LastResult = RFLinkPipeline.Run(
                transmitter.Payload,
                transmitter.Modulation,
                transmitter.SamplesPerSymbol,
                transmitter.SymbolRateBaud,
                LinkDistanceMeters,
                transmitter.CarrierFrequencyHz,
                receiver.NoiseStdDev,
                manifest.seed + frameIndex,
                RadialVelocityTowardTransmitter);
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

        private void HandleModulationInput()
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

            if (Input.GetKeyDown(KeyCode.Escape))
            {
                Application.Quit();
            }
        }
    }
}
