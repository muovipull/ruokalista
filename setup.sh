#!/bin/bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="ruokalista"
LOG_DIR="$APP_DIR/logs"

echo "--------------------------------------------------------"
echo "  Päivitetään Ruokalistasovellus ja tausta-ajastin...  "
echo "--------------------------------------------------------"

if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "Asennetaan puuttuva python3-venv..."
    sudo apt update && sudo apt install -y python3-venv python3-pip
fi

mkdir -p "$LOG_DIR"

if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi

source "$APP_DIR/venv/bin/activate"

echo "Asennetaan Flask, Requests, Gunicorn ja APScheduler..."
pip install --upgrade pip
pip install flask requests gunicorn apscheduler

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TEMP_SERVICE=$(mktemp)

cat << SERVICE_EOF > "$TEMP_SERVICE"
[Unit]
Description=Viikin Normaalikoulun Ruokalistasovellus
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 1 --threads 4 --bind 0.0.0.0:5000 --access-logfile $LOG_DIR/access.log --error-logfile $LOG_DIR/error.log --log-level info app:app
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo mv "$TEMP_SERVICE" "$SERVICE_FILE"
sudo chmod 644 "$SERVICE_FILE"

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service
sudo systemctl restart ${SERVICE_NAME}.service

echo "--------------------------------------------------------"
echo "  PÄIVITYS VALMIS! 🎉"
echo "  Ruokalistat tallentuvat automaattisesti tiedostoon:"
echo "  $APP_DIR/ruokalista_arkisto.csv"
echo "--------------------------------------------------------"
