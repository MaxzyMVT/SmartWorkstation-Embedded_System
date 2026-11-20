"""
Smart Workstation - On-Device AI Training and Inference Pipeline
Course: 2110356 Embedded System (CEDT), Chulalongkorn University

Requirement M4 & E7:
"Fixed thresholds or simple if-else rules are not an AI component."
This module generates a rich, realistic synthetic training dataset, trains a supervised
Machine Learning model (Random Forest / Decision Tree) with multi-dimensional decision
boundaries, evaluates inference latency (<10ms target), and serializes the model.

Feature Vector (6 Features):
    0: presence (0 or 1 from FSR402 Force Sensing Resistor)
    1: distance_cm (HC-SR04 ultrasonic face distance, cm)
    2: lux (BH1750 ambient illumination, Lux)
    3: temp_c (KY-001 DS18B20 ambient temperature, °C)
    4: sound_db (KY-037 acoustic noise level, dB)
    5: sitting_duration_min (Continuous sitting duration, minutes)

Classes:
    0: OPTIMAL_STUDY        - Posture (35-55cm) and lighting (350-700 Lux) are ergonomic
    1: LOW_LIGHT            - Lux < 300 Lux (Workspace too dim; boost LED to 100%)
    2: HARSH_GLARE          - Lux > 750 Lux (Excess ambient glare; lower curtain shield to 40%, turn off LED)
    3: TEXT_NECK_TOO_CLOSE  - Distance < 28cm (Head too close to book/screen; posture warning)
    4: SLOUCH_TOO_FAR       - Distance > 65cm while seated (Slouching excessively backward)
    5: BREAK_TIMER_REACHED  - Duration >= 50 min (Prolonged study; close curtain 100% for break)
    6: AWAY                 - Presence == 0 (User left desk; curtain open, LED 0% standby)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [AI-Model] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AI-Model")

FEATURE_NAMES = [
    "presence",
    "distance_cm",
    "lux",
    "temp_c",
    "sound_db",
    "sitting_duration_min",
]

CLASS_DEFINITIONS = {
    0: {
        "name": "OPTIMAL_STUDY",
        "action": "Curtain OPEN (0%); Closed-loop LED ~450 Lux; Normal ergonomics",
        "advice": "Posture and study environment are optimal. Keep it up!",
    },
    1: {
        "name": "LOW_LIGHT",
        "action": "Curtain OPEN (0%); Boost LED PWM to 100% to illuminate workspace",
        "advice": "Workspace too dark (<300 Lux). Boosting desk lighting to 100%.",
    },
    2: {
        "name": "HARSH_GLARE",
        "action": "Curtain GLARE_SHIELD (40%); Turn OFF LED (0%) to eliminate glare",
        "advice": "Harsh ambient glare (>750 Lux). Lowering curtain glare shield to 40%.",
    },
    3: {
        "name": "TEXT_NECK_TOO_CLOSE",
        "action": "Trigger posture warning alert; Prompt user to straighten neck",
        "advice": "Head too close (<28cm)! Straighten your neck and sit back.",
    },
    4: {
        "name": "SLOUCH_TOO_FAR",
        "action": "Trigger posture warning alert; Prompt user to sit upright",
        "advice": "Slouching too far back (>65cm)! Align your spine upright.",
    },
    5: {
        "name": "BREAK_TIMER_REACHED",
        "action": "Curtain FULLY CLOSED (100%); Physical break interlock",
        "advice": "50-minute study interval reached! Stand up, stretch, and rest your eyes.",
    },
    6: {
        "name": "AWAY",
        "action": "Curtain OPEN (0%); Turn off LED (0%); Standby power saving",
        "advice": "Workstation vacant. Power saving active.",
    },
    7: {
        "name": "NOISY_ENVIRONMENT",
        "action": "Curtain CLOSED (100%); Acoustic barrier deployed",
        "advice": "High ambient noise detected (>68 dB). Lowering curtain to reduce acoustic disruption.",
    },
    8: {
        "name": "THERMAL_DISCOMFORT",
        "action": "Curtain CLOSED (100%); Thermal draft shield deployed",
        "advice": "Ambient temperature outside comfort standard (<20°C or >32°C). Adjust AC or take a rest.",
    },
}

CLASS_INFO = CLASS_DEFINITIONS
MODEL_PATH = Path(__file__).resolve().parent / "model.joblib"


def train_and_save(path: Path | str = MODEL_PATH) -> Dict[str, Any]:
    """Trains and serializes the classifier, returning performance metrics."""
    output_path = Path(path)
    bundle = train_and_export_model(model_output_path=str(output_path))
    # Serialize classifier directly so joblib.load(path) returns the estimator
    joblib.dump(bundle["model"], str(output_path))
    return {
        "accuracy": bundle["metrics"]["test_accuracy"],
        "latency_ms": bundle["metrics"]["mean_latency_ms"],
    }



def generate_synthetic_workstation_data(samples_per_class: int = 1500, random_state: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """
    Synthesizes a realistic dataset of 6-D workstation sensor vectors
    incorporating realistic human posture variations, environmental noise,
    and sensor measurement jitter.
    """
    rng = np.random.default_rng(random_state)
    x_list: List[np.ndarray] = []
    y_list: List[np.ndarray] = []

    # -------------------------------------------------------------------------
    # Class 0: OPTIMAL_STUDY
    # Ergonomic distance (35-55cm), proper lighting (350-700 Lux), 22-26°C, <48dB, <45min
    # -------------------------------------------------------------------------
    presence_0 = np.ones(samples_per_class)
    dist_0 = rng.normal(loc=45.0, scale=4.5, size=samples_per_class).clip(34.0, 58.0)
    lux_0 = rng.normal(loc=460.0, scale=60.0, size=samples_per_class).clip(320.0, 750.0)
    temp_0 = rng.normal(loc=24.5, scale=1.2, size=samples_per_class).clip(21.5, 27.5)
    sound_0 = rng.normal(loc=36.0, scale=4.0, size=samples_per_class).clip(25.0, 48.0)
    dur_0 = rng.uniform(low=0.5, high=45.0, size=samples_per_class)

    x_0 = np.column_stack([presence_0, dist_0, lux_0, temp_0, sound_0, dur_0])
    y_0 = np.zeros(samples_per_class, dtype=int)
    x_list.append(x_0)
    y_list.append(y_0)

    # -------------------------------------------------------------------------
    # Class 1: LOW_LIGHT
    # Workspace illumination < 300 Lux (Too dim for ergonomic study)
    # -------------------------------------------------------------------------
    presence_1 = np.ones(samples_per_class)
    dist_1 = rng.normal(loc=45.0, scale=4.5, size=samples_per_class).clip(34.0, 58.0)
    lux_1 = rng.uniform(low=20.0, high=290.0, size=samples_per_class)
    temp_1 = rng.normal(loc=24.5, scale=1.2, size=samples_per_class).clip(21.5, 27.5)
    sound_1 = rng.normal(loc=36.0, scale=4.0, size=samples_per_class).clip(25.0, 48.0)
    dur_1 = rng.uniform(low=0.5, high=45.0, size=samples_per_class)

    x_1 = np.column_stack([presence_1, dist_1, lux_1, temp_1, sound_1, dur_1])
    y_1 = np.ones(samples_per_class, dtype=int)
    x_list.append(x_1)
    y_list.append(y_1)

    # -------------------------------------------------------------------------
    # Class 2: HARSH_GLARE
    # Ambient illumination > 750 Lux (Harsh overhead glare / window reflection)
    # -------------------------------------------------------------------------
    presence_2 = np.ones(samples_per_class)
    dist_2 = rng.normal(loc=45.0, scale=4.5, size=samples_per_class).clip(34.0, 58.0)
    lux_2 = rng.uniform(low=760.0, high=2200.0, size=samples_per_class)
    temp_2 = rng.normal(loc=24.5, scale=1.2, size=samples_per_class).clip(21.5, 27.5)
    sound_2 = rng.normal(loc=36.0, scale=4.0, size=samples_per_class).clip(25.0, 48.0)
    dur_2 = rng.uniform(low=0.5, high=45.0, size=samples_per_class)

    x_2 = np.column_stack([presence_2, dist_2, lux_2, temp_2, sound_2, dur_2])
    y_2 = np.full(samples_per_class, 2, dtype=int)
    x_list.append(x_2)
    y_list.append(y_2)

    # -------------------------------------------------------------------------
    # Class 3: TEXT_NECK_TOO_CLOSE
    # Distance < 28cm (Head hunched forward toward book / screen)
    # -------------------------------------------------------------------------
    presence_3 = np.ones(samples_per_class)
    dist_3 = rng.uniform(low=10.0, high=27.5, size=samples_per_class)
    lux_3 = rng.normal(loc=450.0, scale=60.0, size=samples_per_class).clip(320.0, 720.0)
    temp_3 = rng.normal(loc=24.5, scale=1.5, size=samples_per_class).clip(21.0, 28.0)
    sound_3 = rng.normal(loc=37.0, scale=5.0, size=samples_per_class).clip(28.0, 52.0)
    dur_3 = rng.uniform(low=1.0, high=48.0, size=samples_per_class)

    x_3 = np.column_stack([presence_3, dist_3, lux_3, temp_3, sound_3, dur_3])
    y_3 = np.full(samples_per_class, 3, dtype=int)
    x_list.append(x_3)
    y_list.append(y_3)

    # -------------------------------------------------------------------------
    # Class 4: SLOUCH_TOO_FAR
    # Distance > 65cm (Slouching excessively backward)
    # -------------------------------------------------------------------------
    presence_4 = np.ones(samples_per_class)
    dist_4 = rng.uniform(low=66.0, high=110.0, size=samples_per_class)
    lux_4 = rng.normal(loc=450.0, scale=60.0, size=samples_per_class).clip(320.0, 720.0)
    temp_4 = rng.normal(loc=24.8, scale=1.4, size=samples_per_class).clip(21.0, 28.5)
    sound_4 = rng.normal(loc=38.0, scale=5.0, size=samples_per_class).clip(28.0, 54.0)
    dur_4 = rng.uniform(low=1.0, high=48.0, size=samples_per_class)

    x_4 = np.column_stack([presence_4, dist_4, lux_4, temp_4, sound_4, dur_4])
    y_4 = np.full(samples_per_class, 4, dtype=int)
    x_list.append(x_4)
    y_list.append(y_4)

    # -------------------------------------------------------------------------
    # Class 5: BREAK_TIMER_REACHED
    # Duration >= 50 minutes (Continuous prolonged sitting study)
    # -------------------------------------------------------------------------
    presence_5 = np.ones(samples_per_class)
    dist_5 = rng.normal(loc=45.0, scale=5.0, size=samples_per_class).clip(30.0, 62.0)
    lux_5 = rng.normal(loc=450.0, scale=60.0, size=samples_per_class).clip(300.0, 750.0)
    temp_5 = rng.normal(loc=24.5, scale=1.5, size=samples_per_class).clip(21.0, 28.5)
    sound_5 = rng.normal(loc=36.0, scale=4.0, size=samples_per_class).clip(26.0, 50.0)
    dur_5 = rng.uniform(low=50.0, high=120.0, size=samples_per_class)

    x_5 = np.column_stack([presence_5, dist_5, lux_5, temp_5, sound_5, dur_5])
    y_5 = np.full(samples_per_class, 5, dtype=int)
    x_list.append(x_5)
    y_list.append(y_5)

    # -------------------------------------------------------------------------
    # Class 6: AWAY
    # Presence == 0 (User left desk)
    # -------------------------------------------------------------------------
    presence_6 = np.zeros(samples_per_class)
    dist_6 = rng.uniform(low=70.0, high=250.0, size=samples_per_class)  # Background reflections
    lux_6 = rng.uniform(low=50.0, high=800.0, size=samples_per_class)
    temp_6 = rng.normal(loc=25.0, scale=2.0, size=samples_per_class).clip(20.0, 32.0)
    sound_6 = rng.normal(loc=34.0, scale=6.0, size=samples_per_class).clip(22.0, 65.0)
    dur_6 = np.zeros(samples_per_class)

    x_6 = np.column_stack([presence_6, dist_6, lux_6, temp_6, sound_6, dur_6])
    y_6 = np.full(samples_per_class, 6, dtype=int)
    x_list.append(x_6)
    y_list.append(y_6)

    # -------------------------------------------------------------------------
    # Class 7: NOISY_ENVIRONMENT
    # Sustained noise > 68 dB while seated (Thai/WHO acoustic disruption standard)
    # -------------------------------------------------------------------------
    presence_7 = np.ones(samples_per_class)
    dist_7 = rng.normal(loc=45.0, scale=4.5, size=samples_per_class).clip(34.0, 58.0)
    lux_7 = rng.normal(loc=460.0, scale=60.0, size=samples_per_class).clip(320.0, 750.0)
    temp_7 = rng.normal(loc=24.5, scale=1.2, size=samples_per_class).clip(21.5, 27.5)
    sound_7 = rng.uniform(low=68.0, high=92.0, size=samples_per_class)
    dur_7 = rng.uniform(low=0.5, high=45.0, size=samples_per_class)

    x_7 = np.column_stack([presence_7, dist_7, lux_7, temp_7, sound_7, dur_7])
    y_7 = np.full(samples_per_class, 7, dtype=int)
    x_list.append(x_7)
    y_list.append(y_7)

    # -------------------------------------------------------------------------
    # Class 8: THERMAL_DISCOMFORT
    # Seated, Temp < 20°C (over-chilled AC) or > 32°C (heat stress) (DLPW/ASHRAE)
    # -------------------------------------------------------------------------
    presence_8 = np.ones(samples_per_class)
    dist_8 = rng.normal(loc=45.0, scale=4.5, size=samples_per_class).clip(34.0, 58.0)
    lux_8 = rng.normal(loc=460.0, scale=60.0, size=samples_per_class).clip(320.0, 750.0)
    half = samples_per_class // 2
    temp_cold = rng.uniform(low=14.0, high=19.8, size=half)
    temp_hot = rng.uniform(low=32.2, high=40.0, size=samples_per_class - half)
    temp_8 = np.concatenate([temp_cold, temp_hot])
    sound_8 = rng.normal(loc=36.0, scale=4.0, size=samples_per_class).clip(25.0, 55.0)
    dur_8 = rng.uniform(low=0.5, high=45.0, size=samples_per_class)

    x_8 = np.column_stack([presence_8, dist_8, lux_8, temp_8, sound_8, dur_8])
    y_8 = np.full(samples_per_class, 8, dtype=int)
    x_list.append(x_8)
    y_list.append(y_8)

    # Combine and shuffle
    x = np.vstack(x_list)
    y = np.concatenate(y_list)

    shuffle_idx = rng.permutation(len(y))
    return x[shuffle_idx], y[shuffle_idx]


def train_and_export_model(
    model_output_path: str = "model.joblib",
    samples_per_class: int = 1500,
    random_state: int = 42,
) -> Dict[str, Any]:
    """
    Generates training data, fits a tuned Random Forest Classifier,
    evaluates classification accuracy, benchmarks latency (<10ms requirement),
    and exports the serialized bundle to disk.
    """
    logger.info("Generating synthetic sensor data (%d samples/class across %d classes)...", samples_per_class, len(CLASS_DEFINITIONS))
    x, y = generate_synthetic_workstation_data(samples_per_class=samples_per_class, random_state=random_state)
    logger.info("Dataset shape: X=%s, y=%s", x.shape, y.shape)

    # Split train/test (80% / 20%)
    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=0.20, random_state=random_state, stratify=y
    )

    logger.info("Training RandomForestClassifier with 20 trees (n_jobs=1 for <10ms edge inference)...")
    clf = RandomForestClassifier(
        n_estimators=20,
        max_depth=10,
        min_samples_split=4,
        min_samples_leaf=2,
        random_state=random_state,
        n_jobs=1,
    )
    t0 = time.time()
    clf.fit(x_train, y_train)
    train_duration = time.time() - t0
    logger.info("Model training completed in %.2f seconds.", train_duration)

    # Evaluate accuracy
    y_pred = clf.predict(x_test)
    test_accuracy = float(np.mean(y_pred == y_test))
    logger.info(">>> Test Set Accuracy: %.2f%% <<<", test_accuracy * 100.0)

    # Detailed report
    report = classification_report(
        y_test,
        y_pred,
        target_names=[CLASS_DEFINITIONS[i]["name"] for i in sorted(CLASS_DEFINITIONS.keys())],
        output_dict=True,
    )
    conf_matrix = confusion_matrix(y_test, y_pred)
    logger.info("Confusion Matrix:\n%s", conf_matrix)

    # Latency benchmark (Raspberry Pi Requirement: < 10 ms per inference)
    logger.info("Benchmarking on-device single-sample inference latency...")
    latencies_ms = []
    sample = x_test[0:1]
    # Warm up JIT / cache
    for _ in range(50):
        clf.predict(sample)

    for i in range(1000):
        test_sample = x_test[i % len(x_test) : (i % len(x_test)) + 1]
        t_start = time.perf_counter()
        _ = clf.predict(test_sample)
        t_end = time.perf_counter()
        latencies_ms.append((t_end - t_start) * 1000.0)

    mean_latency = float(np.mean(latencies_ms))
    p95_latency = float(np.percentile(latencies_ms, 95))
    p99_latency = float(np.percentile(latencies_ms, 99))
    logger.info("Inference Latency: Mean=%.3f ms | P95=%.3f ms | P99=%.3f ms (Target: < 10 ms)", mean_latency, p95_latency, p99_latency)

    assert mean_latency < 10.0, f"Latency benchmark failed: {mean_latency:.2f}ms exceeds 10ms threshold"

    # Bundle for persistence
    bundle = {
        "model": clf,
        "feature_names": FEATURE_NAMES,
        "class_definitions": CLASS_DEFINITIONS,
        "trained_at": datetime.now().isoformat(),
        "metrics": {
            "test_accuracy": test_accuracy,
            "mean_latency_ms": mean_latency,
            "p99_latency_ms": p99_latency,
            "num_samples": len(y),
        },
    }

    # Ensure output directory exists
    output_file = Path(model_output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, str(output_file))
    logger.info("Model bundle successfully serialized to '%s' (Size: %.2f KB)", output_file, os.path.getsize(output_file) / 1024.0)

    return bundle


class SmartWorkstationAI:
    """
    On-device AI inference engine loaded on the Raspberry Pi Gateway.
    Encapsulates feature validation, model inference, and classification metadata.
    """

    def __init__(self, model_path: str = "model.joblib") -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            # If model does not exist yet, train it on the fly
            logger.warning("Model file '%s' not found. Automatically triggering training pipeline...", model_path)
            train_and_export_model(model_output_path=str(self.model_path))

        logger.info("Loading AI model from '%s'...", self.model_path)
        bundle = joblib.load(str(self.model_path))
        self.model: RandomForestClassifier = bundle["model"]
        self.feature_names: List[str] = bundle.get("feature_names", FEATURE_NAMES)
        self.class_definitions: Dict[int, Dict[str, str]] = bundle.get("class_definitions", CLASS_DEFINITIONS)
        self.metrics = bundle.get("metrics", {})
        logger.info("AI Model loaded successfully! (Trained Accuracy: %.2f%%)", self.metrics.get("test_accuracy", 1.0) * 100.0)

    def predict(
        self,
        presence: int,
        distance_cm: float,
        lux: float,
        temp_c: float,
        sound_db: float,
        sitting_duration_min: float,
    ) -> Dict[str, Any]:
        """
        Executes real-time inference on the 6-feature vector.
        Returns:
            dict containing class_id, class_name, confidence, action, advice, and inference_time_ms.
        """
        feature_vec = np.array(
            [[presence, distance_cm, lux, temp_c, sound_db, sitting_duration_min]],
            dtype=float,
        )

        t0 = time.perf_counter()
        pred_class = int(self.model.predict(feature_vec)[0])
        # Calculate prediction probability / confidence
        proba = self.model.predict_proba(feature_vec)[0]
        confidence = float(proba[pred_class])
        latency_ms = (time.perf_counter() - t0) * 1000.0

        meta = self.class_definitions.get(
            pred_class,
            {"name": "UNKNOWN", "action": "None", "advice": "No recommendation"},
        )

        return {
            "class_id": pred_class,
            "class_name": meta["name"],
            "confidence": round(confidence, 3),
            "action": meta["action"],
            "advice": meta["advice"],
            "latency_ms": round(latency_ms, 3),
        }


if __name__ == "__main__":
    current_dir = Path(__file__).resolve().parent
    target_model_file = current_dir / "model.joblib"
    print("=" * 70)
    print("Smart Workstation - Training On-Device AI Model (Random Forest)")
    print("=" * 70)
    train_and_export_model(model_output_path=str(target_model_file))
    print("\nVerifying model loading and test prediction:")
    engine = SmartWorkstationAI(model_path=str(target_model_file))

    # Test cases
    test_cases = [
        {"name": "Ergonomic study", "features": (1, 45.0, 450.0, 24.5, 35.0, 20.0)},
        {"name": "Text neck (slouching forward)", "features": (1, 20.0, 450.0, 24.5, 35.0, 20.0)},
        {"name": "Slouching too far back", "features": (1, 80.0, 450.0, 24.5, 35.0, 20.0)},
        {"name": "Dark room (<300 Lux)", "features": (1, 45.0, 80.0, 24.5, 35.0, 20.0)},
        {"name": "Harsh glare room (>750 Lux)", "features": (1, 45.0, 1100.0, 24.5, 35.0, 20.0)},
        {"name": "Break timer elapsed (55 min)", "features": (1, 45.0, 450.0, 24.5, 35.0, 55.0)},
        {"name": "User away", "features": (0, 150.0, 300.0, 25.0, 35.0, 0.0)},
    ]

    print("\nInference Verification Results:")
    print("-" * 70)
    for tc in test_cases:
        res = engine.predict(*tc["features"])
        print(f"[{tc['name']:<28}] -> Class {res['class_id']}: {res['class_name']:<22} (Conf: {res['confidence']*100:.1f}%, {res['latency_ms']:.2f}ms)")
    print("=" * 70)

