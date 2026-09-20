# robot-link

Binary communication and service gateway between the BeagleBone machine controller and the Raspberry Pi accessory computer.

The first working functions are BeagleBone-requested Pi shutdown for low battery and BeagleBone-requested WAV or speech output through the Pi ALSA/I2S device. The BeagleBone is the TCP server on port 5555; `robot-linkd` reconnects automatically.

```text
AA VERSION LENGTH_H LENGTH_L TYPE_H TYPE_L FLAGS SEQ_H SEQ_L
PAYLOAD... CRC_H CRC_L 55
```

Run `make test`. Use `--dry-run` during bench testing so shutdown and audio requests are logged without executing.

The Bone daemon reads `/run/batt_status.json`, requires several consecutive low samples, and ignores stale readings. Its shutdown threshold is configurable; the example starts at 9.6 V for a 3S pack and must be checked against the pack under real load.

After installing the package on each computer, use `systemd/robot-link-boned.service` on the Bone and `systemd/robot-linkd.service` on the Pi. From the Bone:

```bash
robot-linkctl status
robot-linkctl sound alert.wav
robot-linkctl speak "Battery low"
robot-linkctl shutdown --reason test --delay 10
```
