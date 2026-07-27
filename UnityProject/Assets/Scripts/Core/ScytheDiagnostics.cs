using UnityEngine;

namespace SCYTHE.Core
{
    public enum EvidenceLevel
    {
        Demonstrated,
        Simulated,
        Hypothesized,
        Illustrative,
    }

    public static class ScytheDiagnostics
    {
        public static void Log(string message)
        {
            Debug.Log($"[SCYTHE] {message}");
        }

        public static void Warn(string message)
        {
            Debug.LogWarning($"[SCYTHE] {message}");
        }
    }

    public static class PhysicalConstants
    {
        public const double SpeedOfLightMetersPerSecond = 299_792_458d;
    }
}
