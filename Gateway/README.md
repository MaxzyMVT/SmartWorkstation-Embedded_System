# Smart Workstation - Raspberry Pi Gateway & On-Device AI

> **Course**: 2110356 Embedded System (CEDT), Chulalongkorn University  
> **Target Device**: Raspberry Pi 4 Model B (Raspberry Pi OS 32/64-bit)  
> **Simulation**: Supports full hardware simulation on Windows / macOS / Linux laptops  

---

## 1. System Architecture & Course Compliance

This component serves as the central intelligent edge controller for the Smart Workstation:

- **Requirement M1 (≥2 Local Wired Sensors on Gateway)**:
  1. **HC-SR04 Ultrasonic Distance Sensor**: Measures face-to-book distance angled at 45° from the workstation front arch (GPIO 23 TRIG, GPIO 24 ECHO).
  2. **KY-001 DS18B20 1-Wire Digital Temperature Sensor**: Measures ambient workstation temperature directly (GPIO 4 1-Wire bus).
  *(Node 2 provides KY-037 Sound and BH1750 Ambient Light via I2C; Node 1 provides FSR402 Seat Mat)*
- **Requirement M2 (Closed-Loop Physical Actuators)**:
  1. **Motorized Blocker Curtain (28BYJ-48 Stepper + ULN2003)**: Half-step driven barrier that rolls down when poor posture, severe noise, or eye strain is detected, with M7 software bounds.
  2. **High-CRI 5V LED Bar**: Driven via logic-level MOSFET using closed-loop PWM (proportional controller maintaining optimal study lighting).
- **Requirement M4 & E7 (Genuine On-Device Machine Learning)**:
  - *No simple if-else logic or fixed thresholds.*
  - Multi-dimensional classification pipeline trained with `scikit-learn` (`RandomForestClassifier`, 100 trees, 6 features, 9 classes).
  - Inference latency strictly **< 10 ms** (mean ~5 ms on Raspberry Pi).
- **IoT & Alerts**:
  - Ingests wireless ESP32 telemetry from `smart_workstation/node1` and `smart_workstation/node2`.
  - Publishes consolidated JSON telemetry to `smart_workstation/telemetry` for the React Web Dashboard (`Interface/`).
  - Dispatches automated webhook alerts to **Discord** and **LINE Notify** with built-in cooldown debouncing.

---

## 2. Raspberry Pi 4 GPIO Pinout (BCM Numbering)

| Peripheral | Component | Physical Pin | BCM GPIO | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Temp Sensor** | KY-001 DS18B20 Module | Pin 7 | **GPIO 4** | 1-Wire digital bus (3.3V power, Pin 1) |
| **Ultrasonic TRIG** | HC-SR04 | Pin 16 | **GPIO 23** | 10µs trigger pulse output |
| **Ultrasonic ECHO** | HC-SR04 | Pin 18 | **GPIO 24** | **Requires 1kΩ / 2kΩ (or 10k/20k) voltage divider to 3.3V** |
| **Stepper IN1** | ULN2003 Driver | Pin 29 | **GPIO 5** | Half-step phase 1 |
| **Stepper IN2** | ULN2003 Driver | Pin 31 | **GPIO 6** | Half-step phase 2 |
| **Stepper IN3** | ULN2003 Driver | Pin 33 | **GPIO 13** | Half-step phase 3 |
| **Stepper IN4** | ULN2003 Driver | Pin 35 | **GPIO 19** | Half-step phase 4 |
| **LED PWM** | MOSFET Gate | Pin 12 | **GPIO 18** | Hardware PWM0 (1000 Hz) |
| **Servo PWM** | SG90 Servo Signal | Pin 32 | **GPIO 12** | PWM (50 Hz, 2.5%–12.5% duty) |
| **Power** | Sensors & Actuators | Pins 2, 4 (5V) / 1, 17 (3.3V) | - | Ensure common GND across all devices |

---

## 3. Installation & Setup

### Option A: On Raspberry Pi 4 (Automated Setup)

Run the automated provisioning script to install system packages, configure Mosquitto broker, enable I2C/SPI, create Python `.venv`, and install dependencies in one command:

```bash
cd SmartWorkstation-Embedded_System/Gateway
chmod +x setup_rpi.sh
bash setup_rpi.sh
```

#### Manual Step-by-Step (Alternative)
1. Enable I2C interface on Raspberry Pi OS:
   ```bash
   sudo raspi-config nonint do_i2c 0
   sudo raspi-config nonint do_spi 0
   ```
2. Install system packages & Mosquitto broker:
   ```bash
   sudo apt update
   sudo apt install -y python3-pip python3-venv python3-dev libgpiod-dev i2c-tools mosquitto mosquitto-clients
   ```
3. Configure Mosquitto listener & anonymous access:
   ```bash
   sudo mkdir -p /etc/mosquitto/conf.d
   sudo bash -c 'cat > /etc/mosquitto/conf.d/workstation.conf <<EOF
listener 1883
allow_anonymous true
EOF'
   sudo systemctl restart mosquitto
   ```
4. Install Python dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

### Option B: On Development Laptop (Simulation Mode)

The gateway code automatically falls back to **MOCK_HARDWARE** mode when `RPi.GPIO` or `smbus2` are not present, allowing development and testing on Windows/macOS:
```bash
pip install -r requirements.txt
```

---

## 4. Usage Instructions

### 1. Train the On-Device AI Model (Requirement M4)
To train the Random Forest model and benchmark inference latency:
```bash
python train_ai_model.py
```
This generates `model.joblib` with high accuracy and verifies `< 10 ms` latency on test vectors.

### 2. Configure Environment Variables (Optional)
Copy `.env.example` to `.env` and fill in optional webhook tokens:
```bash
cp .env.example .env
```

### 3. Launch the Gateway Daemon
```bash
# On physical Raspberry Pi (manual foreground execution):
python gateway_main.py

# On Laptop (forcing mock simulation):
python gateway_main.py --mock
```

### 4. Background Service Management (Systemd)
The gateway daemon runs in the background as a systemd service (`smartworkstation.service`) that automatically launches on boot.

```bash
# Check service status and PID
sudo systemctl status smartworkstation

# Stream live real-time daemon logs (press Ctrl+C to exit)
sudo journalctl -u smartworkstation -f

# View the last 100 log lines
sudo journalctl -u smartworkstation -n 100 --no-pager

# Restart service (e.g. after code update or git pull)
sudo systemctl restart smartworkstation

# Stop / Start service
sudo systemctl stop smartworkstation
sudo systemctl start smartworkstation
```

---

## 5. MQTT Data Specifications

### Subscribed Topics (from ESP32 Nodes):
- **`smart_workstation/node1`**:
  ```json
  {
    "presence": 1,
    "sitting_duration_min": 24.5
  }
  ```
- **`smart_workstation/node2`**:
  ```json
  {
    "temp_c": 24.8,
    "sound_db": 36.2
  }
  ```

### Published Telemetry (`smart_workstation/telemetry`):
Formatted specifically for direct binding with the React Frontend (`Interface/src/App.jsx`):
```json
{
  "seat": true,
  "distance": 45.2,
  "light": 448.0,
  "temp": 24.8,
  "sound": 36.2,
  "sitting_duration_min": 24.5,
  "curtain_state": "OPEN",
  "led_duty_cycle": 58.5,
  "servo_angle": 85.0,
  "ai_state": {
    "class_id": 0,
    "class_name": "OPTIMAL_STUDY",
    "confidence": 0.985,
    "action": "Curtain OPEN; Closed-loop LED tuned to ~450 Lux; Normal operation",
    "advice": "Posture and study environment are optimal. Keep it up!",
    "latency_ms": 0.62
  },
  "timestamp": 1731381234.56
}
```

