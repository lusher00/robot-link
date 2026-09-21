# Robot Link Deployment

This procedure installs Robot Link on the BeagleBone Blue and Raspberry Pi 5,
verifies the dedicated USB network, and confirms battery telemetry and OLED
integration. The Bone is the TCP server. The Pi is the reconnecting client.

## Network

| Machine | Interface | Address | Role |
|---|---|---|---|
| Raspberry Pi 5 | USB Ethernet | `192.168.7.1/24` | TCP client |
| BeagleBone Blue | `usb0` | `192.168.7.2/24` | TCP server |

Confirm both directions before installing services:

```bash
# Pi
ping -c 3 192.168.7.2

# Bone
ping -c 3 192.168.7.1
```

## Install on the BeagleBone

```bash
cd ~/robot-link
git pull --rebase
sudo python3 -m pip install --break-system-packages --force-reinstall .
sudo install -m 644 systemd/robot-link-boned.service \
  /etc/systemd/system/robot-link-boned.service
sudo systemctl daemon-reload
sudo systemctl enable --now robot-link-boned
sudo systemctl restart robot-link-boned
```

If `/etc/default/robot-link-bone` does not exist, start from the example:

```bash
sudo install -m 644 systemd/robot-link-bone.env.example \
  /etc/default/robot-link-bone
```

Robot Link only needs its listen address, the battery status path and
forwarding timing:

```text
ROBOT_LINK_LISTEN=192.168.7.2
ROBOT_LINK_BATTERY_FILE=/run/batt_status.json
ROBOT_LINK_BATTERY_INTERVAL=1
ROBOT_LINK_BATTERY_MAX_AGE=120
ROBOT_LINK_PI_SHUTDOWN_DELAY=5
```

`ROBOT_LINK_LISTEN` must be the USB gadget address. `0.0.0.0` also accepts
connections over the Bone's Wi-Fi, and any such client would occupy the single
Pi slot. If `usb0` is not up yet at boot, the service logs one warning and
waits for the address instead of exiting.

`batt_monitor` owns calibration, thresholds, confirmation, long-term trend,
and the Bone shutdown grace period. Its watch service publishes a unique
`shutdown_event`; Robot Link forwards each event to the Pi exactly once. Do not
configure a second voltage threshold in Robot Link.

Verify the Bone service and its battery source:

```bash
systemctl status robot-link-boned --no-pager
sudo cat /run/batt_status.json
sudo robot-linkctl status
```

The battery file should contain fields similar to:

```json
{"voltage":10.757,"status":"ok","shutdown_enabled":1,"critical_samples":0,"shutdown_requested":0,"shutdown_event":0}
```

## Install on the Raspberry Pi

```bash
cd ~/robot-link
git pull --rebase
sudo python3 -m pip install --break-system-packages --force-reinstall .
sudo install -m 644 systemd/robot-linkd.service \
  /etc/systemd/system/robot-linkd.service
sudo systemctl daemon-reload
sudo systemctl enable --now robot-linkd
sudo systemctl restart robot-linkd
```

If `/etc/default/robot-link` does not exist, start from the example:

```bash
sudo install -m 644 systemd/robot-link.env.example /etc/default/robot-link
```

Verify the connection:

```bash
systemctl status robot-linkd --no-pager
sudo journalctl -u robot-linkd -n 30 --no-pager
sudo cat /run/robot-link/status.json
```

`connected:true` means the Pi has a live session to the Bone. `voltage` is the
latest battery sample sent by the Bone. A null voltage means no battery sample
has reached the Pi yet; check `batt_monitor`, restart the Bone service, and
wait for a fresh sample.

Only `robot-linkd.service` belongs on the Pi. If the Bone service was installed
there accidentally, remove it:

```bash
sudo systemctl disable --now robot-link-boned 2>/dev/null || true
sudo rm -f /etc/systemd/system/robot-link-boned.service
sudo systemctl daemon-reload
```

## Install the Pi OLED display

The OLED program lives in the separate `oled-utils` repository and reads
`/run/robot-link/status.json`. It displays the real link state, Bone voltage,
Wi-Fi address, and USB address.

```bash
cd ~/oled-utils
git pull --rebase
sudo python3 -m pip install --break-system-packages luma.oled pillow
sudo ./oled_status.py --probe
sudo ./oled_status.py --install --force
sudo systemctl reset-failed oled-status
sudo systemctl restart oled-status
systemctl status oled-status --no-pager
```

The generated Pi unit should resolve to settings like:

```ini
SupplementaryGroups=i2c
ProtectHome=read-only
```

Run the end-to-end OLED diagnostic with:

```bash
cd ~/oled-utils
sudo ./oled_check.py --draw
```

## Functional checks

From the Bone:

```bash
sudo robot-linkctl status
sudo robot-linkctl sound alert.wav
sudo robot-linkctl speak "Robot Link audio test"
```

Use the shutdown command only when an orderly Pi shutdown is safe:

```bash
sudo robot-linkctl shutdown --reason test --delay 10
```

## Troubleshooting

### Pi status is connected but voltage is null

```bash
# Bone
systemctl status batt_monitor robot-link-boned --no-pager
sudo cat /run/batt_status.json
sudo systemctl restart robot-link-boned

# Pi
sudo systemctl restart robot-linkd
sleep 5
sudo cat /run/robot-link/status.json
```

### OLED exits with 216 GROUP

The service names a supplemental group that does not exist. Regenerate the
unit with the corrected installer or remove the nonexistent group. On the Pi,
the expected group is normally `i2c`.

### OLED exits with 200 CHDIR

`ProtectHome=yes` hid the script and its working directory. The corrected unit
uses `ProtectHome=read-only`.

### OLED service runs but the panel is dark

```bash
cd ~/oled-utils
sudo ./oled_check.py --draw
journalctl -u oled-status -n 30 --no-pager
```

The diagnostic checks the unit, I2C device permissions, display address,
Python modules, initialization, and an actual test draw.
