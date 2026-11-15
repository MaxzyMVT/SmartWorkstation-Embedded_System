#include <Arduino.h>
#include <WiFi.h>
#include <mqtt_client.h>
#include <freertos/event_groups.h>
#include "node1_config.h"
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
QueueHandle_t ackQueue;
EventGroupHandle_t mqttEvents;
constexpr EventBits_t kConnected = BIT0;
constexpr char kTopic[] = "smart_workstation/node1";

// Library callbacks only post RTOS events. The telemetry task owns the FIFO head.
void onMqttEvent(void *, esp_event_base_t, int32_t eventId, void *data) {
  auto *event = static_cast<esp_mqtt_event_t *>(data);
  if (eventId == MQTT_EVENT_CONNECTED) {
    xEventGroupSetBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_DISCONNECTED) {
    xEventGroupClearBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_PUBLISHED) {
    xQueueOverwrite(ackQueue, &event->msg_id);
  }
  xSemaphoreGive(telemetryWake);
}

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
  esp_mqtt_client_handle_t client = nullptr;
  const bool configured = NODE1_WIFI_SSID[0] && NODE1_MQTT_BROKER_HOST[0];
  if (configured) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(NODE1_WIFI_SSID, NODE1_WIFI_PASSWORD);
    esp_mqtt_client_config_t config{};
    config.host = NODE1_MQTT_BROKER_HOST;
    config.port = NODE1_MQTT_BROKER_PORT;
    config.transport = MQTT_TRANSPORT_OVER_TCP; // Local gateway link; E5 cloud TLS is gateway-owned.
    config.username = NODE1_MQTT_USERNAME[0] ? NODE1_MQTT_USERNAME : nullptr;
    config.password = NODE1_MQTT_PASSWORD[0] ? NODE1_MQTT_PASSWORD : nullptr;
    config.keepalive = 15;
    config.reconnect_timeout_ms = 5000;
    config.network_timeout_ms = 2000;
    config.message_retransmit_timeout = 5000;
    client = esp_mqtt_client_init(&config);
    if (!client) abort();
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(client, MQTT_EVENT_ANY, onMqttEvent, nullptr));
    ESP_ERROR_CHECK(esp_mqtt_client_start(client));
  } else {
    Serial.println("Network disabled: configure Node1/.env and rebuild");
  }
  uint32_t lastWifiRetry = millis(), sentAt = 0;
  uint32_t lastEnqueueAttempt = millis() - 1000;
  int pendingId = -1;
  for (;;) {
    xSemaphoreTake(telemetryWake, pdMS_TO_TICKS(20));
    const uint32_t now = millis();
    if (configured && WiFi.status() != WL_CONNECTED && now - lastWifiRetry >= 10000) {
      lastWifiRetry = now;
      WiFi.reconnect();
    }
    int acknowledgedId;
    Telemetry packet;
    // The immutable head remains in the queue until its matching PUBACK.
    if (xQueueReceive(ackQueue, &acknowledgedId, 0) == pdTRUE &&
        pendingId >= 0 && acknowledgedId == pendingId &&
        xQueueReceive(telemetryQueue, &packet, 0) == pdTRUE) {
      pendingId = -1;
      Serial.printf("PUBACK seq=%lu queued=%u\n", static_cast<unsigned long>(packet.seq),
                    static_cast<unsigned>(uxQueueMessagesWaiting(telemetryQueue)));
    }
    // ESP-MQTT retries every 5 s, including after reconnect. Only re-enqueue
    // ourselves if its outbox expired the packet, keeping the same sequence.
    if (client && pendingId >= 0 && now - sentAt >= 30000 &&
        esp_mqtt_client_get_outbox_size(client) == 0) pendingId = -1;
    if (client && pendingId < 0 && (xEventGroupGetBits(mqttEvents) & kConnected) &&
        now - lastEnqueueAttempt >= 100 && xQueuePeek(telemetryQueue, &packet, 0) == pdTRUE) {
      lastEnqueueAttempt = now;
      char payload[192];
      const int length = snprintf(payload, sizeof(payload),
          "{\"node_id\":\"node1\",\"presence\":%u,"
          "\"sitting_duration_min\":%.6f,\"battery_mv\":0,\"seq\":%lu}",
          packet.presence ? 1u : 0u, packet.sittingUs / 60000000.0,
          static_cast<unsigned long>(packet.seq));
      if (length > 0 && static_cast<size_t>(length) < sizeof(payload)) {
        pendingId = esp_mqtt_client_enqueue(client, kTopic, payload, length, 1, 0, true);
        if (pendingId >= 0) sentAt = now;
        else Serial.println("MQTT enqueue failed: retaining head for retry");
      }
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
  ackQueue = xQueueCreate(1, sizeof(int));
  mqttEvents = xEventGroupCreate();
  if (!stateMutex || !telemetryWake || !edgeQueue || !telemetryQueue || !ackQueue || !mqttEvents) abort();
  attachInterruptArg(kSeatPin, onSeatEdge, nullptr, CHANGE);
  const TickType_t initial = xTaskGetTickCount();
  xQueueOverwrite(edgeQueue, &initial); // Debounce the boot/wake level too.
  // Sampling and networking outrank the slow timer; all tasks block when idle.
  if (xTaskCreate(vTaskSeatSample, "seat", 3072, nullptr, 2, nullptr) != pdPASS ||
      xTaskCreate(vTaskSessionTimer, "session", 3072, nullptr, 1, nullptr) != pdPASS ||
      xTaskCreate(vTaskTelemetrySend, "telemetry", 6144, nullptr, 2, nullptr) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
