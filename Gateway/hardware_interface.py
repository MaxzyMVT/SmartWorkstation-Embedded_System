"""
Smart Workstation - Raspberry Pi Hardware Interface Layer
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Hardware Specifications:
- Sensors (Mandatory Requirement M1):
    1. HC-SR04 Ultrasonic Distance Sensor (Face-to-workstation posture measurement)
    2. BH1750 Digital Ambient Light Sensor (Reading surface illumination via I2C)
    3. KW11-3Z Limit Switch / Emergency Stop (Curtain physical boundary safety)
- Actuators (Mandatory Requirement M2):
    1. 28BYJ-48 Stepper Motor + ULN2003 (Motorized Blocker Curtain, half-step drive)
    2. 5V High-CRI LED Bar via N-Channel Logic MOSFET (Closed-loop PWM lighting)
    3. SG90 Micro Servo (Light Bar Tilt Angle Modulation)

Features:
- Dual-mode operation: Real Raspberry Pi GPIO / I2C or Mock Simulation mode.
- Non-blocking safety timeouts and coil de-energizing on stepper idle.
- Median filter on ultrasonic distance to reject acoustic multipath reflections.
"""

from __future__ import annotations

import abc
import logging
import math
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

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

# Check for RPi.GPIO and smbus2 availability
_CAN_IMPORT_RPI = False
_CAN_IMPORT_SMBUS = False

try:
    import RPi.GPIO as GPIO  # type: ignore
    _CAN_IMPORT_RPI = True
except (ImportError, RuntimeError):
    GPIO = None

try:
    from smbus2 import SMBus  # type: ignore
    _CAN_IMPORT_SMBUS = True
except ImportError:
    SMBus = None


@dataclass
class PinConfig:
    """BCM Pin Numbering Configuration for Raspberry Pi 4 Model B."""
    # Ultrasonic HC-SR04
    ULTRASONIC_TRIG: int = 23
    ULTRASONIC_ECHO: int = 24  # 5V echo line stepped down to 3.3V via 1k/2k divider

    # Limit Switch / Emergency Button (KW11-3Z)
    LIMIT_SWITCH: int = 17     # Internal pull-up enabled (active LOW when closed)

    # 28BYJ-48 Stepper Motor via ULN2003
    STEPPER_IN1: int = 5
    STEPPER_IN2: int = 6
    STEPPER_IN3: int = 13
    STEPPER_IN4: int = 19

    # LED Light Bar (MOSFET Gate PWM)
    LED_PWM_PIN: int = 18      # Hardware PWM0 on RPi

    # SG90 Micro Servo (PWM)
    SERVO_PIN: int = 12        # Hardware PWM0 alternate on RPi

    # I2C Configuration for BH1750
    I2C_BUS: int = 1
    BH1750_I2C_ADDR: int = 0x23  # 0x23 (ADDR grounded) or 0x5C (ADDR connected to VCC)


class CurtainState:
    OPEN = "OPEN"
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
        """Reads ambient lighting intensity in Lux via BH1750 sensor."""
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
        """Sets the SG90 micro-servo tilt angle (0.0° to 180.0°)."""
        ...

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Safely shuts down PWM, resets pins, and closes I2C handles."""
        ...


class MockHardware(BaseHardware):
    """Simulated hardware interface for laptop development and offline testing."""

    def __init__(self) -> None:
        self._led_duty: float = 0.0
        self._servo_angle: float = 85.0
        self._position: int = 0
        self._base_lux: float = 300.0
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
            # Ambient light responds in closed-loop to LED PWM duty
            boost = (self._led_duty / 100.0) * 350.0
            noise = random.uniform(-2.0, 2.0)
            return round(max(0.0, self._base_lux + boost + noise), 1)

    def limit_switch_pressed(self) -> bool:
        # Triggered at home (OPEN, position <= 0)
        return self._position <= 0

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
        if not _CAN_IMPORT_RPI or not _CAN_IMPORT_SMBUS:
            raise RuntimeError("RPi.GPIO or smbus2 is not available on this platform.")

        self.pins = pin_config or PinConfig()
        self._lock = threading.Lock()
        self._step_phase = 0
        self._led_duty = 0.0
        self._servo_angle = 85.0

        # Physical hardware handles
        self._led_pwm = None
        self._servo_pwm = None
        self._i2c_bus = None

        self._init_gpio()
        logger.info("Physical RPiHardware initialized successfully.")

    @property
    def is_mock(self) -> bool:
        return False

    def _init_gpio(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # 1. Ultrasonic HC-SR04
        GPIO.setup(self.pins.ULTRASONIC_TRIG, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.pins.ULTRASONIC_ECHO, GPIO.IN)

        # 2. Limit Switch (KW11-3Z) with pull-up resistor
        GPIO.setup(self.pins.LIMIT_SWITCH, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # 3. 28BYJ-48 Stepper Motor Pins
        for pin in (self.pins.STEPPER_IN1, self.pins.STEPPER_IN2, self.pins.STEPPER_IN3, self.pins.STEPPER_IN4):
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

        # 4. LED PWM (1000 Hz)
        GPIO.setup(self.pins.LED_PWM_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._led_pwm = GPIO.PWM(self.pins.LED_PWM_PIN, 1000)
        self._led_pwm.start(0.0)

        # 5. SG90 Servo PWM (50 Hz)
        GPIO.setup(self.pins.SERVO_PIN, GPIO.OUT, initial=GPIO.LOW)
        self._servo_pwm = GPIO.PWM(self.pins.SERVO_PIN, 50)
        self._servo_pwm.start(0)

        # 6. BH1750 I2C
        try:
            self._i2c_bus = SMBus(self.pins.I2C_BUS)
            # Power on BH1750
            self._i2c_bus.write_byte(self.pins.BH1750_I2C_ADDR, 0x01)
        except Exception as e:
            logger.warning("Failed to initialize BH1750 on I2C bus %d: %s", self.pins.I2C_BUS, e)

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
        if self._i2c_bus is None:
            return 0.0
        try:
            with self._lock:
                data = self._i2c_bus.read_i2c_block_data(self.pins.BH1750_I2C_ADDR, 0x10, 2)
            raw_val = (data[0] << 8) | data[1]
            return round(raw_val / 1.2, 1)
        except Exception as e:
            logger.warning("Error reading BH1750: %s", e)
            return 0.0

    def limit_switch_pressed(self) -> bool:
        # Pin pulled HIGH; switch connects to GND (active LOW)
        return GPIO.input(self.pins.LIMIT_SWITCH) == GPIO.LOW

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
        angle_deg = max(0.0, min(180.0, float(angle_deg)))
        self._servo_angle = round(angle_deg, 1)
        if self._servo_pwm is not None:
            # Map 0° -> 2.5% duty, 180° -> 12.5% duty
            duty = 2.5 + (self._servo_angle / 18.0)
            self._servo_pwm.ChangeDutyCycle(duty)
            time.sleep(0.12)
            self._servo_pwm.ChangeDutyCycle(0)  # Silence servo motor hum

    def cleanup(self) -> None:
        logger.info("Cleaning up RPi GPIO and PWM handles...")
        try:
            self.set_led_duty(0.0)
            if self._led_pwm:
                self._led_pwm.stop()
            if self._servo_pwm:
                self._servo_pwm.stop()
            self.release_stepper()
            GPIO.cleanup()
            if self._i2c_bus:
                self._i2c_bus.close()
        except Exception as e:
            logger.warning("Error during GPIO cleanup: %s", e)


def create_hardware(mock: bool = False) -> BaseHardware:
    """Factory creating either real RPiHardware or simulated MockHardware."""
    if mock:
        return MockHardware()
    if not _CAN_IMPORT_RPI or not _CAN_IMPORT_SMBUS:
        logger.info("RPi.GPIO or smbus2 not installed; using MockHardware.")
        return MockHardware()
    try:
        return RPiHardware()
    except Exception as exc:
        logger.warning("Failed to initialize physical hardware (%s); falling back to MockHardware.", exc)
        return MockHardware()


# Backward-compatible alias
HardwareInterface = MockHardware if not _CAN_IMPORT_RPI else RPiHardware
