using System;
using UnityEngine;

namespace SCYTHE.Global
{
    public enum EvidenceClass
    {
        Measured,
        SolverOutput,
        ReducedOrder,
        Synthetic,
        Illustrative,
    }

    public readonly struct EvidenceStyle
    {
        public EvidenceStyle(string label, string pattern, Color color)
        {
            Label = label;
            Pattern = pattern;
            Color = color;
        }

        public string Label { get; }
        public string Pattern { get; }
        public Color Color { get; }
    }

    public static class EvidenceStyleRouter
    {
        public static EvidenceClass Parse(string value)
        {
            switch (value?.Trim().ToUpperInvariant())
            {
                case "MEASURED":
                    return EvidenceClass.Measured;
                case "SOLVER_OUTPUT":
                    return EvidenceClass.SolverOutput;
                case "REDUCED_ORDER":
                    return EvidenceClass.ReducedOrder;
                case "SYNTHETIC":
                    return EvidenceClass.Synthetic;
                case "ILLUSTRATIVE":
                    return EvidenceClass.Illustrative;
                default:
                    throw new ArgumentException($"Unsupported evidence class {value}.");
            }
        }

        public static string ToContractName(EvidenceClass evidence)
        {
            switch (evidence)
            {
                case EvidenceClass.Measured:
                    return "MEASURED";
                case EvidenceClass.SolverOutput:
                    return "SOLVER_OUTPUT";
                case EvidenceClass.ReducedOrder:
                    return "REDUCED_ORDER";
                case EvidenceClass.Synthetic:
                    return "SYNTHETIC";
                case EvidenceClass.Illustrative:
                    return "ILLUSTRATIVE";
                default:
                    throw new ArgumentOutOfRangeException(nameof(evidence));
            }
        }

        public static EvidenceStyle Get(EvidenceClass evidence)
        {
            switch (evidence)
            {
                case EvidenceClass.Measured:
                    return new EvidenceStyle(
                        "MEASURED",
                        "SOLID",
                        new Color(0.20f, 1f, 0.58f));
                case EvidenceClass.SolverOutput:
                    return new EvidenceStyle(
                        "SOLVER_OUTPUT",
                        "HASHED",
                        new Color(0.25f, 0.72f, 1f));
                case EvidenceClass.ReducedOrder:
                    return new EvidenceStyle(
                        "REDUCED_ORDER",
                        "DOTTED",
                        new Color(0.98f, 0.78f, 0.22f));
                case EvidenceClass.Synthetic:
                    return new EvidenceStyle(
                        "SYNTHETIC",
                        "TRANSLUCENT",
                        new Color(0.78f, 0.48f, 1f));
                case EvidenceClass.Illustrative:
                    return new EvidenceStyle(
                        "ILLUSTRATIVE",
                        "DASHED",
                        new Color(0.65f, 0.72f, 0.75f));
                default:
                    throw new ArgumentOutOfRangeException(nameof(evidence));
            }
        }
    }
}
