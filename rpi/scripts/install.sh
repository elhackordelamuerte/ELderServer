#!/bin/bash
# ============================================================
# Eldersafe - install.sh
# Installation complète sur un Raspberry Pi vierge
# À exécuter UNE SEULE FOIS en root
# ============================================================

set -e

ELDERSAFE_DIR="/etc/eldersafe"
LOG="/var/log/eldersafe/install.log"

mkdir -p /var/log/eldersafe "$ELDERSAFE_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== Installation Eldersafe ==="

# --- Vérification root ---
if [ "$EUID" -ne 0 ]; then
    echo "Ce script doit être exécuté en root (sudo ./install.sh)"
    exit 1
fi

# --- Dépendances système ---
log "Installation des dépendances système..."
apt-get update -qq
apt-get install -y -qq \
    hostapd dnsmasq iptables \
    python3 python3-pip python3-venv \
    docker.io docker-compose \
    wireless-tools iw net-tools

# --- Désactiver hostapd/dnsmasq (gérés par nos scripts) ---
systemctl unmask hostapd
systemctl disable hostapd dnsmasq 2>/dev/null || true

# --- Générer le mot de passe WiFi aléatoire ---
WIFI_PASSWORD=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 20)
WIFI_SSID="ELDERSAFE_SECURE"
RPI_IP="192.168.10.1"

log "Génération des credentials WiFi..."

# --- Créer le fichier .env ---
cat > "$ELDERSAFE_DIR/.env" << EOF
# Eldersafe - Variables d'environnement
# NE PAS PARTAGER CE FICHIER
WIFI_SSID=$WIFI_SSID
WIFI_PASSWORD=$WIFI_PASSWORD
RPI_IP=$RPI_IP
MYSQL_ROOT_PASSWORD=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
MYSQL_PASSWORD=$(openssl rand -base64 16 | tr -dc 'a-zA-Z0-9' | head -c 16)
MYSQL_USER=eldersafe
MYSQL_DATABASE=eldersafe_db
SOCKET_PORT=9000
FASTAPI_PORT=8000
EOF
chmod 600 "$ELDERSAFE_DIR/.env"

log "Credentials WiFi générés et sauvegardés dans $ELDERSAFE_DIR/.env"

# --- Copier les configs ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$SCRIPT_DIR/../hostapd/hostapd.conf" "$ELDERSAFE_DIR/hostapd.conf.template"
cp "$SCRIPT_DIR/../hostapd/dnsmasq.conf" "/etc/dnsmasq.d/eldersafe.conf"

# Injecter le SSID dans le template
sed -i "s/^ssid=.*/ssid=$WIFI_SSID/" "$ELDERSAFE_DIR/hostapd.conf.template"

# --- Désactiver wpa_supplicant sur wlan0 (conflit avec hostapd) ---
log "Configuration de wpa_supplicant pour ignorer wlan0..."
mkdir -p /etc/wpa_supplicant
cat > /etc/wpa_supplicant/wpa_supplicant-wlan0.conf << 'EOF'
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
EOF

# --- Configurer systemd pour wlan0 IP statique ---
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/10-wlan0-static.network << EOF
[Match]
Name=wlan0

[Network]
Address=${RPI_IP}/24
EOF

# --- Installer le service eldersafe-network ---
cat > /etc/systemd/system/eldersafe-network.service << EOF
[Unit]
Description=Eldersafe - Configuration réseau WiFi
After=network.target
Before=eldersafe-provisioning.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/etc/eldersafe/scripts/setup_network.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/eldersafe/scripts
cp "$SCRIPT_DIR/setup_network.sh" /etc/eldersafe/scripts/
chmod +x /etc/eldersafe/scripts/setup_network.sh

systemctl daemon-reload
systemctl enable eldersafe-network

# --- Environnement Python pour le provisioning (hors Docker) ---
log "Création de l'environnement Python pour le provisioning..."
python3 -m venv /opt/eldersafe-provisioning
/opt/eldersafe-provisioning/bin/pip install -q requests

# --- Copier les scripts de provisioning ---
cp -r "$SCRIPT_DIR/../provisioning" /opt/eldersafe-provisioning/app

# --- Installer le service de provisioning ---
cat > /etc/systemd/system/eldersafe-provisioning.service << EOF
[Unit]
Description=Eldersafe - Service de provisioning ESP32
After=eldersafe-network.service docker.service
Requires=eldersafe-network.service

[Service]
Type=simple
User=root
EnvironmentFile=$ELDERSAFE_DIR/.env
ExecStart=/opt/eldersafe-provisioning/bin/python /opt/eldersafe-provisioning/app/provisioner.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable eldersafe-provisioning

log ""
log "=== Installation terminée ==="
log "  SSID WiFi     : $WIFI_SSID"
log "  Mot de passe  : $WIFI_PASSWORD"
log "  IP du RPI     : $RPI_IP"
log "  Config        : $ELDERSAFE_DIR/.env"
log ""
log "  Prochaine étape : cd docker && docker-compose up -d"
log "  Puis redémarrer : sudo reboot"
