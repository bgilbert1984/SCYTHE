using System;
using System.Collections.Generic;
using System.IO;
using SCYTHE.Core;
using SCYTHE.Global;
using SCYTHE.Optics;
using SCYTHE.Presentation;
using SCYTHE.RF;
using SCYTHE.World;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Scythe.Editor
{
    public static class BuildCommand
    {
        private const string GeneratedScenePath = "Assets/Scenes/GeneratedMain.unity";

        public static void BuildLinux()
        {
            string buildPath = GetArgument("-buildPath")
                ?? Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Builds", "Linux", "SCYTHE_RF_Sim.x86_64"));

            BuildStandalone(BuildTarget.StandaloneLinux64, buildPath, "Linux");
        }

        public static void BuildWindows()
        {
            string buildPath = GetArgument("-buildPath")
                ?? Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Builds", "Windows", "SCYTHE_RF_Sim.exe"));

            BuildStandalone(BuildTarget.StandaloneWindows64, buildPath, "Windows");
        }

        public static void GenerateSceneForValidation()
        {
            ValidationCommand.ValidateCoreModels();
            GenerateScene();
            CesiumGeospatialAdapter adapter =
                UnityEngine.Object.FindFirstObjectByType<CesiumGeospatialAdapter>();
            GlobalDatasetManager manager =
                UnityEngine.Object.FindFirstObjectByType<GlobalDatasetManager>();
            if (adapter == null
                || !adapter.TryGetOperatorPosition(out GeodeticPosition position))
            {
                throw new InvalidOperationException(
                    "Generated scene did not produce a geodetic operator position.");
            }

            if (manager == null
                || manager.ValidatedDatasetCount != 0
                || !manager.Status.Contains("NO REGISTERED GLOBAL DATASET"))
            {
                throw new InvalidOperationException(
                    "Generated scene invented or misreported global propagation data.");
            }

            Debug.Log(
                "[SCYTHE VALIDATION] PASS: generated v0.5.0 global monocle scene; "
                + $"backend={adapter.BackendStatus}; "
                + $"operator=({position.LongitudeDegrees:F8},"
                + $"{position.LatitudeDegrees:F8},{position.HeightMeters:F3}m); "
                + $"data={manager.Status}.");
        }

        private static void BuildStandalone(BuildTarget target, string buildPath, string platformName)
        {
            ValidationCommand.ValidateCoreModels();
            GenerateScene();
            ConfigurePlayer();
            Directory.CreateDirectory(Path.GetDirectoryName(buildPath) ?? ".");

            var options = new BuildPlayerOptions
            {
                scenes = new[] { GeneratedScenePath },
                locationPathName = buildPath,
                target = target,
                options = BuildOptions.CleanBuildCache,
            };

            BuildReport report = BuildPipeline.BuildPlayer(options);
            BuildSummary summary = report.summary;
            Debug.Log($"Build result: {summary.result}; size: {summary.totalSize} bytes; time: {summary.totalTime}");

            if (summary.result != BuildResult.Succeeded)
            {
                throw new InvalidOperationException($"{platformName} build failed: {summary.result} ({summary.totalErrors} errors)");
            }
        }

        private static void ConfigurePlayer()
        {
            PlayerSettings.companyName = "SCYTHE";
            PlayerSettings.productName = "SCYTHE Global Monocle";
            PlayerSettings.bundleVersion = "0.5.0";
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Standalone, "dev.scythe.rfsim");
            PlayerSettings.defaultScreenWidth = 1280;
            PlayerSettings.defaultScreenHeight = 720;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.runInBackground = true;
        }

        private static void GenerateScene()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(GeneratedScenePath) ?? "Assets");
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            ScenarioManifest manifest = ScenarioManifest.Load("Scenarios/rf_milestone_01.json");
            ScenarioGlobalSettings global = manifest.globalSettings;

            GameObject georeferenceObject = new GameObject("SCYTHE Cesium Georeference");
            Component georeference =
                CesiumGeospatialAdapter.TryAddCesiumGeoreference(
                    georeferenceObject,
                    global);
            Transform spatialRoot = georeferenceObject.transform;

            GameObject operatorObject = new GameObject("SCYTHE Mobile Operator");
            operatorObject.transform.SetParent(spatialRoot, false);
            operatorObject.transform.localPosition =
                manifest.probeStartPositionMeters.ToVector3();
            CharacterController character = operatorObject.AddComponent<CharacterController>();
            character.height = 1.8f;
            character.radius = 0.35f;
            character.center = new Vector3(0f, 0.9f, 0f);
            character.stepOffset = 0.3f;
            character.slopeLimit = 50f;
            AddGlobeAnchor(operatorObject);
            RFReceiver receiver = operatorObject.AddComponent<RFReceiver>();
            ScytheCharacterController walker = operatorObject.AddComponent<ScytheCharacterController>();
            walker.Configure(
                manifest.probeWalkSpeedMetersPerSecond,
                manifest.probeSprintSpeedMetersPerSecond);

            Camera camera = new GameObject("Main Camera").AddComponent<Camera>();
            camera.tag = "MainCamera";
            camera.transform.SetParent(operatorObject.transform, false);
            camera.transform.localPosition = new Vector3(0f, 1.62f, 0f);
            camera.transform.localRotation = Quaternion.identity;
            camera.backgroundColor = new Color(0.012f, 0.025f, 0.04f);
            camera.clearFlags = CameraClearFlags.SolidColor;
            walker.BindCamera(camera);
            if (global.enableOriginShifting)
            {
                AddGlobeAnchor(camera.gameObject);
                CesiumGeospatialAdapter.TryAddOriginShift(camera.gameObject);
            }

            Light keyLight = new GameObject("Directional Light").AddComponent<Light>();
            keyLight.transform.SetParent(spatialRoot, false);
            keyLight.type = LightType.Directional;
            keyLight.color = new Color(0.35f, 1f, 0.72f);
            keyLight.intensity = 1.7f;
            keyLight.transform.rotation = Quaternion.Euler(48f, -32f, 0f);

            var transmitters = new List<RFTransmitter>();
            for (int index = 0; index < manifest.transmitters.Count; index++)
            {
                ScenarioTransmitter definition = manifest.transmitters[index];
                GameObject emitter = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                emitter.name = $"RF Emitter {definition.id} // {definition.displayName}";
                emitter.transform.SetParent(spatialRoot, false);
                emitter.transform.localPosition = definition.positionMeters.ToVector3();
                emitter.transform.localScale = new Vector3(1.15f, 0.12f, 1.15f);
                AddGlobeAnchor(emitter);
                RFTransmitter transmitter = emitter.AddComponent<RFTransmitter>();
                transmitter.SetEmitterId(definition.id);
                transmitters.Add(transmitter);

                GameObject mast = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                mast.name = $"Antenna Mast {definition.id}";
                mast.transform.SetParent(emitter.transform);
                mast.transform.localPosition = new Vector3(0f, 6f, 0f);
                mast.transform.localScale = new Vector3(0.08f, 6f, 0.08f);

                GameObject rings = new GameObject($"RF Range Rings {definition.id}");
                rings.transform.SetParent(emitter.transform, false);
                rings.AddComponent<RFRangeRingRenderer>();
            }

            GameObject ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.name = "Operations Grid";
            ground.transform.SetParent(spatialRoot, false);
            ground.transform.localPosition = new Vector3(0f, -0.1f, 0f);
            ground.transform.localScale = new Vector3(6f, 1f, 6f);
            AddGlobeAnchor(ground);

            CreateLabObstacle(
                spatialRoot,
                "RF Shield Alpha",
                new Vector3(-1f, 1.6f, 1f),
                new Vector3(0.65f, 3.2f, 8f));
            CreateLabObstacle(
                spatialRoot,
                "RF Shield Bravo",
                new Vector3(7f, 1.4f, 2f),
                new Vector3(5f, 2.8f, 0.65f));
            CreateLabObstacle(
                spatialRoot,
                "Calibration Block",
                new Vector3(-8f, 1f, -7f),
                new Vector3(3f, 2f, 2f));
            CreateLabObstacle(
                spatialRoot,
                "Calibration Wall",
                new Vector3(0f, 1.25f, 12f),
                new Vector3(10f, 2.5f, 0.5f));

            GameObject systems = new GameObject("SCYTHE Systems");
            systems.transform.SetParent(spatialRoot, false);
            SimulationClock clock = systems.AddComponent<SimulationClock>();
            clock.FixedStepSeconds = manifest.fixedStepSeconds;
            RFOcclusionModel occlusionModel = systems.AddComponent<RFOcclusionModel>();
            occlusionModel.Configure(manifest.occlusion);
            RFSimulationController simulation = systems.AddComponent<RFSimulationController>();
            simulation.Bind(transmitters, receiver, occlusionModel);
            RFFieldSampler sampler = operatorObject.AddComponent<RFFieldSampler>();
            sampler.Bind(simulation, camera);
            RFFieldVisualizer fieldVisualizer = systems.AddComponent<RFFieldVisualizer>();
            fieldVisualizer.Bind(simulation);
            ScenarioDirector director = systems.AddComponent<ScenarioDirector>();
            director.Bind(simulation);
            OpticalDatasetLoader opticalLoader = systems.AddComponent<OpticalDatasetLoader>();
            opticalLoader.Bind(
                LoadDeclaredOpticalDataset(manifest),
                manifest.opticalDatasetRelativeDirectory,
                manifest.opticalDatasetRequired);
            CesiumGeospatialAdapter geospatialAdapter =
                systems.AddComponent<CesiumGeospatialAdapter>();
            geospatialAdapter.Bind(
                spatialRoot,
                operatorObject.transform,
                global,
                georeference);
            GlobalDatasetManager globalDatasetManager =
                systems.AddComponent<GlobalDatasetManager>();
            globalDatasetManager.Bind(global, geospatialAdapter);
            MonocleHUD hud = systems.AddComponent<MonocleHUD>();
            hud.Bind(
                simulation,
                fieldVisualizer,
                sampler,
                opticalLoader,
                director,
                geospatialAdapter,
                globalDatasetManager);

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, GeneratedScenePath);
            AssetDatabase.SaveAssets();
        }

        private static void CreateLabObstacle(
            Transform spatialRoot,
            string name,
            Vector3 position,
            Vector3 scale)
        {
            GameObject obstacle = GameObject.CreatePrimitive(PrimitiveType.Cube);
            obstacle.name = name;
            obstacle.transform.SetParent(spatialRoot, false);
            obstacle.transform.localPosition = position;
            obstacle.transform.localScale = scale;
            AddGlobeAnchor(obstacle);
            obstacle.AddComponent<RFOccluder>();
        }

        private static Component AddGlobeAnchor(GameObject target)
        {
            return CesiumGeospatialAdapter.TryAddGlobeAnchor(target);
        }

        internal static OpticalDataset LoadDeclaredOpticalDataset(ScenarioManifest manifest)
        {
            if (string.IsNullOrWhiteSpace(manifest.opticalDatasetRelativeDirectory))
            {
                Debug.Log(
                    "[SCYTHE] No optical dataset declared; fusion HUD will report "
                    + "NO SOLVER DATASET BUNDLED.");
                return null;
            }

            string relativeDirectory = manifest.opticalDatasetRelativeDirectory
                .Trim()
                .Trim('/', '\\')
                .Replace('\\', '/');
            string assetDirectory = $"Assets/OpticalDatasets/{relativeDirectory}";
            string absoluteDirectory = Path.GetFullPath(
                Path.Combine(Application.dataPath, "OpticalDatasets", relativeDirectory));
            if (!Directory.Exists(absoluteDirectory))
            {
                throw new InvalidDataException(
                    $"Declared optical dataset directory does not exist: {assetDirectory}");
            }

            string metadataPath = $"{assetDirectory}/metadata.json";
            string phasePath = $"{assetDirectory}/phase.exr";
            string intensityPath = $"{assetDirectory}/intensity.exr";
            RequireFile(metadataPath);
            RequireFile(phasePath);
            RequireFile(intensityPath);
            AssetDatabase.ImportAsset(metadataPath, ImportAssetOptions.ForceUpdate);
            AssetDatabase.ImportAsset(phasePath, ImportAssetOptions.ForceUpdate);
            AssetDatabase.ImportAsset(intensityPath, ImportAssetOptions.ForceUpdate);

            var dataset = new OpticalDataset
            {
                metadataJson = AssetDatabase.LoadAssetAtPath<TextAsset>(metadataPath),
                phaseRadians = AssetDatabase.LoadAssetAtPath<Texture2D>(phasePath),
                intensity = AssetDatabase.LoadAssetAtPath<Texture2D>(intensityPath),
                polarization = AssetDatabase.LoadAssetAtPath<Texture2D>(
                    $"{assetDirectory}/polarization.exr"),
                depthPlanes = LoadTextureDirectory(
                    assetDirectory,
                    absoluteDirectory,
                    "depth_planes"),
                laneMasks = LoadTextureDirectory(
                    assetDirectory,
                    absoluteDirectory,
                    "lane_masks"),
            };
            dataset.ValidateCompleteDataset();
            Debug.Log($"[SCYTHE VALIDATION] Optical dataset PASS: {assetDirectory}");
            return dataset;
        }

        private static List<Texture2D> LoadTextureDirectory(
            string assetDirectory,
            string absoluteDirectory,
            string childDirectory)
        {
            var textures = new List<Texture2D>();
            string absoluteChild = Path.Combine(absoluteDirectory, childDirectory);
            if (!Directory.Exists(absoluteChild))
            {
                return textures;
            }

            string[] files = Directory.GetFiles(absoluteChild, "*.exr");
            Array.Sort(files, StringComparer.Ordinal);
            foreach (string file in files)
            {
                string assetPath =
                    $"{assetDirectory}/{childDirectory}/{Path.GetFileName(file)}";
                AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceUpdate);
                Texture2D texture = AssetDatabase.LoadAssetAtPath<Texture2D>(assetPath);
                if (texture == null)
                {
                    throw new InvalidDataException($"Could not import optical texture {assetPath}.");
                }

                textures.Add(texture);
            }

            return textures;
        }

        private static void RequireFile(string assetPath)
        {
            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string absolutePath = Path.GetFullPath(Path.Combine(projectRoot, assetPath));
            if (!File.Exists(absolutePath))
            {
                throw new InvalidDataException($"Required optical asset is missing: {assetPath}");
            }
        }

        private static string GetArgument(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.FindIndex(args, value => string.Equals(value, name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
    }
}
