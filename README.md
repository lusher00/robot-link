# robot-link

`robot-link` is the persistent control and service connection between the
BeagleBone Blue machine controller and the Raspberry Pi 5 accessory computer.
The Bone remains autonomous and safe without the Pi. The Pi adds higher-level
services such as audio, perception, UI, logging, lidar, SLAM, and later
navigation.

The current implementation runs one TCP session over the USB gadget network:

- BeagleBone: `robot-link-boned`, TCP server at `192.168.7.2:5555`
- Raspberry Pi: `robot-linkd`, reconnecting client at `192.168.7.1`
- Protocol: framed version 2 packets with CRC-16/XMODEM, message types, flags,
  and sequence numbers

The working feature set currently includes:

- automatic reconnect and heartbeat timeout detection
- forwarding batt_monitor's confirmed shutdown event to the Pi
- Bone-requested WAV playback and text-to-speech through the Pi I2S output
- Bone battery voltage telemetry to the Pi
- Pi runtime status at `/run/robot-link/status.json`
- OLED integration for real link state, Bone voltage, and both Pi IP addresses

The complete planned architecture, including Hailo, ROS 2, lidar, SLAM,
odometry, configuration, and navigation boundaries, is in
[docs/PROTOCOL_DESIGN.md](docs/PROTOCOL_DESIGN.md). It is the design baseline,
not a claim that every listed message and subsystem is implemented today.

## Repository layout

```text
robot_link/                 Python package and daemons
systemd/                    Pi and Bone service units and environment examples
tests/                      Protocol, service, battery, and status tests
docs/DEPLOYMENT.md          Installation and operating procedure
docs/PROTOCOL_DESIGN.md     Full architecture and requirements baseline
```

## Install

Install the package on both computers, but install only the service belonging
to that machine. Full commands and configuration are in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```bash
sudo python3 -m pip install --break-system-packages --force-reinstall .
```

On the Bone:

```bash
sudo install -m 644 systemd/robot-link-boned.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robot-link-boned
```

On the Pi:

```bash
sudo install -m 644 systemd/robot-linkd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robot-linkd
```

## Verify

From the Bone:

```bash
sudo robot-linkctl status
sudo robot-linkctl sound alert.wav
sudo robot-linkctl speak "Battery voltage is eleven point eight volts"
sudo robot-linkctl shutdown --reason test --delay 10
```

From the Pi:

```bash
systemctl status robot-linkd --no-pager
sudo cat /run/robot-link/status.json
```

A healthy Pi status file resembles:

```json
{"connected":true,"bone_host":"192.168.7.2","voltage":11.77}
```

## Test

```bash
make test
```

Use `--dry-run` during bench testing when shutdown or audio commands should be
logged without being executed.

## Safety boundary

Robot Link coordinates software behavior; it is not the last line of battery
or motion safety. Balance control, motor limits, e-stop validation, watchdogs,
and hardware undervoltage protection remain local to the Bone or independent
hardware. The Pi may disappear or reboot without preventing the Bone from
keeping the machine safe.

`batt_monitor` exclusively owns voltage thresholds, trend qualification, and
critical-sample confirmation. Robot Link forwards its explicit shutdown event;
it does not make a second voltage decision.
