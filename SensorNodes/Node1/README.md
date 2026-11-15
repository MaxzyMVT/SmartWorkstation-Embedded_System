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
Power loss or a normal reset clears the RAM queue and restarts the sequence
and sitting total. Orderly deep sleep preserves all three in RTC memory.

PUBACK proves broker receipt, not processing by the Python gateway or cloud
persistence. The current gateway has no application ACK. Run the broker on
the gateway for the node-to-gateway reliability demo. The node uses local
MQTT/TCP; the course rubric makes encryption optional on this link and
requires E5 TLS/authentication on the gateway-to-cloud link.
See the [Espressif MQTT reference](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/api-reference/protocols/mqtt.html).

Loader checks: `python -m unittest discover -s test -p test_load_config.py`.

## Deep sleep and E3 power budget

After **more than 60 seconds** continuously vacant, the telemetry task pauses
snapshot production, stops MQTT and Wi-Fi, and rechecks both accepted presence
and the raw pin. If someone sat during shutdown, networking resumes. Otherwise
it checkpoints the entire FIFO (including an unacknowledged head), last
generated sequence, and sitting total into RTC slow RAM, then calls
`esp_sleep_enable_ext0_wakeup(GPIO_NUM_33, 1)` / `esp_deep_sleep_start()`.
The external pull-down holds the vacant level LOW during sleep.

GPIO 33 HIGH wakes the ESP32 through reset; the boot path restores the FIFO
before starting tasks and calls `rtc_gpio_deinit` before using digital I/O.
RTC data is accepted only after an ext0 deep-sleep reset with a valid marker
and bounded count. An ACK arriving during shutdown can cause a duplicate on
wake; it cannot remove a packet from the checkpoint. No timer wake is enabled:
offline data waits until the next seat wake, and a full queue still follows
the drop-newest policy. Boot, Wi-Fi reconnect, and debounce add latency; wake
does not imply immediate network delivery. An input already HIGH at sleep
entry immediately wakes it. No flash writes are made for each sample.

Measure current at the **battery input**, including the DevKit regulator,
USB-UART chip, power LED, FSR circuit, and supply losses. DevKit sleep draw can
be much higher than the ESP32 chip's sleep draw. Record active, connected idle,
radio shutdown, and deep sleep current plus boot/reconnect charge. Disconnect
USB during battery measurements to avoid back-powering. Repeat measurements
and report mean, min, max, and variance.

For measured active fraction `f` (including the 60-second vacancy tail),
`I_avg = f * I_active + (1 - f) * I_sleep` and
`life_hours = usable_capacity_mAh / I_avg_mA`.
Illustration only, **not measured**: a 2000 mAh battery, 80% usable capacity,
8 active hours/day at 80 mA and 16 sleeping hours/day at 5 mA gives
`I_avg = 30 mA`, or about **53.3 hours**. Replace these assumptions with
measurements from the assembled node to substantiate E3.

See [Espressif sleep documentation](https://docs.espressif.com/projects/esp-idf/en/v4.4/esp32/api-reference/system/sleep_modes.html)
for ext0 level wake, RTC memory, and GPIO reconfiguration.

## Physical acceptance checks

1. Subscribe on the gateway with
   `mosquitto_sub -h <gateway> -q 1 -t smart_workstation/node1`.
   Verify exact JSON keys, numeric values, 1/0 presence polarity, increasing
   sequence, and PUBACK serial logs. Test boot both seated and vacant.
2. Apply contact bounce shorter than 40 ms: no accepted transition should
   appear. Hold HIGH/LOW longer than 40 ms: one accepted transition each.
   Compare cumulative seated time with a reference stopwatch over repeated
   seated/standing intervals; standing must pause the total.
3. Remain vacant for 59 seconds: no sleep. Continue past 60 seconds: expect
   the deep-sleep log and reduced supply current. Sit, then verify ext0 wake,
   preserved sitting total, and increasing sequence. Repeat at least 30 times,
   including sitting during radio shutdown. Check wake and delivery latency.
4. Stop the broker or disable Wi-Fi for 30 seconds **while seated**, then
   restore it. Sampling must continue and buffered sequences drain in order.
   Repeat beyond 120 seconds: drops must match logged overflow sequences.
5. Suppress PUBACK with a test proxy while allowing outbound PUBLISH. Verify
   retries reuse the same payload/sequence and memory remains bounded. Restore
   ACKs and confirm the FIFO drains. This tests ACK loss, not just disconnects.
6. Disconnect the broker, stand, and allow deep sleep with queued packets.
   Restore the broker and sit: old packets must precede new ones, allowing
   QoS 1 duplicates. Repeat multiple sleep/wake cycles, then compare with a
   cold reset, which must start a fresh sequence and sitting total.
7. Run both physical nodes for at least 24 hours. Record packet delivery
   ratio, sequence gaps/duplicates, heap stability, resets, and current
   measurements. A disconnected FSR held LOW is indistinguishable from an
   empty chair with this single digital input; inspect hardware separately.

Verified on 2026-11-15: PlatformIO builds passed with empty configuration
and synthetic Wi-Fi/broker settings; all three loader tests passed. These
are compilation/configuration checks. No board was flashed and the physical
acceptance checks and current measurements above remain to be performed.
