# Node 2 desk environment monitor

ESP32 DOIT DevKit V1: KY-037 Sound Sensor OUT to ADC1 GPIO 35, KY-001 DS18B20 data
(with 4.7 kohm pull-up to 3.3 V) to GPIO 4. Use a common ground.
Power the DS18B20 externally (not parasite mode).

## Build

Copy `.env.example` to `.env` and fill in the Wi-Fi and local gateway broker
settings before flashing. Empty defaults compile but disable networking.
The loader accepts `KEY=value`, optional matching quotes, and full-line `#`
comments (no inline comments, interpolation, or escape expansion). Process
environment variables override `.env`. Generated headers live under ignored
`.pio/`; neither credentials nor compiler flags containing them are committed.
Firmware binaries contain the configured credentials; keep them private.

Install PlatformIO, then run from this directory:

```
pio run                         # Build KY-001 DS18B20 firmware (default)
pio run -e node2-ds18b20          # Explicit DS18B20 target
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
against a reference meter at the installed gain. KY-037 sensitivity and the lack of
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

## MQTT and E4

Publish to `smart_workstation/node2` at 1 Hz with QoS 1, no retain:

```json
{"node_id":"node2","temp_c":24.8,"sound_db":42.5,"seq":204}
```

The dispatch task owns a 120-packet FIFO RAM buffer. It keeps the head until
the matching MQTT packet ID receives PUBACK. Only one packet is in flight.
ESP-MQTT retries unacknowledged packets at 5-second intervals and reconnects
automatically. If the library outbox expires a packet, the application retains
and re-enqueues it with the same sequence. Enqueue failures also retain the head.
Recovery drains the FIFO at up to 10 packets/second while new samples continue.
Wi-Fi reconnect attempts are spaced 10 seconds apart. Sensor queues remain
independent of network state. Invalid/stale temperature (>6 seconds) or sound
(>1 second) prevents creation of a new packet; valid buffered packets still drain.

When full, the FIFO drops the **newest** packet and logs its sequence plus a
cumulative drop counter; existing packets, including the unacknowledged head,
are preserved. `seq` increments for each valid sample, including overflow drops,
wraps at 32 bits, and restarts after reboot. RAM data is lost on power loss.
QoS 1 can deliver duplicates: consumers should deduplicate within a boot session.
Backlogged readings are historical; this required schema has no sample timestamp.

PUBACK confirms **broker receipt**, not execution by the Python gateway or cloud
persistence. The current gateway subscribes at QoS 0 and has no application ACK.
For the E4 demo, run the broker on the gateway and explain this boundary;
end-to-end processing guarantees would additionally require gateway changes.
The local node link uses MQTT/TCP; cloud TLS belongs to the gateway per the course
FAQ. See [Espressif ESP-MQTT API](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/api-reference/protocols/mqtt.html)
for QoS 1 outbox and `MQTT_EVENT_PUBLISHED` behavior.

## Reliability verification

Host buffer tests (a C++11 compiler is required):

```
g++ -std=c++11 -Wall -Wextra -Werror -I include test/test_telemetry_buffer.cpp -o .pio/test_telemetry_buffer.exe
.pio/test_telemetry_buffer.exe
python -m unittest discover -s test -p test_load_config.py
```

Tests cover full-buffer drops, repeated retries preserving the head, wrong and
duplicate ACKs, FIFO drain, ring wrap, and sequence rollover. These are logic
tests, not hardware/network tests. The Python tests check missing configuration,
quoting, environment overrides, and rejection of malformed settings.

Verified on 2026-11-15: `pio run` passed DS18B20 build, with
empty configuration and with synthetic network settings to exercise linking of
the complete Wi-Fi/MQTT path. Buffer tests and all three configuration tests
passed. OneWire 2.3.8 emits upstream `#undef` warnings; builds still succeed.
No board was flashed and no physical/network acceptance test was performed.

For the physical acceptance test:

1. Subscribe with `mosquitto_sub -h <gateway> -q 1 -t smart_workstation/node2`.
   Verify the exact keys, numeric readings, increasing `seq`, and PUBACK logs.
2. Stop the broker or disable Wi-Fi for 30 seconds, then restore it. Check that
   buffered sequences drain in order, with possible QoS 1 duplicates but no
   missing sequences before overflow, while sensor tasks keep running.
3. Repeat for more than 120 seconds. Verify drop counters and sequence gaps
   correspond to overflow, and automatic recovery without a reset.
4. Use a test proxy to suppress PUBACK while allowing outbound publishes.
   Confirm retransmissions preserve the same payload/sequence, memory stays
   bounded, and the queue drains once ACKs are restored.
5. Disconnect temperature during the outage and reconnect it: no fabricated
   temperature should appear; only previously buffered valid packets may drain.
