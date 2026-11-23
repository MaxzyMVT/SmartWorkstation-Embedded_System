#!/usr/bin/env python3
"""
Smart Workstation - Interactive Task Lighting (LED PWM) Test & Calibration Utility
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Hardware:
  - Raspberry Pi 4: GPIO 18 (Physical Pin 12, Hardware PWM0)
  - Driver: ULN2003 Channel 5 (IN5 -> Pin 12, OUT5 -> LED [-], 5V Rail -> LED [5V+])
            or N-Channel MOSFET (Gate -> Pin 12, Drain -> LED [-], Source -> GND)

Usage on Raspberry Pi:
    python test_light.py             # Interactive CLI menu
    python test_light.py --sweep     # Automatic 0 -> 100 -> 0% test sweep
    python test_light.py --freq 200  # Set PWM frequency to 200 Hz
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

from hardware_interface import BaseHardware, LED_PWM_FREQ_HZ, create_hardware


def check_background_service() -> bool:
    """Checks if the background smartworkstation daemon is currently running on Linux/Pi."""
    if sys.platform.startswith("linux"):
        import subprocess
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "smartworkstation"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2,
            )
            if res.stdout.strip() == "active":
                return True
        except Exception:
            pass
    return False


def update_env_file(freq_hz: int) -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        example = Path(__file__).resolve().parent / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ""
    else:
        content = env_file.read_text(encoding="utf-8")

    lines = content.splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("LED_PWM_FREQ_HZ="):
            new_lines.append(f"LED_PWM_FREQ_HZ={freq_hz}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"LED_PWM_FREQ_HZ={freq_hz}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n[OK] Successfully saved LED_PWM_FREQ_HZ={freq_hz} to {env_file}")


def sweep_duty_cycle(hw: BaseHardware, speed_s: float = 0.02) -> None:
    """Smoothly sweeps LED brightness: 0% -> 100% -> 0%."""
    print("\n>>> Sweeping LED: 0% -> 100%...")
    for duty in range(0, 101, 2):
        hw.set_led_duty(float(duty))
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
    parser.add_argument("--freq", type=int, default=None, help="Set PWM frequency in Hz (default: 200)")
    parser.add_argument("--duty", type=float, default=None, help="Set specific duty cycle %% (0-100) and hold")
    args = parser.parse_args()

    mock_mode = args.mock or os.getenv("MOCK_HARDWARE", "0") in ("1", "true", "True")
    hw = create_hardware(mock_mode)

    current_freq = args.freq if args.freq is not None else LED_PWM_FREQ_HZ
    hw.set_led_freq(current_freq)

    print("=" * 70)
    print("  SMART WORKSTATION - TASK LIGHTING (LED PWM) TEST UTILITY")
    print("=" * 70)
    print(f"  Mode:            {'MOCK SIMULATION' if mock_mode else 'PHYSICAL RASPBERRY PI GPIO'}")
    print("  PWM Pin:         GPIO 18 (Physical Header Pin 12, Hardware PWM0)")
    print(f"  PWM Frequency:   {current_freq} Hz (200 Hz recommended for jitter-free dimming)")
    print("  Driver Path:     ULN2003 Channel 5 (IN5/OUT5) or MOSFET Gate")
    print("=" * 70)

    if not mock_mode and check_background_service():
        print("\n" + "!" * 70)
        print("  [WARNING] 'smartworkstation' service is currently running in background!")
        print("  It continuously updates PWM duty cycle and will cause flicker or override tests.")
        print("  Please stop it before manual calibration:")
        print("      sudo systemctl stop smartworkstation")
        print("!" * 70 + "\n")

    print("  Troubleshooting Flicker Tips:")
    print("    1. Stop background daemon:  sudo systemctl stop smartworkstation")
    print("    2. Ensure solid Common GND between Pi Pin 6/14/30 and ULN2003 / 5V GND rail")
    print("    3. Ensure 5V power connects to [5V+] pad on COB LED (NOT [3V+] pad)")
    print("    4. If flickers at 1000 Hz, test 200 Hz or 100 Hz (command: freq 200)")
    print("=" * 70)

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
        print(f"\n[OK] LED set to {target_duty:.1f}% duty cycle @ {current_freq} Hz.")
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
    print("  sweep / ramp       : Automatically sweep duty 0% -> 100% -> 0%")
    print("  breathe            : Run 3 cycles of smooth breathing pulse")
    print("  0, 25, 50, 75, 100 : Quick brightness presets")
    print("  <number>           : Set exact duty cycle % (e.g. 15, 65, 80)")
    print("  +N / -N            : Increase or decrease brightness by N% (e.g. +10, -5)")
    print("  freq <hz>          : Change PWM frequency (e.g. 100, 200, 500, 1000)")
    print("  save               : Save current LED_PWM_FREQ_HZ to Gateway/.env")
    print("  off / on           : Turn LED fully OFF (0%) or ON (100%)")
    print("  q / exit           : Turn off LED and exit")
    print("=" * 70)

    try:
        while True:
            bar_len = int(current_duty // 4)
            bar = "█" * bar_len + "░" * (25 - bar_len)
            print(f"\n[LED Status] Brightness: {current_duty:5.1f}%  [{bar}] | Freq: {current_freq} Hz")
            cmd = input("Command [sweep/breathe/0..100/+N/-N/freq/save/off/q] > ").strip().lower()

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

            elif cmd.startswith("freq"):
                parts = cmd.split()
                if len(parts) < 2:
                    print(f"Current frequency: {current_freq} Hz. (Usage: freq 100 | freq 200 | freq 500)")
                    continue
                try:
                    new_freq = int(parts[1])
                    if 10 <= new_freq <= 10000:
                        current_freq = new_freq
                        hw.set_led_freq(current_freq)
                        print(f"[OK] PWM frequency changed to {current_freq} Hz.")
                        print("     Type 'save' to commit this frequency to Gateway/.env")
                    else:
                        print("[!] Frequency must be between 10 Hz and 10000 Hz.")
                except ValueError:
                    print(f"[!] Invalid frequency: {parts[1]}")

            elif cmd == "save":
                update_env_file(current_freq)

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
