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
echo "[6/6] Checking Scikit-Learn AI model..."
if [ ! -f "model.joblib" ]; then
    echo "       model.joblib not found. Training model now..."
    python train_ai_model.py
else
    echo "       model.joblib already present."
fi

echo "=========================================================="
echo "  Setup Complete! You can now start the Gateway:"
echo "    source .venv/bin/activate"
echo "    python gateway_main.py --mock --broker localhost"
echo "  (Or without --mock once physical GPIO sensors are wired)"
echo "=========================================================="
