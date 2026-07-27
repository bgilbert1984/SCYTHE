using System;
using System.IO;
using SCYTHE.Core;
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
            PlayerSettings.productName = "SCYTHE RF Sim";
            PlayerSettings.bundleVersion = "0.3.0";
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

            GameObject operatorObject = new GameObject("SCYTHE Mobile Operator");
            operatorObject.transform.position = manifest.probeStartPositionMeters.ToVector3();
            CharacterController character = operatorObject.AddComponent<CharacterController>();
            character.height = 1.8f;
            character.radius = 0.35f;
            character.center = new Vector3(0f, 0.9f, 0f);
            character.stepOffset = 0.3f;
            character.slopeLimit = 50f;
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

            Light keyLight = new GameObject("Directional Light").AddComponent<Light>();
            keyLight.type = LightType.Directional;
            keyLight.color = new Color(0.35f, 1f, 0.72f);
            keyLight.intensity = 1.7f;
            keyLight.transform.rotation = Quaternion.Euler(48f, -32f, 0f);

            GameObject emitter = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            emitter.name = "RF Emitter";
            emitter.transform.position = manifest.transmitterPositionMeters.ToVector3();
            emitter.transform.localScale = new Vector3(1.2f, 0.12f, 1.2f);
            RFTransmitter transmitter = emitter.AddComponent<RFTransmitter>();

            GameObject mast = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            mast.name = "Antenna Mast";
            mast.transform.SetParent(emitter.transform);
            mast.transform.localPosition = new Vector3(0f, 6f, 0f);
            mast.transform.localScale = new Vector3(0.1f, 6f, 0.1f);

            GameObject ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.name = "Operations Grid";
            ground.transform.position = new Vector3(0f, -0.1f, 0f);
            ground.transform.localScale = new Vector3(6f, 1f, 6f);

            CreateLabObstacle("Calibration Block A", new Vector3(-9f, 1f, -7f), new Vector3(3f, 2f, 2f));
            CreateLabObstacle("Calibration Block B", new Vector3(10f, 1.5f, 6f), new Vector3(2f, 3f, 4f));
            CreateLabObstacle("Calibration Wall", new Vector3(0f, 1.25f, 12f), new Vector3(10f, 2.5f, 0.5f));

            GameObject rings = new GameObject("RF Range Rings");
            rings.transform.position = new Vector3(emitter.transform.position.x, 0f, emitter.transform.position.z);
            rings.AddComponent<RFRangeRingRenderer>();

            GameObject systems = new GameObject("SCYTHE Systems");
            systems.AddComponent<SimulationClock>();
            RFSimulationController simulation = systems.AddComponent<RFSimulationController>();
            simulation.Bind(transmitter, receiver);
            RFFieldSampler sampler = operatorObject.AddComponent<RFFieldSampler>();
            sampler.Bind(simulation, camera);
            RFFieldVisualizer fieldVisualizer = systems.AddComponent<RFFieldVisualizer>();
            fieldVisualizer.Bind(simulation);
            MonocleHUD hud = systems.AddComponent<MonocleHUD>();
            hud.Bind(simulation, fieldVisualizer, sampler);

            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, GeneratedScenePath);
            AssetDatabase.SaveAssets();
        }

        private static void CreateLabObstacle(string name, Vector3 position, Vector3 scale)
        {
            GameObject obstacle = GameObject.CreatePrimitive(PrimitiveType.Cube);
            obstacle.name = name;
            obstacle.transform.position = position;
            obstacle.transform.localScale = scale;
        }

        private static string GetArgument(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.FindIndex(args, value => string.Equals(value, name, StringComparison.OrdinalIgnoreCase));
            return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
        }
    }
}
