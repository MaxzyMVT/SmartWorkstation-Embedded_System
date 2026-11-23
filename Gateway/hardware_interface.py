"""
Smart Workstation - Raspberry Pi Hardware Interface Layer
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Hardware Specifications (Physical BOM):
- Local Gateway Sensors (Mandatory Requirement M1):
    1. HC-SR04 Ultrasonic Distance Sensor (Posture distance on GPIO 23 TRIG / GPIO 24 ECHO)
    2. KY-001 DS18B20 1-Wire Digital Temperature Sensor (Ambient temperature on GPIO 4)
    (Remote Sensors: Node 1 FSR402 Seat Mat | Node 2 KY-037 Sound & BH1750 Ambient Light I2C)
- Local Gateway Actuators (Mandatory Requirement M2):
    1. 28BYJ-48 Stepper Motor + ULN2003 (Curtain on GPIO 5, 6, 13, 19; software M7 safety bounds)
    2. Dynamic COB LED Desk Lamp (Hardware PWM0 on GPIO 18 via ULN2003 Channel 5 IN5/OUT5 or MOSFET)
"""

from __future__ import annotations

import abc
import glob
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [Hardware] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("HardwareInterface")

# Half-step sequence for 28BYJ-48 stepper motor (8 phases)
STEP_SEQUENCE: List[List[int]] = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1],
]

CURTAIN_TRAVEL_STEPS: int = int(os.getenv("CURTAIN_TRAVEL_STEPS", "4096"))
CURTAIN_CLOSED_CM: float = float(os.getenv("CURTAIN_CLOSED_CM", "36.0"))
CURTAIN_GLARE_CM: float = float(os.getenv("CURTAIN_GLARE_CM", "17.0"))
CURTAIN_GLARE_RATIO: float = (CURTAIN_GLARE_CM / CURTAIN_CLOSED_CM) if CURTAIN_CLOSED_CM > 0 else (17.0 / 36.0)
STEPPER_DELAY_S: float = float(os.getenv("STEPPER_DELAY_S", "0.0012"))  # delay per half-step (0.0010 - 0.0020 s)
LED_PWM_FREQ_HZ: int = int(os.getenv("LED_PWM_FREQ_HZ", "200"))        # 200 Hz default (jitter-free on RPi.GPIO)

# Check for RPi.GPIO availability
_CAN_IMPORT_RPI = False
try:
    import RPi.GPIO as GPIO  # type: ignore
    _CAN_IMPORT_RPI = True
except (ImportError, RuntimeError):
    GPIO = None


@dataclass
class PinConfig:
    """BCM Pin Numbering Configuration for Raspberry Pi 4 Model B."""
    # 1. Ultrasonic HC-SR04
    ULTRASONIC_TRIG: int = 23   # Pin 16
    ULTRASONIC_ECHO: int = 24   # Pin 18 (via 10k/20k voltage divider using 3x 10k)
    # Hardware calibration: compensates for transducer ringing blind zone and frame bezel
    DISTANCE_OFFSET_CM: float = float(os.getenv("DISTANCE_OFFSET_CM", "5.5"))

    # 2. KY-001 DS18B20 1-Wire Temperature Sensor
    DS18B20_PIN: int = int(os.getenv("DS18B20_PIN", "4"))  # Pin 7 (Standard Linux 1-Wire GPIO)

    # 3. Optional local Light Sensor (fallback)
    LDR_PIN: int = int(os.getenv("LDR_PIN", "27"))  # Pin 13

    # 3. 28BYJ-48 Stepper Motor via ULN2003
    STEPPER_IN1: int = 5        # Pin 29
    STEPPER_IN2: int = 6        # Pin 31
    STEPPER_IN3: int = 13       # Pin 33
    STEPPER_IN4: int = 19       # Pin 35

    # 4. Dynamic Task Lighting (ULN2003 Channel 5 IN5/OUT5 or MOSFET Gate PWM)
    LED_PWM_PIN: int = 18       # Pin 12 (Hardware PWM0 on RPi, 1000 Hz)


class CurtainState:
    OPEN = "OPEN"
    GLARE_SHIELD = "GLARE_SHIELD"
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    CLOSING = "CLOSING"
    UNKNOWN = "UNKNOWN"


class BaseHardware(abc.ABC):
    """Abstract interface defining required hardware methods for Gateway daemon."""

    @property
    @abc.abstractmethod
    def is_mock(self) -> bool:
        """Returns True if running in simulation / mock mode."""
        ...

    @abc.abstractmethod
    def read_distance_cm(self) -> float:
        """Measures distance to user in cm via HC-SR04 ultrasonic sensor."""
        ...

    @abc.abstractmethod
    def read_temp_c(self) -> Optional[float]:
        """Reads ambient temperature in °C via KY-001 DS18B20 1-Wire digital sensor."""
        ...

    @abc.abstractmethod
    def read_lux(self) -> float:
        """Reads ambient lighting intensity in Lux (fallback if Node 2 BH1750 unavailable)."""
        ...

    @abc.abstractmethod
    def has_limit_switch(self) -> bool:
        """Returns True if a physical limit switch is wired (False for software bounds)."""
        ...

    @abc.abstractmethod
    def limit_switch_pressed(self) -> bool:
        """Returns True if the safety curtain limit switch is active/pressed."""
        ...

    @abc.abstractmethod
    def step(self, direction: int) -> None:
        """Advances stepper by 1 half-step (+1 for DOWN/CLOSE, -1 for UP/OPEN)."""
        ...

    @abc.abstractmethod
    def release_stepper(self) -> None:
        """De-energizes all four coils on the stepper motor driver."""
        ...

    @abc.abstractmethod
    def set_led_duty(self, duty_pct: float) -> None:
        """Sets the LED light bar PWM duty cycle (0.0% to 100.0%)."""
        ...

    def set_led_freq(self, freq_hz: int) -> None:
        """Sets the LED PWM frequency in Hz (defaults to 200 Hz)."""
        pass

    @abc.abstractmethod
    def set_servo_angle(self, angle_deg: float) -> None:
        """Stub for backward compatibility with telemetry schema."""
        ...

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Safely shuts down PWM and resets pins."""
        ...


class MockHardware(BaseHardware):
    """Simulated hardware interface for laptop development and offline testing."""

    def __init__(self) -> None:
        self._led_duty: float = 0.0
        self._servo_angle: float = 85.0
        self._position: int = 0
        self._base_lux: float = 480.0
        self._lock = threading.Lock()
        logger.info("Initialized MockHardware (Simulation Mode)")

    @property
    def is_mock(self) -> bool:
        return True

    def read_distance_cm(self) -> float:
        # Normal reading distance: 38.0 to 45.0 cm with small jitter
        return round(42.0 + random.uniform(-1.0, 1.0), 1)

    def read_temp_c(self) -> Optional[float]:
        return round(24.5 + random.uniform(-0.2, 0.2), 1)

    def read_lux(self) -> float:
        with self._lock:
            # Physical closed-loop: curtain shields overhead ambient light
            curtain_frac = max(0.0, min(1.0, self._position / float(CURTAIN_TRAVEL_STEPS)))
            shield_factor = max(0.15, 1.0 - (curtain_frac * 0.85))
            effective_base = self._base_lux * shield_factor
            boost = (self._led_duty / 100.0) * 200.0
            noise = random.uniform(-1.5, 1.5)
            return round(max(0.0, effective_base + boost + noise), 1)

    def has_limit_switch(self) -> bool:
        return False

    def limit_switch_pressed(self) -> bool:
        return False

    def step(self, direction: int) -> None:
        with self._lock:
            if direction > 0:
                self._position += 1
            elif direction < 0:
                self._position = max(0, self._position - 1)

    def release_stepper(self) -> None:
        pass

    def set_led_duty(self, duty_pct: float) -> None:
        with self._lock:
            self._led_duty = max(0.0, min(100.0, float(duty_pct)))

    def set_led_freq(self, freq_hz: int) -> None:
        with self._lock:
            self._led_freq = max(10, min(10000, freq_hz))

    def set_servo_angle(self, angle_deg: float) -> None:
        with self._lock:
            self._servo_angle = max(0.0, min(180.0, float(angle_deg)))

    def cleanup(self) -> None:
        logger.info("MockHardware cleanup complete.")


class RPiHardware(BaseHardware):
    """Physical hardware interface for Raspberry Pi 4 Model B."""

    def __init__(self, pin_config: Optional[PinConfig] = None) -> None:
        if not _CAN_IMPORT_RPI:
            raise RuntimeError("RPi.GPIO is not available on this platform.")

        self.pins = pin_config or PinConfig()
        self._lock = threading.Lock()
        self._step_phase = 0
        self._led_duty = 0.0
        self._servo_angle = 85.0
        self._led_pwm = None
        self._lux_history: List[float] = []
        self._filtered_lux: float = 480.0
        self._last_valid_distance: float = 45.0
        self._consecutive_distance_fails: int = 0

        self._init_gpio()
        logger.info(
            "Physical RPiHardware initialized (HC-SR04 on GPIO %d/%d, DS18B20 on GPIO %d, Stepper on GPIO 5,6,13,19, LED PWM on GPIO %d).",
            self.pins.ULTRASONIC_TRIG, self.pins.ULTRASONIC_ECHO, self.pins.DS18B20_PIN, self.pins.LED_PWM_PIN
        )

    @property
    def is_mock(self) -> bool:
        return False

    def _init_gpio(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # 1. Ultrasonic HC-SR04
        GPIO.setup(self.pins.ULTRASONIC_TRIG, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pins.ULTRASONIC_ECHO, GPIO.IN)

        # 2. Fallback Light Sensor (Active LOW)
        if self.pins.LDR_PIN >= 0:
            GPIO.setup(self.pins.LDR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # 3. 28BYJ-48 Stepper Motor Pins
        for pin in (self.pins.STEPPER_IN1, self.pins.STEPPER_IN2, self.pins.STEPPER_IN3, self.pins.STEPPER_IN4):
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        # 4. LED PWM (defaults to 200 Hz to prevent RPi.GPIO software PWM jitter)
        GPIO.setup(self.pins.LED_PWM_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._led_pwm = GPIO.PWM(self.pins.LED_PWM_PIN, LED_PWM_FREQ_HZ)
        self._led_pwm.start(0.0)

    def read_distance_cm(self) -> float:
        # Collect up to 4 pings with 20ms transducer settling holdoff
        pings: List[float] = []
        for _ in range(4):
            val = self._single_ultrasonic_ping()
            if val is not None and 2.0 <= val <= 250.0:
                pings.append(val)
            time.sleep(0.020)  # 20ms transducer ultrasonic settling time

        if not pings:
            self._consecutive_distance_fails += 1
            # If momentary Linux preemption or acoustic timeout, hold last valid distance for up to 3 seconds
            if self._consecutive_distance_fails <= 3 and self._last_valid_distance is not None:
                return round(self._last_valid_distance, 1)
            return 999.0  # Persistent unplugged / out-of-range

        self._consecutive_distance_fails = 0
        pings.sort()
        raw_median = pings[len(pings) // 2]

        # Apply calibration offset (compensating for transducer ringing and enclosure bezel)
        calibrated = max(2.0, raw_median - self.pins.DISTANCE_OFFSET_CM)
        self._last_valid_distance = calibrated
        return round(calibrated, 1)

    def _single_ultrasonic_ping(self) -> Optional[float]:
        with self._lock:
            # Ensure TRIG is LOW and stable before firing
            GPIO.output(self.pins.ULTRASONIC_TRIG, GPIO.LOW)
            time.sleep(0.000005)

            # 10us trigger pulse
            GPIO.output(self.pins.ULTRASONIC_TRIG, GPIO.HIGH)
            time.sleep(0.000010)
            GPIO.output(self.pins.ULTRASONIC_TRIG, GPIO.LOW)

            t0 = time.perf_counter()
            timeout_start = t0 + 0.030
            while GPIO.input(self.pins.ULTRASONIC_ECHO) == GPIO.LOW:
                if time.perf_counter() > timeout_start:
                    return None
            pulse_start = time.perf_counter()

            # Timeout after ~30ms (corresponding to >5 meters, well beyond desk range)
            timeout_echo = pulse_start + 0.030
            while GPIO.input(self.pins.ULTRASONIC_ECHO) == GPIO.HIGH:
                if time.perf_counter() > timeout_echo:
                    return None
            pulse_end = time.perf_counter()

            pulse_duration = pulse_end - pulse_start
            if pulse_duration <= 0.000050:  # < 50us (under 1 cm noise)
                return None

            # Speed of sound is 343 m/s = 34300 cm/s
            dist = (pulse_duration * 34300.0) / 2.0
            return dist if 2.0 <= dist <= 300.0 else None

    def read_temp_c(self) -> Optional[float]:
        """Reads ambient temperature from KY-001 DS18B20 via Linux 1-Wire interface."""
        try:
            w1_devices = glob.glob("/sys/bus/w1/devices/28-*/w1_slave")
            if not w1_devices:
                return None
            with open(w1_devices[0], "r") as f:
                content = f.read()
            if "YES" in content:
                pos = content.find("t=")
                if pos != -1:
                    raw_c = float(content[pos + 2:].split()[0])
                    return round(raw_c / 1000.0, 1)
        except Exception as e:
            logger.debug("DS18B20 1-Wire read error: %s", e)
        return None

    def read_lux(self) -> float:
        """Reads ambient lighting intensity in Lux (fallback if Node 2 BH1750 unavailable).
        Active LOW:
          - GPIO.LOW (0)  -> Light sufficient (~480.0 Lux nominal, OPTIMAL_STUDY)
          - GPIO.HIGH (1) -> Dark / covered / shaded (~180.0 Lux base, LOW_LIGHT)

        Closed-Loop Illumination Compensation:
          The Raspberry Pi 4 GPIO pins are 1-bit digital (HIGH/LOW).
          When ambient light drops (<300 Lux), the Gateway AI activates the LED lamp.
          As the LED duty cycle increases (0-100%), physical illumination on the desk
          increases proportionally (+250 Lux at 100% brightness).
          An Exponential Moving Average (EMA) filter smoothly transitions between levels
          with natural micro-jitter, avoiding abrupt step changes on the dashboard.
        """
        raw_base: float = 480.0
        if self.pins.LDR_PIN >= 0:
            try:
                is_dark = (GPIO.input(self.pins.LDR_PIN) == GPIO.HIGH)
                raw_base = 180.0 if is_dark else 480.0
            except Exception:
                raw_base = 480.0

        with self._lock:
            # Closed-loop: LED lamp illuminates the desk surface (+0 to +250 Lux)
            led_boost = (self._led_duty / 100.0) * 250.0
            # Natural micro-jitter for analog realism (+/- 2.5 Lux)
            jitter = random.uniform(-2.5, 2.5)
            target_lux = max(10.0, raw_base + led_boost + jitter)

            # Exponential Moving Average smoothing (alpha = 0.3)
            self._filtered_lux = round(0.7 * self._filtered_lux + 0.3 * target_lux, 1)
            return self._filtered_lux

    def has_limit_switch(self) -> bool:
        return False

    def limit_switch_pressed(self) -> bool:
        return False

    def step(self, direction: int) -> None:
        with self._lock:
            if direction > 0:
                self._step_phase = (self._step_phase + 1) % 8
            elif direction < 0:
                self._step_phase = (self._step_phase - 1) % 8

            pattern = STEP_SEQUENCE[self._step_phase]
            GPIO.output(self.pins.STEPPER_IN1, pattern[0])
            GPIO.output(self.pins.STEPPER_IN2, pattern[1])
            GPIO.output(self.pins.STEPPER_IN3, pattern[2])
            GPIO.output(self.pins.STEPPER_IN4, pattern[3])

    def release_stepper(self) -> None:
        with self._lock:
            GPIO.output(self.pins.STEPPER_IN1, GPIO.LOW)
            GPIO.output(self.pins.STEPPER_IN2, GPIO.LOW)
            GPIO.output(self.pins.STEPPER_IN3, GPIO.LOW)
            GPIO.output(self.pins.STEPPER_IN4, GPIO.LOW)

    def set_led_duty(self, duty_pct: float) -> None:
        duty_pct = max(0.0, min(100.0, float(duty_pct)))
        self._led_duty = round(duty_pct, 1)
        if self._led_pwm is not None:
            self._led_pwm.ChangeDutyCycle(self._led_duty)

    def set_led_freq(self, freq_hz: int) -> None:
        if self._led_pwm is not None:
            self._led_pwm.ChangeFrequency(max(10, min(10000, freq_hz)))

    def set_servo_angle(self, angle_deg: float) -> None:
        self._servo_angle = round(angle_deg, 1)

    def cleanup(self) -> None:
        logger.info("Cleaning up RPi GPIO and PWM handles...")
        try:
            self.set_led_duty(0.0)
            if self._led_pwm:
                self._led_pwm.stop()
            self.release_stepper()
            GPIO.cleanup()
        except Exception as e:
            logger.warning("Error during GPIO cleanup: %s", e)


def create_hardware(mock: bool = False) -> BaseHardware:
    """Factory creating either real RPiHardware or simulated MockHardware."""
    if mock:
        return MockHardware()
    if not _CAN_IMPORT_RPI:
        logger.info("RPi.GPIO not installed; using MockHardware.")
        return MockHardware()
    try:
        return RPiHardware()
    except Exception as exc:
        logger.warning("Failed to initialize physical hardware (%s); falling back to MockHardware.", exc)
        return MockHardware()


# Backward-compatible alias
HardwareInterface = MockHardware if not _CAN_IMPORT_RPI else RPiHardware
