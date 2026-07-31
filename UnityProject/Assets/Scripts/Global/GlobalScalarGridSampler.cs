using System;

namespace SCYTHE.Global
{
    public static class GlobalScalarGridSampler
    {
        public static bool TrySampleBilinear(
            double[] values,
            int longitudeCount,
            int latitudeCount,
            double westDegrees,
            double southDegrees,
            double eastDegrees,
            double northDegrees,
            double longitudeDegrees,
            double latitudeDegrees,
            out double value)
        {
            value = double.NaN;
            if (values == null
                || longitudeCount < 2
                || latitudeCount < 2
                || values.Length != longitudeCount * latitudeCount
                || eastDegrees <= westDegrees
                || northDegrees <= southDegrees
                || longitudeDegrees < westDegrees
                || longitudeDegrees > eastDegrees
                || latitudeDegrees < southDegrees
                || latitudeDegrees > northDegrees)
            {
                return false;
            }

            double x = (longitudeDegrees - westDegrees)
                / (eastDegrees - westDegrees)
                * (longitudeCount - 1);
            double y = (latitudeDegrees - southDegrees)
                / (northDegrees - southDegrees)
                * (latitudeCount - 1);
            int x0 = Math.Min((int)Math.Floor(x), longitudeCount - 2);
            int y0 = Math.Min((int)Math.Floor(y), latitudeCount - 2);
            int x1 = x0 + 1;
            int y1 = y0 + 1;
            double tx = x - x0;
            double ty = y - y0;

            double southwest = values[y0 * longitudeCount + x0];
            double southeast = values[y0 * longitudeCount + x1];
            double northwest = values[y1 * longitudeCount + x0];
            double northeast = values[y1 * longitudeCount + x1];
            if (double.IsNaN(southwest)
                || double.IsNaN(southeast)
                || double.IsNaN(northwest)
                || double.IsNaN(northeast))
            {
                return false;
            }

            double south = southwest + (southeast - southwest) * tx;
            double north = northwest + (northeast - northwest) * tx;
            value = south + (north - south) * ty;
            return true;
        }
    }
}
