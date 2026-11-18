#!/usr/bin/env bash
# ==============================================================================
# Smart Workstation - Raspberry Pi Automated Provisioning & Setup Script
# Course: 2110356 Embedded System (CEDT), Chulalongkorn University
# Target: Raspberry Pi 4 Model B (Raspberry Pi OS Bookworm / Bullseye)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "=========================================================="
echo "  Smart Workstation - Raspberry Pi 4 Gateway Setup"
echo "=========================================================="

# 1. System Package Installation
echo "[1/6] Updating system packages and installing OS dependencies..."
sudo apt-get update -y
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    libgpiod-dev \
    i2c-tools \
    mosquitto \
    mosquitto-clients

# 2. Hardware Interfaces (I2C / SPI)
if command -v raspi-config >/dev/null 2>&1; then
    echo "[2/6] Enabling hardware I2C and SPI via raspi-config..."
    sudo raspi-config nonint do_i2c 0 || true
    sudo raspi-config nonint do_spi 0 || true
else
    echo "[2/6] Notice: raspi-config not found (skipping non-Pi hardware config)."
fi

# 3. Configure Mosquitto MQTT Broker
echo "[3/6] Configuring Mosquitto MQTT broker..."
sudo mkdir -p /etc/mosquitto/conf.d

sudo bash -c 'cat > /etc/mosquitto/conf.d/workstation.conf <<EOF
listener 1883
allow_anonymous true
EOF'

sudo systemctl enable mosquitto || true
sudo systemctl restart mosquitto || true

if systemctl is-active --quiet mosquitto; then
    echo "       Mosquitto is active and listening on port 1883."
else
    echo "       WARNING: Mosquitto service could not be started automatically."
fi

# 4. Python Virtual Environment Setup
echo "[4/6] Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "       Created .venv directory."
fi

# Activate virtual environment
source .venv/bin/activate

echo "       Upgrading pip and installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Environment File Configuration
echo "[5/6] Checking configuration files..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "       Created .env from .env.example."
    fi
else
    echo "       .env already exists."
fi

# 6. Verify / Train AI Model
echo "[6/7] Checking Scikit-Learn AI model..."
if [ ! -f "model.joblib" ]; then
    echo "       model.joblib not found. Training model now..."
    python train_ai_model.py
else
    echo "       model.joblib already present."
fi

# 7. Configure Systemd Auto-Start Service on Boot
echo "[7/7] Configuring systemd auto-start service (smartworkstation.service)..."
SERVICE_USER="$(whoami)"
SERVICE_DIR="${SCRIPT_DIR}"

sudo bash -c "cat > /etc/systemd/system/smartworkstation.service <<EOF
[Unit]
Description=Smart Workstation Gateway Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SERVICE_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${SERVICE_DIR}/.venv/bin/python ${SERVICE_DIR}/gateway_main.py --broker broker.emqx.io
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable smartworkstation.service
echo "       smartworkstation.service enabled to start on system boot."

echo "=========================================================="
echo "  Setup Complete! The Gateway service is installed and set to"
echo "  auto-start on boot. Manage with:"
echo "    sudo systemctl start smartworkstation"
echo "    sudo systemctl stop smartworkstation"
echo "    sudo systemctl status smartworkstation"
echo "    journalctl -u smartworkstation -f"
echo "=========================================================="
