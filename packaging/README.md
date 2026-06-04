# FableGear Mac .app Launcher

This folder contains scripts to build a native Mac .app wrapper for FableGear with a custom dock icon.

## Files

- `FableGearLauncher.applescript`: AppleScript source for the launcher.
- `build_applescript_app.sh`: Script to compile the .app and set the icon.
- `FableGear-app-icon.png`: Custom dock icon (must be present).

## Build Instructions

1. Ensure the macOS command line tools used by this script are available (`osacompile`, `sips`, and `Rez`).
2. Run the build script:

    ```sh
    cd packaging
    bash build_applescript_app.sh
    ```

3. The resulting `FableGear.app` can be moved to `/Applications` or the Dock.

## Behavior

- On launch, the app runs `launch.sh` from the repo root.
- Homebrew and FableGear update checks run silently; if offline, the current version opens.
- Closing the window quits the app and venv.

---

For advanced customization, edit the AppleScript or build script as needed.
