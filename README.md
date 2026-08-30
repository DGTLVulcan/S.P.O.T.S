# S.P.O.T.S

**Shot Placement and Optical Target Scoring** — an automatic shot-scoring
system for the range. Point a network camera at a paper target, and S.P.O.T.S
watches the target for you: it spots each new bullet hole as it appears,
plots it, and keeps running group statistics on a web dashboard you open from
your phone. No walking downrange between strings, no spotting scope, no
manual measuring.

It is designed to run headless on a Raspberry Pi that also acts as its own
WiFi network, so the whole thing works at a bay with no internet, no router,
and nothing to start by hand:

```
   Z CAM E2  ---Ethernet--->  Raspberry Pi (WiFi AP + runs S.P.O.T.S)  <---WiFi---  Phone/tablet
```

## How it works

Frames are sampled from the camera a few times a second and differenced
against a clean reference image of the target captured when you press **New
Target**. Blobs that pass an area and circularity filter, and that persist
across consecutive frames, are committed as shots — then painted into the
reference, so the next shot is measured against what the target looks like
*now*. That burn-in is what lets tight, overlapping groups keep registering
shot by shot instead of merging into one growing blob.

Before each diff, the frame is re-aligned onto the reference with ORB (or
SIFT) feature matching and a homography, so a target swaying in the wind or
kicked by a nearby impact isn't read as a wall of new holes. A two-point
calibration against a known real-world distance converts pixels into
centimetres (or whatever unit you configure), which is what makes group sizes
and MOA meaningful.

## Features

**Live dashboard**
- Live video feed with every detected shot numbered and overlaid.
- **New Target** to reset the reference frame, **Undo Last Shot** to drop a
  false positive, and **Mark Center** to put the calibration origin on the
  true bullseye so shots are reported relative to the aim point.
- Two-point **Calibrate** for real-world scale, plus a target distance so
  group sizes can be reported in MOA.
- Digital zoom (1x–5x) with click-to-pan, for when the lens can't get close
  enough to fill the frame. Detection scales its filters to match, so no
  re-tuning after zooming.
- Shot-group diagram, live shot list, fullscreen view, and a light / dark /
  follow-OS theme toggle.

**Group statistics**
- Shot count, group center, extreme spread, mean radius, and standard
  deviation, updated as each shot lands.
- MOA conversion at your configured target distance.
- Best N-shot subgroups (3 and 5 by default) once enough shots exist.

**Session history**
- Every string is stored in SQLite with a snapshot image per shot.
- Browse, rename, delete individual sessions, or clear them all.
- Per-session detail page with the same group stats and diagram as the live
  view, plus a CSV export of every shot (sequence, real-world and pixel
  coordinates, timestamp).

**Camera and settings**
- Z CAM E2-series support over the network — auto-discovered on the Ethernet
  link, so there is no IP address to look up or type in.
- Live camera controls from the browser: ISO, EV, white balance, brightness,
  contrast, sharpness, saturation.
- Settings page for target, detection, and camera options, written back to
  `config.yaml`. Detection tuning applies immediately without a restart.
- A **synthetic** frame source that fabricates a target with holes appearing
  over time, so the whole pipeline can be developed and demoed with no
  hardware attached.

**Field deployment**
- One-line installer that sets up the Pi as a WiFi access point, hands the
  camera an address over Ethernet by DHCP, and installs a systemd service so
  S.P.O.T.S starts on every boot.
- A `spots` command for updating (`spots -update`), re-running the network or
  service setup, and returning the Pi to a normal WiFi/LAN client.

## Quick start (local, no hardware)

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml     # defaults to the synthetic camera
python S.P.O.T.S.py
```

Then open <http://localhost:8080/>. Press **Calibrate**, then **New Target**,
then click anywhere on the feed to punch a hole in the synthetic target and
watch it register. (On a live feed the same click records a manually tagged
test shot instead, for exercising calibration and stats without firing.)

## Installing on a Raspberry Pi

See **[SETUP.md](SETUP.md)** for the full field setup: the one-line
installer, WiFi/network topology, updating, and the at-the-range checklist.

## Configuration

All settings live in `config.yaml` (gitignored; `config.example.yaml` is used
as a fallback if it doesn't exist and documents every option inline). Most of
it is also editable from the dashboard's Settings page. The main groups are:

| Section | What it covers |
| --- | --- |
| `camera` | Source (`zcam` / `synthetic`), IP, stream resolution and bitrate, digital zoom |
| `target` | Target width and unit name, distance for MOA, best-subgroup sizes |
| `detection` | Sample rate, diff threshold, hole area/circularity filters, debounce, re-alignment |
| `storage` | SQLite database path and snapshot directory |
| `web` | Bind host/port, stream frame rate, quality, and max width |

## Project layout

```
spots/
  camera/      Z CAM HTTP client, RTSP + synthetic frame sources, discovery, controls
  vision/      calibration, shot detection, group statistics
  web/         Flask app, routes, templates, static assets
  worker.py    background thread: frame source -> detector -> storage
  storage.py   SQLite persistence for sessions and shots
  config.py    YAML settings load/save
scripts/       Pi installer, network setup/teardown, systemd unit, spots CLI
```

## Requirements

Python 3.9+, Flask, OpenCV (headless), NumPy, requests, PyYAML — see
`requirements.txt`. A Z CAM E2-series camera is optional; the synthetic
source covers everything except the camera itself.

## Status

The detection pipeline and dashboard are verified end-to-end against the
synthetic frame source. The Z CAM HTTP/RTSP client is written against Z CAM's
documented API but has not yet been exercised against real hardware on a Pi —
see the top of [SETUP.md](SETUP.md) for what to check first on-site.
