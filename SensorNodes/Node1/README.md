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
