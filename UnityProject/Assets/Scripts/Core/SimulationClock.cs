using System;
using UnityEngine;

namespace SCYTHE.Core
{
    /// <summary>
    /// A reproducible simulation time source. Fixed-step mode decouples RF updates
    /// from rendering frame rate while still allowing the scene to run interactively.
    /// </summary>
    public sealed class SimulationClock : MonoBehaviour
    {
        [SerializeField] private bool useFixedStep = true;
        [SerializeField, Min(0.000001f)] private float fixedStepSeconds = 1f / 60f;
        [SerializeField, Min(1)] private int maximumStepsPerFrame = 8;

        private double accumulator;

        public static double TimeSeconds { get; private set; }
        public static double DeltaSeconds { get; private set; }
        public static ulong TickIndex { get; private set; }

        public static event Action<double> Ticked;

        public bool UseFixedStep
        {
            get => useFixedStep;
            set => useFixedStep = value;
        }

        public float FixedStepSeconds
        {
            get => fixedStepSeconds;
            set => fixedStepSeconds = Mathf.Max(0.000001f, value);
        }

        private void Awake()
        {
            ResetClock();
        }

        private void Update()
        {
            if (!useFixedStep)
            {
                Advance(UnityEngine.Time.unscaledDeltaTime);
                return;
            }

            accumulator += UnityEngine.Time.unscaledDeltaTime;
            int steps = 0;
            while (accumulator >= fixedStepSeconds && steps < maximumStepsPerFrame)
            {
                Advance(fixedStepSeconds);
                accumulator -= fixedStepSeconds;
                steps++;
            }

            if (steps == maximumStepsPerFrame)
            {
                accumulator = Math.Min(accumulator, fixedStepSeconds);
            }
        }

        public static void ResetClock(double startTimeSeconds = 0d)
        {
            TimeSeconds = Math.Max(0d, startTimeSeconds);
            DeltaSeconds = 0d;
            TickIndex = 0;
        }

        public static void Advance(double deltaSeconds)
        {
            if (deltaSeconds <= 0d)
            {
                throw new ArgumentOutOfRangeException(nameof(deltaSeconds), "Clock increments must be positive.");
            }

            DeltaSeconds = deltaSeconds;
            TimeSeconds += deltaSeconds;
            TickIndex++;
            Ticked?.Invoke(deltaSeconds);
        }
    }
}
