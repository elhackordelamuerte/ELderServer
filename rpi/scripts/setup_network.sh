#!/bin/bash
# ============================================================
# Eldersafe - setup_network.sh
# Lancé au boot par systemd: configure réseau + hostapd + dnsmasq
# ============================================================

set -e

LOG="/var/log/eldersafe/setup_network.log"
ELDERSAFE_DIR="/etc/eldersafe"

mkdir -p /var/log/eldersafe

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== Eldersafe Network Setup (systemd boot) ==="

# --- Load environment variables (with error checking) ---
if [ ! -f "$ELDERSAFE_DIR/.env" ]; then
    log "❌ ERROR: Config file missing: $ELDERSAFE_DIR/.env"
    echo "ERROR: Missing $ELDERSAFE_DIR/.env - run install.sh first" >&2
    exit 1
fi

# Source the environment file
set -a
source "$ELDERSAFE_DIR/.env"
set +a

log "✓ Config loaded from $ELDERSAFE_DIR/.env"

# Validate required variables
if [ -z "$WIFI_SSID" ] || [ -z "$WIFI_PASSWORD" ] || [ -z "$RPI_IP" ]; then
    log "❌ ERROR: Missing required environment variables"
    echo "ERROR: WIFI_SSID, WIFI_PASSWORD, or RPI_IP not set in .env" >&2
    exit 1
fi

WIFI_INTERFACE="wlan0"
RPI_SUBNET="${RPI_IP%.*}.0/24"

# Detect Ethernet interface
ETH_INTERFACE=$(ip route | grep '^default' | awk '{print $5}' | head -1)
if [ -z "$ETH_INTERFACE" ]; then
    ETH_INTERFACE="eth0"
fi
log "   Ethernet interface: $ETH_INTERFACE"

# --- Ensure required tools are available ---
required_commands=("ip" "hostapd" "dnsmasq" "iptables" "grep" "awk")
for cmd in "${required_commands[@]}"; do
    if ! command -v "$cmd" &> /dev/null; then
        log "❌ ERROR: Required command not found: $cmd"
        exit 1
    fi
done

# --- Configure wlan0 with static IP ---
log "📡 Configuring $WIFI_INTERFACE..."
ip link set "$WIFI_INTERFACE" up 2>/dev/null || true
ip addr flush dev "$WIFI_INTERFACE" 2>/dev/null || true
ip addr add "$RPI_IP/24" dev "$WIFI_INTERFACE" 2>/dev/null || true
ip route add "$RPI_SUBNET" dev "$WIFI_INTERFACE" 2>/dev/null || true

log "   ✓ IP: $RPI_IP / Subnet: $RPI_SUBNET"

# --- Enable IPv4 forwarding + iptables NAT ---
log "🔀 Enabling routing (Ethernet ↔ WiFi)..."

echo 1 > /proc/sys/net/ipv4/ip_forward

# Flush existing rules
iptables -t nat -F POSTROUTING 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

# Configure NAT
iptables -t nat -A POSTROUTING -o "$ETH_INTERFACE" -j MASQUERADE
iptables -A FORWARD -i "$WIFI_INTERFACE" -o "$ETH_INTERFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i "$ETH_INTERFACE" -o "$WIFI_INTERFACE" -j ACCEPT

# Save iptables rules
mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4 2>/dev/null || true

log "✓ Routing enabled"

# --- Start hostapd ---
log "📶 Starting hostapd..."

# Generate hostapd config if missing
if [ ! -f "$ELDERSAFE_DIR/hostapd/hostapd.conf" ]; then
    mkdir -p "$ELDERSAFE_DIR/hostapd"
    cat > "$ELDERSAFE_DIR/hostapd/hostapd.conf" << HOSTAPD_CONF
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
max_num_sta=20
HOSTAPD_CONF
    log "   Generated hostapd.conf"
fi

# Stop any existing hostapd instances
killall hostapd 2>/dev/null || true
sleep 1

# Start hostapd
if [ ! -x /usr/sbin/hostapd ]; then
    log "❌ ERROR: hostapd not found or not executable at /usr/sbin/hostapd"
    exit 1
fi

/usr/sbin/hostapd -B "$ELDERSAFE_DIR/hostapd/hostapd.conf" 2>/dev/null
sleep 1
log "✓ hostapd started"

# --- Start dnsmasq ---
log "🌐 Starting dnsmasq..."

# Generate dnsmasq config if missing
if [ ! -f "$ELDERSAFE_DIR/dnsmasq/dnsmasq.conf" ]; then
    mkdir -p "$ELDERSAFE_DIR/dnsmasq"
    cat > "$ELDERSAFE_DIR/dnsmasq/dnsmasq.conf" << DNSMASQ_CONF
interface=$WIFI_INTERFACE
listen-address=$RPI_IP
dhcp-range=192.168.10.10,192.168.10.249,24h
dhcp-option=option:router,$RPI_IP
dhcp-option=option:dns-server,$RPI_IP
server=8.8.8.8
server=8.8.4.4
address=/#/$RPI_IP
log-dhcp
DNSMASQ_CONF
    log "   Generated dnsmasq.conf"
fi

# Stop any existing dnsmasq instances
killall dnsmasq 2>/dev/null || true
sleep 1

# Start dnsmasq
if [ ! -x /usr/sbin/dnsmasq ]; then
    log "❌ ERROR: dnsmasq not found or not executable at /usr/sbin/dnsmasq"
    exit 1
fi

/usr/sbin/dnsmasq -C "$ELDERSAFE_DIR/dnsmasq/dnsmasq.conf" 2>/dev/null
sleep 1
log "✓ dnsmasq started"

log ""
log "╔════════════════════════════════════════════╗"
log "║  ✓ Network setup complete                 ║"
log "╠════════════════════════════════════════════╣"
log "║  SSID:      $WIFI_SSID"
log "║  IP:        $RPI_IP"
log "║  Routing:   Enabled"
log "║  hostapd:   Running"
log "║  dnsmasq:   Running"
log "╚════════════════════════════════════════════╝"
log ""
log "✓ Ready for device provisioning"


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
