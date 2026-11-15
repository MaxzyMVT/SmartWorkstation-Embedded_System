"""Exercise the build loader without PlatformIO or real credentials."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "load_config.py"


class FakeEnv:
    def __init__(self, root):
        self.root = root
        self.paths = []

    def subst(self, value):
        return {"$PROJECT_DIR": str(self.root), "$PIOENV": "test"}[value]

    def Append(self, CPPPATH):
        self.paths.extend(CPPPATH)


class ConfigTests(unittest.TestCase):
    def generate(self, content=None, overrides=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            if content is not None:
                (root / ".env").write_text(content, encoding="utf-8")
            env = FakeEnv(root)
            with patch.dict(os.environ, overrides or {}, clear=True):
                runpy.run_path(str(SCRIPT), init_globals={"env": env, "Import": lambda _: None})
            self.assertEqual(env.paths, [str(root / ".pio" / "node2-config" / "test")])
            return (Path(env.paths[0]) / "node2_config.h").read_text(encoding="utf-8")

    def test_empty_config_builds_without_network(self):
        header = self.generate()
        self.assertIn('NODE2_WIFI_SSID[] = "";', header)
        self.assertIn('NODE2_MQTT_BROKER_HOST[] = "";', header)
        self.assertIn('NODE2_MQTT_BROKER_PORT = 1883;', header)

    def test_quotes_and_environment_override(self):
        header = self.generate(
            '# Example fixture only\nWIFI_SSID="test desk"\n'
            'WIFI_PASSWORD=quote"and\\slash\nMQTT_BROKER_PORT=1884\n',
            {"WIFI_SSID": "override"})
        self.assertIn('NODE2_WIFI_SSID[] = "override";', header)
        self.assertIn('NODE2_WIFI_PASSWORD[] = "quote\\"and\\\\slash";', header)
        self.assertIn('NODE2_MQTT_BROKER_PORT = 1884;', header)

    def test_invalid_config_fails(self):
        for content in ("not-an-assignment", "MQTT_BROKER_PORT=0",
                        "MQTT_BROKER_PORT=65536", "MQTT_BROKER_PORT=abc"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.generate(content)


if __name__ == "__main__":
    unittest.main()
