#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <mqtt_client.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <cmath>
#include "TelemetryBuffer.h"
#include "node2_config.h"

namespace {
// ----------------------------------------------------------------------------
// Pin Configuration
// ----------------------------------------------------------------------------
// 1. KY-037 Sound Sensor: Analog OUT on ADC1 (works concurrently with Wi-Fi)
constexpr uint8_t kSoundPin = 35; // ADC1 Channel 7

// 2. BH1750 Ambient Light Sensor (I2C Digital Bus)
constexpr uint8_t kSdaPin = 21;   // I2C Data
constexpr uint8_t kSclPin = 22;   // I2C Clock
constexpr uint8_t kBh1750Addr = 0x23; // Default address (ADDR floating / GND)
constexpr uint8_t kBh1750AltAddr = 0x5C; // Alternate address if ADDR tied to 3.3V

constexpr uint32_t kWindowUs = 50000; // 50ms sound sample window

struct Reading {
  float value;
  float raw;
  uint16_t dcSample;
  uint32_t sampledMs;
};

QueueHandle_t soundQueue;
QueueHandle_t lightQueue;
QueueHandle_t ackQueue;
EventGroupHandle_t mqttEvents;

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
// Baseline room noise floor: ~65-75 p2p raw ADC units
// Quiet study baseline: 36.0 dB; speaking/typing: 50-65 dB; clap/loud noise: 75-85 dB
// ----------------------------------------------------------------------------
constexpr float kKy037NoiseFloor = 70.0f;
constexpr float kKy037BaseDb     = 36.0f;
constexpr float kKy037Scale      = 32.0f;

void vTaskSoundSample(void *) {
  analogReadResolution(12);
  analogSetPinAttenuation(kSoundPin, ADC_11db);
  float peakDb = 36.0f;
  float peakAmp = 0.0f;
  uint32_t peakHoldUntil = 0;
  uint32_t ampHoldUntil = 0;
  uint16_t lastSample = 0;

  for (;;) {
    uint16_t low = 4095, high = 0;
    const uint32_t start = micros();
    do {
      const uint16_t sample = analogRead(kSoundPin);
      lastSample = sample;
      if (sample < low) low = sample;
      if (sample > high) high = sample;
      delayMicroseconds(100);
    } while (static_cast<uint32_t>(micros() - start) < kWindowUs);

    const float amplitude = high > low ? static_cast<float>(high - low) : 0.0f;

    float instDb = 36.0f;
    if (amplitude > kKy037NoiseFloor) {
      instDb = kKy037BaseDb + kKy037Scale * log10f(amplitude / kKy037NoiseFloor);
      if (instDb > 88.0f) instDb = 88.0f;
    } else {
      // Sub-threshold electrical jitter maps linearly between 34.0 and 36.5 dB
      instDb = 34.0f + 2.5f * (amplitude / kKy037NoiseFloor);
    }

    const uint32_t nowMs = millis();
    if (instDb >= peakDb) {
      peakDb = instDb;
      peakHoldUntil = nowMs + 600; // Hold peak for 600ms (fast, responsive)
    } else if (nowMs >= peakHoldUntil) {
      // Quick decay back down to ambient quiet room level
      peakDb = (peakDb > 37.0f) ? (peakDb - 2.0f) : 36.0f;
    }

    if (amplitude >= peakAmp) {
      peakAmp = amplitude;
      ampHoldUntil = nowMs + 600;
    } else if (nowMs >= ampHoldUntil) {
      peakAmp = (peakAmp > 2.0f) ? (peakAmp * 0.70f) : 0.0f;
    }

    Reading reading{peakDb, peakAmp, lastSample, millis()};
    xQueueOverwrite(soundQueue, &reading);
    vTaskDelay(pdMS_TO_TICKS(50)); // Yield between 50ms windows
  }
}

// ----------------------------------------------------------------------------
// BH1750 Digital Ambient Light Sensor (I2C Bus: SDA=GPIO21, SCL=GPIO22)
// ----------------------------------------------------------------------------
uint8_t activeBh1750Addr = kBh1750Addr;
bool bh1750Ready = false;

bool initBh1750() {
  // Test default address 0x23, then fallback to 0x5C
  for (uint8_t addr : {kBh1750Addr, kBh1750AltAddr}) {
    Wire.beginTransmission(addr);
    Wire.write(0x10); // Continuously H-Resolution Mode (1 Lux resolution, 120ms typ)
    if (Wire.endTransmission() == 0) {
      activeBh1750Addr = addr;
      bh1750Ready = true;
      Serial.printf("[Node2 I2C] BH1750 initialized successfully at 0x%02X!\n", addr);
      return true;
    }
  }
  bh1750Ready = false;
  return false;
}

float readBh1750Lux(uint16_t &rawOut) {
  if (!bh1750Ready) {
    if (!initBh1750()) return -1.0f;
  }

  Wire.requestFrom(activeBh1750Addr, (uint8_t)2);
  if (Wire.available() >= 2) {
    uint16_t raw = Wire.read();
    raw <<= 8;
    raw |= Wire.read();
    rawOut = raw;
    // Standard BH1750 optical transfer formula: Lux = raw / 1.2
    float lux = static_cast<float>(raw) / 1.2f;
    return roundf(lux * 10.0f) / 10.0f;
  }
  bh1750Ready = false; // Trigger re-init on next cycle if bus stalled
  return -1.0f;
}

void vTaskLightSample(void *) {
  Wire.begin(kSdaPin, kSclPin, 100000); // 100 kHz standard I2C
  vTaskDelay(pdMS_TO_TICKS(200));       // Power-on settling
  initBh1750();

  TickType_t lastWake = xTaskGetTickCount();

  for (;;) {
    uint16_t raw = 0;
    float lux = readBh1750Lux(raw);
    const bool valid = isfinite(lux) && lux >= 0.0f;

    Reading reading{valid ? lux : NAN, static_cast<float>(raw), raw, millis()};
    xQueueOverwrite(lightQueue, &reading);

    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(150)); // Sample every 150ms
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
      Reading sound{}, light{};
      const bool hasSound = (xQueuePeek(soundQueue, &sound, 0) == pdTRUE);
      const bool hasLight = (xQueuePeek(lightQueue, &light, 0) == pdTRUE);

      char lightStr[35];
      if (hasLight && isfinite(light.value)) {
        snprintf(lightStr, sizeof(lightStr), "%.1f Lux (raw:%u)", light.value, light.dcSample);
      } else {
        snprintf(lightStr, sizeof(lightStr), "WAITING_I2C");
      }

      char soundStr[35];
      if (hasSound && isfinite(sound.value)) {
        snprintf(soundStr, sizeof(soundStr), "%.1f dB (p2p:%.0f)", sound.value, sound.raw);
      } else {
        snprintf(soundStr, sizeof(soundStr), "WAITING");
      }

      const bool wifiOk = (WiFi.status() == WL_CONNECTED);
      const bool mqttOk = (xEventGroupGetBits(mqttEvents) & kConnected) != 0;

      Serial.printf("[Node2 Monitor] Light: %-22s | Sound: %-20s | WiFi: %s | MQTT: %s | Queue: %u\n",
                    lightStr, soundStr,
                    wifiOk ? WiFi.localIP().toString().c_str() : "DISCONNECTED",
                    mqttOk ? "ONLINE" : "OFFLINE",
                    static_cast<unsigned>(backlog.size()));

      if (hasSound && hasLight &&
          isfinite(light.value) && isfinite(sound.value) &&
          millis() - light.sampledMs <= 3000 && millis() - sound.sampledMs <= 1500) {
        const Telemetry packet{sound.value, light.value, ++seq};
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
  Serial.println("  I2C SDA: GPIO 21 (BH1750 Ambient Light) ");
  Serial.println("  I2C SCL: GPIO 22 (BH1750 Ambient Light) ");
  Serial.println("  ADC1 35: GPIO 35 (KY-037 Sound Sensor)  ");
  Serial.println("==========================================");

  WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t) {
    if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
      Serial.printf("[Node2 WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
      Serial.println("[Node2 WiFi] Disconnected from AP!");
    }
  });

  soundQueue = xQueueCreate(1, sizeof(Reading));
  lightQueue = xQueueCreate(1, sizeof(Reading));
  ackQueue   = xQueueCreate(1, sizeof(int));
  mqttEvents = xEventGroupCreate();

  if (!soundQueue || !lightQueue || !ackQueue || !mqttEvents) abort();

  // E1 Multitasking: 3 distinct FreeRTOS tasks with reasoned priorities
  // Task 1: Sound sampling (Priority 3, core 1 - high frequency 50ms window)
  // Task 2: Light sampling (Priority 1, core 0 - 150ms BH1750 I2C sampling)
  // Task 3: MQTT dispatch (Priority 2, core 0 - 1s telemetry publishing)
  if (xTaskCreatePinnedToCore(vTaskSoundSample, "sound", 4096, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskLightSample, "light", 4096, nullptr, 1, nullptr, 0) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskMqttDispatch,"mqtt",  6144, nullptr, 2, nullptr, 0) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
