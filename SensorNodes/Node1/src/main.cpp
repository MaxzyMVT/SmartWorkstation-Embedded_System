#include <Arduino.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace {
constexpr uint8_t kSeatPin = 33;
constexpr uint32_t kDebounceMs = 40;
struct Telemetry {
  bool presence;
  uint64_t sittingUs;
  uint32_t seq;
};
struct SeatState {
  bool ready = false;
  bool presence = false;
  uint64_t sittingUs = 0;
  int64_t accountedUs = 0;
  int64_t vacantSinceUs = 0;
};
SeatState seat;
SemaphoreHandle_t stateMutex;
SemaphoreHandle_t telemetryWake;
QueueHandle_t edgeQueue;
QueueHandle_t telemetryQueue;

// Called with stateMutex held. Absolute monotonic time avoids RTOS tick drift.
void accountUntil(int64_t now) {
  if (seat.ready && seat.presence) seat.sittingUs += now - seat.accountedUs;
  seat.accountedUs = now;
}

void IRAM_ATTR onSeatEdge(void *) {
  const TickType_t edge = xTaskGetTickCountFromISR();
  BaseType_t wake = pdFALSE;
  // One-item mailbox coalesces bounce; processing waits for a quiet period.
  xQueueOverwriteFromISR(edgeQueue, &edge, &wake);
  xSemaphoreGiveFromISR(telemetryWake, &wake);
  if (wake) portYIELD_FROM_ISR();
}

void vTaskSeatSample(void *) {
  TickType_t edge;
  for (;;) {
    xQueueReceive(edgeQueue, &edge, portMAX_DELAY);
    while (xQueueReceive(edgeQueue, &edge, pdMS_TO_TICKS(kDebounceMs)) == pdTRUE) {}
    const bool occupied = digitalRead(kSeatPin) == HIGH;
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    accountUntil(now);
    if (!seat.ready || seat.presence != occupied) {
      seat.presence = occupied;
      seat.vacantSinceUs = occupied ? 0 : now;
      seat.ready = true;
    }
    xSemaphoreGive(stateMutex);
    xSemaphoreGive(telemetryWake);
  }
}

void vTaskSessionTimer(void *) {
  TickType_t lastWake = xTaskGetTickCount();
  int64_t lastReport = 0;
  bool reported = false, previousPresence = false;
  uint32_t seq = 0;
  for (;;) {
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    accountUntil(now);
    if (seat.ready && (!reported || seat.presence != previousPresence ||
                        now - lastReport >= 1000000)) {
      const Telemetry packet{seat.presence, seat.sittingUs, ++seq};
      if (xQueueSend(telemetryQueue, &packet, 0) != pdTRUE)
        Serial.printf("Telemetry queue full: dropped seq=%lu\n", static_cast<unsigned long>(seq));
      previousPresence = seat.presence;
      reported = true;
      lastReport = now;
      xSemaphoreGive(telemetryWake);
    }
    xSemaphoreGive(stateMutex);
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(100));
  }
}

void vTaskTelemetrySend(void *) {
  for (;;) {
    xSemaphoreTake(telemetryWake, pdMS_TO_TICKS(20));
    Telemetry packet;
    while (xQueueReceive(telemetryQueue, &packet, 0) == pdTRUE) {
      Serial.printf("{\"node_id\":\"node1\",\"presence\":%u,"
                    "\"sitting_duration_min\":%.6f,\"battery_mv\":0,\"seq\":%lu}\n",
                    packet.presence ? 1u : 0u, packet.sittingUs / 60000000.0,
                    static_cast<unsigned long>(packet.seq));
    }
  }
}
} // namespace

void setup() {
  Serial.begin(115200);
  pinMode(kSeatPin, INPUT); // External 10 kohm pull-down; active HIGH.
  stateMutex = xSemaphoreCreateMutex();
  telemetryWake = xSemaphoreCreateBinary();
  edgeQueue = xQueueCreate(1, sizeof(TickType_t));
  telemetryQueue = xQueueCreate(120, sizeof(Telemetry));
  if (!stateMutex || !telemetryWake || !edgeQueue || !telemetryQueue) abort();
  attachInterruptArg(kSeatPin, onSeatEdge, nullptr, CHANGE);
  const TickType_t initial = xTaskGetTickCount();
  xQueueOverwrite(edgeQueue, &initial); // Debounce the boot/wake level too.
  // Sampling and networking outrank the slow timer; all tasks block when idle.
  if (xTaskCreate(vTaskSeatSample, "seat", 3072, nullptr, 2, nullptr) != pdPASS ||
      xTaskCreate(vTaskSessionTimer, "session", 3072, nullptr, 1, nullptr) != pdPASS ||
      xTaskCreate(vTaskTelemetrySend, "telemetry", 6144, nullptr, 2, nullptr) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
