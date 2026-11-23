#!/usr/bin/env python3
"""
Smart Workstation - Interactive Task Lighting (LED PWM) Test Utility
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Hardware:
  - Raspberry Pi 4: GPIO 18 (Physical Pin 12, Hardware PWM0, 1000 Hz)
  - Driver: ULN2003 Channel 5 (IN5 -> Pin 12, OUT5 -> LED [-], 5V Rail -> LED [5V+])
            or N-Channel MOSFET (Gate -> Pin 12, Drain -> LED [-], Source -> GND)

Usage on Raspberry Pi:
    python test_light.py             # Interactive CLI menu
    python test_light.py --sweep     # Automatic 0 -> 100 -> 0% test sweep
    python test_light.py --duty 75   # Set to 75% and hold

Usage in Mock Simulation Mode:
    python test_light.py --mock
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Load environment if present
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from hardware_interface import BaseHardware, create_hardware


def sweep_duty_cycle(hw: BaseHardware, speed_s: float = 0.02) -> None:
    """Smoothly sweeps LED brightness: 0% -> 100% -> 0%."""
    print("\n>>> Sweeping LED: 0% -> 100%...")
    for duty in range(0, 101, 2):
        hw.set_led_duty(float(duty))
        # Print inline progress bar
        bar_len = duty // 4
        bar = "█" * bar_len + "░" * (25 - bar_len)
        print(f"\r  [{bar}] {duty:3d}%", end="", flush=True)
        time.sleep(speed_s)

    time.sleep(0.5)

    print("\n>>> Sweeping LED: 100% -> 0%...")
    for duty in range(100, -1, -2):
        hw.set_led_duty(float(duty))
        bar_len = duty // 4
        bar = "█" * bar_len + "░" * (25 - bar_len)
        print(f"\r  [{bar}] {duty:3d}%", end="", flush=True)
        time.sleep(speed_s)

    hw.set_led_duty(0.0)
    print("\n[OK] Sweep complete. LED returned to 0% (OFF).\n")


def breathe_cycle(hw: BaseHardware, cycles: int = 3, period_s: float = 2.5) -> None:
    """Pulsing breathing effect for visual aesthetic verification."""
    print(f"\n>>> Running breathing pulse ({cycles} cycles)... Press Ctrl+C to cancel.")
    step_delay = period_s / 200.0
    try:
        for c in range(1, cycles + 1):
            print(f"  Pulse Cycle {c}/{cycles}...")
            # Fade in
            for duty in range(0, 101):
                hw.set_led_duty(float(duty))
                time.sleep(step_delay)
            # Fade out
            for duty in range(100, -1, -1):
                hw.set_led_duty(float(duty))
                time.sleep(step_delay)
        print("[OK] Breathing cycle complete.")
    except KeyboardInterrupt:
        print("\n[!] Breathing interrupted by user.")
    finally:
        hw.set_led_duty(0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test & Calibrate Dynamic LED PWM Task Lighting")
    parser.add_argument("--mock", action="store_true", help="Force mock hardware mode")
    parser.add_argument("--sweep", action="store_true", help="Run 0 -> 100 -> 0% sweep and exit")
    parser.add_argument("--breathe", action="store_true", help="Run breathing pulse test and exit")
    parser.add_argument("--duty", type=float, default=None, help="Set specific duty cycle %% (0-100) and hold")
    args = parser.parse_args()

    mock_mode = args.mock or os.getenv("MOCK_HARDWARE", "0") in ("1", "true", "True")
    hw = create_hardware(mock_mode)

    print("=" * 68)
    print("  SMART WORKSTATION - TASK LIGHTING (LED PWM) TEST UTILITY")
    print("=" * 68)
    print(f"  Mode:            {'MOCK SIMULATION' if mock_mode else 'PHYSICAL RASPBERRY PI GPIO'}")
    print("  PWM Pin:         GPIO 18 (Physical Header Pin 12, Hardware PWM0)")
    print("  PWM Frequency:   1000 Hz")
    print("  Driver Path:     ULN2003 Channel 5 (IN5/OUT5) or MOSFET Gate")
    print("=" * 68)

    # Direct non-interactive modes
    if args.sweep:
        sweep_duty_cycle(hw)
        hw.cleanup()
        return

    if args.breathe:
        breathe_cycle(hw)
        hw.cleanup()
        return

    if args.duty is not None:
        target_duty = max(0.0, min(100.0, args.duty))
        hw.set_led_duty(target_duty)
        print(f"\n[OK] LED set to {target_duty:.1f}% duty cycle.")
        print("Press Ctrl+C to turn off and exit.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            hw.set_led_duty(0.0)
            hw.cleanup()
            print("\n[Safe Shutdown] LED turned off.")
        return

    # Interactive CLI Menu
    current_duty = 0.0
    hw.set_led_duty(current_duty)

    print("Commands:")
    print("  sweep / ramp : Automatically sweep duty 0% -> 100% -> 0%")
    print("  breathe      : Run 3 cycles of smooth breathing pulse")
    print("  0, 25, 50, 75, 100 : Quick brightness presets")
    print("  <number>     : Set exact duty cycle % (e.g. 15, 65, 80)")
    print("  +N / -N      : Increase or decrease brightness by N% (e.g. +10, -5)")
    print("  off / on     : Turn LED fully OFF (0%) or ON (100%)")
    print("  q / exit     : Turn off LED and exit")
    print("=" * 68)

    try:
        while True:
            bar_len = int(current_duty // 4)
            bar = "█" * bar_len + "░" * (25 - bar_len)
            print(f"\n[LED Status] Brightness: {current_duty:5.1f}%  [{bar}]")
            cmd = input("Command [sweep/breathe/0..100/+N/-N/off/q] > ").strip().lower()

            if not cmd:
                continue

            if cmd in ("q", "quit", "exit"):
                break

            elif cmd in ("sweep", "ramp", "auto"):
                sweep_duty_cycle(hw)
                current_duty = 0.0

            elif cmd == "breathe":
                breathe_cycle(hw)
                current_duty = 0.0

            elif cmd == "off":
                current_duty = 0.0
                hw.set_led_duty(current_duty)
                print("[OK] LED turned OFF (0%).")

            elif cmd == "on":
                current_duty = 100.0
                hw.set_led_duty(current_duty)
                print("[OK] LED turned ON (100%).")

            elif cmd.startswith("+") or cmd.startswith("-"):
                try:
                    delta = float(cmd)
                    current_duty = max(0.0, min(100.0, current_duty + delta))
                    hw.set_led_duty(current_duty)
                    print(f"[OK] LED adjusted to {current_duty:.1f}%")
                except ValueError:
                    print("[!] Invalid step value.")

            else:
                try:
                    val = float(cmd)
                    if 0.0 <= val <= 100.0:
                        current_duty = val
                        hw.set_led_duty(current_duty)
                        print(f"[OK] LED set to {current_duty:.1f}%")
                    else:
                        print("[!] Duty cycle must be between 0 and 100%.")
                except ValueError:
                    print(f"[!] Unknown command: {cmd}")

    finally:
        hw.set_led_duty(0.0)
        hw.cleanup()
        print("\n[Safe Shutdown] LED turned off (0.0%). GPIO cleaned up.\n")


if __name__ == "__main__":
    main()
