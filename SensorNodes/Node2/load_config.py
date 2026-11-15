"""Generate an ignored C++ header; never place credentials in compiler flags."""
import json
import os
from pathlib import Path

Import("env")

project = Path(env.subst("$PROJECT_DIR"))
values = {}
source = project / ".env"
if source.exists():
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError("Node 2 .env requires KEY=value lines")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value

defaults = {
    "WIFI_SSID": "", "WIFI_PASSWORD": "", "MQTT_BROKER_HOST": "",
    "MQTT_USERNAME": "", "MQTT_PASSWORD": "",
}
lines = ["#pragma once", "// Generated from local configuration. Do not commit."]
for key, default in defaults.items():
    value = os.environ.get(key, values.get(key, default))
    lines.append(f"static const char NODE2_{key}[] = {json.dumps(value)};")
port = int(os.environ.get("MQTT_BROKER_PORT", values.get("MQTT_BROKER_PORT", "1883")))
if not 1 <= port <= 65535:
    raise ValueError("MQTT_BROKER_PORT must be between 1 and 65535")
lines.append(f"static const unsigned NODE2_MQTT_BROKER_PORT = {port};")
directory = project / ".pio" / "node2-config" / env.subst("$PIOENV")
directory.mkdir(parents=True, exist_ok=True)
header = directory / "node2_config.h"
content = "\n".join(lines) + "\n"
if not header.exists() or header.read_text(encoding="utf-8") != content:
    header.write_text(content, encoding="utf-8")
env.Append(CPPPATH=[str(directory)])
