# HP Sprout Pro — setup for Spike A (light-guide registration)

Everything needed to run the Spike A scripts on a fresh HP Sprout Pro, from a
bare Windows install. All commands are **PowerShell**.

| Script | What it does |
|---|---|
| [config/checkerboard.py](config/checkerboard.py) | Projects a checkerboard and solves the camera → projector homography. Writes `config/cam_to_proj_homography.npz`. |
| [config/corner_check.py](config/corner_check.py) | Projects a filled rectangle onto four tape marks, then measures the offset at each corner in mm and runs the 10-minute stability check. |
| [config/rig.py](config/rig.py) | Shared code for display/camera lookup, the projector window and blank-and-capture. |

**Pass criterion (PLAN.md):** ≤ 5 mm at every canvas corner, stable across 10 minutes.

---

## Tech stack

| Layer | Tool | Why |
|---|---|---|
| OS | Windows 10/11 (as shipped on the Sprout) | The Sprout projector shows up as a second display. Its overhead cameras are DirectShow devices. |
| Package/Python manager | [uv](https://docs.astral.sh/uv/) | Installs Python 3.12, creates `.venv`, installs dependencies from `pyproject.toml`. |
| Runtime | Python 3.12 (pinned in `.python-version`) | Installed by uv, so no separate Python installer is needed. |
| Vision / projection | `opencv-python`, `numpy` | Capture, checkerboard detection, homography, rectangle detection, fullscreen projector window. |
| Display lookup | `screeninfo` | Finds where the projector sits on the Windows desktop, so you don't hard-code an offset. |
| Camera names | `pygrabber` | Lists DirectShow camera names alongside OpenCV indices (`--list`). |
| Source control | Git | To clone the repo. |
| Optional | FFmpeg | Lists the exact resolutions each camera supports. |

---

## 1. Install the tooling

Open **PowerShell** (as a normal user, not Admin).

```powershell
# Allow local scripts (needed once, for uv's installer and .venv activation)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# uv (Python + package manager)
winget install --id astral-sh.uv -e

# Git
winget install --id Git.Git -e

# Optional: FFmpeg, to list camera resolutions
winget install --id Gyan.FFmpeg -e

# Pick up the new PATH entries without reopening PowerShell
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

uv --version
git --version
```

**If `winget` isn't available** (it's missing on older Windows 10 builds), install uv
with its own script and Git from https://git-scm.com/download/win:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 2. Get the code and the environment

```powershell
cd $HOME\Desktop
git clone https://github.com/chess10kp/rob-boss.git
cd rob-boss

uv python install 3.12     # uv-managed Python, independent of any system Python
uv sync                    # creates .venv and installs the dependencies from pyproject.toml

uv run python -c "import cv2, numpy, screeninfo; print('OpenCV', cv2.__version__)"
```

`uv run <script>` always uses the project's `.venv`, so you never have to activate
it. To activate it anyway: `.\.venv\Scripts\Activate.ps1`.

## 3. Prepare the Sprout

1. **Free the camera and projector.** LightGuide and HP's own Sprout apps can hold
   the overhead camera exclusively. Close them, then check that nothing is left
   running:
   ```powershell
   Get-Process | Where-Object { $_.ProcessName -match 'light|guide|sprout|workspace|hp' } |
       Select-Object ProcessName, Id, Path
   # stop any that own the camera, e.g.:
   # Stop-Process -Id <Id>
   ```
2. **Allow desktop apps to use the camera:** open the settings page below, then turn
   on *Let desktop apps access your camera*.
   ```powershell
   start ms-settings:privacy-webcam
   ```
3. **Projector in Extend mode** (it must be its own display, not mirrored):
   ```powershell
   DisplaySwitch.exe /extend
   ```
4. **Find the projector display and the overhead camera:**
   ```powershell
   uv run config/checkerboard.py --list
   ```
   Note the index of the projector display (the non-primary one matching the
   projector's resolution) and the overhead camera's index. The front-facing
   webcam and the depth sensor also show up here, so pick the downward-facing
   colour camera.
5. **Find the camera's highest resolution** (optional, needs FFmpeg):
   ```powershell
   ffmpeg -hide_banner -list_devices true -f dshow -i dummy
   ffmpeg -hide_banner -list_options true -f dshow -i video="<camera name from above>"
   ```
   Pass the largest mode to `--cam-res`. Without it, OpenCV defaults to 640×480,
   which limits precision to a few mm.

## 4. Run Spike A

Replace `--camera 1 --cam-res 3840x2160 --display 1` with the values from step 3.
`--display` can be left off if the projector is the only non-primary display.

### Part 1 — homography

Put the **canvas** on the mat first and project onto the canvas itself. The
homography only holds at the height it was solved at, and a canvas board is
several mm thick.

```powershell
uv run config/checkerboard.py --camera 1 --cam-res 3840x2160 --display 1
```

| Key | Action |
|---|---|
| `S` | Open the camera driver dialog. Turn auto-exposure **off** and lower exposure until the white squares aren't blown out. Lock focus. |
| `SPACE` | Capture and solve. Green circles should land on the checkerboard corners. Aim for a reprojection error well under 1 projector px. |
| `ESC` | Quit |

### Part 2 — tape marks and corner offset

1. Put four **L-shaped** tape marks on the canvas near its corners. Point each L
   inward, so the tape lies *outside* the rectangle and the L's inner vertex is
   the target point. Use painter's or masking tape, not black tape.
2. Measure the vertex-to-vertex width (TL→TR) and height (TL→BL) with a ruler, in mm.
   Measure both diagonals too; if they differ by more than a mm, the marks aren't
   square and that error ends up in the measurement.
3. Run the script:

```powershell
uv run config/corner_check.py --camera 1 --cam-res 3840x2160 --display 1 --tape-w-mm 400 --tape-h-mm 300
```

- The projector goes black and a camera still opens. Click the tape vertices in order
  **TL, TR, BR, BL**. The magnifier in the top-left shows full-resolution pixels.
  Arrow keys nudge the last point by 1 camera px, `U` undoes, `ENTER` accepts.
- A filled rectangle is projected onto the marks. The console prints dx / dy / |err|
  in mm for each corner, plus PASS/FAIL. The camera window shows green circles
  for the tape and red crosses for the projected corners.
- The rectangle stays up, so check the gaps with a ruler too. That's the
  ground truth for the camera's measurement.

| Key | Action |
|---|---|
| `SPACE` | Measure again |
| `M` | Stability run: every 30 s for 10 min, logged to `config/logs/corner_check_*.csv`, with the worst error and max drift at the end |
| `R` | Re-click the tape marks |
| `S` | Camera driver dialog |
| `ESC` | Quit (also aborts a stability run) |

To rerun without clicking again (same camera position and resolution):

```powershell
uv run config/corner_check.py --camera 1 --cam-res 3840x2160 --display 1 --reuse-marks --monitor
```

Other flags: `--tolerance-mm 5`, `--minutes 10`, `--interval 30`, `--fill 255`
(lower it if the rectangle blooms), `--settle-ms 300` (raise it if captures
catch the previous projection). See `--help` on either script.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `camera N did not open` | Another app holds it. Close LightGuide / HP apps (step 3.1), or check the index with `--list`. |
| Projector window lands on the wrong screen or is mis-sized | Pass `--display` explicitly. The scripts are DPI-aware, but check Windows *Display → Scale* on the projector display if it persists. |
| `Checkerboard not found` | Lower exposure (`S`), fix focus, or shrink the board with `--square-px 70`. |
| `Projected rectangle not found` | Exposure too high (the whole canvas saturates) or too low. Try `--fill 180` or raise `--settle-ms`. |
| Camera resolution warning | That mode isn't offered. Pick one from the FFmpeg `list_options` output. |
| Errors grow toward the canvas edges | Lens distortion. Calibrate intrinsics, set `UNDISTORT`, `K` and `DIST` in `checkerboard.py`, and recalibrate. |
| Homography / tape-mark warnings about size | Camera or projector resolution changed since calibration. Rerun Part 1, then re-click the marks. |

Calibration outputs (`*.npz`, `tape_marks.json`, `logs/`) are specific to each rig
and are git-ignored.
