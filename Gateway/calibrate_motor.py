#!/usr/bin/env python3
"""
Smart Workstation - Interactive Stepper Motor Range Calibration Utility
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Calibrated Curtain Physical Range:
  - Full Open:    0.0 cm (Axis Origin)
  - Glare Shield: 17.0 cm (~47.2% of full travel)
  - Fully Closed: 36.0 cm (100.0% of full travel)

Usage on Raspberry Pi:
    python calibrate_motor.py

Usage in Mock Simulation Mode:
    python calibrate_motor.py --mock
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

from hardware_interface import (
    CURTAIN_CLOSED_CM,
    CURTAIN_GLARE_CM,
    CURTAIN_GLARE_RATIO,
    CURTAIN_TRAVEL_STEPS,
    BaseHardware,
    create_hardware,
)

STEP_DELAY_S = 0.002  # ~500 half-steps/sec (safe torque speed for 28BYJ-48)


def move_steps(hw: BaseHardware, steps: int, delay: float = STEP_DELAY_S) -> None:
    direction = 1 if steps > 0 else -1
    count = abs(steps)
    for _ in range(count):
        hw.step(direction)
        time.sleep(delay)
    hw.release_stepper()


def update_env_file(travel_steps: int) -> None:
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
        if line.strip().startswith("CURTAIN_TRAVEL_STEPS="):
            new_lines.append(f"CURTAIN_TRAVEL_STEPS={travel_steps}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"CURTAIN_TRAVEL_STEPS={travel_steps}")
        new_lines.append("CURTAIN_CLOSED_CM=36.0")
        new_lines.append("CURTAIN_GLARE_CM=17.0")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n[OK] Successfully saved CURTAIN_TRAVEL_STEPS={travel_steps} to {env_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate 28BYJ-48 Stepper Motor Curtain Range")
    parser.add_argument("--mock", action="store_true", help="Force mock hardware mode")
    args = parser.parse_args()

    mock_mode = args.mock or os.getenv("MOCK_HARDWARE", "0") in ("1", "true", "True")
    hw = create_hardware(mock_mode)

    current_steps = 0
    total_travel_steps = CURTAIN_TRAVEL_STEPS
    closed_cm = CURTAIN_CLOSED_CM
    glare_cm = CURTAIN_GLARE_CM

    print("=" * 68)
    print("  SMART WORKSTATION - STEPPER MOTOR RANGE CALIBRATION")
    print("=" * 68)
    print(f"  Mode:            {'MOCK SIMULATION' if mock_mode else 'PHYSICAL RASPBERRY PI GPIO'}")
    print(f"  Closed Length:   {closed_cm:.1f} cm (Target: 100% travel)")
    print(f"  Glare Shield:    {glare_cm:.1f} cm (Target: {glare_cm/closed_cm*100:.1f}% travel)")
    print(f"  Full Open:       0.0 cm (Axis Origin / Home)")
    print(f"  Current Config:  CURTAIN_TRAVEL_STEPS = {total_travel_steps} half-steps")
    print("=" * 68)
    print("Commands:")
    print("  +N / -N      : Step down (+) or up (-) by N steps (e.g., +500, -100)")
    print("  d / u        : Quick step down (+500) or up (-500)")
    print("  zero         : Set current position as 0.0 cm (Full OPEN axis origin)")
    print("  set36        : Mark current position as 36.0 cm (Full CLOSED)")
    print("  goto <pos>   : Travel to 'open' (0cm), 'glare' (17cm), or 'closed' (36cm)")
    print("  save         : Save calibrated CURTAIN_TRAVEL_STEPS to Gateway/.env")
    print("  q            : Quit and safely de-energize coils")
    print("=" * 68)

    try:
        while True:
            steps_per_cm = (total_travel_steps / closed_cm) if closed_cm > 0 else 1.0
            cur_cm = current_steps / steps_per_cm if steps_per_cm > 0 else 0.0
            glare_steps = int(total_travel_steps * (glare_cm / closed_cm))

            print(f"\n[Status] Position: {current_steps} steps | ~{cur_cm:.2f} cm | Config Max: {total_travel_steps} steps (36.0 cm) | Glare: {glare_steps} steps (17.0 cm)")
            cmd = input("Command [d/u/+N/-N/zero/set36/goto/save/q] > ").strip().lower()

            if not cmd:
                continue

            if cmd in ("q", "quit", "exit"):
                break

            elif cmd == "zero":
                current_steps = 0
                print("[OK] Position reset to 0 steps (0.0 cm: Full OPEN axis origin)")

            elif cmd in ("set36", "set_closed", "setmax"):
                if current_steps <= 0:
                    print("[ERROR] Current step count must be > 0 to set as 36.0 cm! Move curtain down first.")
                    continue
                total_travel_steps = current_steps
                glare_steps = int(total_travel_steps * (glare_cm / closed_cm))
                print(f"[SUCCESS] Calibrated 36.0 cm = {total_travel_steps} steps!")
                print(f"          Glare shield 17.0 cm = {glare_steps} steps (~{glare_cm/closed_cm*100:.1f}%)")
                print("          Type 'save' to commit to Gateway/.env")

            elif cmd in ("d", "down"):
                print("Moving DOWN (+500 steps)...")
                move_steps(hw, 500)
                current_steps += 500

            elif cmd in ("u", "up"):
                print("Moving UP (-500 steps)...")
                move_steps(hw, -500)
                current_steps -= 500

            elif cmd.startswith("+") or cmd.startswith("-") or cmd.isdigit():
                try:
                    delta = int(cmd)
                    print(f"Moving {delta:+d} steps...")
                    move_steps(hw, delta)
                    current_steps += delta
                except ValueError:
                    print("[!] Invalid step number.")

            elif cmd.startswith("goto"):
                parts = cmd.split()
                if len(parts) < 2:
                    print("[!] Usage: goto open | goto glare | goto closed | goto <cm>")
                    continue
                target = parts[1]
                target_step = 0
                if target in ("open", "0"):
                    target_step = 0
                    target_desc = "0.0 cm (OPEN)"
                elif target in ("glare", "17", "shield"):
                    target_step = int(total_travel_steps * (glare_cm / closed_cm))
                    target_desc = "17.0 cm (GLARE_SHIELD)"
                elif target in ("closed", "36", "close"):
                    target_step = total_travel_steps
                    target_desc = "36.0 cm (CLOSED)"
                else:
                    try:
                        target_val_cm = float(target)
                        target_step = int(target_val_cm * steps_per_cm)
                        target_desc = f"{target_val_cm:.1f} cm"
                    except ValueError:
                        print("[!] Unknown target.")
                        continue

                delta = target_step - current_steps
                print(f"Traveling to {target_desc} ({delta:+d} steps)...")
                move_steps(hw, delta)
                current_steps = target_step

            elif cmd == "save":
                update_env_file(total_travel_steps)

            else:
                print(f"[!] Unknown command: {cmd}")

    finally:
        hw.release_stepper()
        hw.cleanup()
        print("\n[Safety Stop] Coils de-energized. Calibration finished.\n")


if __name__ == "__main__":
    main()
