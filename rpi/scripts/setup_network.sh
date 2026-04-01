#!/bin/bash
# ============================================================
# Eldersafe - setup_network.sh
# Lancé au boot par systemd: configure réseau + hostapd + dnsmasq
# ============================================================

set -e

LOG="/var/log/eldersafe/setup_network.log"
ELDERSAFE_DIR="/etc/eldersafe"
CONFIG_DIR="$ELDERSAFE_DIR"

mkdir -p /var/log/eldersafe

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== Eldersafe Network Setup (systemd boot) ==="

# --- Load environment variables ---
if [ -f "$ELDERSAFE_DIR/.env" ]; then
    export $(grep -v '^#' "$ELDERSAFE_DIR/.env" | xargs)
    log "✓ Config chargée depuis $ELDERSAFE_DIR/.env"
else
    log "❌ Fichier config manquant: $ELDERSAFE_DIR/.env"
    exit 1
fi

WIFI_SSID="${WIFI_SSID:-ELDERSAFE_SECURE}"
WIFI_PASSWORD="${WIFI_PASSWORD}"
RPI_IP="${RPI_IP:-192.168.10.1}"
RPI_SUBNET="${RPI_IP%.*}.0/24"
ETH_INTERFACE="${ETH_INTERFACE:-eth0}"
WIFI_INTERFACE="wlan0"

# --- Déterminer automatiquement l'interface Ethernet ---
if [ ! -d "/sys/class/net/$ETH_INTERFACE" ]; then
    ETH_INTERFACE=$(ip -4 route show | grep '^default' | awk '{print $5}' | head -1)
    if [ -z "$ETH_INTERFACE" ]; then
        ETH_INTERFACE="eth0"
    fi
    log "   Interface Ethernet détectée : $ETH_INTERFACE"
fi

# --- Configurer wlan0 en IP static ---
log "📡 Configuration de $WIFI_INTERFACE..."

ip link set "$WIFI_INTERFACE" up
ip addr flush dev "$WIFI_INTERFACE"
ip addr add "$RPI_IP/24" dev "$WIFI_INTERFACE"
ip route add "$RPI_SUBNET" dev "$WIFI_INTERFACE"

log "   ✓ IP : $RPI_IP / Subnet: $RPI_SUBNET"

# --- Activer le forwarding IPv4 + iptables NAT ---
log "🔀 Activation du routage (Ethernet ↔ WiFi)..."

echo 1 > /proc/sys/net/ipv4/ip_forward

# Flush les règles existantes
iptables -t nat -F POSTROUTING 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

# NAT: Ethernet → WiFi
iptables -t nat -A POSTROUTING -o "$ETH_INTERFACE" -j MASQUERADE
iptables -A FORWARD -i "$WIFI_INTERFACE" -o "$ETH_INTERFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i "$ETH_INTERFACE" -o "$WIFI_INTERFACE" -j ACCEPT

# Sauvegarder les règles iptables
iptables-save > /etc/iptables/rules.v4 2>/dev/null || \
    mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4

log "✓ Routage activé"

# --- Configurer et démarrer hostapd ---
log "📶 Démarrage de hostapd..."

# Générer la config hostapd si elle n'existe pas
if [ ! -f "$CONFIG_DIR/hostapd/hostapd.conf" ]; then
    mkdir -p "$CONFIG_DIR/hostapd"
    cat > "$CONFIG_DIR/hostapd/hostapd.conf" << HOSTAPD_CONF
interface=$WIFI_INTERFACE
driver=nl80211
ssid=$WIFI_SSID
hw_mode=g
channel=6
wmm_enabled=1
wpa=2
wpa_passphrase=$WIFI_PASSWORD
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
auth_algs=1
HOSTAPD_CONF
    log "   Fichier hostapd.conf généré"
fi

# Arrêter les instances existantes
killall hostapd 2>/dev/null || true
sleep 1

# Démarrer hostapd en daemon
/usr/sbin/hostapd -B "$CONFIG_DIR/hostapd/hostapd.conf"
log "✓ hostapd a démarré (PID: $!)"

# --- Configurer et démarrer dnsmasq ---
log "🌐 Démarrage de dnsmasq..."

# Générer la config dnsmasq si elle n'existe pas
if [ ! -f "$CONFIG_DIR/dnsmasq/dnsmasq.conf" ]; then
    mkdir -p "$CONFIG_DIR/dnsmasq"
    cat > "$CONFIG_DIR/dnsmasq/dnsmasq.conf" << DNSMASQ_CONF
interface=$WIFI_INTERFACE
listen-address=$RPI_IP
dhcp-range=192.168.10.10,192.168.10.249,24h
dhcp-option=option:router,$RPI_IP
dhcp-option=option:dns-server,$RPI_IP
server=8.8.8.8
server=8.8.4.4

# Captive portal : rediriger *.local vers RPI_IP
address=/#/$RPI_IP
DNSMASQ_CONF
    log "   Fichier dnsmasq.conf généré"
fi

# Arrêter les instances existantes
killall dnsmasq 2>/dev/null || true
sleep 1

# Démarrer dnsmasq
/usr/sbin/dnsmasq -C "$CONFIG_DIR/dnsmasq/dnsmasq.conf"
log "✓ dnsmasq a démarré (PID: $!)"

# --- Vérifier et attendre que hostapd soit opérationnel ---
log "⏳ Vérification de l'AP..."
for i in {1..10}; do
    if iw dev "$WIFI_INTERFACE" link 2>/dev/null | grep -q "Connected to"; then
        log "✓ AP hostapd opérationnel"
        break
    fi
    sleep 1
done

log ""
log "╔════════════════════════════════════════════╗"
log "║  ✓ Configuration réseau terminée          ║"
log "╠════════════════════════════════════════════╣"
log "║  WiFi AP SSID:     $WIFI_SSID"
log "║  RPI IP:           $RPI_IP"
log "║  Interfaces:       $ETH_INTERFACE (WAN), $WIFI_INTERFACE (AP)"
log "║  Routage:          Actif (NAT Ethernet←→WiFi)"
log "║  hostapd:          Actif"
log "║  dnsmasq (DHCP):   Actif"
log "╚════════════════════════════════════════════╝"
log ""

log "Prêt pour le provisioning ESP32 !"

echo 1 > /proc/sys/net/ipv4/ip_forward

# Masquerade : le trafic WiFi sortant passe par Ethernet
iptables -t nat -C POSTROUTING -o "$IFACE_ETH" -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -o "$IFACE_ETH" -j MASQUERADE

iptables -C FORWARD -i "$IFACE_WIFI" -o "$IFACE_ETH" -j ACCEPT 2>/dev/null || \
    iptables -A FORWARD -i "$IFACE_WIFI" -o "$IFACE_ETH" -j ACCEPT

# --- Démarrer dnsmasq ---
log "Démarrage dnsmasq..."
systemctl restart dnsmasq

# --- Démarrer hostapd ---
log "Démarrage hostapd (hotspot $WIFI_SSID)..."
systemctl restart hostapd

log "=== setup_network.sh terminé. RPI IP: $RPI_IP ==="
