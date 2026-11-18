# Smart Workstation - Embedded System Project Notes & Full Score Guide

> **Course**: 2110356 Embedded System (CEDT), Chulalongkorn University (Final Project 2026)  
> **Team**: ลูกลุงโทนี่  
> **Project**: Smart Workstation (โต๊ะทำงานอัจฉริยะตรวจจับท่าทางและปรับสภาพแวดล้อมอัตโนมัติ)  
> **Target Score**: **70 / 70 Points** (Scaled to 35% of course grade)

---

## 1. Executive Summary & Core Concept

**Smart Workstation** is a modular, portable tabletop enclosure ($70 \times 50 \times 60\text{ cm}$) placed over a study/work desk. It creates an ergonomically controlled, distraction-free environment for students and desk workers.

### Closed-Loop Architecture
1. **Sensor Node 1 (Chair)**: ESP32 + FSR402 Force Sensing Resistor (seat pressure mat). Detects seating presence, monitors cumulative study time, and features deep sleep with GPIO 33 interrupt wake-up.
2. **Sensor Node 2 (Enclosure Wall)**: ESP32 + KY-001 DS18B20 1-Wire temperature sensor + KY-037 sound detection sensor. Monitors acoustic noise and thermal comfort.
3. **Gateway (Front Arch / Roof)**: Raspberry Pi 4 + HC-SR04 ultrasonic distance sensor + KY-018 LDR photoresistor module.
4. **Actuators (Closed-Loop)**:
   - **Motorized Blocker Curtain**: 28BYJ-48 Stepper motor + ULN2003 driver with M7 software bounds. Automatically lowers to enforce break time or shield against severe noise / drafts; opens when normal conditions resume.
   - **Adaptive Task Lighting**: High-CRI 5V LED lamp driven by PWM MOSFET (proportional feedback maintaining optimal desk illumination).
5. **On-Device AI Engine**: Scikit-Learn multi-class model (`RandomForestClassifier`, 6 inputs, 9 states, inference $<10\text{ ms}$) processing posture and environmental data in real time to trigger closed-loop actions.
6. **Cloud & Dashboard**: MQTT over TLS $\rightarrow$ Cloud broker & storage $\rightarrow$ React Web Dashboard (`Interface/`) + Automated Webhook notifications (Discord / LINE).

---

## 2. Strict Grading Rubric & Strategy for Full Score (70 / 70)

The course grades on **Embedded Engineering Depth, Reliability, and Rigorous Verification** rather than mere feature quantity.

```
Total: 70 Points = I (5) + II (5) + II′ (10) + III (20) + IV (10) + V (10) + VI (10)
```

### Part 0: Minimum Requirements M1–M8 (Pass / Fail — Missing item = -2 pts each!)

| Code | Requirement | Course Definition & Strict Rules | Smart Workstation Implementation Status |
| :--- | :--- | :--- | :--- |
| **M1** | $\ge 5$ distinct sensor types ($\ge 2$ on Node, $\ge 2$ on Gateway) | Buttons/switches do **not** count. Internal MCU sensors do **not** count. Dual-reading modules count as **1** type. Off-the-shelf devices do not count. | **Pass (5 types total)**:<br>• **Gateway (2)**: HC-SR04 Ultrasonic, KY-018 LDR Module<br>• **Node 1 (1)**: FSR402 Force Sensing Resistor<br>• **Node 2 (2)**: KY-001 DS18B20 Temp, KY-037 Sound Sensor |
| **M2** | $\ge 1$ physical actuator in closed loop | LEDs, buzzers, speakers, displays do **not** count as actuators. Vibration motors count only if doing mechanical work. Must produce physical motion. | **Pass (Physical Actuator)**:<br>• 28BYJ-48 Stepper Motor (rolls curtain up/down via ULN2003)<br>• (Supplementary Dynamic PWM LED lamp via MOSFET) |
| **M3** | Wired or wireless node-to-gateway transmission | Must transmit from sensor nodes to gateway (nodes do **not** connect directly to cloud). | **Pass**: Wi-Fi MQTT with structured JSON telemetry payload (`node_id`, sequence, sensor payload). |
| **M4** | AI component in closed loop | Must process camera/mic/sensor data and drive actuator decisions. **Fixed thresholds and if-else rules are strictly prohibited.** | **Pass**: On-device 9-class Random Forest classifier taking 6 sensor features and predicting workstation state to actuate curtain. |
| **M5** | Cloud storage & Dashboard | Raw sensor data stored in cloud and retrievable via dashboard. | **Pass**: MQTT / Firebase + React Vite Web Dashboard (`Interface/`). |
| **M6** | Event Notification System | Automated alerts for critical events/status. | **Pass**: Discord & LINE Notify webhooks on posture violation, noise warning, curtain closing, and session completion. |
| **M7** | Safety compliance | Enclosed relays/mains, liquid separation, battery protection, moving parts must have safety stops (limit switch/timeout). | **Pass**: Software travel bounds (0..4096), motor watchdog timeout, ULN2003 coil idle release. No exposed high voltage. |
| **M8** | Public Git repository with history from all members | Commit history from **every** group member. | **Pass**: GitHub repository with conventional commits from all 5 members. |

---

### Part I: Problem Definition (5 / 5 Points)
- **Relevance & Impact (3 pts)**: Target CEDT students and sedentary desk workers. Solves poor sitting posture (forward head posture / slouching), continuous visual fatigue from inadequate desk lighting, chronic noise distraction, and lack of periodic breaks.
- **Evidence & Detail (2 pts)**: Ergonomic studies, survey data from students studying in dorms/study rooms, reference standards for desk illumination (CIE / EN 12464-1 recommended 500 Lux for reading).

---

### Part II: System Architecture & Scalability (5 / 5 Points)
- **Architecture & Data Flow (2 pts)**: Clear block diagram (Node 1, Node 2 $\rightarrow$ Gateway $\rightarrow$ Cloud $\rightarrow$ Web App), defined JSON schema with explicit timestamps, node IDs, sensor readings, and real-time processing loop.
- **Scalability (3 pts)**:
  - *Rubric criteria*: 1 pt for multi-node support in code; 2 pts for 1 physical + 2 simulated nodes; **3 pts for $\ge 2$ physical sensor nodes running simultaneously**.
  - **Full Score Action**: Deploy **both physical Node 1 (chair) and physical Node 2 (frame)** concurrently, demonstrating real-time concurrent ingestion at Gateway.

---

### Part II′: Embedded Engineering Depth (10 / 10 Points — 5 items $\times$ 2 pts)

We target 5 designated items:

| Item | Title | Requirement for 2 Points | How Smart Workstation Achieves Full Credit |
| :--- | :--- | :--- | :--- |
| **E1** | **RTOS Multitasking** | FreeRTOS with $\ge 3$ tasks, reasoned priorities, queues/semaphores/event groups. | On ESP32 Node: Task 1 (Sensor Acquisition, Priority 2), Task 2 (Data Processing & Queue Dispatch, Priority 2), Task 3 (Wireless Telemetry Transmission, Priority 1). Inter-task communication via FreeRTOS Queues and binary semaphores for hardware bus. |
| **E2** | **Interrupt-Driven I/O** | Input handled by hardware ISR with deferred processing (no busy-waiting). | FSR pressure plate / seat switch trigger on GPIO interrupt (`gpio_isr_handler`), signaling an event group/queue to wake telemetry task. Limit switch on curtain generates ISR to immediately halt stepper motor. |
| **E3** | **Low Power Operation** | Deep/light sleep with measured current consumption and estimated battery life. | Node 1 (chair) enters light/deep sleep when unoccupied. Wakes via external GPIO interrupt when user sits down. Measure active current (mA) vs. sleep current ($\mu$A) with multimeter/power analyzer, and provide mathematical battery lifetime projection (e.g., 2000 mAh LiPo). |
| **E4** | **Reliable Communication** | Ack/retry between node and gateway, and store-and-forward buffering when cloud/network is down. | ESP32-to-Gateway protocol includes packet sequence numbers + ACK/retry. Gateway features a local SQLite / file-backed buffer that caches telemetry packets during internet dropouts and flushes them when reconnected. |
| **E5** | **Security** | Encrypted and authenticated connection to cloud (MQTT over TLS with certificates/credentials); no secrets in Git. | Gateway connects to cloud via MQTTS (Port 8883, TLS 1.3). Credentials loaded strictly via `.env` with `.env.example` in repo. |

> *Note on E7*: FAQ #4 clarifies that E7 (On-Device AI) only earns points if executed on an **MCU** (e.g. ESP32-S3 TinyML). Since our gateway runs on Raspberry Pi 4, running ML satisfies requirement **M4**, but selecting E1, E2, E3, E4, E5 secures the maximum 10/10 for Section II′ without ambiguity.

---

### Part III: Implementation (20 / 20 Points)
- **Functionality (8 pts)**: Core features (4 pts) + Complete end-to-end features (4 pts): All sensors, curtain actuation, adaptive light dimming & servo angle, and web dashboard fully operational.
- **Field Testing (3 pts)**:
  - 1 pt: minimal test.
  - 2 pts: continuous 24-hour test with uninterrupted data logging.
  - **3 pts**: Tested with actual users / deployed at CEDT study area or dorm desk.
- **Robustness (3 pts)**:
  - System must work reliably and **automatically recover from power loss or Wi-Fi disconnection** without requiring manual reset.
- **Build Quality (3 pts)**:
  - Neat wiring, cables secured, modular electronics mounting plates, beautiful cardboard enclosure ($70 \times 50 \times 60\text{ cm}$), fully portable.
- **User Experience (3 pts)**:
  - Intuitive React dashboard, responsive controls, clear graphs, non-intrusive Discord/LINE notification alerts.

---

### Part IV: Testing & Quantitative Analysis (10 / 10 Points)
- **Test Plan & Procedure (3 pts)**: Complete documented test cases covering normal operation, edge cases, and failure scenarios (Wi-Fi drop, sensor unplugged, power cut, limit switch trip).
- **Quantitative Metrics (4 pts)**: Must measure at least **3 metrics** with real data and stated methodology (3 pts), **plus repeated trials reporting mean, min, max, and variance** (1 pt).
  1. **End-to-End Latency**: Time elapsed from sensor trigger (e.g., sitting down / leaning forward) to physical curtain actuation and web dashboard update ($N \ge 30$ trials).
  2. **Packet Delivery Rate (PDR)**: Percentage of wireless packets successfully received between ESP32 nodes and Gateway under varying distances and obstacles ($N \ge 1000$ packets).
  3. **Sensor Accuracy Validation**: KY-018 LDR readings compared against ambient light levels; KY-001 Temperature readings compared against a laboratory thermometer; KY-037 Sound readings compared against a sound level meter.
  4. **Power Consumption & Battery Life**: Current draw measured across Active, IDLE, and Deep Sleep states on Node 1, calculating expected runtime on a standard battery.
  5. **AI Inference & Accuracy**: Confusion matrix, Precision, Recall, F1-Score, and inference latency distribution ($<10\text{ ms}$).
- **Analysis & Comparison (3 pts)**: Compare measured metrics against project goals AND existing commercial smart desk/ergonomic devices.

---

### Part V: Presentation & Live Demo (10 / 10 Points)
- **Slides & Delivery (5 pts)**: Logical structure, clear speaking, minimal slide reading.
- **Live Demonstration (2 pts)**: Smooth, practiced live demo with a mobile hotspot (avoid campus Wi-Fi pitfalls!).
- **Individual Q&A (3 pts)**: Examiner randomly selects individual members to explain technical details of their subsystem. **Every member must master their own code and role.**

---

### Part VI: Documentation & Video (10 / 10 Points)
- **Progress Video (Checkpoint - Nov 20)**: $\le 2$ minutes, demonstrating end-to-end data flow: Node $\rightarrow$ Gateway $\rightarrow$ Cloud $\rightarrow$ Dashboard.
- **Final Video (4 pts)**: 3–5 minutes, professional overview, working demo, team credits.
- **Final Report (6 pts)**: Max 10 pages containing:
  1. Introduction & Problem Statement
  2. Related Work & Citations
  3. System Design (Block diagrams, pinouts, state machines, packet formats)
  4. Test Results & Quantitative Performance Metrics
  5. Discussion & Conclusion
  6. Individual Contributions, Git link, AI tool declarations.

---

## 3. Team Member Roles & Git Contribution Matrix

To ensure a **1.0 Peer Factor** (no score reduction for any member), commits must be distributed according to declared roles:

| Name | Student ID | Designated Course Role | Primary Subsystem Ownership |
| :--- | :--- | :--- | :--- |
| **นายภัทรพล เสือสืบพันธ์** | 6833213821 | Project Manager & Test Lead | Test plans, quantitative metric collection, failure-mode testing |
| **นายอรรถศาสตร์ อินปั๋น** | 6833287221 | UI/UX & Dashboard Developer | React Web Dashboard (`Interface/`), WebSocket/MQTT client, alerts UI |
| **นายเฉลิมราชย์ ทองขาว** | 6833034821 | System Architect & Integration; Hardware | Physical enclosure, CAD/cardboard fabrication, wiring & assembly |
| **นายธรรมชาติ ปักษ์มงคล** | 6833117221 | Embedded Developer (Gateway & Cloud) | Raspberry Pi Gateway daemon, AI model, cloud MQTT & webhooks |
| **นายญาณภัทร จำนงค์สุทธิ์** | 6833054321 | Embedded Developer (Sensor Nodes) | ESP32 firmware (Node 1 & Node 2), FreeRTOS, sleep modes, ISR |

---

## 4. Key Milestones & Critical Deadlines

- [x] **Fri 30 Oct 2026**: Project Proposal Submitted.
- [ ] **Fri 20 Nov 2026 (23:59)**: **Checkpoint Video ($\le 2$ min)** via MyCourseVille (Demonstrating end-to-end data flow: Node $\rightarrow$ Gateway $\rightarrow$ Cloud $\rightarrow$ Dashboard).
- [ ] **Tue 08 Dec 2026**: **Final Presentation & Live Demo** (Bring private mobile hotspot!).
- [ ] **Tue 08 Dec 2026 (23:59)**: Final Submission on MyCourseVille (Slides, 10-page Report, 3-5 min Video, Source code & Git URL, Peer evaluation form). *(Grace period ends Wed 09 Dec 06:00)*.

---

## 5. Immediate Action Items

1. **Gateway Initialization**: Commit the current Raspberry Pi gateway daemon, AI pipeline, and hardware interface.
2. **Sensor Nodes Firmware**: Implement FreeRTOS tasks (E1), hardware interrupt (E2), and sleep routines (E3) in `SensorNodes/Node1/` and `SensorNodes/Node2/`.
3. **End-to-End Checkpoint Prep**: Validate live data flow across Node $\rightarrow$ Gateway $\rightarrow$ Cloud $\rightarrow$ Dashboard ahead of the **Nov 20 Checkpoint Video**.
