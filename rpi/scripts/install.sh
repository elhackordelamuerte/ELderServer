#!/bin/bash
# ============================================================
# Eldersafe - install.sh (Installation complète - Raspberry Pi)
# À exécuter UNE SEULE FOIS en root : sudo ./install.sh
# ============================================================

set -e

ELDERSAFE_DIR="/etc/eldersafe"
LOG="/var/log/eldersafe/install.log"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

mkdir -p /var/log/eldersafe "$ELDERSAFE_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== Installation Eldersafe (v1.0) ==="

# --- Vérification root ---
if [ "$EUID" -ne 0 ]; then
    echo "❌ Ce script doit être exécuté en root : sudo ./install.sh"
    exit 1
fi

log "✓ Exécuté en root"

# --- Dépendances système ---
log "📦 Installation des dépendances système..."
apt-get update -qq
apt-get install -y -qq \
    hostapd dnsmasq iptables iptables-persistent \
    python3 python3-pip python3-venv \
    docker.io docker-compose \
    wireless-tools iw net-tools \
    curl wget git

usermod -aG docker root 2>/dev/null || true

log "✓ Dépendances système installées"

# --- Désactiver hostapd/dnsmasq (autonomes) ---
systemctl unmask hostapd 2>/dev/null || true
systemctl disable hostapd dnsmasq 2>/dev/null || true
systemctl stop hostapd dnsmasq 2>/dev/null || true

log "✓ hostapd et dnsmasq désactivés (gérés par les scripts)"

# === Génération des credentials WiFi ===
log "🔐 Génération des credentials WiFi..."

WIFI_PASSWORD=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9!@#$%' | head -c 20)
WIFI_SSID="ELDERSAFE_SECURE"
RPI_IP="192.168.10.1"
RPI_SUBNET="192.168.10.0/24"

MYSQL_ROOT_PASSWORD=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 20)
MYSQL_USER="eldersafe"
MYSQL_PASSWORD=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 20)
SECRET_KEY=$(openssl rand -hex 32)
SOCKET_PORT=9000

log "✓ WiFi SSID: $WIFI_SSID"
log "✓ WiFi Password: (générée, ${#WIFI_PASSWORD} chars)"
log "✓ IP serveur: $RPI_IP"

# --- Créer le fichier .env pour Docker ---
cat > "$ELDERSAFE_DIR/.env" << EOF
# ============================================================
# Eldersafe - Variables d'environnement Docker
# ⚠️  NE PAS PARTAGER CE FICHIER ⚠️
# ============================================================

MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
MYSQL_USER=$MYSQL_USER
MYSQL_PASSWORD=$MYSQL_PASSWORD
MYSQL_DATABASE=eldersafe_db

SECRET_KEY=$SECRET_KEY
SOCKET_PORT=$SOCKET_PORT

WIFI_SSID=$WIFI_SSID
WIFI_PASSWORD=$WIFI_PASSWORD
RPI_IP=$RPI_IP
EOF

chmod 600 "$ELDERSAFE_DIR/.env"
log "✓ Fichier .env créé (permissions 600)"

# --- Configuration réseau (netplan pour modernes, /etc/network pour legacy) ---
log "📡 Configuration réseau..."

# Déterminer l'interface Ethernet
ETH_INTERFACE=$(ip route | grep '^default' | awk '{print $5}' | head -1)
if [ -z "$ETH_INTERFACE" ]; then
    ETH_INTERFACE="eth0"
fi
log "   Interface Ethernet détectée : $ETH_INTERFACE"

# Créer la config réseau statique (wlan0 = 192.168.10.1/24)
mkdir -p /etc/network/interfaces.d
cat > /etc/network/interfaces.d/eldersafe-wifi << EOF
# Eldersafe WiFi configuration (AP + DHCP)
auto wlan0
iface wlan0 inet static
    address $RPI_IP
    netmask 255.255.255.0
    # hostapd et dnsmasq gérés par les scripts startup
EOF

log "✓ Configuration réseau statique pour wlan0"

# --- Créer les répertoires de travail ---
log "📁 Création des répertoires..."
mkdir -p "$ELDERSAFE_DIR"/hostapd
mkdir -p "$ELDERSAFE_DIR"/dnsmasq
mkdir -p /etc/systemd/system

# Note: hostapd.conf and dnsmasq.conf are generated dynamically by setup_network.sh
# Do not copy templates - they will be auto-generated at boot time with proper values
log "✓ Config directories created (will be populated at boot)"

# --- Copier et rendre exécutables les scripts ---
log "📝 Copie des scripts..."
if [ -f "$REPO_ROOT/rpi/scripts/setup_network.sh" ]; then
    cp "$REPO_ROOT/rpi/scripts/setup_network.sh" "$ELDERSAFE_DIR/setup_network.sh"
    chmod 755 "$ELDERSAFE_DIR/setup_network.sh"
    log "✓ setup_network.sh copié et rendu exécutable"
fi



# --- Créer les services systemd ---
log "⚙️  Création des services systemd..."

# Service: eldersafe-setup-network (startup)
cat > /etc/systemd/system/eldersafe-setup-network.service << EOF
[Unit]
Description=Eldersafe - Setup Network (startup)
After=network.target
Before=docker.service

[Service]
Type=oneshot
ExecStart=$ELDERSAFE_DIR/setup_network.sh
RemainAfterExit=yes
StandardOutput=journal
StandardError=journal
StandardInput=null

[Install]
WantedBy=multi-user.target
EOF


# Service: eldersafe-docker (docker-compose)
cat > /etc/systemd/system/eldersafe-docker.service << EOF
[Unit]
Description=Eldersafe - Docker Services (FastAPI + Socket Server + MySQL)
After=docker.service eldersafe-setup-network.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker-compose -f $REPO_ROOT/rpi/docker/docker-compose.yml up -d
ExecStop=/usr/bin/docker-compose -f $REPO_ROOT/rpi/docker/docker-compose.yml down
RemainAfterExit=yes
WorkingDirectory=$REPO_ROOT/rpi/docker
EnvironmentFile=$ELDERSAFE_DIR/.env
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
log "✓ Services systemd créés et rechargés"

# --- Activer les services au démarrage ---
systemctl enable eldersafe-setup-network.service
systemctl enable eldersafe-docker.service

log "✓ Services activés au démarrage"

# --- Start immediately ---
log "🚀 Démarrage des services..."
systemctl start eldersafe-setup-network.service
sleep 2
systemctl start eldersafe-docker.service

log "✓ Services démarrés"

# Attendre que Docker soit prêt
log "⏳ Attente de la santé des services Docker (jusqu'à 60s)..."
for i in {1..12}; do
    if systemctl is-active --quiet eldersafe-docker.service; then
        log "✓ Docker services actifs"
        break
    fi
    sleep 5
done

log ""
log "╔═══════════════════════════════════════════╗"
log "║  ✓ Installation Eldersafe terminée !     ║"
log "╠═══════════════════════════════════════════╣"
log "║  WiFi SSID:     $WIFI_SSID"
log "║  WiFi Password: $WIFI_PASSWORD"
log "║                                           ║"
log "║  IP serveur:    $RPI_IP                   ║"
log "║  API FastAPI:   http://$RPI_IP:8000  ║"
log "║  Socket Server: $RPI_IP:$SOCKET_PORT              ║"
log "║  MySQL interne: (172.20.0.2:3306)        ║"
log "║                                           ║"
log "║  Services:                                ║"
log "║    • eldersafe-docker (containers)        ║"
log "║                                           ║"
log "║  Logs:                                    ║"
log "║    journalctl -u eldersafe-*.service -f   ║"
log "║                                           ║"
log "║  Config: $ELDERSAFE_DIR/.env        ║"
log "╚═══════════════════════════════════════════╝"
log ""

log "Installation complète. Système prêt !"


