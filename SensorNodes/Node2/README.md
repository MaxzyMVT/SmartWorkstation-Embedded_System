# Node 2 desk environment monitor

ESP32 DOIT DevKit V1: MAX9814 OUT to ADC1 GPIO 35, DHT22 data
(with 4.7 kohm pull-up to 3.3 V) to GPIO 4. Use a common ground.
The alternative DS18B20 uses GPIO 4 and the same pull-up; connect only
one temperature sensor. Power the DS18B20 externally (not parasite mode).

## Build

Install PlatformIO, then run from this directory:

```
pio run                         # Build both temperature variants
pio run -e esp32doit-devkit-v1    # DHT22
pio run -e node2-ds18b20          # DS18B20
```

Use `pio run -e <environment> -t upload` to flash and
`pio device monitor -b 115200` to inspect diagnostics.

## Sampling and E1

- `vTaskSoundSample`, priority 3, core 1: sample 12-bit ADC for each
  50 ms window, calculate peak-to-peak amplitude, overwrite a one-item
  FreeRTOS queue, then block one tick so the idle task/watchdog can run.
  Windows have a one-tick gap; ADC timing is approximate, not audio-rate DMA.
- `vTaskTempSample`, priority 1, core 0: read every 2.5 seconds using
  `vTaskDelayUntil`. DS18B20 conversion waits yield for 750 ms. A failed
  reading invalidates the temperature mailbox rather than inventing data.
- `vTaskMqttDispatch`, priority 2, core 0: consume sensor mailboxes once
  per second. Networking belongs on this core, separate from sound sampling.
- `loop()` deletes its task; it never polls sensors.

`sound_db` is an **uncalibrated relative estimate**, not measured dBA.
The conversion is `reference_db + 20*log10(peak_to_peak/reference_counts)`;
zero amplitude is floored to one ADC count to keep output finite.
Default reference: 100 counts at 60 dB. Set build definitions
`NODE2_SOUND_REFERENCE_COUNTS` and `NODE2_SOUND_REFERENCE_DB` from measurements
against a reference meter at the installed gain. MAX9814 AGC and the lack of
A-weighting limit accuracy; verify several levels before using AI thresholds.

## Hardware checks (require an ESP32 and sensors)

1. Compare both temperature variants with a reference thermometer; collect
   repeated readings and report error and variation.
2. Unplug the temperature sensor: expect failure logs and no fresh telemetry
   using invalid values. Reconnect and confirm automatic recovery.
3. Apply quiet and loud sounds; verify finite, responsive estimates and
   calibrate against a reference meter. Check ADC clipping and noise floor.
4. Run for at least 24 hours and check watchdog resets, sample cadence, and
   heap stability. Compilation alone does not validate physical measurements.
