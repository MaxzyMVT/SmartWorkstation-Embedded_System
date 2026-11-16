#include <Arduino.h>
#include <WiFi.h>
#include <mqtt_client.h>
#include <freertos/event_groups.h>
#include "node1_config.h"
#include <esp_timer.h>
#include <esp_sleep.h>
#include <esp_system.h>
#include <driver/rtc_io.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace {
constexpr uint8_t kSeatPin = 33;
// Inverted logic: Pressed (Seated) = LOW, Released (Empty) = HIGH
constexpr uint8_t kSeatOccupiedLevel = LOW;
constexpr uint8_t kSeatVacantLevel = HIGH;
constexpr uint32_t kDebounceMs = 40;
constexpr UBaseType_t kQueueCapacity = 120;
constexpr int64_t kVacantSleepUs = 60000000;
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
bool preparingSleep = false; // Protected by stateMutex, including producer checks.
uint32_t sequence = 0;
constexpr uint32_t kRtcMagic = 0x4E314352;
struct SleepCheckpoint {
  uint32_t magic;
  uint32_t seq;
  uint64_t sittingUs;
  uint32_t count;
  Telemetry packets[kQueueCapacity];
};
RTC_DATA_ATTR SleepCheckpoint checkpoint{};
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
    Serial.println("[Node1 MQTT] Connected to broker.emqx.io!");
    xEventGroupSetBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_DISCONNECTED) {
    Serial.println("[Node1 MQTT] Disconnected from broker!");
    xEventGroupClearBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_ERROR) {
    Serial.println("[Node1 MQTT] Connection error encountered!");
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

// The telemetry task owns shutdown and the queue head. Never sleep waiting
// forever for an offline broker: retain every queued packet in RTC slow RAM.
void sleepIfVacant(esp_mqtt_client_handle_t client) {
  xSemaphoreTake(stateMutex, portMAX_DELAY);
  const bool due = seat.ready && !seat.presence && !preparingSleep &&
      esp_timer_get_time() - seat.vacantSinceUs > kVacantSleepUs &&
      digitalRead(kSeatPin) == kSeatVacantLevel;
  if (due) preparingSleep = true;
  xSemaphoreGive(stateMutex);
  if (!due) return;

  ESP_ERROR_CHECK(esp_sleep_enable_ext0_wakeup(GPIO_NUM_33, kSeatOccupiedLevel));
  if (client) ESP_ERROR_CHECK(esp_mqtt_client_stop(client));
  WiFi.mode(WIFI_OFF);

  xSemaphoreTake(stateMutex, portMAX_DELAY);
  // Someone may sit down during radio shutdown. Resume normal operation if so.
  if (seat.presence || digitalRead(kSeatPin) == kSeatOccupiedLevel) {
    preparingSleep = false;
    xSemaphoreGive(stateMutex);
    if (client) {
      WiFi.mode(WIFI_STA);
      WiFi.begin(NODE1_WIFI_SSID, NODE1_WIFI_PASSWORD);
      ESP_ERROR_CHECK(esp_mqtt_client_start(client));
    }
    return;
  }
  accountUntil(esp_timer_get_time());
  checkpoint.magic = 0;
  checkpoint.seq = sequence;
  checkpoint.sittingUs = seat.sittingUs;
  checkpoint.count = 0;
  while (checkpoint.count < kQueueCapacity &&
         xQueueReceive(telemetryQueue, &checkpoint.packets[checkpoint.count], 0) == pdTRUE)
    ++checkpoint.count;
  checkpoint.magic = kRtcMagic;
  // External pull-down remains effective in RTC mode. Disable internal pulls.
  ESP_ERROR_CHECK(rtc_gpio_pullup_dis(GPIO_NUM_33));
  ESP_ERROR_CHECK(rtc_gpio_pulldown_dis(GPIO_NUM_33));
  Serial.printf("Vacant >60 s: deep sleep, retained=%lu seq=%lu\n",
                static_cast<unsigned long>(checkpoint.count),
                static_cast<unsigned long>(checkpoint.seq));
  Serial.flush();
  // Keep the mutex locked until reset to freeze producers/checkpoint state.
  // A HIGH arriving after the final check causes immediate ext0 wake-up.
  esp_deep_sleep_start();
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
    const bool occupied = digitalRead(kSeatPin) == kSeatOccupiedLevel;
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    accountUntil(now);
    if (!seat.ready || seat.presence != occupied) {
      seat.presence = occupied;
      seat.vacantSinceUs = occupied ? 0 : now;
      seat.ready = true;
      Serial.printf(">>> [Seat Event] State changed to: %s (Pin33=%s) <<<\n",
                    occupied ? "OCCUPIED (Seated)" : "EMPTY (Standing)",
                    (digitalRead(kSeatPin) == HIGH) ? "HIGH" : "LOW");
    }
    xSemaphoreGive(stateMutex);
    xSemaphoreGive(telemetryWake);
  }
}

void vTaskSessionTimer(void *) {
  TickType_t lastWake = xTaskGetTickCount();
  int64_t lastReport = 0;
  bool reported = false, previousPresence = false;
  for (;;) {
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const int64_t now = esp_timer_get_time();
    accountUntil(now);
    if (!preparingSleep && seat.ready && (!reported || seat.presence != previousPresence ||
                        now - lastReport >= 1000000)) {
      const Telemetry packet{seat.presence, seat.sittingUs, ++sequence};
      if (xQueueSend(telemetryQueue, &packet, 0) != pdTRUE)
        Serial.printf("Telemetry queue full: dropped seq=%lu\n", static_cast<unsigned long>(sequence));

      const float durationMin = static_cast<float>(seat.sittingUs / 60000000.0);
      const int pinDigital = digitalRead(kSeatPin);
      const bool wifiOk = (WiFi.status() == WL_CONNECTED);
      const bool mqttOk = (xEventGroupGetBits(mqttEvents) & kConnected) != 0;

      Serial.printf("[Node1 Monitor] Presence: %-8s | Pin33: %-4s | Sitting: %.2f min | WiFi: %s | MQTT: %s | Queue: %u\n",
                    seat.presence ? "OCCUPIED" : "EMPTY",
                    pinDigital ? "HIGH" : "LOW",
                    durationMin,
                    wifiOk ? WiFi.localIP().toString().c_str() : "DISCONNECTED",
                    mqttOk ? "ONLINE" : "OFFLINE",
                    static_cast<unsigned>(uxQueueMessagesWaiting(telemetryQueue)));

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
    config.client_id = "smart-workstation-node1-chair";
    config.keepalive = 15;
    config.reconnect_timeout_ms = 3000;
    config.network_timeout_ms = 5000;
    config.message_retransmit_timeout = 5000;
    client = esp_mqtt_client_init(&config);
    if (!client) abort();
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(client, MQTT_EVENT_ANY, onMqttEvent, nullptr));
    ESP_ERROR_CHECK(esp_mqtt_client_start(client));
    Serial.println("[Node1 Net] WiFi & MQTT initialized");
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
    sleepIfVacant(client);
  }
}
} // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n==========================================");
  Serial.println("  SmartWorkstation - Sensor Node 1 (Chair)");
  Serial.println("  GPIO 33: FSR Seat Pressure Mat / Switch ");
  Serial.println("  Power: ext0 deep sleep after 60s vacant ");
  Serial.println("==========================================");
  // ext0 leaves the pad in RTC mode; restore it before digitalRead/interrupts.
  ESP_ERROR_CHECK(rtc_gpio_deinit(GPIO_NUM_33));
  pinMode(kSeatPin, INPUT_PULLUP); // Active LOW: pressed connects to GND, released pulled HIGH.
  stateMutex = xSemaphoreCreateMutex();
  telemetryWake = xSemaphoreCreateBinary();
  edgeQueue = xQueueCreate(1, sizeof(TickType_t));
  telemetryQueue = xQueueCreate(kQueueCapacity, sizeof(Telemetry));
  ackQueue = xQueueCreate(1, sizeof(int));
  mqttEvents = xEventGroupCreate();
  if (!stateMutex || !telemetryWake || !edgeQueue || !telemetryQueue || !ackQueue || !mqttEvents) abort();
  if (esp_reset_reason() == ESP_RST_DEEPSLEEP &&
      esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT0 &&
      checkpoint.magic == kRtcMagic && checkpoint.count <= kQueueCapacity) {
    sequence = checkpoint.seq;
    seat.sittingUs = checkpoint.sittingUs;
    for (uint32_t i = 0; i < checkpoint.count; ++i)
      if (xQueueSend(telemetryQueue, &checkpoint.packets[i], 0) != pdTRUE) abort();
    Serial.printf("ext0 wake: restored=%lu seq=%lu\n", static_cast<unsigned long>(checkpoint.count),
                  static_cast<unsigned long>(sequence));
  }
  checkpoint.magic = 0; // A fresh checkpoint is only valid after orderly sleep.
  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t) {
    if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.printf("[Node1 WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.println("[Node1 WiFi] Disconnected from AP!");
    }
  });
  attachInterruptArg(kSeatPin, onSeatEdge, nullptr, CHANGE);
  const TickType_t initial = xTaskGetTickCount();
  xQueueOverwrite(edgeQueue, &initial); // Debounce the boot/wake level too.
  // Sampling and networking outrank the slow timer; all tasks block when idle.
  if (xTaskCreate(vTaskSeatSample, "seat", 3072, nullptr, 2, nullptr) != pdPASS ||
      xTaskCreate(vTaskSessionTimer, "session", 3072, nullptr, 1, nullptr) != pdPASS ||
      xTaskCreate(vTaskTelemetrySend, "telemetry", 6144, nullptr, 2, nullptr) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
