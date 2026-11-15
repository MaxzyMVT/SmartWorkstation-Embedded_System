#include <Arduino.h>
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
struct Reading { float value; uint32_t sampledMs; };
QueueHandle_t soundQueue;
QueueHandle_t tempQueue;

// Relative peak-to-peak estimate, NOT calibrated SPL or A-weighted dBA.
#ifndef NODE2_SOUND_REFERENCE_COUNTS
#define NODE2_SOUND_REFERENCE_COUNTS 100.0f
#endif
#ifndef NODE2_SOUND_REFERENCE_DB
#define NODE2_SOUND_REFERENCE_DB 60.0f
#endif
static_assert(NODE2_SOUND_REFERENCE_COUNTS > 0, "Reference must be positive");

void vTaskSoundSample(void *) {
  analogReadResolution(12);
  analogSetPinAttenuation(kSoundPin, ADC_11db);
  for (;;) {
    uint16_t low = 4095, high = 0;
    const uint32_t start = micros();
    do {
      const uint16_t sample = analogRead(kSoundPin);
      if (sample < low) low = sample;
      if (sample > high) high = sample;
      delayMicroseconds(100);
    } while (static_cast<uint32_t>(micros() - start) < kWindowUs);
    const float amplitude = high > low ? high - low : 1;
    Reading reading{NODE2_SOUND_REFERENCE_DB +
        20.0f * log10f(amplitude / NODE2_SOUND_REFERENCE_COUNTS), millis()};
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
    Reading reading{valid ? value : NAN, millis()};
    xQueueOverwrite(tempQueue, &reading);
    if (!valid) Serial.println("Temperature read failed");
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(kTempPeriodMs));
  }
}

void vTaskMqttDispatch(void *) {
  for (;;) {
    Reading sound{}, temp{};
    if (xQueuePeek(soundQueue, &sound, 0) == pdTRUE &&
        xQueuePeek(tempQueue, &temp, 0) == pdTRUE && isfinite(temp.value)) {
      Serial.printf("temp_c=%.2f sound_db_estimate=%.2f\n", temp.value, sound.value);
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}
} // namespace

void setup() {
  Serial.begin(115200);
  soundQueue = xQueueCreate(1, sizeof(Reading));
  tempQueue = xQueueCreate(1, sizeof(Reading));
  if (!soundQueue || !tempQueue) abort();
  // Highest application priority for sound, lowest for slow temperature reads.
  // Sound runs away from networking and temperature to reduce sampling jitter.
  if (xTaskCreatePinnedToCore(vTaskSoundSample, "sound", 4096, nullptr, 3, nullptr, 1) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskTempSample, "temperature", 4096, nullptr, 1, nullptr, 0) != pdPASS ||
      xTaskCreatePinnedToCore(vTaskMqttDispatch, "mqtt", 6144, nullptr, 2, nullptr, 0) != pdPASS) abort();
}

void loop() { vTaskDelete(nullptr); }
