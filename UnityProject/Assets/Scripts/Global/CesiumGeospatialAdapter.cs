using System;
using System.Reflection;
using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.Global
{
    public readonly struct GeodeticPosition
    {
        public GeodeticPosition(
            double longitudeDegrees,
            double latitudeDegrees,
            double heightMeters)
        {
            LongitudeDegrees = longitudeDegrees;
            LatitudeDegrees = latitudeDegrees;
            HeightMeters = heightMeters;
        }

        public double LongitudeDegrees { get; }
        public double LatitudeDegrees { get; }
        public double HeightMeters { get; }
    }

    public sealed class CesiumGeospatialAdapter : MonoBehaviour
    {
        private const string GeoreferenceTypeName =
            "CesiumForUnity.CesiumGeoreference, CesiumForUnity";
        private const string GlobeAnchorTypeName =
            "CesiumForUnity.CesiumGlobeAnchor, CesiumForUnity";
        private const string OriginShiftTypeName =
            "CesiumForUnity.CesiumOriginShift, CesiumForUnity";

        [SerializeField] private Transform spatialRoot;
        [SerializeField] private Transform operatorTransform;
        [SerializeField] private Component cesiumGeoreference;
        [SerializeField] private Component operatorAnchor;
        [SerializeField] private string originEvidenceClass;
        [SerializeField] private string originDescription;
        [SerializeField] private double originLongitudeDegrees;
        [SerializeField] private double originLatitudeDegrees;
        [SerializeField] private double originHeightMeters;

        public bool IsReady => spatialRoot != null && operatorTransform != null;
        public bool IsCesiumBacked =>
            cesiumGeoreference != null && operatorAnchor != null;
        public string BackendStatus => IsCesiumBacked
            ? "CESIUM 1.24.0"
            : "WGS84 REFERENCE // CESIUM NATIVE UNAVAILABLE";
        public string OriginEvidenceClass => originEvidenceClass;
        public string OriginDescription => originDescription;

        public void Bind(
            Transform globalSpatialRoot,
            Transform trackedOperator,
            ScenarioGlobalSettings settings,
            Component attachedCesiumGeoreference)
        {
            spatialRoot = globalSpatialRoot;
            operatorTransform = trackedOperator;
            cesiumGeoreference = attachedCesiumGeoreference;
            operatorAnchor = trackedOperator != null
                ? GetComponentByTypeName(trackedOperator.gameObject, GlobeAnchorTypeName)
                : null;
            originEvidenceClass = settings?.originEvidenceClass ?? "ILLUSTRATIVE";
            originDescription = settings?.originDescription ?? "No global origin declared.";
            originLongitudeDegrees = settings?.origin?.longitudeDegrees ?? 0d;
            originLatitudeDegrees = settings?.origin?.latitudeDegrees ?? 0d;
            originHeightMeters = settings?.origin?.heightMeters ?? 0d;
        }

        public bool TryGetOperatorPosition(out GeodeticPosition position)
        {
            position = default;
            if (!IsReady)
            {
                return false;
            }

            if (TryReadCesiumAnchor(out position))
            {
                return true;
            }

            Vector3 local = spatialRoot.InverseTransformPoint(
                operatorTransform.position);
            position = Wgs84Reference.LocalEastUpNorthToLongitudeLatitudeHeight(
                originLongitudeDegrees,
                originLatitudeDegrees,
                originHeightMeters,
                local.x,
                local.y,
                local.z);
            return IsFinite(position.LongitudeDegrees)
                && IsFinite(position.LatitudeDegrees)
                && IsFinite(position.HeightMeters);
        }

        public static Component TryAddCesiumGeoreference(
            GameObject target,
            ScenarioGlobalSettings settings)
        {
            Type type = Type.GetType(GeoreferenceTypeName, false);
            if (type == null)
            {
                Debug.LogWarning(
                    "[SCYTHE GLOBAL] CesiumForUnity runtime is unavailable on this "
                    + "editor platform. Using the labeled WGS84 reference adapter.");
                return null;
            }

            Component component = target.GetComponent(type)
                ?? target.AddComponent(type);
            type.GetMethod(
                    "SetOriginLongitudeLatitudeHeight",
                    BindingFlags.Instance | BindingFlags.Public)
                ?.Invoke(
                    component,
                    new object[]
                    {
                        settings.origin.longitudeDegrees,
                        settings.origin.latitudeDegrees,
                        settings.origin.heightMeters,
                    });
            type.GetProperty("scale", BindingFlags.Instance | BindingFlags.Public)
                ?.SetValue(component, 1d);
            return component;
        }

        public static Component TryAddGlobeAnchor(GameObject target)
        {
            Type type = Type.GetType(GlobeAnchorTypeName, false);
            if (type == null)
            {
                return null;
            }

            Component component = target.GetComponent(type)
                ?? target.AddComponent(type);
            type.GetProperty(
                    "adjustOrientationForGlobeWhenMoving",
                    BindingFlags.Instance | BindingFlags.Public)
                ?.SetValue(component, false);
            type.GetProperty(
                    "detectTransformChanges",
                    BindingFlags.Instance | BindingFlags.Public)
                ?.SetValue(component, true);
            return component;
        }

        public static Component TryAddOriginShift(GameObject target)
        {
            Type type = Type.GetType(OriginShiftTypeName, false);
            if (type == null)
            {
                return null;
            }

            Component component = target.GetComponent(type)
                ?? target.AddComponent(type);
            type.GetProperty("distance", BindingFlags.Instance | BindingFlags.Public)
                ?.SetValue(component, 1000d);
            return component;
        }

        private bool TryReadCesiumAnchor(out GeodeticPosition position)
        {
            position = default;
            if (!IsCesiumBacked)
            {
                return false;
            }

            PropertyInfo property = operatorAnchor.GetType().GetProperty(
                "longitudeLatitudeHeight",
                BindingFlags.Instance | BindingFlags.Public);
            object value = property?.GetValue(operatorAnchor);
            if (value == null
                || !TryReadDoubleMember(value, "x", out double longitude)
                || !TryReadDoubleMember(value, "y", out double latitude)
                || !TryReadDoubleMember(value, "z", out double height))
            {
                return false;
            }

            position = new GeodeticPosition(longitude, latitude, height);
            return IsFinite(longitude) && IsFinite(latitude) && IsFinite(height);
        }

        private static Component GetComponentByTypeName(
            GameObject target,
            string typeName)
        {
            Type type = Type.GetType(typeName, false);
            return type == null ? null : target.GetComponent(type);
        }

        private static bool TryReadDoubleMember(
            object instance,
            string name,
            out double value)
        {
            value = 0d;
            Type type = instance.GetType();
            FieldInfo field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public);
            if (field != null)
            {
                value = Convert.ToDouble(field.GetValue(instance));
                return true;
            }

            PropertyInfo property = type.GetProperty(
                name,
                BindingFlags.Instance | BindingFlags.Public);
            if (property == null)
            {
                return false;
            }

            value = Convert.ToDouble(property.GetValue(instance));
            return true;
        }

        private static bool IsFinite(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }
    }

    public static class Wgs84Reference
    {
        private const double SemiMajorAxisMeters = 6378137d;
        private const double Flattening = 1d / 298.257223563d;
        private const double EccentricitySquared =
            Flattening * (2d - Flattening);

        public static GeodeticPosition LocalEastUpNorthToLongitudeLatitudeHeight(
            double originLongitudeDegrees,
            double originLatitudeDegrees,
            double originHeightMeters,
            double eastMeters,
            double upMeters,
            double northMeters)
        {
            double longitude = DegreesToRadians(originLongitudeDegrees);
            double latitude = DegreesToRadians(originLatitudeDegrees);
            ToEcef(
                longitude,
                latitude,
                originHeightMeters,
                out double originX,
                out double originY,
                out double originZ);

            double sinLongitude = Math.Sin(longitude);
            double cosLongitude = Math.Cos(longitude);
            double sinLatitude = Math.Sin(latitude);
            double cosLatitude = Math.Cos(latitude);
            double x = originX
                - sinLongitude * eastMeters
                - sinLatitude * cosLongitude * northMeters
                + cosLatitude * cosLongitude * upMeters;
            double y = originY
                + cosLongitude * eastMeters
                - sinLatitude * sinLongitude * northMeters
                + cosLatitude * sinLongitude * upMeters;
            double z = originZ
                + cosLatitude * northMeters
                + sinLatitude * upMeters;
            return FromEcef(x, y, z);
        }

        private static void ToEcef(
            double longitudeRadians,
            double latitudeRadians,
            double heightMeters,
            out double x,
            out double y,
            out double z)
        {
            double sinLatitude = Math.Sin(latitudeRadians);
            double cosLatitude = Math.Cos(latitudeRadians);
            double primeVertical = SemiMajorAxisMeters
                / Math.Sqrt(1d - EccentricitySquared * sinLatitude * sinLatitude);
            x = (primeVertical + heightMeters)
                * cosLatitude
                * Math.Cos(longitudeRadians);
            y = (primeVertical + heightMeters)
                * cosLatitude
                * Math.Sin(longitudeRadians);
            z = (primeVertical * (1d - EccentricitySquared) + heightMeters)
                * sinLatitude;
        }

        private static GeodeticPosition FromEcef(double x, double y, double z)
        {
            double longitude = Math.Atan2(y, x);
            double horizontal = Math.Sqrt(x * x + y * y);
            double latitude = Math.Atan2(
                z,
                horizontal * (1d - EccentricitySquared));
            double height = 0d;
            for (int iteration = 0; iteration < 10; iteration++)
            {
                double sinLatitude = Math.Sin(latitude);
                double primeVertical = SemiMajorAxisMeters
                    / Math.Sqrt(
                        1d - EccentricitySquared * sinLatitude * sinLatitude);
                height = horizontal / Math.Cos(latitude) - primeVertical;
                latitude = Math.Atan2(
                    z,
                    horizontal
                        * (1d
                            - EccentricitySquared
                                * primeVertical
                                / (primeVertical + height)));
            }

            return new GeodeticPosition(
                RadiansToDegrees(longitude),
                RadiansToDegrees(latitude),
                height);
        }

        private static double DegreesToRadians(double degrees)
        {
            return degrees * Math.PI / 180d;
        }

        private static double RadiansToDegrees(double radians)
        {
            return radians * 180d / Math.PI;
        }
    }
}
