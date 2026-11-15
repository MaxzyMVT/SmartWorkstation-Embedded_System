# Node 1 chair monitor

ESP32 DOIT DevKit V1, active-high FSR seat signal on GPIO 33 with an external
10 kohm pull-down to ground. The signal must stay within 0–3.3 V and cross
the ESP32 digital input thresholds reliably. Condition an analog FSR signal
with a comparator/hysteresis if necessary; this firmware uses digital edges,
not ADC sampling on GPIO 34. A mechanical switch can test the firmware but
does not count as the project's FSR sensor.

## Build and RTOS design

From this directory, run `pio run`, then `pio run -t upload` when a board is
connected. Inspect serial diagnostics with `pio device monitor -b 115200`.
The ESP32 platform is pinned to 6.10.0 (Arduino 2 / ESP-IDF 4.4).

- `vTaskSeatSample`, priority 2: block on an ISR edge mailbox, then wait for
  40 ms without another edge before reading the seat level. The boot level
  follows the same debounce procedure. The mailbox coalesces contact bounce.
- `vTaskSessionTimer`, priority 1: account elapsed monotonic microseconds
  every 100 ms and at accepted transitions. Only seated intervals accumulate;
  standing pauses the total rather than resetting it. Emit snapshots on
  transitions and every second. Lower priority is sufficient for accounting
  because it uses actual elapsed time rather than counting timer ticks.
- `vTaskTelemetrySend`, priority 2: consume snapshots through a bounded
  FreeRTOS queue. The ISR also gives its wake semaphore with
  `xSemaphoreGiveFromISR`; JSON and all I/O work happen outside the ISR.
- `loop()` deletes its task. No sensor busy polling occurs there.

There is no battery sensing circuit in the specified pinout. `battery_mv: 0`
means unavailable, not a measured empty battery. Add a documented ADC divider
and calibration before reporting a real voltage. Sitting time starts after
boot debounce and includes debounce latency at transitions (about 40 ms per
transition), not time spent booting.

## Network configuration and E4

Copy `.env.example` to `.env` and fill in the Wi-Fi and local gateway broker
settings before building/flashing. Empty defaults compile with networking
disabled. The loader accepts `KEY=value`, matching optional quotes, and
full-line `#` comments. It does not expand escapes, variables, or inline
comments. Process environment variables override `.env`. Generated headers
live under ignored `.pio/`; firmware binaries contain credentials and must
be kept private. No real credentials belong in tracked files.

Publish to `smart_workstation/node1` at 1 Hz and on accepted transitions,
QoS 1, no retain:

```json
{"node_id":"node1","presence":1,"sitting_duration_min":14.5,"battery_mv":0,"seq":102}
```

The timer produces snapshots into a 120-packet FreeRTOS FIFO. Only the
telemetry task consumes it, and only after matching the MQTT packet ID in
PUBACK. ESP-MQTT retries unacknowledged packets every 5 seconds and reconnects
automatically. If the library outbox expires a packet, the application
re-enqueues the unchanged queue head. At most one message is in flight;
recovery drains up to 10 packets/second. Wi-Fi reconnection attempts are
spaced 10 seconds apart; sampling and timing never wait for networking.

A full queue drops the newest snapshot and logs its sequence. Existing
packets, including the unacknowledged head, remain intact. `seq` advances
even for dropped snapshots and wraps at 32 bits. QoS 1 may deliver duplicates;
consumers should deduplicate sequences within a boot session. Backlogged
packets are historical; the required schema has no sample timestamp.
Power loss clears the RAM queue and restarts the sequence and sitting total.

PUBACK proves broker receipt, not processing by the Python gateway or cloud
persistence. The current gateway has no application ACK. Run the broker on
the gateway for the node-to-gateway reliability demo. The node uses local
MQTT/TCP; the course rubric makes encryption optional on this link and
requires E5 TLS/authentication on the gateway-to-cloud link.
See the [Espressif MQTT reference](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/api-reference/protocols/mqtt.html).

Loader checks: `python -m unittest discover -s test -p test_load_config.py`.
