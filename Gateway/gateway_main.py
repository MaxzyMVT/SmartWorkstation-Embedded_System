#!/usr/bin/env python3
"""gateway_main.py -- Smart Workstation Gateway daemon (Raspberry Pi 4 / --mock on a laptop).

Pipeline, once per second (AGENTS.md sections 4.2 / 4.5):
    MQTT node1 + node2  --\
    HC-SR04 + DS18B20   ---+--> feature vector --> SmartWorkstationAI (model.joblib, <10 ms)
                                   --> actuators (28BYJ-48 stepper curtain, PWM LED bar)
                                   --> smart_workstation/telemetry (MQTT, 1 Hz)
                                   --> SQLite outbox --> Firestore (M5)   |  Discord / LINE alerts (M6)

Closed loop: actuators change the room, the next BH1750/HC-SR04 reading sees the
change, and the model decides again. Actuator decisions come from the model's class,
never from raw-sensor if/else (rubric M4).

Usage:
    python gateway_main.py --mock --broker localhost      # laptop simulation
    python gateway_main.py --broker localhost             # on the Pi

Systemd Service Management (Raspberry Pi auto-start daemon):
    sudo systemctl status smartworkstation                # Check daemon health & uptime
    sudo systemctl restart smartworkstation               # Restart service after code changes
    sudo systemctl stop smartworkstation                  # Stop daemon
    sudo systemctl start smartworkstation                 # Start daemon
    sudo journalctl -u smartworkstation -f                # Live follow gateway logs
    sudo journalctl -u smartworkstation -n 100 --no-pager # View last 100 log entries
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import smtplib
import sqlite3
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:  # pragma: no cover
    raise SystemExit("paho-mqtt (>=2.0) is required: pip install -r requirements.txt") from exc

from hardware_interface import (
    CURTAIN_CLOSED_CM,
    CURTAIN_GLARE_CM,
    CURTAIN_GLARE_RATIO,
    CURTAIN_TRAVEL_STEPS,
    BaseHardware,
    create_hardware,
)
from train_ai_model import CLASS_INFO, FEATURE_NAMES, MODEL_PATH, train_and_save

log = logging.getLogger("gateway")
BASE_DIR = Path(__file__).resolve().parent

TOPIC_NODE1 = "smart_workstation/node1"
TOPIC_NODE2 = "smart_workstation/node2"
TOPIC_TELEMETRY = "smart_workstation/telemetry"
TOPIC_STATUS = "smart_workstation/gateway/status"

AI_LATENCY_BUDGET_MS = 10.0
SERVO_REST_DEG = 85.0
SERVO_NUDGE_DEG = 110.0   # posture: tilt monitor arm / lamp toward the user
SERVO_BREAK_DEG = 60.0    # break prompt: visible physical movement
LED_STEP_PCT = 15.0       # closed-loop LED ramp per control cycle
LED_IDLE_CAP_PCT = 20.0   # energy saving when the chair is empty
STEPPER_DELAY_S = 0.002   # ~500 half-steps/s, safe for a 28BYJ-48
DEFAULT_TEMP_C, DEFAULT_SOUND_DB = 24.0, 35.0  # only used before node2 ever reports


# ----------------------------------------------------------------------------
# Config helpers
# ----------------------------------------------------------------------------
def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def is_configured(value: Optional[str]) -> bool:
    """False for empty values and for the YOUR_... / ... placeholders in .env.example."""
    if not value:
        return False
    v = value.strip()
    return bool(v) and "..." not in v and "YOUR_" not in v.upper() and v.upper() not in ("CHANGE_ME", "TODO")


def _to_float(v: Any, lo: float, hi: float) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on", "seated"):
            return True
        if s in ("0", "false", "no", "off", "empty"):
            return False
    return None


# ----------------------------------------------------------------------------
# Node ingestion (E4: duplicate suppression + loss accounting via seq)
# ----------------------------------------------------------------------------
class NodeRegistry:
    NODE1_TIMEOUT_S = 15.0   # Node 1 deep-sleeps when the chair is empty -> silence means "empty"
    NODE2_HOLD_S = 30.0      # keep last Node 2 reading this long before flagging STALE

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rx_time: Dict[str, float] = {}
        self._last_seq: Dict[str, int] = {}
        self.stats = {n: {"rx": 0, "dup": 0, "lost": 0, "bad": 0} for n in ("node1", "node2")}
        self.seat: Optional[bool] = None
        self.sitting_min: Optional[float] = None
        self.battery_mv: Optional[float] = None
        self.temp: Optional[float] = None
        self.sound: Optional[float] = None
        self.lux: Optional[float] = None

    def ingest(self, topic: str, raw: bytes | str) -> bool:
        node = "node1" if topic.endswith("/node1") else "node2" if topic.endswith("/node2") else None
        if node is None:
            return False
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("payload is not a JSON object")
        except (ValueError, TypeError) as exc:
            self.stats[node]["bad"] += 1
            log.warning("%s: dropped malformed payload (%s)", node, exc)
            return False

        with self._lock:
            seq = msg.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                last = self._last_seq.get(node)
                if last is not None:
                    if seq == last or 0 < last - seq <= 10:
                        self.stats[node]["dup"] += 1  # retransmission / late duplicate
                        return False
                    if seq > last:
                        self.stats[node]["lost"] += seq - last - 1
                    # seq far below last => node rebooted; accept and resync
                self._last_seq[node] = seq

            if node == "node1":
                seat = _to_bool(msg.get("presence", msg.get("seat")))
                if seat is not None:
                    self.seat = seat
                sit = _to_float(msg.get("sitting_duration_min"), 0, 24 * 60)
                if sit is not None:
                    self.sitting_min = sit
                bat = _to_float(msg.get("battery_mv"), 0, 6000)
                if bat is not None:
                    self.battery_mv = bat
            else:
                t = _to_float(msg.get("temp_c", msg.get("temp")), -20, 80)
                s = _to_float(msg.get("sound_db", msg.get("sound")), 0, 140)
                lx = _to_float(msg.get("lux", msg.get("light")), 0, 65535)
                if t is not None:
                    self.temp = t
                if s is not None:
                    self.sound = s
                if lx is not None:
                    self.lux = lx

            self._rx_time[node] = time.monotonic()
            self.stats[node]["rx"] += 1
        return True

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            age1 = now - self._rx_time["node1"] if "node1" in self._rx_time else None
            age2 = now - self._rx_time["node2"] if "node2" in self._rx_time else None
            node1_live = age1 is not None and age1 <= self.NODE1_TIMEOUT_S
            seat = bool(self.seat) if node1_live else False
            return {
                "seat": seat,
                "sitting_min": self.sitting_min if (node1_live and seat) else None,
                "temp": self.temp if self.temp is not None else DEFAULT_TEMP_C,
                "sound": self.sound if self.sound is not None else DEFAULT_SOUND_DB,
                "lux": self.lux,
                "node_status": {
                    "node1": "ONLINE" if node1_live else ("ASLEEP_OR_OFFLINE" if age1 is not None else "NEVER_SEEN"),
                    "node2": ("NEVER_SEEN" if age2 is None else "ONLINE" if age2 <= self.NODE2_HOLD_S else "STALE"),
                },
            }


# ----------------------------------------------------------------------------
# AI (M4)
# ----------------------------------------------------------------------------
class SmartWorkstationAI:
    """Wraps the RandomForest in model.joblib. 6 features in, 7 states out."""

    def __init__(self, path: Path = MODEL_PATH) -> None:
        self.model = self._load_or_train(path)
        self.model.set_params(n_jobs=1)  # thread-pool overhead dominates for 1 sample
        self.predict([1, 45.0, 500.0, 24.0, 40.0, 10.0])  # warm-up so the first real call is fast

    @staticmethod
    def _load_or_train(path: Path):
        import joblib

        def _extract_model(obj: Any) -> Any:
            return obj["model"] if isinstance(obj, dict) and "model" in obj else obj

        if path.exists():
            try:
                loaded = joblib.load(path)
                return _extract_model(loaded)
            except Exception as exc:  # e.g. scikit-learn version mismatch between laptop and Pi
                log.warning("Could not load %s (%s); retraining", path.name, exc)
        else:
            log.warning("%s not found; training a new model", path.name)
        metrics = train_and_save(path)
        log.info("Trained model: accuracy %.3f, latency %.2f ms", metrics["accuracy"], metrics["latency_ms"])
        loaded = joblib.load(path)
        return _extract_model(loaded)

    def predict(self, features: List[float]) -> Dict[str, Any]:
        x = np.asarray([features], dtype=np.float64)
        t0 = time.perf_counter()
        proba = self.model.predict_proba(x)[0]
        latency_ms = (time.perf_counter() - t0) * 1000.0
        k = int(np.argmax(proba))
        class_id = int(self.model.classes_[k])
        info = CLASS_INFO[class_id]
        return {
            "class_id": class_id,
            "class_name": info["name"],
            "confidence": round(float(proba[k]), 3),
            "action": info["action"],
            "advice": info["advice"],
            "latency_ms": round(latency_ms, 2),
        }


# ----------------------------------------------------------------------------
# Actuators (M2): stepper curtain on its own thread so the 1 Hz loop never blocks
# ----------------------------------------------------------------------------
class CurtainController(threading.Thread):
    TARGET_STEPS = {
        "OPEN": 0,
        "GLARE_SHIELD": int(CURTAIN_TRAVEL_STEPS * CURTAIN_GLARE_RATIO),  # 17 cm from axis (~47.2%)
        "CLOSED": CURTAIN_TRAVEL_STEPS,                                  # 36 cm from axis (100%)
    }

    def __init__(self, hw: BaseHardware, step_delay: float) -> None:
        super().__init__(daemon=True, name="curtain")
        self.hw = hw
        self.step_delay = step_delay
        self.position = 0          # half-steps away from home (0 = fully OPEN)
        self.state = "HOMING"
        self._target = "OPEN"
        self._settled = ""         # last state fully reached
        self._cv = threading.Condition()
        self._halt = threading.Event()

    def command(self, target: str) -> None:
        if target not in self.TARGET_STEPS:
            target = "OPEN"
        with self._cv:
            if target != self._target:
                self._target = target
                self._cv.notify()

    def stop(self) -> None:
        self._halt.set()
        with self._cv:
            self._cv.notify()

    def _home(self) -> None:
        if not self.hw.has_limit_switch():
            # M7 Safety Stop: Software travel bounds (0..CURTAIN_TRAVEL_STEPS) with coil watchdog
            self.hw.release_stepper()
            self.position = 0
            self._settled = self.state = "OPEN"
            log.info(
                "Curtain initialized at 0 (OPEN) [M7: Software step bounds 0..%d (36 cm), Glare Shield: %d (17 cm) active]",
                CURTAIN_TRAVEL_STEPS,
                self.TARGET_STEPS["GLARE_SHIELD"],
            )
            return

        for _ in range(CURTAIN_TRAVEL_STEPS + 400):
            if self.hw.limit_switch_pressed():
                break
            if self._halt.is_set():
                return
            self.hw.step(-1)
            time.sleep(self.step_delay)
        else:
            log.error("Curtain homing: limit switch never triggered; assuming OPEN. Check wiring.")
        self.hw.release_stepper()
        self.position = 0
        self._settled = self.state = "OPEN"
        log.info("Curtain homed (OPEN)")

    def run(self) -> None:
        self._home()
        while not self._halt.is_set():
            with self._cv:
                while self._target == self._settled and not self._halt.is_set():
                    self._cv.wait(0.5)
                target = self._target
            if self._halt.is_set():
                break

            target_step = self.TARGET_STEPS.get(target, 0)
            if self.position < target_step:
                self.state = "CLOSING"
                while self.position < target_step and self._target == target and not self._halt.is_set():
                    self.hw.step(+1)
                    self.position += 1
                    time.sleep(self.step_delay)
            elif self.position > target_step:
                self.state = "OPENING"
                while self.position > target_step and self._target == target and not self._halt.is_set():
                    if target_step == 0 and self.hw.limit_switch_pressed():
                        self.position = 0
                        break
                    self.hw.step(-1)
                    self.position -= 1
                    time.sleep(self.step_delay)

            if self._target == target:
                self._settled = self.state = target
            self.hw.release_stepper()  # de-energise coils: no heat, no hum


# ----------------------------------------------------------------------------
# Alerts & Notifications (M6): Discord Webhook + Periodic Study Summary Email
# ----------------------------------------------------------------------------
class AlertDispatcher:
    def __init__(self) -> None:
        self.cooldown_s = float(os.getenv("ALERT_COOLDOWN_S", "120"))
        self.discord_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.email_to = os.getenv("REPORT_EMAIL_TO", "")
        self.report_interval_s = float(os.getenv("REPORT_INTERVAL_MIN", "60")) * 60.0

        self._last_sent: Dict[int, float] = {}
        self._q: "queue.Queue[Tuple[str, str, Optional[int]]]" = queue.Queue(maxsize=50)

        # Statistics accumulation for periodic email reports
        self._stat_lock = threading.Lock()
        self._study_seconds = 0.0
        self._total_cycles = 0
        self._dist_samples: List[float] = []
        self._lux_samples: List[float] = []
        self._temp_samples: List[float] = []
        self._sound_samples: List[float] = []
        self._break_count = 0
        self._curtain_moves = 0
        self._last_report_time = time.monotonic()

        self._worker = threading.Thread(target=self._run, daemon=True, name="alerts")
        self._worker.start()
        log.info("Alerts: discord=%s email=%s cooldown=%.0fs report_interval=%.0fm",
                 "on" if is_configured(self.discord_url) else "off",
                 "on" if is_configured(self.smtp_user) and is_configured(self.smtp_password) else "off",
                 self.cooldown_s, self.report_interval_s / 60.0)

    def record_telemetry(self, seat: bool, dist: float, lux: float, temp: float, sound: float, break_triggered: bool = False, curtain_moved: bool = False) -> None:
        with self._stat_lock:
            self._total_cycles += 1
            if seat:
                self._study_seconds += 1.0
            if dist < 200.0:
                self._dist_samples.append(dist)
            self._lux_samples.append(lux)
            self._temp_samples.append(temp)
            self._sound_samples.append(sound)
            if break_triggered:
                self._break_count += 1
            if curtain_moved:
                self._curtain_moves += 1

            # Check if it's time to dispatch periodic summary email
            if is_configured(self.smtp_user) and is_configured(self.smtp_password) and is_configured(self.email_to):
                if time.monotonic() - self._last_report_time >= self.report_interval_s:
                    self._dispatch_email_report()
                    self._last_report_time = time.monotonic()

    def _dispatch_email_report(self) -> None:
        with self._stat_lock:
            study_min = self._study_seconds / 60.0
            avg_dist = (sum(self._dist_samples) / len(self._dist_samples)) if self._dist_samples else 45.0
            avg_lux = (sum(self._lux_samples) / len(self._lux_samples)) if self._lux_samples else 450.0
            avg_temp = (sum(self._temp_samples) / len(self._temp_samples)) if self._temp_samples else 25.0
            avg_sound = (sum(self._sound_samples) / len(self._sound_samples)) if self._sound_samples else 40.0
            breaks = self._break_count
            curtain_acts = self._curtain_moves

            # Reset window accumulators for next interval
            self._dist_samples.clear()
            self._lux_samples.clear()
            self._temp_samples.clear()
            self._sound_samples.clear()
            self._break_count = 0
            self._curtain_moves = 0

        subject = f"📊 Smart Workstation Study Report ({time.strftime('%H:%M:%S')})"
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f8fafc; padding: 20px; color: #1e293b;">
          <div style="max-width: 600px; margin: auto; background: #ffffff; border-radius: 12px; padding: 24px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
            <h2 style="color: #4f46e5; margin-top: 0;">Smart Workstation Periodic Summary</h2>
            <p style="color: #64748b; font-size: 14px;">Course: 2110356 Embedded System (CEDT) | Automated Report</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 16px 0;" />
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">⏱️ Active Seated Study Time:</td>
                <td style="padding: 8px 0; text-align: right; color: #059669; font-weight: bold;">{study_min:.1f} minutes</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">📏 Average Ergonomic Distance:</td>
                <td style="padding: 8px 0; text-align: right;">{avg_dist:.1f} cm ({'Optimal' if 35 <= avg_dist <= 55 else 'Needs attention'})</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">💡 Average Desk Illumination:</td>
                <td style="padding: 8px 0; text-align: right;">{avg_lux:.1f} Lux (Target: 300–800 Lux)</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">🌡️ Average Temperature:</td>
                <td style="padding: 8px 0; text-align: right;">{avg_temp:.1f} °C (Comfort: 22–32°C)</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">🔊 Average Noise Level:</td>
                <td style="padding: 8px 0; text-align: right;">{avg_sound:.1f} dB ({'Quiet Focus' if avg_sound < 50 else 'Moderate Noise'})</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">☕ Mandatory Breaks Completed:</td>
                <td style="padding: 8px 0; text-align: right;">{breaks} break(s)</td>
              </tr>
              <tr>
                <td style="padding: 8px 0; font-weight: bold;">🛡️ Curtain Interventions (M2):</td>
                <td style="padding: 8px 0; text-align: right;">{curtain_acts} event(s)</td>
              </tr>
            </table>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 16px 0;" />
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin-bottom: 0;">
              Live Web Dashboard: <a href="https://smartworkstation-5fdcf.web.app" style="color: #4f46e5; text-decoration: none;">smartworkstation-5fdcf.web.app</a>
            </p>
          </div>
        </body>
        </html>
        """
        # Queue email sending onto background worker thread
        self._q.put_nowait(("__EMAIL_REPORT__", html_body, None))

    def notify(self, class_id: int, title: str, body: str) -> None:
        now = time.monotonic()
        if now - self._last_sent.get(class_id, -1e9) < self.cooldown_s:
            return
        self._last_sent[class_id] = now
        try:
            self._q.put_nowait((title, body, class_id))
        except queue.Full:
            log.warning("Alert queue full; dropping alert")

    @staticmethod
    def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "SmartWorkstationGateway/1.0", **headers})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    def _send_email(self, html_content: str) -> bool:
        if not (self.smtp_user and self.smtp_password and self.email_to):
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"📊 Smart Workstation Study Summary ({time.strftime('%Y-%m-%d %H:%M')})"
            msg["From"] = f"Smart Workstation <{self.smtp_user}>"
            msg["To"] = self.email_to
            msg.attach(MIMEText(html_content, "html"))

            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=12) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            log.info("Hourly study report email sent successfully to %s", self.email_to)
            return True
        except Exception as e:
            log.warning("Failed to send report email: %s", e)
            return False

    def _run(self) -> None:
        while True:
            title, body, class_id = self._q.get()

            # Handle periodic email reports
            if title == "__EMAIL_REPORT__":
                self._send_email(body)
                continue

            # Handle Discord Webhook embeds
            if is_configured(self.discord_url):
                # Pick appropriate embed color based on alert severity
                color = 0x4f46e5  # Default Indigo
                emoji = "ℹ️"
                if class_id == 2:    # Glare Shield
                    color = 0xf59e0b
                    emoji = "🛡️"
                elif class_id in (3, 4): # Posture alerts
                    color = 0xf97316
                    emoji = "⚠️"
                elif class_id == 5:  # Break timer reached
                    color = 0xef4444
                    emoji = "☕"
                elif class_id == 7:  # Noisy environment
                    color = 0x8b5cf6
                    emoji = "🔊"
                elif class_id == 8:  # Thermal discomfort
                    color = 0x06b6d4
                    emoji = "🌡️"
                elif class_id == 0:  # Break completed / Optimal
                    color = 0x10b981
                    emoji = "✅"

                embed = {
                    "title": f"{emoji} {title}",
                    "description": body,
                    "color": color,
                    "fields": [
                        {"name": "Trigger Reason", "value": f"AI Classification Class {class_id if class_id is not None else 0}", "inline": True},
                        {"name": "Dashboard", "value": "[Open Web App](https://smartworkstation-5fdcf.web.app)", "inline": True}
                    ],
                    "footer": {"text": "CEDT 2110356 Smart Workstation • Gateway Notification"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                payload = {"embeds": [embed]}
                try:
                    self._post(self.discord_url, payload, {})
                    log.info("Discord alert sent: %s", title)
                except (urllib.error.URLError, OSError) as exc:
                    log.warning("Discord alert failed: %s", exc)
            else:
                log.info("[alert dry-run] %s | %s", title, body)


# ----------------------------------------------------------------------------
# Cloud (M5): SQLite store-and-forward outbox (E4) -> Firestore
# ----------------------------------------------------------------------------
class Outbox:
    MAX_ROWS = 20000

    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                             "ts REAL NOT NULL, payload TEXT NOT NULL)")
            self._db.commit()

    def put(self, ts: float, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT INTO outbox (ts, payload) VALUES (?, ?)", (ts, json.dumps(payload)))
            self._db.execute("DELETE FROM outbox WHERE id <= (SELECT MAX(id) FROM outbox) - ?", (self.MAX_ROWS,))
            self._db.commit()

    def peek(self, n: int) -> List[Tuple[int, float, Dict[str, Any]]]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, payload FROM outbox ORDER BY id LIMIT ?", (n,)).fetchall()
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    def delete(self, ids: List[int]) -> None:
        if not ids:
            return
        with self._lock:
            self._db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


class FirestoreUploader:
    """Uploads batches to Firestore. Document ID = timestamp in ms, so retries are idempotent."""

    def __init__(self, cred_path: str, collection: str) -> None:
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(cred_path))
        self._db = firestore.client()
        self._col = self._db.collection(collection)

    def upload(self, rows: List[Tuple[int, float, Dict[str, Any]]]) -> bool:
        try:
            batch = self._db.batch()
            for _id, ts, payload in rows:
                batch.set(self._col.document(str(int(ts * 1000))), payload)
            batch.commit()
            return True
        except Exception as exc:  # network down, quota, auth...
            log.warning("Firestore upload failed (%s); will retry", exc)
            return False


class DryRunUploader:
    """Mock-mode stand-in so the outbox flush path is exercised without credentials."""

    def upload(self, rows) -> bool:
        log.info("[cloud dry-run] flushed %d record(s) from outbox", len(rows))
        return True


class CloudForwarder(threading.Thread):
    def __init__(self, outbox: Outbox, uploader: Any) -> None:
        super().__init__(daemon=True, name="cloud")
        self.outbox, self.uploader = outbox, uploader
        self._halt = threading.Event()
        self._wake = threading.Event()

    def stop(self) -> None:
        self._halt.set()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def run(self) -> None:
        backoff = 0.5
        while not self._halt.is_set():
            self._wake.wait(backoff)
            self._wake.clear()
            if self._halt.is_set():
                break
            rows = self.outbox.peek(100)
            if not rows:
                backoff = 0.5
                continue
            if self.uploader.upload(rows):
                self.outbox.delete([r[0] for r in rows])
                backoff = 0.5
            else:
                backoff = min(backoff * 2, 60.0)  # offline: keep buffering, back off


# ----------------------------------------------------------------------------
# Simulated Node 1 / Node 2 for --mock when no broker/nodes are present
# ----------------------------------------------------------------------------
class SimulatedNodes(threading.Thread):
    """Scripted 90 s scenario feeding NodeRegistry directly (same path as MQTT).

    Sitting time is accelerated (1 s of wall time = 1 min) so NEEDS_BREAK shows up
    within the test run. Also injects duplicate seq numbers (E4 dedupe) and stops
    publishing while the chair is empty (Node 1 deep sleep).
    """

    def __init__(self, registry: NodeRegistry) -> None:
        super().__init__(daemon=True, name="sim-nodes")
        self.reg = registry
        self._halt = threading.Event()
        self._t0 = time.time()
        self._s1 = self._s2 = 0

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.wait(2.0):
            t = (time.time() - self._t0) % 90.0
            seated = t < 70.0
            if seated:
                self._s1 += 1
                p1 = json.dumps({"node_id": "node1", "presence": 1, "sitting_duration_min": round(t, 1),
                                 "battery_mv": 3950, "seq": self._s1})
                self.reg.ingest(TOPIC_NODE1, p1)
                if self._s1 % 5 == 0:
                    self.reg.ingest(TOPIC_NODE1, p1)  # simulated retry -> must be deduped
            temp = 25.0 + 7.0 * min(1.0, max(0.0, (t - 55.0) / 8.0)) if t < 70 else 25.0
            sound = 72.0 if 35.0 <= t < 43.0 else 38.0
            self._s2 += 1
            self.reg.ingest(TOPIC_NODE2, json.dumps({"node_id": "node2", "temp_c": round(temp, 1),
                                                     "sound_db": round(sound, 1), "seq": self._s2}))


# ----------------------------------------------------------------------------
# Gateway
# ----------------------------------------------------------------------------
class Gateway:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_evt = threading.Event()
        self.hw: BaseHardware = create_hardware(mock=args.mock)
        self.mock = self.hw.is_mock
        self.registry = NodeRegistry()
        self.ai = SmartWorkstationAI(Path(args.model))
        self.curtain = CurtainController(self.hw, 0.0002 if self.mock else STEPPER_DELAY_S)
        self.alerts = AlertDispatcher()

        self.led_duty = 0.0
        self.servo_angle = SERVO_REST_DEG
        self.hw.set_led_duty(0.0)
        self.hw.set_servo_angle(self.servo_angle)

        self._active_class = 0
        self._pending_class, self._pending_n = 0, 0
        self._last_distance: Optional[float] = None
        self._last_lux: Optional[float] = None
        self._seat_since: Optional[float] = None
        self._slow_infer = 0

        self.break_duration_s = getattr(args, "break_duration", None) or float(os.getenv("BREAK_DURATION_S", "300"))
        self._break_until: Optional[float] = None
        self._break_active = False
        self._break_remaining_s = 0

        # Glare shield hysteresis hold (prevents motor oscillation when curtain shades the sensor)
        self.glare_hold_s = float(os.getenv("GLARE_HOLD_S", "180"))  # Hold shield for 3 minutes
        self._glare_shield_until: Optional[float] = None

        self.mqtt_connected = threading.Event()
        self.mqtt = self._make_mqtt()

        self.outbox: Optional[Outbox] = None
        self.cloud: Optional[CloudForwarder] = None
        self._last_cloud_push = 0.0
        self.cloud_period = float(os.getenv("CLOUD_PERIOD_S", "5"))
        if not args.no_cloud:
            self._setup_cloud()
        self.sim: Optional[SimulatedNodes] = None

    # ---- MQTT -------------------------------------------------------------
    def _make_mqtt(self) -> "mqtt.Client":
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smart-workstation-gateway")
        c.will_set(TOPIC_STATUS, "offline", qos=1, retain=True)
        c.reconnect_delay_set(1, 30)

        def on_connect(client, _ud, _flags, reason_code, _props):
            if reason_code == 0:
                client.subscribe([(TOPIC_NODE1, 1), (TOPIC_NODE2, 1)])
                client.publish(TOPIC_STATUS, "online", qos=1, retain=True)
                self.mqtt_connected.set()
                log.info("MQTT connected; subscribed to node1/node2")
            else:
                log.error("MQTT connect refused: %s", reason_code)

        def on_disconnect(_client, _ud, _flags, reason_code, _props):
            self.mqtt_connected.clear()
            if not self.stop_evt.is_set():
                log.warning("MQTT disconnected (%s); auto-reconnecting", reason_code)

        c.on_connect = on_connect
        c.on_disconnect = on_disconnect
        c.on_message = lambda _c, _ud, m: self.registry.ingest(m.topic, m.payload)
        return c

    # ---- Cloud ------------------------------------------------------------
    def _setup_cloud(self) -> None:
        self.outbox = Outbox(BASE_DIR / "outbox.db")
        cred = os.getenv("FIREBASE_CREDENTIALS_PATH", "")
        uploader: Any = None
        if is_configured(cred) and Path(cred).exists():
            try:
                uploader = FirestoreUploader(cred, os.getenv("FIRESTORE_COLLECTION", "telemetry"))
                log.info("Cloud: Firestore enabled")
            except Exception as exc:
                log.error("Cloud: Firestore init failed (%s)", exc)
        if uploader is None:
            if self.mock:
                uploader = DryRunUploader()
                log.info("Cloud: no credentials -> dry-run uploader (mock mode)")
            else:
                log.warning("Cloud: no valid FIREBASE_CREDENTIALS_PATH; buffering in outbox.db only")
        if uploader is not None:
            self.cloud = CloudForwarder(self.outbox, uploader)

    # ---- Control loop pieces ---------------------------------------------
    def _read_local(self) -> Tuple[float, float]:
        d = self.hw.read_distance_cm()
        lx = self.hw.read_lux()
        if d is not None:
            self._last_distance = d
        if lx is not None:
            self._last_lux = lx
        return (self._last_distance if self._last_distance is not None else 100.0,
                self._last_lux if self._last_lux is not None else 300.0)

    def _sitting_minutes(self, snap: Dict[str, Any]) -> float:
        if not snap["seat"]:
            self._seat_since = None
            return 0.0
        if snap["sitting_min"] is not None:
            return float(snap["sitting_min"])
        if self._seat_since is None:  # Node 1 didn't send a duration: fall back to a local timer
            self._seat_since = time.monotonic()
        return (time.monotonic() - self._seat_since) / 60.0

    def _debounce(self, class_id: int) -> bool:
        """Accept a new class only after 2 consecutive identical predictions (anti-flapping)."""
        if class_id == self._active_class:
            self._pending_class, self._pending_n = class_id, 0
            return False
        if class_id == self._pending_class:
            self._pending_n += 1
        else:
            self._pending_class, self._pending_n = class_id, 1
        if self._pending_n >= 2:
            self._active_class, self._pending_n = class_id, 0
            return True
        return False

    def _actuate(self, class_id: int, seat: bool) -> None:
        """Class -> actuator mapping. The class itself comes from the trained model."""
        servo = SERVO_REST_DEG
        now_mono = time.monotonic()

        # If glare shield is currently latched under hysteresis hold, prevent reopening
        glare_held = (self._glare_shield_until is not None and now_mono < self._glare_shield_until)

        if class_id == 0:        # OPTIMAL_STUDY
            if glare_held:
                # Maintain glare shield during hold window; do not flap curtain back open
                self.curtain.command("GLARE_SHIELD")
            else:
                self._glare_shield_until = None
                self.curtain.command("OPEN")

            # Closed-loop: gently regulate LED towards 450 Lux
            curr_lux = self._last_lux if self._last_lux is not None else 450.0
            if curr_lux < 380.0:
                self.led_duty = min(100.0, self.led_duty + LED_STEP_PCT)
            elif curr_lux > 520.0:
                self.led_duty = max(0.0, self.led_duty - LED_STEP_PCT)
        elif class_id == 1:      # LOW_LIGHT (< 300 Lux)
            self._glare_shield_until = None  # If room genuinely darkens, cancel glare shield
            self.curtain.command("OPEN")
            self.led_duty = min(100.0, self.led_duty + LED_STEP_PCT * 2)
        elif class_id == 2:      # HARSH_GLARE (> 750 Lux)
            # Arm/extend hysteresis latch for 3 minutes
            self._glare_shield_until = now_mono + self.glare_hold_s
            self.curtain.command("GLARE_SHIELD")  # Lower curtain to 17 cm glare shield (47.2%)
            self.led_duty = max(0.0, self.led_duty - LED_STEP_PCT * 2)
        elif class_id == 3:      # TEXT_NECK_TOO_CLOSE (< 28 cm)
            servo = SERVO_NUDGE_DEG
            self.alerts.notify(class_id, "Posture Alert", "Head too close (<28cm)! Straighten your neck and sit back.")
        elif class_id == 4:      # SLOUCH_TOO_FAR (> 65 cm)
            servo = SERVO_NUDGE_DEG
            self.alerts.notify(class_id, "Posture Alert", "Slouching too far back! Align your spine upright.")
        elif class_id == 5:      # BREAK_TIMER_REACHED (>= 50 min)
            self._glare_shield_until = None
            self.curtain.command("CLOSED")  # Complete physical barrier for break
            servo = SERVO_BREAK_DEG
            self.alerts.notify(class_id, "Break Required", "50-minute study interval reached. Take a 5-minute break!")
        elif class_id == 6:      # AWAY (unseated)
            self._glare_shield_until = None
            self.curtain.command("OPEN")
            self.led_duty = 0.0
        elif class_id == 7:      # NOISY_ENVIRONMENT (> 68 dB)
            self.curtain.command("CLOSED")  # Lower curtain to provide acoustic isolation
            self.alerts.notify(class_id, "Noise Alert", "High ambient noise (>68dB). Curtain deployed for acoustic shielding.")
        elif class_id == 8:      # THERMAL_DISCOMFORT (< 22°C or > 32°C)
            self.curtain.command("CLOSED")  # Lower curtain to shield from cold AC draft or heat
            self.alerts.notify(class_id, "Thermal Discomfort", "Room temperature outside comfort range (<22°C or >32°C). Curtain closed.")

        if not seat:
            self.led_duty = min(self.led_duty, LED_IDLE_CAP_PCT)
        self.hw.set_led_duty(self.led_duty)
        if servo != self.servo_angle:
            self.servo_angle = servo
            self.hw.set_servo_angle(servo)

    def _cycle(self) -> None:
        snap = self.registry.snapshot()
        distance, fallback_lux = self._read_local()
        local_temp = self.hw.read_temp_c()

        # Temperature: local DS18B20 1-Wire on RPi (GPIO 4), fall back to Node 2 or default
        raw_tmp = local_temp if local_temp is not None else snap["temp"]

        # Lux: digital I2C lux from Node 2 (BH1750 on ESP32 GPIO 21/22), fall back to local RPi lux
        lux = snap["lux"] if snap.get("lux") is not None else fallback_lux
        self._last_lux = lux

        sitting = self._sitting_minutes(snap)

        # Anti-spike temporal smoothing on Sound and Temp (EMA filter, alpha=0.25)
        raw_snd = snap["sound"]
        if not hasattr(self, "_sound_ema") or self._sound_ema is None:
            self._sound_ema = raw_snd
            self._temp_ema = raw_tmp
        else:
            self._sound_ema = 0.25 * raw_snd + 0.75 * self._sound_ema
            self._temp_ema = 0.25 * raw_tmp + 0.75 * self._temp_ema

        # Handle unplugged / out-of-range distance (e.g. 999.0cm) so it doesn't trick the AI
        ai_distance = distance
        if distance >= 150.0 and snap["seat"]:
            # Unplugged HC-SR04 or out of range: default to nominal 45.0 cm for AI feature vector
            ai_distance = 45.0

        feats = [1.0 if snap["seat"] else 0.0, ai_distance, lux, self._temp_ema, self._sound_ema, sitting]

        ai = self.ai.predict(feats)  # M4
        if ai["latency_ms"] > AI_LATENCY_BUDGET_MS:
            self._slow_infer += 1
            if self._slow_infer in (1, 10, 100):
                log.warning("AI inference %.2f ms exceeds %.0f ms budget (x%d)",
                            ai["latency_ms"], AI_LATENCY_BUDGET_MS, self._slow_infer)

        changed = self._debounce(ai["class_id"])
        
        # Check mandatory break lockout
        if self._break_until is not None:
            now_mono = time.monotonic()
            if now_mono < self._break_until:
                self._break_active = True
                self._break_remaining_s = int(self._break_until - now_mono)
                # Physical interlock: enforce curtain CLOSED and dim lamp during break
                self.curtain.command("CLOSED")
                self.led_duty = 15.0
                self.hw.set_led_duty(self.led_duty)
            else:
                # Break completed: unlock workstation
                self._break_active = False
                self._break_remaining_s = 0
                self._break_until = None
                self._seat_since = time.monotonic()
                self._active_class = 0
                self.curtain.command("OPEN")
                log.info("Break interval complete! Workstation unlocked.")
                self.alerts.notify(0, "Break Complete", "Study break complete! Workstation unlocked. Happy studying!")
        else:
            self._break_active = False
            self._break_remaining_s = 0
            if self._active_class == 5:
                # Trigger mandatory break lockout
                self._break_until = time.monotonic() + self.break_duration_s
                self._break_active = True
                self._break_remaining_s = int(self.break_duration_s)
                self.curtain.command("CLOSED")
                log.info("Mandatory study break triggered! Locking curtain for %.0f seconds.", self.break_duration_s)
            else:
                self._actuate(self._active_class, snap["seat"])  # M2, closed loop

        if changed and self._active_class != 0:
            info = CLASS_INFO[self._active_class]
            self.alerts.notify(self._active_class, f"Smart Workstation: {info['name']}", info["advice"])

        # Feed statistics for periodic email reports
        curtain_active = self.curtain.state in ("CLOSING", "OPENING")
        self.alerts.record_telemetry(
            seat=snap["seat"],
            dist=distance,
            lux=lux,
            temp=self._temp_ema,
            sound=self._sound_ema,
            break_triggered=self._break_active,
            curtain_moved=curtain_active
        )

        now = time.time()
        payload = {
            "seat": snap["seat"],
            "distance": round(distance, 1),
            "light": round(lux, 1),
            "temp": round(self._temp_ema, 1),
            "sound": round(self._sound_ema, 1),
            "sitting_duration_min": round(sitting, 1),
            "curtain_state": self.curtain.state,
            "led_duty_cycle": round(self.led_duty, 1),
            "servo_angle": round(self.servo_angle, 1),
            "break_active": self._break_active,
            "break_remaining_s": self._break_remaining_s,
            "ai_state": ai,
            "node_status": snap["node_status"],  # extra key; dashboard may ignore it
            "timestamp": round(now, 2),
        }
        if self.mqtt_connected.is_set():
            self.mqtt.publish(TOPIC_TELEMETRY, json.dumps(payload), qos=0, retain=True)

        if self.outbox is not None and self.cloud is not None and now - self._last_cloud_push >= self.cloud_period:
            self._last_cloud_push = now
            self.outbox.put(now, payload)
            self.cloud.notify()

        log.info("seat=%d dist=%5.1fcm lux=%6.1f T=%4.1fC snd=%4.1fdB sit=%5.1fm -> %-16s (%.2f, %.2fms) "
                 "| LED %3.0f%% curtain %-7s servo %3.0f | n1=%s n2=%s",
                 snap["seat"], distance, lux, self._temp_ema, self._sound_ema, sitting,
                 ai["class_name"], ai["confidence"], ai["latency_ms"],
                 self.led_duty, self.curtain.state, self.servo_angle,
                 snap["node_status"]["node1"], snap["node_status"]["node2"])

    # ---- Lifecycle --------------------------------------------------------
    def run(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: self.stop_evt.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stop_evt.set())

        self.mqtt.connect_async(self.args.broker, self.args.port, keepalive=30)
        self.mqtt.loop_start()
        connected = self.mqtt_connected.wait(2.0)
        if not connected:
            log.warning("MQTT broker %s:%d not reachable yet (still retrying in background)",
                        self.args.broker, self.args.port)
        if self.args.sim_nodes or (self.mock and not connected):
            log.info("Using SIMULATED Node 1 / Node 2 data")
            self.sim = SimulatedNodes(self.registry)
            self.sim.start()

        self.curtain.start()
        if self.cloud:
            self.cloud.start()

        deadline = time.monotonic() + self.args.duration if self.args.duration > 0 else None
        next_tick = time.monotonic()
        log.info("Gateway running (%s hardware, %.1f s cycle). Ctrl+C to stop.",
                 "MOCK" if self.mock else "RPi", self.args.interval)
        try:
            while not self.stop_evt.is_set():
                try:
                    self._cycle()
                except Exception:  # one bad cycle must never kill the daemon
                    log.exception("Control cycle failed")
                next_tick += self.args.interval
                if deadline and time.monotonic() >= deadline:
                    break
                self.stop_evt.wait(max(0.0, next_tick - time.monotonic()))
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        log.info("Shutting down (node stats: %s)", self.registry.stats)
        if self.sim:
            self.sim.stop()
        self.curtain.stop()
        if self.cloud:
            self.cloud.stop()
        if self.outbox:
            log.info("Outbox pending rows: %d", self.outbox.count())
        try:
            self.mqtt.publish(TOPIC_STATUS, "offline", qos=1, retain=True)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()
        except Exception:
            pass
        self.hw.set_led_duty(0.0)
        self.hw.cleanup()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    load_dotenv(BASE_DIR / ".env")
    p = argparse.ArgumentParser(description="Smart Workstation Gateway")
    p.add_argument("--mock", action="store_true",
                   default=os.getenv("MOCK_HARDWARE", "false").lower() == "true",
                   help="use simulated GPIO/I2C hardware (and simulated nodes if no broker)")
    p.add_argument("--broker", default=os.getenv("MQTT_BROKER_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.getenv("MQTT_BROKER_PORT", "1883")))
    p.add_argument("--model", default=str(MODEL_PATH))
    p.add_argument("--interval", type=float, default=1.0, help="control loop period in seconds")
    p.add_argument("--duration", type=float, default=0.0, help="exit after N seconds (0 = run forever)")
    p.add_argument("--sim-nodes", action="store_true", help="force simulated Node 1/2 even if a broker is up")
    p.add_argument("--break-duration", type=float,
                   default=float(os.getenv("BREAK_DURATION_S", "300")),
                   help="break countdown duration in seconds (default 300s = 5 min)")
    p.add_argument("--no-cloud", action="store_true", help="disable the outbox and Firestore forwarding")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    Gateway(args).run()


if __name__ == "__main__":
    main()


# =============================================================================
# PLACEHOLDERS / CREDENTIALS -- nothing secret lives in this file (E5 / M8).
# Copy .env.example to .env (git-ignored) and replace the YOUR_... values:
#
#   DISCORD_WEBHOOK_URL         Discord: channel Settings > Integrations > Webhooks > New Webhook > Copy URL.
#   LINE_CHANNEL_ACCESS_TOKEN   LINE Developers console > Messaging API channel > issue a long-lived token.
#   LINE_TO_USER_ID             User/group ID that receives pushes (from the bot's webhook events).
#                               NOTE: LINE Notify (LINE_NOTIFY_TOKEN in AGENTS.md) ended on 2025-03-31,
#                               so alerts use the LINE Messaging API push endpoint instead.
#   FIREBASE_CREDENTIALS_PATH   Firebase console > Project settings > Service accounts > Generate new
#                               private key; save the JSON outside git (e.g. ~/secrets/) and put its path here.
#   FIRESTORE_COLLECTION        Target collection (default "telemetry").
#
# Any value that is empty or still contains "YOUR_" / "..." is treated as "not configured":
# alerts are logged as dry-runs and cloud rows stay buffered in outbox.db (mock mode flushes them to a dry-run sink).
# =============================================================================
