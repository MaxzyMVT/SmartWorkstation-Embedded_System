#pragma once
#include <stddef.h>
#include <stdint.h>

struct Telemetry {
  float tempC;
  float soundDb;
  uint32_t seq;
};

// Single owner: the MQTT dispatch task. The head remains immutable until ACK.
template <size_t Capacity>
class TelemetryBuffer {
  static_assert(Capacity > 0, "Buffer must have capacity");
 public:
  bool push(const Telemetry &packet) {
    if (count_ == Capacity) return false; // Drop newest, preserve unacknowledged data.
    packets_[(head_ + count_) % Capacity] = packet;
    ++count_;
    return true;
  }
  const Telemetry *front() const { return count_ ? &packets_[head_] : nullptr; }
  bool acknowledge(uint32_t seq) {
    if (!count_ || packets_[head_].seq != seq) return false;
    head_ = (head_ + 1) % Capacity;
    --count_;
    return true;
  }
  size_t size() const { return count_; }
 private:
  Telemetry packets_[Capacity]{};
  size_t head_ = 0;
  size_t count_ = 0;
};
