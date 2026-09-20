# robot-link

Binary communication and service gateway between the BeagleBone machine controller and the Raspberry Pi accessory computer.

The first working functions are BeagleBone-requested Pi shutdown for low battery and BeagleBone-requested WAV or speech output through the Pi ALSA/I2S device. The BeagleBone is the TCP server on port 5555; `robot-linkd` reconnects automatically.

```text
AA VERSION LENGTH_H LENGTH_L TYPE_H TYPE_L FLAGS SEQ_H SEQ_L
PAYLOAD... CRC_H CRC_L 55
```

Run `make test`. Use `--dry-run` during bench testing so shutdown and audio requests are logged without executing.
