# S.P.O.T.S Setup

## What's verified vs. not

The detection pipeline and dashboard were developed and tested locally using
a **synthetic frame source** (a fabricated target with holes appearing over
time) — see `config.example.yaml`'s `camera.source: synthetic`. That path is
verified end-to-end: new-target reset, live shot detection, group stats, and
undo all work against it.

The **Z CAM E2 HTTP/RTSP client** (`spots/camera/client.py`,
`spots/camera/source.py`'s `RtspFrameSource`) is written against the
official documented API (`imaginevision/Z-Camera-Doc`, `E2/protocol/http/http.md`)
but has **not** been tested against a real camera or a Raspberry Pi — that
needs to happen on-site with your actual hardware. Likely first things to
check there: the exact RTSP path for your firmware version (`/live_stream` is
what's documented, but confirm with `ffprobe rtsp://<camera-ip>/live_stream`),
and whether `stream_setting` needs `index=stream0` instead of `stream1`
depending on how you have the camera's dual-stream config set.

## Field network topology

The Pi needs to reach the camera over IP, and your phone/tablet needs to
reach the Pi. The simplest reliable setup is a **portable travel router**
(e.g. a cheap GL.iNet-style unit) that all three devices join:

```
   Z CAM E2  ---Wi-Fi--->  [ travel router ]  <---Wi-Fi---  Phone/tablet
                                   ^
                                   | Wi-Fi or Ethernet
                                   Raspberry Pi (runs S.P.O.T.S)
```

This avoids configuring the Pi as its own Wi-Fi access point (hostapd/dnsmasq),
which is more fragile to debug in the field. Set `camera.ip` in `config.yaml`
to whatever IP the router hands the Z CAM (check the router's client list, or
hit `http://<candidate-ip>/info` from the Pi to confirm).

## Raspberry Pi install

On a fresh Raspberry Pi (Raspberry Pi OS or other Debian-based image), run:

```bash
curl -fsSL https://raw.githubusercontent.com/DGTLVulcan/S.P.O.T.S/main/scripts/install.sh | bash
```

This installs the OS packages S.P.O.T.S needs (git, Python venv, OpenCV
runtime libs), clones the repo to `~/spots`, creates a Python virtual
environment there, installs the Python dependencies, copies
`config.example.yaml` to `config.yaml` if it doesn't already exist, and
installs a `spots` command to `~/.local/bin`. If `~/.local/bin` isn't already
on your `PATH`, the installer tells you the one line to add to `~/.bashrc`.

If you'd rather clone the repo yourself first (e.g. to a non-default
location), that works too -- run the same script from inside the clone and
it installs in place instead of cloning a second copy:

```bash
git clone https://github.com/DGTLVulcan/S.P.O.T.S.git && cd S.P.O.T.S
./scripts/install.sh
```

Edit `~/spots/config.yaml` (or `<your clone>/config.yaml`): set `camera.source`
to `zcam` and `camera.ip` to your Z CAM's IP address. Then start it:

```bash
spots
```

Then from your phone, browse to `http://<pi-ip>:8080/`.

### Updating

When you've pushed changes to the git repo and want the Pi to pick them up,
run:

```bash
spots -update
```

This pulls the latest commit (`git pull --ff-only`), reinstalls Python
dependencies in case `requirements.txt` changed, then starts the app as
usual. Plain `spots` (no flag) just starts it without touching git.

## At the range

1. Mount the camera on a tripod aimed at the target, connect power.
2. Power on the Pi (a USB power bank works fine).
3. Load the dashboard on your phone.
4. If the lens can't get physically close enough to fill the frame with the
   target, use the **Zoom** slider (and **Center Zoom Here** to pan) on the
   live view first -- see Digital zoom below. Do this before calibrating,
   since changing zoom afterward moves everything and invalidates it.
5. Click **Calibrate**, then click two points on the target a known distance
   apart (e.g. the printed target's left/right edge), and enter that
   real-world distance when prompted. Only needs to be redone if the camera
   moves, or if you change zoom again.
6. Click **New Target** right before shooting starts (with a clean target in
   frame) — this captures the reference frame everything is diffed against.
7. Shoot. New holes should appear on the feed with a stat panel updating
   live. Use **Undo Last Shot** if wind/debris triggers a false positive.
8. Review past strings any time from **Session History**, including a
   snapshot image of each shot and a CSV export.

## Digital zoom

If the lens can't physically get close enough to fill the frame with the
target, use the **Zoom** slider on the live view (1x-5x) plus **Center Zoom
Here** to pan (click the feed after enabling it). This is a software
crop+resize, not an optical zoom -- it trades resolution for framing, so
prefer getting physically closer or using a longer lens first if that's an
option. Detection accounts for the zoom level automatically (a hole's pixel
footprint grows with the square of zoom level, and the area filter scales to
match), so no threshold re-tuning is needed after zooming.

Changing zoom or pan moves every pixel in the frame, exactly like moving the
camera would -- it invalidates the current calibration and reference frame,
so re-run Calibrate and New Target afterward. The setting persists across
restarts (saved to `config.yaml`), so you only need to dial it in once per
camera setup, not every session.

## Wind / shockwave re-alignment

By default (`detection.realignment_enabled: true`) each frame is matched
against the clean reference via ORB feature points before diffing, so a
target swaying on its stand doesn't get misread as shots. This needs enough
visual texture in frame to find feature points on — a tight crop of blank
white paper can starve it. Frame the shot so the target stand, backstop, or
surrounding terrain is visible too, not just the paper itself. If a frame
can't find enough matches it's skipped for detection that cycle (logged as a
warning) rather than risking a bad alignment; occasional skips are fine,
constant skipping means reframe for more texture or increase
`stream_width`/`stream_height`. If your Pi is CPU-constrained and the target
setup is genuinely wind-free (indoor range, no nearby impacts), set
`realignment_enabled: false` to skip this work entirely.
