using System;
using SCYTHE.RF;
using UnityEngine;

namespace SCYTHE.Core
{
    /// <summary>
    /// Executes the manifest event list against deterministic simulation time.
    /// Events are applied once, in declared order, independent of render rate.
    /// </summary>
    public sealed class ScenarioDirector : MonoBehaviour
    {
        [SerializeField] private RFSimulationController simulation;

        private int nextEventIndex;

        public string LastEventLabel { get; private set; } = "none";
        public int ExecutedEventCount => nextEventIndex;
        public int TotalEventCount => simulation?.Manifest?.events?.Count ?? 0;
        public float SecondsUntilNextEvent
        {
            get
            {
                if (simulation?.Manifest?.events == null
                    || nextEventIndex >= simulation.Manifest.events.Count)
                {
                    return float.PositiveInfinity;
                }

                return Mathf.Max(
                    0f,
                    simulation.Manifest.events[nextEventIndex].timeSeconds
                        - (float)SimulationClock.TimeSeconds);
            }
        }

        public void Bind(RFSimulationController controller)
        {
            simulation = controller;
        }

        private void Start()
        {
            if (simulation == null || simulation.Manifest == null)
            {
                throw new InvalidOperationException("ScenarioDirector requires an initialized RF simulation.");
            }

            SimulationClock.Ticked += OnSimulationTick;
            ProcessDueEvents();
        }

        private void OnDestroy()
        {
            SimulationClock.Ticked -= OnSimulationTick;
        }

        private void OnSimulationTick(double deltaSeconds)
        {
            ProcessDueEvents();
        }

        private void ProcessDueEvents()
        {
            while (nextEventIndex < simulation.Manifest.events.Count
                && simulation.Manifest.events[nextEventIndex].timeSeconds
                    <= SimulationClock.TimeSeconds + 0.000001d)
            {
                ScenarioEvent scenarioEvent = simulation.Manifest.events[nextEventIndex];
                Execute(scenarioEvent);
                nextEventIndex++;
            }
        }

        private void Execute(ScenarioEvent scenarioEvent)
        {
            RFTransmitter transmitter = simulation.FindTransmitter(scenarioEvent.transmitterId);
            if (transmitter == null)
            {
                throw new InvalidOperationException(
                    $"Scenario event references missing transmitter {scenarioEvent.transmitterId}.");
            }

            switch (scenarioEvent.action.Trim().ToLowerInvariant())
            {
                case "setenabled":
                    transmitter.SetRadiating(scenarioEvent.numericValue >= 0.5f);
                    break;
                case "setpowerwatts":
                    transmitter.SetPowerWatts(scenarioEvent.numericValue);
                    break;
                case "setmodulation":
                    transmitter.Modulation = RFTransmitter.ParseModulation(scenarioEvent.textValue);
                    break;
                case "selectactive":
                    simulation.SelectActiveTransmitter(transmitter.EmitterId);
                    break;
                default:
                    throw new InvalidOperationException(
                        $"Unsupported scenario action {scenarioEvent.action}.");
            }

            LastEventLabel =
                $"t={scenarioEvent.timeSeconds:F1}s {scenarioEvent.action} {transmitter.EmitterId}";
            ScytheDiagnostics.Log($"Scenario event: {LastEventLabel}.");
            simulation.RunFrame();
        }
    }
}
