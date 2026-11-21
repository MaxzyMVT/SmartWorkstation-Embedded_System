# Smart Workstation — Complete Sensor & Hardware Pin Connection Guide

> **Course**: 2110356 Embedded System (CEDT), Chulalongkorn University  
> **Team**: ลูกลุงโทนี่  
> **Project**: Smart Workstation (โต๊ะทำงานอัจฉริยะ)  
> **Rubric Compliance**: Strictly enforces **Requirement M1 (≥5 Distinct Sensor Types: ≥2 on Nodes, ≥2 on Gateway)** and **Requirement M2 (Physical Motion Actuator in Closed Loop)**.

---

## 1. Quick Master Reference Table

| Peripheral | Sensor Type | Connected Device | Board Pins / Protocol | Electrical Operating Voltage | Notes / Circuitry |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **FSR402 Force Sensing Resistor** | **Pressure / Force** *(Type 1)* | **Node 1 (Chair ESP32)** | **GPIO 33** (Analog ADC1 / Digital ISR) | 3.3V | 10kΩ pull-down resistor to GND; wakes from Deep Sleep via `ext0` ISR |
| **BH1750 Ambient Light Sensor** | **Digital Illuminance** *(Type 2)* | **Node 2 (Desk ESP32)** | **SDA: GPIO 21**<br>**SCL: GPIO 22** (I2C Bus) | 3.3V | I2C Addr `0x23` (ADDR pin floating/GND); 16-bit digital (1–65,535 Lux); white dome cover |
| **KY-037 Microphone Sensor** | **Acoustic Noise / Sound** *(Type 3)* | **Node 2 (Desk ESP32)** | **AO: GPIO 35** (Analog ADC1) | 3.3V (or 5V) | 50ms windowed peak-to-peak sampling for dBA estimation; DO pin unused |
| **HC-SR04 Ultrasonic Sensor** | **Distance / Ergonomics** *(Type 4)* | **Gateway (Raspberry Pi 4)** | **TRIG: GPIO 23** (Pin 16)<br>**ECHO: GPIO 24** (Pin 18) | 5V VCC, **3.3V Logic** | **CRITICAL: 1kΩ / 2kΩ voltage divider on ECHO** to protect Pi 3.3V GPIO |
| **KY-001 DS18B20 Temp Sensor** | **Ambient Temperature** *(Type 5)* | **Gateway (Raspberry Pi 4)** | **DATA: GPIO 4** (Pin 7, 1-Wire Bus) | 3.3V | Standard Linux 1-Wire interface (`/sys/bus/w1/devices/28-*`); onboard 4.7kΩ pull-up |
| **28BYJ-48 Stepper Motor** | **Physical Actuator (M2)** | **Gateway (Raspberry Pi 4)** | **IN1: GPIO 5** (Pin 29)<br>**IN2: GPIO 6** (Pin 31)<br>**IN3: GPIO 13** (Pin 33)<br>**IN4: GPIO 19** (Pin 35) | 5V External Rail | Driven via ULN2003 Darlington array; M7 software safety bounds (0–4096 half-steps) |
| **Dynamic Task Light Bar** | **Supplementary PWM Lamp** | **Gateway (Raspberry Pi 4)** | **GATE: GPIO 18** (Pin 12, Hardware PWM0) | 5V External Rail | N-Channel Logic-Level MOSFET (e.g. IRLZ44N); 1000 Hz PWM frequency |

---

## 2. Sensor Node 1 — Chair Presence Node (`SensorNodes/Node1/`)

Monitors seating duration and ergonomics directly from the workstation chair.

```
       +--------------------+
       |  ESP32 DevKit V1   |
       |                    |
       |            GPIO 33 <-------+
       |               3.3V >---+   |
       |                GND >-  |   |
       +--------------------+   |   |
                                |   |
               +----------------+   |
               |                    |
            [ FSR402 ]              |
               |                    |
               +--------------------+
               |
             [10kΩ] (Pull-Down)
               |
              GND
```

### Pin Assignment
| Sensor Pin | ESP32 Pin | Function | Notes |
| :--- | :--- | :--- | :--- |
| **FSR Lead 1** | **3.3V** | Power rail | Connected to 3.3V out from ESP32 |
| **FSR Lead 2** | **GPIO 33** | Sensor Signal | Junction between FSR and 10kΩ pull-down resistor |
| **Pull-down Resistor** | **GND** | Reference pull-down | 10kΩ $\pm 5\%$ metal film or carbon resistor |

### Embedded Features (E1–E4)
- **Interrupt Wake-up (E2 & E3)**: GPIO 33 is configured with `attachInterruptArg()` on `CHANGE`. When empty for $>60\text{ s}$, the ESP32 enters deep sleep (`esp_sleep_enable_ext0_wakeup(GPIO_NUM_33, 1)`) and wakes instantaneously upon sitting.
- **Sitting Timer (E1)**: Tracks cumulative study duration in minutes without drift.
- **MQTT Telemetry (E4)**: Publishes to `smart_workstation/node1` with incrementing `seq` number.

---

## 3. Sensor Node 2 — Desk Environment Node (`SensorNodes/Node2/`)

Monitors ambient noise and study surface lighting directly on the desk reading plane.

```
                  +--------------------+
                  |  ESP32 DevKit V1   |
                  |                    |
                  |            3.3V ---+----+ (3.3V Power Bus)
                  |             GND ---+--+ | (Common GND)
                  |                    |  | |
                  |            GPIO 21 <==|=== SDA (BH1750)
                  |            GPIO 22 <==|=== SCL (BH1750)
                  |            GPIO 35 <==|====== AO (KY-037)
                  +--------------------+  | |
                                          | |
          +-------------------------------+ |
          |                               | |
    [ BH1750 Light ]                      | |
      VCC: 3.3V                           | |
      GND: GND                            | |
      SCL: GPIO 22                        | |
      SDA: GPIO 21                        | |
      ADDR: Disconnected (0x23)           | |
                                          | |
          +-------------------------------+ |
          |                                 |
    [ KY-037 Sound ]                        |
      VCC: 3.3V                             |
      GND: GND -----------------------------+
      AO:  GPIO 35
      DO:  (Leave Disconnected)
```

### Pin Assignment
| Sensor Module | Module Pin | ESP32 Pin | Function / Protocol | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **BH1750 Light** | **VCC** | **3.3V** | Power (3.3V) | Safe 3.3V logic level |
| | **GND** | **GND** | Ground | Common Ground with ESP32 |
| | **SCL** | **GPIO 22** | I2C Clock | Hardware I2C Clock |
| | **SDA** | **GPIO 21** | I2C Data | Hardware I2C Data |
| | **ADDR** | *(Floating)* | I2C Address select | Leave unconnected or connect to GND for `0x23` |
| **KY-037 Sound** | **VCC** | **3.3V** (or 5V) | Power | 3.3V from ESP32 rail |
| | **GND** | **GND** | Ground | Common Ground |
| | **AO** | **GPIO 35** | Analog Audio Signal | ADC1 channel 7 (50ms Peak-to-Peak windowing) |
| | **DO** | *(Unused)* | Digital Threshold | Do not connect |

### Physical Placement Strategy
- **BH1750 (with white dome cover)**: Mount on the **rear-center desk reading plane pointing straight UP towards the ceiling / workstation LED light bar**. The dome acts as a cosine diffuser capturing light across a $180^\circ$ hemisphere (target: 500 Lux according to EN 12464-1).
- **KY-037 Sound Sensor**: Mount on the side or rear upright wall, isolated from direct vibrations of the stepper motor.

---

## 4. Gateway — Central Controller (`Gateway/` on Raspberry Pi 4)

Runs the on-device AI Random Forest classifier (`model.joblib`), samples 2 local wired sensors, drives actuators in closed loop, and forwards telemetry to Firebase/Cloud.

```
Raspberry Pi 4 Physical Header (40-Pin BCM Pinout)
==================================================
           3.3V (1)  (2)  5V (External Sensor Rail)
(KY-001) GPIO 4 (7)  (6)  GND
(PWM0)  GPIO 18 (12) (14) GND
(TRIG)  GPIO 23 (16) (18) GPIO 24 (ECHO via 1k/2k divider)
(IN1)   GPIO 5  (29) (30) GND
(IN2)   GPIO 6  (31) (32) GPIO 12 (Optional Servo)
(IN3)   GPIO 13 (33) (34) GND
(IN4)   GPIO 19 (35) (39) GND
```

### 4.1 Local Wired Sensors (Requirement M1 Compliance)

#### 1. HC-SR04 Ultrasonic Distance Sensor (Posture & Text-Neck Detection)
Measures user face-to-desk distance (35–55 cm optimal study posture).
- **VCC**: Connect to Raspberry Pi **5V (Physical Pin 2)**.
- **GND**: Connect to Raspberry Pi **GND (Physical Pin 6)**.
- **TRIG**: Connect directly to **GPIO 23 (Physical Pin 16)** (RPi 3.3V output pulse triggers the transducer).
- **ECHO**: **CRITICAL: MUST USE VOLTAGE DIVIDER**:
  ```
  HC-SR04 ECHO (5V Out) 
          |
        [1kΩ]
          |
          +---> Raspberry Pi GPIO 24 (Pin 18) [3.3V Safe!]
          |
        [2kΩ] (or two 1kΩ in series)
          |
         GND
  ```
  *Formula*: $V_{\text{out}} = 5.0\text{ V} \times \frac{2\text{k}\Omega}{1\text{k}\Omega + 2\text{k}\Omega} = 3.33\text{ V}$.

#### 2. KY-001 DS18B20 1-Wire Digital Temperature Sensor (Thermal Comfort)
Measures ambient workspace temperature with 0.1°C precision.
- **VCC**: Connect to **3.3V (Physical Pin 1)**.
- **GND**: Connect to **GND (Physical Pin 9)**.
- **DATA**: Connect to **GPIO 4 (Physical Pin 7)**.
- *Pull-Up*: The KY-001 breakout board has an integrated 4.7kΩ pull-up resistor between DATA and VCC.
- *Linux Driver*: Read directly from `/sys/bus/w1/devices/28-*/w1_slave` via 1-Wire kernel module (`dtoverlay=w1-gpio` in `/boot/config.txt`).

---

### 4.2 Actuators (Requirement M2 Compliance)

#### 1. 28BYJ-48 Stepper Motor + ULN2003 Driver (Smart Blocker Curtain)
Physical mechanical motion device providing closed-loop intervention (lowers curtain during severe noise, excessive eye-strain glare, or mandatory break time).
| ULN2003 Pin | Raspberry Pi Pin | Physical Header Pin | Function |
| :--- | :--- | :--- | :--- |
| **+ (Power)** | **5V External Rail** | External 5V or Pin 2/4 | 5V Stepper motor coil power |
| **- (Ground)**| **Common GND** | Physical Pin 30 or 34 | Ground return |
| **IN1** | **GPIO 5** | Physical Pin 29 | Stepper half-step coil phase 1 |
| **IN2** | **GPIO 6** | Physical Pin 31 | Stepper half-step coil phase 2 |
| **IN3** | **GPIO 13** | Physical Pin 33 | Stepper half-step coil phase 3 |
| **IN4** | **GPIO 19** | Physical Pin 35 | Stepper half-step coil phase 4 |

#### 2. Dynamic Task Lighting (5V High-CRI LED Bar)
Proportional closed-loop illumination control to maintain 450–550 Lux on the desk.
- **GATE**: Connect to **GPIO 18 (Physical Pin 12, Hardware PWM0)** via a $100\Omega$ current-limiting resistor, with a $10\text{k}\Omega$ pull-down resistor to GND.
- **SOURCE**: Connect to Power GND.
- **DRAIN**: Connect to negative cathode of the 5V LED lamp.
- **LED Positive (+)**: Connect to +5V rail.

---

## 5. Course Rubric Validation Checklist (M1 & M2)

| Rubric Rule | Criteria | Smart Workstation Implementation | Status |
| :--- | :--- | :--- | :---: |
| **M1 Sensor Count** | $\ge 5$ distinct types | 1. Ultrasonic (HC-SR04)<br>2. Illuminance (BH1750 Digital Lux)<br>3. Acoustic Noise (KY-037 Sound)<br>4. Temperature (KY-001 DS18B20)<br>5. Pressure (FSR402 Force Sensing Resistor) | **PASSED** (5/5) |
| **M1 Gateway Count**| $\ge 2$ types on Gateway | • HC-SR04 Ultrasonic (GPIO 23/24)<br>• KY-001 DS18B20 Temp (GPIO 4) | **PASSED** (2/2) |
| **M1 Node Count** | $\ge 2$ types on Nodes | • BH1750 Light on Node 2 (I2C GPIO 21/22)<br>• KY-037 Sound on Node 2 (GPIO 35)<br>• FSR402 Seat on Node 1 (GPIO 33) | **PASSED** (3/2) |
| **M2 Actuator** | $\ge 1$ physical motion device | 28BYJ-48 Stepper Motor driving physical motorized curtain via ULN2003 (with M7 software bounds 0–4096) | **PASSED** |
| **M4 AI Closed-Loop**| Genuine ML (no simple if-else) | On-device 9-class `RandomForestClassifier` (`model.joblib`) executing in $<10\text{ ms}$ on Raspberry Pi | **PASSED** |

---

## 6. Wiring & Electrical Best Practices for Demo Day

1. **Common Ground is Mandatory**: Ensure that all power supplies (Raspberry Pi, ESP32 nodes, and external 5V step-down rails) share a single common GND rail.
2. **Voltage Divider Integrity**: Never connect HC-SR04 `ECHO` directly to Raspberry Pi GPIO without the 1kΩ / 2kΩ resistor divider; 5V will permanently damage the Pi 3.3V SoC.
3. **I2C Bus Length**: Keep the 4-wire jumper cable between Node 2 ESP32 and BH1750 light sensor under $30\text{ cm}$ to prevent I2C capacitive signal degradation.
4. **Stepper Motor Power**: Power the ULN2003 driver board from a stable 5V rail (minimum 1.5A power supply) rather than directly from a single Pi GPIO pin to prevent brownout reboots.
