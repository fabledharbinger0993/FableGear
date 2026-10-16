# FableGear packaging

## The download (what `build_release.sh` makes)

`FableGear.zip` holds one app, **Install FableGear.app**:

- `installer_launcher.sh` is its executable. It finds a Python (3.11-3.13 preferred; it never runs
  the `/usr/bin/python3` shim unless the Command Line Tools are already installed) and runs
  `installer_wizard.py --target "~/Library/Application Support/FableGear/app"`.
- `Contents/Resources/payload/` is a snapshot of committed `HEAD` plus a `VERSION` file.
- The wizard builds the private venv first, installs the packages only there, copies the code to the
  target, then calls `create_launcher_app.sh` to put **FableGear.app** in `/Applications`
  (or `~/Applications`). `launch.sh` and the in-app update check read `VERSION`; an installed copy
  has no `.git` and is updated by running a newer installer.

`bash build_release.sh` builds `FableGear.zip` in the current directory; `--release` also publishes it.

## Other launcher scripts

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
