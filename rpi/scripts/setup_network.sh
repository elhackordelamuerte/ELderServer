#!/bin/bash
# ============================================================
# Eldersafe - setup_network.sh
# Configure l'IP statique + hotspot WiFi au démarrage du RPI
# À exécuter en root (systemd service ou /etc/rc.local)
# ============================================================

set -e

IFACE_WIFI="wlan0"
IFACE_ETH="eth0"
RPI_IP="192.168.10.1"
HOSTAPD_CONF="/etc/eldersafe/hostapd.conf"
ENV_FILE="/etc/eldersafe/.env"
LOG="/var/log/eldersafe/network_setup.log"

mkdir -p /var/log/eldersafe

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== Démarrage setup_network.sh ==="

# --- Charger les variables d'environnement ---
if [ ! -f "$ENV_FILE" ]; then
    log "ERREUR: $ENV_FILE introuvable. Lancer install.sh d'abord."
    exit 1
fi
source "$ENV_FILE"

if [ -z "$WIFI_PASSWORD" ]; then
    log "ERREUR: WIFI_PASSWORD non défini dans $ENV_FILE"
    exit 1
fi

# --- Configurer l'IP statique sur wlan0 ---
log "Configuration IP statique $RPI_IP sur $IFACE_WIFI..."
ip link set "$IFACE_WIFI" up
ip addr flush dev "$IFACE_WIFI" 2>/dev/null || true
ip addr add "${RPI_IP}/24" dev "$IFACE_WIFI"

# --- Injecter le mot de passe WiFi dans hostapd.conf ---
log "Injection du mot de passe WiFi dans hostapd.conf..."
sed "s/PLACEHOLDER_REPLACED_AT_BOOT/$WIFI_PASSWORD/" \
    "$HOSTAPD_CONF.template" > "$HOSTAPD_CONF"
chmod 600 "$HOSTAPD_CONF"

# --- Activer le routage IP (pour que le RPI partage son Ethernet) ---
log "Activation IP forwarding..."
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
