#!/usr/bin/env python3
"""
Smart Workstation - Interactive Stepper Motor Range & Speed Calibration Utility
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Calibrated Curtain Physical Range:
  - Full Open:    0.0 cm (Axis Origin)
  - Glare Shield: 17.0 cm (~47.2% of full travel)
  - Fully Closed: 36.0 cm (100.0% of full travel)

Speed Tuning (Delay per half-step):
  - turbo:  0.0010 s (~1000 half-steps/s, 2x speed)
  - fast:   0.0012 s (~833 half-steps/s, recommended)
  - normal: 0.0015 s (~667 half-steps/s)
  - slow:   0.0020 s (~500 half-steps/s, stock conservative)

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
    STEPPER_DELAY_S,
    BaseHardware,
    create_hardware,
)


def move_steps(hw: BaseHardware, steps: int, delay: float) -> None:
    direction = 1 if steps > 0 else -1
    count = abs(steps)
    for _ in range(count):
        hw.step(direction)
        time.sleep(delay)
    hw.release_stepper()


def update_env_file(travel_steps: int, step_delay: float) -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        example = Path(__file__).resolve().parent / ".env.example"
        content = example.read_text(encoding="utf-8") if example.exists() else ""
    else:
        content = env_file.read_text(encoding="utf-8")

    lines = content.splitlines()
    found_steps = False
    found_delay = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("CURTAIN_TRAVEL_STEPS="):
            new_lines.append(f"CURTAIN_TRAVEL_STEPS={travel_steps}")
            found_steps = True
        elif line.strip().startswith("STEPPER_DELAY_S="):
            new_lines.append(f"STEPPER_DELAY_S={step_delay:.4f}")
            found_delay = True
        else:
            new_lines.append(line)

    if not found_steps:
        new_lines.append(f"CURTAIN_TRAVEL_STEPS={travel_steps}")
        new_lines.append("CURTAIN_CLOSED_CM=36.0")
        new_lines.append("CURTAIN_GLARE_CM=17.0")

    if not found_delay:
        new_lines.append(f"STEPPER_DELAY_S={step_delay:.4f}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    speed_hz = 1.0 / step_delay if step_delay > 0 else 0
    print(f"\n[OK] Successfully saved configuration to {env_file}:")
    print(f"     CURTAIN_TRAVEL_STEPS = {travel_steps}")
    print(f"     STEPPER_DELAY_S      = {step_delay:.4f} (~{speed_hz:.0f} steps/s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate 28BYJ-48 Stepper Motor Curtain Range & Speed")
    parser.add_argument("--mock", action="store_true", help="Force mock hardware mode")
    args = parser.parse_args()

    mock_mode = args.mock or os.getenv("MOCK_HARDWARE", "0") in ("1", "true", "True")
    hw = create_hardware(mock_mode)

    current_steps = 0
    total_travel_steps = CURTAIN_TRAVEL_STEPS
    closed_cm = CURTAIN_CLOSED_CM
    glare_cm = CURTAIN_GLARE_CM
    current_delay = STEPPER_DELAY_S

    print("=" * 70)
    print("  SMART WORKSTATION - STEPPER MOTOR RANGE & SPEED CALIBRATION")
    print("=" * 70)
    print(f"  Mode:            {'MOCK SIMULATION' if mock_mode else 'PHYSICAL RASPBERRY PI GPIO'}")
    print(f"  Closed Length:   {closed_cm:.1f} cm (Target: 100% travel)")
    print(f"  Glare Shield:    {glare_cm:.1f} cm (Target: {glare_cm/closed_cm*100:.1f}% travel)")
    print(f"  Full Open:       0.0 cm (Axis Origin / Home)")
    print(f"  Current Config:  CURTAIN_TRAVEL_STEPS = {total_travel_steps} half-steps")
    print(f"  Current Speed:   STEPPER_DELAY_S = {current_delay:.4f} s (~{1/current_delay:.0f} steps/s)")
    print("=" * 70)
    print("Commands:")
    print("  +N / -N      : Step down (+) or up (-) by N steps (e.g., +500, -100)")
    print("  d / u        : Quick step down (+500) or up (-500)")
    print("  zero         : Set current position as 0.0 cm (Full OPEN axis origin)")
    print("  set36        : Mark current position as 36.0 cm (Full CLOSED)")
    print("  speed <val>  : Set step delay in seconds (e.g. 0.0010, 0.0012, 0.0015, 0.0020)")
    print("                 or presets: 'speed turbo' (1ms), 'speed fast' (1.2ms), 'speed normal' (1.5ms)")
    print("  goto <pos>   : Travel to 'open' (0cm), 'glare' (17cm), or 'closed' (36cm)")
    print("  save         : Save calibrated CURTAIN_TRAVEL_STEPS & STEPPER_DELAY_S to .env")
    print("  q            : Quit and safely de-energize coils")
    print("=" * 70)

    try:
        while True:
            steps_per_cm = (total_travel_steps / closed_cm) if closed_cm > 0 else 1.0
            cur_cm = current_steps / steps_per_cm if steps_per_cm > 0 else 0.0
            glare_steps = int(total_travel_steps * (glare_cm / closed_cm))
            speed_hz = 1.0 / current_delay if current_delay > 0 else 0

            print(f"\n[Status] Position: {current_steps} steps (~{cur_cm:.2f} cm) | Speed: {current_delay*1000:.1f}ms ({speed_hz:.0f} steps/s) | Max: {total_travel_steps} steps | Glare: {glare_steps} steps")
            cmd = input("Command [d/u/+N/-N/zero/set36/speed/goto/save/q] > ").strip().lower()

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

            elif cmd.startswith("speed"):
                parts = cmd.split()
                if len(parts) < 2:
                    print(f"Current speed: {current_delay:.4f} s/step (~{speed_hz:.0f} steps/s)")
                    print("Usage: speed <seconds> (e.g. 0.0010, 0.0012, 0.0015, 0.0020)")
                    print("       speed turbo | speed fast | speed normal | speed slow")
                    continue
                val = parts[1]
                presets = {
                    "turbo": 0.0010,   # ~1000 steps/s (2x speed)
                    "fast": 0.0012,    # ~833 steps/s (fast & reliable)
                    "normal": 0.0015,  # ~667 steps/s (balanced)
                    "slow": 0.0020,    # ~500 steps/s (conservative stock)
                }
                new_delay = None
                if val in presets:
                    new_delay = presets[val]
                else:
                    try:
                        parsed = float(val)
                        if 0.0005 <= parsed <= 0.0100:
                            new_delay = parsed
                        else:
                            print("[!] Recommended delay is between 0.0008s and 0.0050s.")
                            continue
                    except ValueError:
                        print(f"[!] Invalid speed value: {val}")
                        continue

                if new_delay:
                    current_delay = new_delay
                    new_hz = 1.0 / current_delay
                    print(f"[OK] Speed updated to {current_delay:.4f} s/step (~{new_hz:.0f} steps/s).")
                    print("     Type 'save' to make this the permanent default in Gateway/.env")

            elif cmd in ("d", "down"):
                print(f"Moving DOWN (+500 steps @ {current_delay*1000:.1f}ms)...")
                move_steps(hw, 500, delay=current_delay)
                current_steps += 500

            elif cmd in ("u", "up"):
                print(f"Moving UP (-500 steps @ {current_delay*1000:.1f}ms)...")
                move_steps(hw, -500, delay=current_delay)
                current_steps -= 500

            elif cmd.startswith("+") or cmd.startswith("-") or cmd.isdigit():
                try:
                    delta = int(cmd)
                    print(f"Moving {delta:+d} steps @ {current_delay*1000:.1f}ms...")
                    move_steps(hw, delta, delay=current_delay)
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
                print(f"Traveling to {target_desc} ({delta:+d} steps @ {current_delay*1000:.1f}ms)...")
                move_steps(hw, delta, delay=current_delay)
                current_steps = target_step

            elif cmd == "save":
                update_env_file(total_travel_steps, current_delay)

            else:
                print(f"[!] Unknown command: {cmd}")

    finally:
        hw.release_stepper()
        hw.cleanup()
        print("\n[Safety Stop] Coils de-energized. Calibration finished.\n")


if __name__ == "__main__":
    main()
