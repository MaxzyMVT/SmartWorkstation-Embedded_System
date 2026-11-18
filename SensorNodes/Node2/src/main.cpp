#include <Arduino.h>
#include <WiFi.h>
#include <mqtt_client.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <cmath>
#include "TelemetryBuffer.h"
#include "node2_config.h"

namespace {
// Pin Configuration: Both pins are on ADC1 to allow uninterrupted analog reads during Wi-Fi operation
constexpr uint8_t kSoundPin = 35; // KY-037 Sound Sensor Analog OUT (ADC1 Channel 7)
constexpr uint8_t kLdrPin   = 34; // KY-018 LDR Photoresistor Signal (ADC1 Channel 6)

constexpr uint32_t kWindowUs = 50000; // 50ms sound sample window

struct Reading {
  float value;
  float raw;
  uint16_t dcSample;
  uint32_t sampledMs;
};

QueueHandle_t soundQueue;
QueueHandle_t ldrQueue;
QueueHandle_t ackQueue;
EventGroupHandle_t mqttEvents;
SemaphoreHandle_t adcMutex;

constexpr EventBits_t kConnected = BIT0;
constexpr char kTopic[] = "smart_workstation/node2";
TelemetryBuffer<120> backlog; // RAM store-and-forward buffer

void onMqttEvent(void *, esp_event_base_t, int32_t eventId, void *data) {
  auto *event = static_cast<esp_mqtt_event_t *>(data);
  if (eventId == MQTT_EVENT_CONNECTED) {
    Serial.println("[Node2 MQTT] Connected to broker.emqx.io!");
    xEventGroupSetBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_DISCONNECTED) {
    Serial.println("[Node2 MQTT] Disconnected from broker!");
    xEventGroupClearBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_ERROR) {
    Serial.println("[Node2 MQTT] Connection error encountered!");
  } else if (eventId == MQTT_EVENT_PUBLISHED) {
    xQueueOverwrite(ackQueue, &event->msg_id);
  }
}

// ----------------------------------------------------------------------------
// KY-037 Microphone Calibration
// ----------------------------------------------------------------------------
constexpr float kKy037NoiseFloor = 10.0f;
constexpr float kKy037BaseDb     = 35.0f;
constexpr float kKy037Scale      = 22.0f;

void vTaskSoundSample(void *) {
  analogReadResolution(12);
  analogSetPinAttenuation(kSoundPin, ADC_11db);
  float peakDb = 38.0f;
  float peakAmp = 0.0f;
  uint32_t peakHoldUntil = 0;
  uint32_t ampHoldUntil = 0;
  uint16_t lastSample = 0;

  for (;;) {
    uint16_t low = 4095, high = 0;
    if (xSemaphoreTake(adcMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
      const uint32_t start = micros();
      do {
        const uint16_t sample = analogRead(kSoundPin);
        lastSample = sample;
        if (sample < low) low = sample;
        if (sample > high) high = sample;
        delayMicroseconds(100);
      } while (static_cast<uint32_t>(micros() - start) < kWindowUs);
      xSemaphoreGive(adcMutex);
    }

    const float amplitude = high > low ? static_cast<float>(high - low) : 0.0f;

    float instDb = 35.0f;
    if (amplitude > 0.0f) {
      const float effectiveAmp = (amplitude > kKy037NoiseFloor) ? amplitude : kKy037NoiseFloor;
      instDb = kKy037BaseDb + kKy037Scale * log10f(effectiveAmp / kKy037NoiseFloor);
      if (instDb > 88.0f) instDb = 88.0f;
    }

    const uint32_t nowMs = millis();
    if (instDb >= peakDb) {
      peakDb = instDb;
      peakHoldUntil = nowMs + 1500;
    } else if (nowMs >= peakHoldUntil) {
      peakDb = (peakDb > 35.5f) ? (peakDb - 0.5f) : 35.0f;
    }

    if (amplitude >= peakAmp) {
      peakAmp = amplitude;
      ampHoldUntil = nowMs + 1500;
    } else if (nowMs >= ampHoldUntil) {
      peakAmp = (peakAmp > 1.0f) ? (peakAmp * 0.90f) : 0.0f;
    }

    Reading reading{peakDb, peakAmp, lastSample, millis()};
    xQueueOverwrite(soundQueue, &reading);
    vTaskDelay(pdMS_TO_TICKS(50)); // Yield ADC and CPU to other tasks between 50ms windows
  }
}

// ----------------------------------------------------------------------------
// KY-018 LDR Calibration (Continuous 12-bit Analog Lux Reading)
// ----------------------------------------------------------------------------
float adcToLux(uint16_t adc) {
  // Calibrated for KY-018 LDR on ESP32 ADC:
  // With a 10k resistor from GPIO 34 to GND (5k effective pull-down):
  // - Dark / Covered (adc <= 2200):   80 - 250 Lux (LOW_LIGHT < 300)
  // - Room light     (adc 2600-3600): 420 - 580 Lux (OPTIMAL_STUDY 350-700)
  // - Direct Glare   (adc >= 3900):   780 - 1500+ Lux (HARSH_GLARE > 750)
  float lux;
  if (adc <= 2200) {
    lux = 80.0f + (static_cast<float>(adc) / 2200.0f) * 170.0f; // 80 - 250 Lux
  } else if (adc < 3600) {
    const float t = static_cast<float>(adc - 2200) / 1400.0f;
    lux = 250.0f + t * 350.0f; // 250 - 600 Lux (Nominal ~480-520 at 3100)
  } else {
    const float t = (adc >= 4095) ? 1.0f : static_cast<float>(adc - 3600) / 495.0f;
    lux = 600.0f + t * 900.0f; // 600 - 1500 Lux (Flashlight/sunlight triggers > 750)
  }
  return roundf(lux * 10.0f) / 10.0f;
}

void vTaskLdrSample(void *) {
  analogReadResolution(12);
  analogSetPinAttenuation(kLdrPin, ADC_11db);

  constexpr int kFilterSize = 8;
  uint16_t buffer[kFilterSize] = {0};
  int idx = 0;
  bool filled = false;

  vTaskDelay(pdMS_TO_TICKS(200));
  TickType_t lastWake = xTaskGetTickCount();

  for (;;) {
    uint16_t raw = 0;
    if (xSemaphoreTake(adcMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
      raw = analogRead(kLdrPin);
      xSemaphoreGive(adcMutex);
    }
    buffer[idx] = raw;
    idx = (idx + 1) % kFilterSize;
    if (idx == 0) filled = true;

    const int count = filled ? kFilterSize : idx;
    uint32_t sum = 0;
    for (int i = 0; i < count; ++i) {
      sum += buffer[i];
    }
    const uint16_t avgAdc = sum / count;
    const float lux = adcToLux(avgAdc);

    Reading reading{lux, static_cast<float>(raw), avgAdc, millis()};
    xQueueOverwrite(ldrQueue, &reading);

    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(100)); // Sample at 10 Hz
  }
}

// ----------------------------------------------------------------------------
// MQTT Telemetry Dispatch Task (E1 Priority 2, E4 Reliable Comm)
// ----------------------------------------------------------------------------
void vTaskMqttDispatch(void *) {
  esp_mqtt_client_handle_t client = nullptr;
  const bool configured = NODE2_WIFI_SSID[0] && NODE2_MQTT_BROKER_HOST[0];
  if (configured) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(NODE2_WIFI_SSID, NODE2_WIFI_PASSWORD);

    const uint32_t wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 4000) {
      vTaskDelay(pdMS_TO_TICKS(100));
    }

    esp_mqtt_client_config_t config{};
    config.host = NODE2_MQTT_BROKER_HOST;
    config.port = NODE2_MQTT_BROKER_PORT;
    config.transport = MQTT_TRANSPORT_OVER_TCP;
    config.username = NODE2_MQTT_USERNAME[0] ? NODE2_MQTT_USERNAME : nullptr;
    config.password = NODE2_MQTT_PASSWORD[0] ? NODE2_MQTT_PASSWORD : nullptr;
    config.client_id = "smart-workstation-node2-desk";
    config.keepalive = 15;
    config.reconnect_timeout_ms = 3000;
    config.network_timeout_ms = 5000;
    config.message_retransmit_timeout = 5000;
    client = esp_mqtt_client_init(&config);
    if (!client) abort();
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(client, MQTT_EVENT_ANY, onMqttEvent, nullptr));
    ESP_ERROR_CHECK(esp_mqtt_client_start(client));
    Serial.println("[Node2 Net] WiFi & MQTT initialized");
  } else {
    Serial.println("Network disabled: configure Node2/.env and rebuild");
  }

  uint32_t seq = 0;
  uint32_t lastSample = millis(), lastWifiRetry = millis(), sentAt = 0;
  uint32_t lastEnqueueAttempt = millis() - 1000;
  int pendingId = -1;
  TickType_t lastWake = xTaskGetTickCount();

  for (;;) {
    const uint32_t now = millis();
    if (now - lastSample >= 1000) {
      lastSample += 1000;
      Reading sound{}, ldr{};
      const bool hasSound = (xQueuePeek(soundQueue, &sound, 0) == pdTRUE);
      const bool hasLdr   = (xQueuePeek(ldrQueue, &ldr, 0) == pdTRUE);

      char ldrStr[35];
      if (hasLdr && isfinite(ldr.value)) {
        snprintf(ldrStr, sizeof(ldrStr), "%.1f Lux (adc:%u)", ldr.value, ldr.dcSample);
      } else {
        snprintf(ldrStr, sizeof(ldrStr), "WAITING");
      }

      char soundStr[35];
      if (hasSound && isfinite(sound.value)) {
        snprintf(soundStr, sizeof(soundStr), "%.1f dB (p2p:%.0f)", sound.value, sound.raw);
      } else {
        snprintf(soundStr, sizeof(soundStr), "WAITING");
      }

      const bool wifiOk = (WiFi.status() == WL_CONNECTED);
      const bool mqttOk = (xEventGroupGetBits(mqttEvents) & kConnected) != 0;

      Serial.printf("[Node2 Monitor] Lux: %-22s | Sound: %-20s | WiFi: %s | MQTT: %s | Queue: %u\n",
                    ldrStr, soundStr,
                    wifiOk ? WiFi.localIP().toString().c_str() : "DISCONNECTED",
                    mqttOk ? "ONLINE" : "OFFLINE",
                    static_cast<unsigned>(backlog.size()));

      if (hasSound && hasLdr &&
          isfinite(ldr.value) && isfinite(sound.value) &&
          millis() - ldr.sampledMs <= 3000 && millis() - sound.sampledMs <= 1500) {
        const Telemetry packet{sound.value, ldr.value, ++seq};
        while (backlog.size() >= 3) {
          if (backlog.front()) backlog.acknowledge(backlog.front()->seq);
        }
        backlog.push(packet);
      }
    }

    if (configured && WiFi.status() != WL_CONNECTED && now - lastWifiRetry >= 10000) {
      lastWifiRetry = now;
      WiFi.reconnect();
    }

    int acknowledgedId;
    if (xQueueReceive(ackQueue, &acknowledgedId, 0) == pdTRUE) {
      pendingId = -1;
      if (backlog.front()) {
        const uint32_t acknowledgedSeq = backlog.front()->seq;
        backlog.acknowledge(acknowledgedSeq);
        Serial.printf("PUBACK seq=%lu queued=%u\n", static_cast<unsigned long>(acknowledgedSeq),
                      static_cast<unsigned>(backlog.size()));
      }
    }

    if (client && pendingId >= 0 && now - sentAt >= 4000) {
      pendingId = -1;
      if (backlog.front()) {
        backlog.acknowledge(backlog.front()->seq);
      }
    }

    if (client && pendingId < 0 && backlog.front() &&
        (xEventGroupGetBits(mqttEvents) & kConnected) &&
        now - lastEnqueueAttempt >= 100) {
      lastEnqueueAttempt = now;
      const auto &packet = *backlog.front();
      char payload[160];
      const int length = snprintf(payload, sizeof(payload),
          "{\"node_id\":\"node2\",\"lux\":%.1f,\"sound_db\":%.2f,\"seq\":%lu}",
          packet.lux, packet.soundDb, static_cast<unsigned long>(packet.seq));
      if (length > 0 && static_cast<size_t>(length) < sizeof(payload)) {
        pendingId = esp_mqtt_client_enqueue(client, kTopic, payload, length, 1, 0, true);
        if (pendingId >= 0) sentAt = now;
        else Serial.println("MQTT enqueue failed; retaining head for retry");
      }
    }
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(20));
  }
}
} // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n==========================================");
  Serial.println("  SmartWorkstation - Sensor Node 2 (Desk) ");
  Serial.println("  GPIO 34: KY-018 LDR Photoresistor (ADC1)");
  Serial.println("  GPIO 35: KY-037 Sound Sensor (ADC1)     ");
  Serial.println("==========================================");

  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t) {
    if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.printf("[Node2 WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.println("[Node2 WiFi] Disconnected from AP!");
    }
  });

  soundQueue = xQueueCreate(1, sizeof(Reading));
  ldrQueue   = xQueueCreate(1, sizeof(Reading));
  ackQueue   = xQueueCreate(1, sizeof(int));
  mqttEvents = xEventGroupCreate();
  adcMutex   = xSemaphoreCreateMutex();

  if (!soundQueue || !ldrQueue || !ackQueue || !mqttEvents || !adcMutex) abort();

  // E1 Multitasking: 3 distinct FreeRTOS tasks with reasoned priorities
  // Task 1: Sound sampling (Priority 3, core 1 - high frequency 50ms window)
  // Task 2: LDR lux sampling (Priority 1, core 0 - 100ms sample period)
  // Task 3: MQTT dispatch (Priority 2, core 0 - 1s telemetry publishing)
  if (xTaskCreatePinnedToCore(vTaskSoundSample, "sound", 4096, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskLdrSample,   "ldr",   4096, nullptr, 1, nullptr, 0) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskMqttDispatch,"mqtt",  6144, nullptr, 2, nullptr, 0) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
