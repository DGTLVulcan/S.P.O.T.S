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

The Pi **is** the network -- no separate router needed. The installer
configures the Pi as its own WiFi access point for your phone/tablet, and as
a static-IP DHCP server on its Ethernet port for the Z CAM, which always
plugs directly into the Pi:

```
   Z CAM E2  ---Ethernet--->  Raspberry Pi (WiFi AP + runs S.P.O.T.S)  <---WiFi---  Phone/tablet
```

The Pi hands the camera its IP address via DHCP on Ethernet and the
dashboard auto-discovers it on every connect (see `spots/camera/discovery.py`)
-- you never need to look up or type in the camera's IP. See
`scripts/setup-network.sh` for exactly what it configures (WiFi AP SSID/
password, the Pi's IP on each interface); it prints the SSID and password to
join at the end.

## Raspberry Pi install

On a fresh Raspberry Pi (Raspberry Pi OS Bookworm or later -- the installer
uses NetworkManager, which is the default network stack there), from the
Pi's own console or a wired SSH session (see the warning below), run:

```bash
curl -fsSL https://raw.githubusercontent.com/DGTLVulcan/S.P.O.T.S/main/scripts/install.sh | bash
```

This installs the OS packages S.P.O.T.S needs (git, Python venv, OpenCV
runtime libs), clones the repo to `~/spots`, creates a Python virtual
environment there, installs the Python dependencies, copies
`config.example.yaml` to `config.yaml` if it doesn't already exist, installs
a `spots` command to `~/.local/bin`, configures the Pi's own WiFi AP +
camera DHCP (see Field network topology above), and installs a systemd
service so **S.P.O.T.S starts automatically on every boot** -- there's
nothing to manually run at the range.

**Warning:** the network setup step puts the Pi's WiFi into access-point
mode, which immediately drops any WiFi connection to the Pi (e.g. an SSH
session over WiFi). Run the installer from the Pi's console, over a wired
Ethernet SSH session, or set `SPOTS_SKIP_NETWORK=1` before running it to
skip that step and run `scripts/setup-network.sh` yourself later from the
console.

If you'd rather clone the repo yourself first (e.g. to a non-default
location), that works too -- run the same script from inside the clone and
it installs in place instead of cloning a second copy:

```bash
git clone https://github.com/DGTLVulcan/S.P.O.T.S.git && cd S.P.O.T.S
./scripts/install.sh
```

To use the real camera instead of the default synthetic feed, edit
`~/spots/config.yaml` (or `<your clone>/config.yaml`) and set `camera.source`
to `zcam` (leave `camera.ip` blank -- it's auto-discovered), then either
`sudo systemctl restart spots` or reboot for the change to take effect.

Once installed, join the WiFi network the installer printed and browse to
`http://<pi's AP IP>:8080/` (default `http://192.168.4.1:8080/`, or try
`http://<hostname>.local:8080/`).

Installer env var overrides (all optional): `SPOTS_DIR`, `REPO_URL`,
`SPOTS_SKIP_NETWORK`, `SPOTS_SKIP_SERVICE`, `SPOTS_AP_SSID`,
`SPOTS_AP_PASSWORD`, `SPOTS_AP_IP`, `SPOTS_ETH_IP`, `SPOTS_WIFI_COUNTRY` --
see the comments at the top of `scripts/install.sh` and
`scripts/setup-network.sh`.

### Updating

When you've pushed changes to the git repo and want the Pi to pick them up,
run:

```bash
spots -update
```

This pulls the latest commit (`git pull --ff-only`), reinstalls Python
dependencies in case `requirements.txt` changed, refreshes the installed
`spots` command itself (so new flags/fixes to it take effect immediately),
and restarts the systemd service (prompting for your sudo password) so the
update takes effect immediately. Plain `spots` (no flag) just reports that
the service is already running -- since it now starts automatically on
boot, you shouldn't normally need to start it by hand. `journalctl -u spots
-f` tails its logs.

### If the WiFi network or autostart didn't come up

If the installer's network/service steps failed partway (or you skipped
them with `SPOTS_SKIP_NETWORK`/`SPOTS_SKIP_SERVICE`), force them
individually without re-running the whole installer:

```bash
spots -initnetwork   # force (re)configure the WiFi AP + camera DHCP
spots -initservice    # force (re)install + enable + start the systemd service
```

Both are safe to re-run any time. If you're not sure what went wrong, the
simplest fix is usually to just re-run the installer itself (`curl -fsSL
.../install.sh | bash` or `./scripts/install.sh` from the clone) -- it's
idempotent and picks up wherever a previous run left off, including if it
aborted partway through (e.g. an apt package error before reaching the
network/service steps).

### Getting normal WiFi/internet back on the Pi

Once `setup-network.sh` has run, `wlan0` is a dedicated access point and
won't join your home WiFi (or get internet) on its own. To leave range mode
and reconnect it as a normal client:

```bash
sudo SPOTS_WIFI_SSID="YourHomeNetwork" SPOTS_WIFI_PASSWORD="yourpassword" \
  bash ~/spots/scripts/stop-network.sh
# or, once your installed `spots` command has this flag:
spots -stopnetwork
```

Omit the env vars to just take `wlan0` out of AP mode without reconnecting
it anywhere (e.g. if you'll connect it manually or it already knows a
network). `eth0`/`spots-eth` is left alone unless you set
`SPOTS_RESET_ETH=1` -- if you're SSH'd into the Pi *through* eth0, your
session's address almost certainly came from spots-eth's own DHCP server,
so reverting it will likely drop that session; only do that from the
console, or a connection you don't need to keep. Run `spots -initnetwork`
(or `setup-network.sh`) again whenever you're ready to go back to range mode.

## At the range

1. Mount the camera on a tripod aimed at the target, connect it to the Pi's
   Ethernet port, and power it on.
2. Power on the Pi (a USB power bank works fine). S.P.O.T.S starts itself --
   give it a minute to boot and connect to the camera.
3. Join the Pi's WiFi network from your phone (SSID/password from the
   installer output -- see Field network topology above) and load the
   dashboard.
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
