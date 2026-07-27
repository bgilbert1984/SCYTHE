# Unity Linux and Windows builds in Codespaces

This repository contains a small Unity 6 project in `UnityProject/`. Its scene
and standalone Linux and Windows applications are generated entirely from the
C# files in `Assets/Editor` and `Assets/Scripts`.

## 1. Install the pinned editor

The project is pinned to Unity `6000.3.15f1` (Unity 6.3 LTS):

```bash
./scripts/setup_unity_linux.sh
```

The Linux editor download is about 4.2 GiB and the installed editor needs
roughly 12 GiB of free disk space. The script streams the archive into
`/opt/unity/6000.3.15f1` so it does not retain a second archive copy.

## 2. Supply a Unity license

Unity still requires a valid license in batch mode. Do not commit Unity license
files or account credentials.

### Unity Personal

Personal licenses must be activated interactively through Unity Hub. Manual
`.alf`/`.ulf` activation and command-line activation are not supported for
Personal.

Start the private, temporary browser desktop:

```bash
./scripts/start_unity_desktop.sh
```

Open the printed Codespaces URL. Keep forwarded port `6080` private. In Unity
Hub, sign in, open the license preferences, and add a free Personal license.
Microsoft Edge opens inside the remote desktop for sign-in, while the final
`unityhub://` callback remains assigned to Unity Hub in this Codespace.

When finished with the GUI:

```bash
./scripts/stop_unity_desktop.sh
```

Before deleting or rebuilding the Codespace, start the desktop again and sign
out of Unity Hub so the Personal activation is returned.

### Serial-based organization licenses

The helper scripts for generating an activation request and importing a `.ulf`
are only for plans that support manual or serial-based activation:

```bash
./scripts/request_unity_license.sh
./scripts/activate_unity_license.sh /path/to/UnityLicense.ulf
```

## 3. Compile the Linux player

```bash
./build_unity_linux.sh
```

Output:

```text
UnityProject/Builds/Linux/SCYTHE_RF_Sim.x86_64
UnityProject/Builds/Linux/SCYTHE_RF_Sim_Data/
UnityProject/Builds/Linux/build.log
```

Override paths without editing the script:

```bash
UNITY_PATH=/custom/Editor/Unity \
UNITY_BUILD_NAME=MyApp.x86_64 \
./build_unity_linux.sh
```

The output is a graphical Linux player. Download the binary and its matching
`_Data` directory as one folder before running it on a Linux desktop.

## 4. Compile the Windows player

The Unity Editor needs Windows Build Support (Mono) installed before this
command can cross-compile the project from Linux:

```bash
./build_unity_windows.sh
```

Output:

```text
UnityProject/Builds/Windows/SCYTHE_RF_Sim.exe
UnityProject/Builds/Windows/SCYTHE_RF_Sim_Data/
UnityProject/Builds/Windows/MonoBleedingEdge/
UnityProject/Builds/Windows/UnityPlayer.dll
UnityProject/Builds/Windows/build.log
```

Keep the executable and all companion folders and DLLs together. The packaged
ZIP at `UnityProject/Builds/SCYTHE_RF_Sim-windows-x86_64.zip` contains the
complete runnable Windows application.
