#include <Arduino.h>
#include <WiFi.h>
#include <mqtt_client.h>
#include <freertos/event_groups.h>
#include "TelemetryBuffer.h"
#include "node2_config.h"
#include <cmath>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#ifdef NODE2_USE_DS18B20
#include <DallasTemperature.h>
#include <OneWire.h>
#else
#include <DHT.h>
#endif

namespace {
constexpr uint8_t kSoundPin = 35; // ADC1 works while Wi-Fi runs.
constexpr uint8_t kTempPin = 4;
constexpr uint32_t kWindowUs = 50000;
constexpr uint32_t kTempPeriodMs = 2500;
struct Reading { float value; float raw; uint32_t sampledMs; };
QueueHandle_t soundQueue;
QueueHandle_t tempQueue;
QueueHandle_t ackQueue;
EventGroupHandle_t mqttEvents;
constexpr EventBits_t kConnected = BIT0;
constexpr char kTopic[] = "smart_workstation/node2";
TelemetryBuffer<120> backlog; // Two minutes at 1 Hz; RAM only.

// MQTT callbacks run in the library task. Only pass state through RTOS objects.
void onMqttEvent(void *, esp_event_base_t, int32_t eventId, void *data) {
  auto *event = static_cast<esp_mqtt_event_t *>(data);
  if (eventId == MQTT_EVENT_CONNECTED) {
    xEventGroupSetBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_DISCONNECTED) {
    xEventGroupClearBits(mqttEvents, kConnected);
  } else if (eventId == MQTT_EVENT_PUBLISHED) {
    // One outstanding message: this mailbox cannot lose a different valid ACK.
    xQueueOverwrite(ackQueue, &event->msg_id);
  }
}

// KY-037 Microphone Acoustic Calibration:
// Electret capsule with low-gain pre-amp:
// - Quiet room noise floor:  ~20 - 35 ADC counts  -> ~42 - 45 dB
// - Normal speaking:          ~70 - 130 ADC counts -> ~54 - 60 dB
// - Shouting / Loud talk:     ~160 - 300 ADC counts-> ~63 - 70 dB
// - Clapping near microphone: ~350 - 800+ ADC counts-> ~72 - 82 dB
constexpr float kKy037NoiseFloor = 25.0f;
constexpr float kKy037BaseDb     = 42.0f;
constexpr float kKy037Scale      = 25.0f;

void vTaskSoundSample(void *) {
  analogReadResolution(12);
  analogSetPinAttenuation(kSoundPin, ADC_11db);
  float peakDb = 42.0f;  // Peak decibel hold with smooth decay
  float peakAmp = 25.0f; // Peak amplitude hold with smooth decay

  for (;;) {
    uint16_t low = 4095, high = 0;
    const uint32_t start = micros();
    do {
      const uint16_t sample = analogRead(kSoundPin);
      if (sample < low) low = sample;
      if (sample > high) high = sample;
      delayMicroseconds(100);
    } while (static_cast<uint32_t>(micros() - start) < kWindowUs);

    const float amplitude = high > low ? static_cast<float>(high - low) : 1.0f;

    // Logarithmic decibel estimation tuned for KY-037 dynamic range
    const float effectiveAmp = (amplitude > kKy037NoiseFloor) ? amplitude : kKy037NoiseFloor;
    float instDb = kKy037BaseDb + kKy037Scale * log10f(effectiveAmp / kKy037NoiseFloor);
    if (instDb > 88.0f) instDb = 88.0f;

    // Peak Program Meter (PPM) ballistic response:
    // 1. Instant attack: instantaneous sound spikes (claps/shouts) are NEVER dampened.
    // 2. Controlled release: decay of 0.6 dB per 50 ms (~12 dB/s) so peaks stay visible.
    if (instDb >= peakDb) {
      peakDb = instDb;
    } else {
      peakDb = (peakDb > 42.6f) ? (peakDb - 0.6f) : 42.0f;
    }

    if (amplitude >= peakAmp) {
      peakAmp = amplitude;
    } else {
      peakAmp = (peakAmp > 26.0f) ? (peakAmp * 0.92f) : 25.0f;
    }

    Reading reading{peakDb, peakAmp, millis()};
    xQueueOverwrite(soundQueue, &reading);
    vTaskDelay(1); // Let idle/watchdog run after every bounded window.
  }
}

void vTaskTempSample(void *) {
#ifdef NODE2_USE_DS18B20
  OneWire wire(kTempPin);
  DallasTemperature sensor(&wire);
  sensor.begin();
  sensor.setResolution(12);
  sensor.setWaitForConversion(false);
#else
  DHT sensor(kTempPin, DHT22);
  sensor.begin();
#endif
  vTaskDelay(pdMS_TO_TICKS(2000)); // Power-on settling.
  TickType_t lastWake = xTaskGetTickCount();
  for (;;) {
#ifdef NODE2_USE_DS18B20
    sensor.requestTemperatures();
    vTaskDelay(pdMS_TO_TICKS(750));
    const float value = sensor.getTempCByIndex(0);
    const bool valid = isfinite(value) && value >= -55 && value <= 125;
#else
    const float value = sensor.readTemperature();
    const bool valid = isfinite(value) && value >= -40 && value <= 80;
#endif
    Reading reading{valid ? value : NAN, value, millis()};
    xQueueOverwrite(tempQueue, &reading);
    if (!valid) Serial.printf("[Temp Sensor] Read failed on GPIO 4 (got: %.2f) - check pin wiring or pull-up\n", value);
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(kTempPeriodMs));
  }
}

void vTaskMqttDispatch(void *) {
  esp_mqtt_client_handle_t client = nullptr;
  const bool configured = NODE2_WIFI_SSID[0] && NODE2_MQTT_BROKER_HOST[0];
  if (configured) {
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(NODE2_WIFI_SSID, NODE2_WIFI_PASSWORD);
    esp_mqtt_client_config_t config{};
    config.host = NODE2_MQTT_BROKER_HOST;
    config.port = NODE2_MQTT_BROKER_PORT;
    config.transport = MQTT_TRANSPORT_OVER_TCP; // Local gateway; cloud TLS is gateway-owned.
    config.username = NODE2_MQTT_USERNAME[0] ? NODE2_MQTT_USERNAME : nullptr;
    config.password = NODE2_MQTT_PASSWORD[0] ? NODE2_MQTT_PASSWORD : nullptr;
    config.keepalive = 15;
    config.reconnect_timeout_ms = 5000;
    config.network_timeout_ms = 2000;
    config.message_retransmit_timeout = 5000;
    client = esp_mqtt_client_init(&config);
    if (!client) abort();
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(client, MQTT_EVENT_ANY, onMqttEvent, nullptr));
    ESP_ERROR_CHECK(esp_mqtt_client_start(client));
  } else {
    Serial.println("Network disabled: configure Node2/.env and rebuild");
  }
  uint32_t seq = 0, dropped = 0;
  uint32_t lastSample = millis(), lastWifiRetry = millis(), sentAt = 0;
  uint32_t lastEnqueueAttempt = millis() - 1000;
  int pendingId = -1;
  TickType_t lastWake = xTaskGetTickCount();
  for (;;) {
    const uint32_t now = millis();
    if (now - lastSample >= 1000) {
      lastSample += 1000;
      Reading sound{}, temp{};
      const bool hasSound = (xQueuePeek(soundQueue, &sound, 0) == pdTRUE);
      const bool hasTemp = (xQueuePeek(tempQueue, &temp, 0) == pdTRUE);

      char tempStr[20];
      if (hasTemp && isfinite(temp.value)) {
        snprintf(tempStr, sizeof(tempStr), "%.1f C", temp.value);
      } else {
        snprintf(tempStr, sizeof(tempStr), "FAIL/NAN");
      }

      char soundStr[32];
      if (hasSound && isfinite(sound.value)) {
        snprintf(soundStr, sizeof(soundStr), "%.1f dB (p2p:%.0f)", sound.value, sound.raw);
      } else {
        snprintf(soundStr, sizeof(soundStr), "FAIL/NAN");
      }

      Serial.printf("[Node2 Monitor] Temp: %-10s | Sound: %-20s | Queue: %u\n",
                    tempStr, soundStr, static_cast<unsigned>(backlog.size()));

      if (hasSound && hasTemp &&
          isfinite(temp.value) && isfinite(sound.value) &&
          millis() - temp.sampledMs <= 6000 && millis() - sound.sampledMs <= 1000) {
        const Telemetry packet{temp.value, sound.value, ++seq};
        if (!backlog.push(packet)) {
          Serial.printf("Buffer full: dropped newest seq=%lu total=%lu\n",
                        static_cast<unsigned long>(seq), static_cast<unsigned long>(++dropped));
        }
      }
    }
    if (configured && WiFi.status() != WL_CONNECTED && now - lastWifiRetry >= 10000) {
      lastWifiRetry = now;
      WiFi.reconnect(); // Bounded attempts; no loop waiting for connectivity.
    }
    int acknowledgedId;
    if (xQueueReceive(ackQueue, &acknowledgedId, 0) == pdTRUE &&
        pendingId >= 0 && acknowledgedId == pendingId && backlog.front()) {
      const uint32_t acknowledgedSeq = backlog.front()->seq;
      backlog.acknowledge(acknowledgedSeq);
      pendingId = -1;
      Serial.printf("PUBACK seq=%lu queued=%u\n", static_cast<unsigned long>(acknowledgedSeq),
                    static_cast<unsigned>(backlog.size()));
    }
    // ESP-MQTT resends QoS 1 packets after 5 s and on reconnect. If its outbox
    // expires a packet (or an ACK event is lost), re-enqueue the SAME seq.
    // Never grow the library outbox with repeated copies during an outage.
    if (client && pendingId >= 0 && now - sentAt >= 30000 &&
        esp_mqtt_client_get_outbox_size(client) == 0) {
      pendingId = -1;
      Serial.println("Retrying unacknowledged head after outbox expiry");
    }
    if (client && pendingId < 0 && backlog.front() &&
        (xEventGroupGetBits(mqttEvents) & kConnected) &&
        now - lastEnqueueAttempt >= 100) {
      lastEnqueueAttempt = now;
      const auto &packet = *backlog.front();
      char payload[160];
      const int length = snprintf(payload, sizeof(payload),
          "{\"node_id\":\"node2\",\"temp_c\":%.2f,\"sound_db\":%.2f,\"seq\":%lu}",
          packet.tempC, packet.soundDb, static_cast<unsigned long>(packet.seq));
      if (length > 0 && static_cast<size_t>(length) < sizeof(payload)) {
        // Queue network work in the library task so sampling continues offline.
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
  Serial.println("  GPIO 4 : DHT22 / DS18B20 Temp Sensor    ");
  Serial.println("  GPIO 35: MAX9814 Acoustic Sound Sensor  ");
  Serial.println("==========================================");
  soundQueue = xQueueCreate(1, sizeof(Reading));
  tempQueue = xQueueCreate(1, sizeof(Reading));
  ackQueue = xQueueCreate(1, sizeof(int));
  mqttEvents = xEventGroupCreate();
  if (!soundQueue || !tempQueue || !ackQueue || !mqttEvents) abort();
  // Highest application priority for sound, lowest for slow temperature reads.
  // Sound runs away from networking and temperature to reduce sampling jitter.
  if (xTaskCreatePinnedToCore(vTaskSoundSample, "sound", 4096, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskTempSample, "temperature", 4096, nullptr, 1, nullptr, 0) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskMqttDispatch, "mqtt", 6144, nullptr, 2, nullptr, 0) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
