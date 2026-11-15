#include "TelemetryBuffer.h"
#include <cassert>
#include <cstdio>

int main() {
  TelemetryBuffer<3> queue;
  assert(!queue.front());
  assert(!queue.acknowledge(1));
  for (uint32_t seq = 1; seq <= 3; ++seq)
    assert(queue.push({24.5f, 42.0f, seq}));
  // An outage fills the queue; overflow cannot evict the pending head.
  assert(!queue.push({30.0f, 80.0f, 4}));
  for (int retry = 0; retry < 1000; ++retry) {
    assert(queue.front()->seq == 1);
    assert(queue.front()->tempC == 24.5f);
    assert(!queue.acknowledge(99));
    assert(queue.size() == 3);
  }
  assert(queue.acknowledge(1));
  assert(!queue.acknowledge(1)); // Duplicate ACK must not discard the next sample.
  assert(queue.push({25.0f, 43.0f, 5})); // Ring wrap after overflow.
  assert(queue.acknowledge(2));
  assert(queue.acknowledge(3));
  assert(queue.front()->seq == 5);
  assert(queue.acknowledge(5));
  // Sequence rollover remains FIFO and equality-based, not ordered comparison.
  assert(queue.push({0, 0, UINT32_MAX}));
  assert(queue.push({0, 0, 0}));
  assert(queue.acknowledge(UINT32_MAX));
  assert(queue.acknowledge(0));
  assert(queue.size() == 0);
  puts("Telemetry buffer tests passed");
}
