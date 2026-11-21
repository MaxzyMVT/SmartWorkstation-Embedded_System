# AGENTS.md — Multi-Agent Engineering Guide: Smart Workstation

> **Notice for AI Agents (Antigravity, Cursor, Claude Code, Copilot, Codex)**:  
> This project is for **2110356 Embedded System (CEDT), Chulalongkorn University**.  
> The team targets a **Full Score (70/70)**. All code and firmware developed by any agent must strictly follow the architectural boundaries, pin assignments, MQTT schemas, and Embedded Engineering Depth items (E1–E5) outlined below.

---

## 1. System Topology & Responsibilities

```
[ Sensor Node 1 (Chair) ]  --\
  ESP32 + FSR Seat Mat       \ (MQTT / Wi-Fi)
                              +---> [ Gateway (Raspberry Pi 4) ] ---> [ Cloud / Firebase ]
[ Sensor Node 2 (Desk) ]   --/       - Direct Wired Sensors            - React Web Dashboard
  ESP32 + Sound & BH1750 Light       - On-Device Scikit-Learn AI       - Discord & LINE Alerts
                                     - Stepper Curtain & LED PWM
```

| Subsystem | Folder | Target Hardware | Primary Agent Responsibilities |
| :--- | :--- | :--- | :--- |
| **Node 1** | `SensorNodes/Node1/` | ESP32 DevKit V1 | Chair seat detection via FSR402 Force Sensing Resistor, FreeRTOS tasks (E1), GPIO ISR (E2), deep sleep & power budget (E3), packet retries (E4). |
| **Node 2** | `SensorNodes/Node2/` | ESP32 DevKit V1 | Ambient acoustic noise sampling via KY-037, ambient lux via BH1750 I2C (GPIO 21/22), FreeRTOS scheduling (E1), packet retries (E4). |
| **Gateway** | `Gateway/` | Raspberry Pi 4 (or PC Mock) | Ingest node MQTT telemetry, sample local sensors (HC-SR04 ultrasonic, KY-001 DS18B20 temp), run AI classification (M4), drive actuators (28BYJ-48 stepper curtain, dynamic LED PWM), forward cloud telemetry (M5), dispatch alerts (M6). |
| **Interface**| `Interface/` | React 18 + Vite | Display real-time telemetry, session timers, and closed-loop actuator feedback. |

---

## 2. Sensor Node 1 Setup (`SensorNodes/Node1/`)

### 2.1 Hardware Connections
- **Board**: ESP32 DOIT DevKit V1 (`board = esp32doit-devkit-v1` in `platformio.ini`)
- **FSR Pressure Plate / Seat Switch**: Connected to **GPIO 33** (or analog ADC1 channel **GPIO 34**) with a 10kΩ pull-down resistor.
- **Power**: 3.7V LiPo battery or 5V USB (for testing).

### 2.2 Mandatory Embedded Engineering Items (Rubric Depth)
- **E1 (RTOS Multitasking)**: Implement at least 3 FreeRTOS tasks:
  1. `vTaskSeatSample` (Priority 2): Reads pressure pad, debounces contact.
  2. `vTaskSessionTimer` (Priority 1): Tracks cumulative sitting time without drift.
  3. `vTaskTelemetrySend` (Priority 2): Serializes JSON and transmits via MQTT/Wi-Fi using FreeRTOS Queue.
- **E2 (Interrupt-Driven I/O)**: Configure seat switch pin with `attachInterruptArg()` or `gpio_isr_handler_add()` on `RISING`/`FALLING` edges. Wake telemetry task via FreeRTOS `xSemaphoreGiveFromISR()`. **No busy-polling in `loop()`**.
- **E3 (Low Power Sleep)**: When chair is unoccupied for $>60\text{ s}$, configure `esp_sleep_enable_ext0_wakeup(GPIO_NUM_33, 1)` and enter `esp_deep_sleep_start()`. Wakes instantly upon sitting down.
- **E4 (Reliable Comm)**: Include auto-incrementing `seq` number; retry publish if Wi-Fi/MQTT ACK drops.

### 2.3 MQTT Telemetry Output
- **Topic**: `smart_workstation/node1`
- **JSON Schema**:
  ```json
  {
    "node_id": "node1",
    "presence": 1,
    "sitting_duration_min": 14.5,
    "battery_mv": 3950,
    "seq": 102
  }
  ```
  *(Note: `presence` must be `1` when seated, `0` when empty. Key alias `"seat"` is accepted by Gateway).*

### 2.4 Agent Build & Test Commands
```bash
cd SensorNodes/Node1
pio run                  # Compile firmware
pio run -t upload        # Flash to ESP32
pio device monitor -b 115200  # Monitor serial logs
```

---

## 3. Sensor Node 2 Setup (`SensorNodes/Node2/`)

### 3.1 Hardware Connections
- **Board**: ESP32 DOIT DevKit V1
- **Sound Sensor**: KY-037 Sound Sensor Analog OUT connected to ADC1 pin **GPIO 35** (50ms Peak-to-Peak sampling window).
- **Light Sensor**: BH1750 Digital Ambient Light Sensor connected via I2C (SDA **GPIO 21**, SCL **GPIO 22**, 3.3V, 16-bit digital Lux 1–65535).
- **Power**: Micro-USB 5V power from workstation rail.

### 3.2 Mandatory Embedded Engineering Items (Rubric Depth)
- **E1 (RTOS Multitasking)**:
  1. `vTaskSoundSample` (Priority 3, core 1): Continuous 50ms windowed ADC sampling for acoustic amplitude / dBA estimation.
  2. `vTaskLightSample` (Priority 1, core 0): Continuous BH1750 digital I2C Lux sampling (~6.6 Hz).
  3. `vTaskMqttDispatch` (Priority 2, core 0): Enqueues telemetry and publishes to Gateway over MQTT.
- **E4 (Reliable Comm)**: Sequence counter + local buffer queue in RAM if MQTT broker disconnects.

### 3.3 MQTT Telemetry Output
- **Topic**: `smart_workstation/node2`
- **JSON Schema**:
  ```json
  {
    "node_id": "node2",
    "lux": 485.0,
    "sound_db": 42.5,
    "seq": 204
  }
  ```

---

## 4. Gateway Controller Setup (`Gateway/`)

The Gateway is the central brain running on a **Raspberry Pi 4 Model B** (or any machine in `--mock` mode).

### 4.1 Raspberry Pi 4 GPIO Pinout (BCM Numbering)
| Peripheral | Pin (BCM) | Physical Header Pin | Electrical Requirements |
| :--- | :--- | :--- | :--- |
| **KY-001 DS18B20 Temp Sensor** | **GPIO 4** | Pin 7 | 3.3V power, 1-Wire digital bus (KY-001 has onboard pull-up) |
| **HC-SR04 TRIG** | **GPIO 23** | Pin 16 | 3.3V Output pulse (10µs) |
| **HC-SR04 ECHO** | **GPIO 24** | Pin 18 | **5V Input $\rightarrow$ 10kΩ / 20kΩ (3 × 10kΩ) divider to 3.3V** |
| **28BYJ-48 Stepper IN1–IN4** | **GPIO 5, 6, 13, 19** | Pins 29, 31, 33, 35 | ULN2003 driver powered by 5V external rail |
| **LED Lamp PWM (ULN2003 IN5/OUT5)** | **GPIO 18** | Pin 12 | Hardware PWM0 (1000 Hz) -> ULN2003 IN5; OUT5 drives COB LED [-] |
| *(Optional Limit Switch)* | *(GPIO 17)* | *(Pin 11)* | *Optional; M7 safety stop satisfied via software bounds* |

### 4.2 Software Stack & Architecture
- **Language**: Python 3.9+
- **Key Modules**:
  - `gateway_main.py`: Central daemon. Ingests MQTT node packets, reads local hardware, executes AI loop, drives actuators, dispatches alerts.
  - `hardware_interface.py`: Hardware Abstraction Layer (HAL). Automatically activates **Mock Hardware Mode** on Windows/macOS/Linux when RPi GPIO is unavailable.
  - `train_ai_model.py`: Trains and serializes `model.joblib` (`RandomForestClassifier`, 6 inputs, 9 output states including acoustic and thermal comfort).

### 4.3 Environment Configuration (`Gateway/.env`)
Copy `Gateway/.env.example` to `Gateway/.env`:
```ini
MQTT_BROKER_HOST=broker.emqx.io
MQTT_BROKER_PORT=1883
MOCK_HARDWARE=false
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
LINE_NOTIFY_TOKEN=...
```

### 4.4 Agent Commands for Gateway
```bash
cd Gateway
python -m venv .venv
# On Windows: .venv\Scripts\activate | On Linux/Pi: source .venv/bin/activate
pip install -r requirements.txt

# Run in Mock Simulation Mode (laptop development):
python gateway_main.py --mock --broker broker.emqx.io

# Run on actual Raspberry Pi hardware:
python gateway_main.py --broker broker.emqx.io
# Or run with local offline Mosquitto broker:
# python gateway_main.py --broker localhost

# Train / retrain AI model if features or weights change:
python train_ai_model.py

# Systemd background service management (auto-start daemon):
sudo systemctl status smartworkstation          # View daemon status & uptime
sudo journalctl -u smartworkstation -f          # Stream live daemon logs
sudo journalctl -u smartworkstation -n 100 --no-pager  # View last 100 lines
sudo systemctl restart smartworkstation         # Restart service after updates
```

### 4.5 Unified Telemetry Output for Web Dashboard
The Gateway publishes consolidated JSON every 1 second to:
- **Topic**: `smart_workstation/telemetry`
- **Payload Format**:
  ```json
  {
    "seat": true,
    "distance": 38.5,
    "light": 450.2,
    "temp": 24.8,
    "sound": 36.1,
    "sitting_duration_min": 14.5,
    "curtain_state": "OPEN",
    "led_duty_cycle": 65.0,
    "servo_angle": 85.0,
    "ai_state": {
      "class_id": 0,
      "class_name": "OPTIMAL_STUDY",
      "confidence": 0.96,
      "action": "Maintain optimal study ergonomics",
      "advice": "All metrics in healthy range.",
      "latency_ms": 0.45
    },
    "timestamp": 1731612000.12
  }
  ```

---

## 5. Web Interface Setup (`Interface/`)

- **Tech Stack**: React 18, Vite, TailwindCSS, Lucide Icons.
- **Run dev server**:
  ```bash
  cd Interface
  npm install
  npm run dev
  ```
- Receives telemetry directly from `smart_workstation/telemetry` or via Firebase SDK.

---

## 6. Critical Course Rubric Rules for All Agents

1. **Buttons & switches are NOT sensors (M1)**: Do not count the limit switch or seat switch as an environment sensor. Our 5 true sensors are: Ultrasonic (HC-SR04), Light (BH1750 Digital Lux), Pressure (FSR402 Force Sensing Resistor), Temperature (KY-001 DS18B20), Sound (KY-037 Mic).
2. **LEDs & buzzers are NOT actuators (M2)**: The mechanical actuator is the 28BYJ-48 stepper motor (smart curtain). Supplementary dynamic task lighting is controlled via MOSFET PWM.
3. **Safety Stop for Actuators (M7)**: Moving parts must have a way to stop safely. Satisfied by software travel limits (0 to 4096 steps = 0% to 100%), stepper watchdog timeout, ULN2003 coil de-energizing on idle, and emergency stop command.
4. **If-Else rules are NOT AI (M4)**: Actuator triggering must be mediated by the trained 9-class model in `model.joblib`.
5. **E7 vs Raspberry Pi**: FAQ #4 clarifies that E7 (On-Device AI) only earns rubric points on an MCU (ESP32-S3). Because Gateway is a Raspberry Pi, we earn our 10 depth points via **E1 + E2 + E3 + E4 + E5**.
6. **No committed credentials (E5 / M8)**: Never commit real Wi-Fi passwords, private keys, or webhook URLs. Keep `.env` git-ignored.
7. **Git Conventional Commits**: Always format commits as `type(scope): message` (e.g., `feat(node1): implement FreeRTOS seat task with deep sleep`).

---

## 7. Immediate Next Tasks for Subsystem Agents

- **Node 1 Agent**: FreeRTOS tasks (E1) for FSR402 seat sampling, GPIO 33 ISR with deferred queue (E2), and deep sleep routine (E3).
- **Node 2 Agent**: FreeRTOS KY-001 DS18B20 temperature reading + windowed ADC KY-037 sound sampling tasks (E1) and MQTT publisher with retry (E4).
- **Gateway Agent**: Manage cloud persistence (M5), local SQLite store-and-forward queue (E4) when offline, and on-device ML closed loop.
