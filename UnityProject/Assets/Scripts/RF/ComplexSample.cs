using System;
using UnityEngine;

namespace SCYTHE.RF
{
    [Serializable]
    public struct ComplexSample
    {
        public float I;
        public float Q;

        public ComplexSample(float i, float q)
        {
            I = i;
            Q = q;
        }

        public float MagnitudeSquared => I * I + Q * Q;
        public float Magnitude => Mathf.Sqrt(MagnitudeSquared);

        public static ComplexSample operator *(ComplexSample sample, float scalar)
        {
            return new ComplexSample(sample.I * scalar, sample.Q * scalar);
        }
    }

    public enum ModulationType
    {
        Ask,
        Fsk,
        Bpsk,
        Qpsk,
    }
}
