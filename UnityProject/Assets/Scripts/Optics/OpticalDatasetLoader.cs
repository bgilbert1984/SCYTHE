using System;
using UnityEngine;

namespace SCYTHE.Optics
{
    /// <summary>
    /// Runtime endpoint for an optical dataset imported and validated during the
    /// build. It never synthesizes fallback physics: absent solver data remains
    /// visibly absent.
    /// </summary>
    public sealed class OpticalDatasetLoader : MonoBehaviour
    {
        [SerializeField] private OpticalDataset dataset;
        [SerializeField] private string datasetDirectory = "";
        [SerializeField] private bool datasetRequired;

        private int selectedDepthPlane;

        public bool IsLoaded { get; private set; }
        public string Status { get; private set; } = "NO SOLVER DATASET BUNDLED";
        public OpticalMetadata Metadata { get; private set; }
        public Texture2D PhaseTexture => IsLoaded ? dataset.phaseRadians : null;
        public Texture2D IntensityTexture => IsLoaded ? dataset.intensity : null;
        public string DatasetDirectory => datasetDirectory;
        public int SelectedDepthPlane => selectedDepthPlane;
        public int DepthPlaneCount => IsLoaded ? dataset.depthPlanes.Count : 0;
        public Texture2D SelectedDepthTexture => IsLoaded && dataset.depthPlanes.Count > 0
            ? dataset.depthPlanes[selectedDepthPlane]
            : null;

        public void Bind(
            OpticalDataset opticalDataset,
            string relativeDirectory,
            bool required)
        {
            dataset = opticalDataset;
            datasetDirectory = relativeDirectory ?? "";
            datasetRequired = required;
        }

        private void Awake()
        {
            if (dataset == null)
            {
                if (datasetRequired)
                {
                    throw new InvalidOperationException(
                        "Scenario requires an optical dataset, but none was bound.");
                }

                Status = "NO SOLVER DATASET BUNDLED";
                return;
            }

            Metadata = dataset.ValidateCompleteDataset();
            IsLoaded = true;
            Status =
                $"LOADED {dataset.phaseRadians.width}×{dataset.phaseRadians.height} "
                + $"// {Metadata.solver} {Metadata.solverVersion}";
            Debug.Log(
                $"[SCYTHE] Optical dataset loaded from {datasetDirectory}: {Status}; "
                + $"provenance={Metadata.provenance}");
        }

        private void Update()
        {
            if (!IsLoaded || dataset.depthPlanes.Count == 0)
            {
                return;
            }

            if (Input.GetKeyDown(KeyCode.RightBracket))
            {
                selectedDepthPlane = (selectedDepthPlane + 1) % dataset.depthPlanes.Count;
            }
            else if (Input.GetKeyDown(KeyCode.LeftBracket))
            {
                selectedDepthPlane =
                    (selectedDepthPlane - 1 + dataset.depthPlanes.Count) % dataset.depthPlanes.Count;
            }
        }
    }
}
