"""
Smart Workstation - Raspberry Pi Hardware Interface Layer
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Hardware Specifications (Physical BOM):
- Local Sensors (Mandatory Requirement M1):
    1. HC-SR04 Ultrasonic Distance Sensor (Posture distance on GPIO 23 TRIG / GPIO 24 ECHO)
    2. KY-018 LDR Photoresistor Module (Ambient reading surface lighting on GPIO 27)
    (Remote Sensors: Node 1 FSR402 Seat Mat | Node 2 KY-037 Sound & KY-001 DS18B20 Temp)
- Local Actuators (Mandatory Requirement M2):
    1. 28BYJ-48 Stepper Motor + ULN2003 (Curtain on GPIO 5, 6, 13, 19; software M7 safety bounds)
    2. Dynamic LED Desk Lamp (Hardware PWM0 on GPIO 18 via N-Channel MOSFET)
"""

from __future__ import annotations

import abc
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
    ULTRASONIC_ECHO: int = 24   # Pin 18 (via 1k/2k voltage divider)

    # 2. KY-018 LDR Photoresistor Module
    LDR_PIN: int = int(os.getenv("LDR_PIN", "27"))  # Pin 13

    # 3. 28BYJ-48 Stepper Motor via ULN2003
    STEPPER_IN1: int = 5        # Pin 29
    STEPPER_IN2: int = 6        # Pin 31
    STEPPER_IN3: int = 13       # Pin 33
    STEPPER_IN4: int = 19       # Pin 35

    # 4. LED Light Bar (MOSFET Gate PWM)
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
    def read_lux(self) -> float:
        """Reads ambient lighting intensity in Lux via KY-018 LDR sensor."""
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

        self._init_gpio()
        logger.info(
            "Physical RPiHardware initialized (HC-SR04 on GPIO %d/%d, KY-018 on GPIO %d, Stepper on GPIO 5,6,13,19, LED PWM on GPIO %d).",
            self.pins.ULTRASONIC_TRIG, self.pins.ULTRASONIC_ECHO, self.pins.LDR_PIN, self.pins.LED_PWM_PIN
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

        # 2. KY-018 LDR Photoresistor Module
        if self.pins.LDR_PIN >= 0:
            GPIO.setup(self.pins.LDR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # 3. 28BYJ-48 Stepper Motor Pins
        for pin in (self.pins.STEPPER_IN1, self.pins.STEPPER_IN2, self.pins.STEPPER_IN3, self.pins.STEPPER_IN4):
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        # 4. LED PWM (1000 Hz)
        GPIO.setup(self.pins.LED_PWM_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._led_pwm = GPIO.PWM(self.pins.LED_PWM_PIN, 1000)
        self._led_pwm.start(0.0)

    def read_distance_cm(self) -> float:
        # Collect 3 pings and use median to reject acoustic multipath spikes
        pings: List[float] = []
        for _ in range(3):
            val = self._single_ultrasonic_ping()
            if val is not None and 2.0 <= val <= 250.0:
                pings.append(val)
            time.sleep(0.005)

        if not pings:
            return 999.0  # Out of range / no object detected
        pings.sort()
        return round(pings[len(pings) // 2], 1)

    def _single_ultrasonic_ping(self) -> Optional[float]:
        with self._lock:
            # 10us trigger pulse
            GPIO.output(self.pins.ULTRASONIC_TRIG, GPIO.HIGH)
            time.sleep(0.00001)
            GPIO.output(self.pins.ULTRASONIC_TRIG, GPIO.LOW)

            timeout_start = time.time() + 0.04
            while GPIO.input(self.pins.ULTRASONIC_ECHO) == GPIO.LOW:
                pulse_start = time.time()
                if pulse_start > timeout_start:
                    return None

            timeout_echo = time.time() + 0.04
            while GPIO.input(self.pins.ULTRASONIC_ECHO) == GPIO.HIGH:
                pulse_end = time.time()
                if pulse_end > timeout_echo:
                    return None

            pulse_duration = pulse_end - pulse_start
            # Speed of sound is 343 m/s = 34300 cm/s
            return (pulse_duration * 34300.0) / 2.0

    def read_lux(self) -> float:
        """Reads ambient lighting intensity via KY-018 LDR module.
        Active LOW:
          - GPIO.LOW (0)  -> Light sufficient (~480 Lux, OPTIMAL_STUDY)
          - GPIO.HIGH (1) -> Dark / covered / shaded (~180 Lux, INSUFFICIENT_LIGHT)
        """
        raw_lux: float = 480.0
        if self.pins.LDR_PIN >= 0:
            try:
                is_dark = (GPIO.input(self.pins.LDR_PIN) == GPIO.HIGH)
                raw_lux = 180.0 if is_dark else 480.0
            except Exception:
                raw_lux = 480.0

        # 3-sample moving median filter to reject shadow spikes and noise
        with self._lock:
            self._lux_history.append(raw_lux)
            if len(self._lux_history) > 3:
                self._lux_history.pop(0)
            sorted_history = sorted(self._lux_history)
            return round(sorted_history[len(sorted_history) // 2], 1)

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
