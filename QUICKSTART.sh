#!/bin/bash
# ============================================================
# Eldersafe - Quick Reference & Verification Guide
# ============================================================

echo "╔═════════════════════════════════════════════════════╗"
echo "║        ELDERSAFE INSTALLATION CHECKLIST             ║"
echo "╚═════════════════════════════════════════════════════╝"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ This script should be run as root: sudo bash $0"
    exit 1
fi

echo "📋 Checking Eldersafe installation..."
echo ""

# Check files exist
echo "Checking files..."
files=(
    "/etc/eldersafe/.env"
    "/etc/systemd/system/eldersafe-setup-network.service"
    "/etc/systemd/system/eldersafe-provisioner.service"
    "/etc/systemd/system/eldersafe-docker.service"
    "/usr/sbin/hostapd"
    "/usr/sbin/dnsmasq"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $file"
    else
        echo "✗ $file (MISSING)"
    fi
done

echo ""
echo "Checking services..."

# Check services
services=(
    "eldersafe-setup-network.service"
    "eldersafe-provisioner.service"
    "eldersafe-docker.service"
)

for service in "${services[@]}"; do
    status=$(systemctl is-active $service 2>/dev/null || echo "inactive")
    if [ "$status" = "active" ]; then
        echo "✓ $service: active"
    else
        echo "✗ $service: $status"
    fi
done

echo ""
echo "Checking Docker containers..."

# Check Docker containers
containers=("eldersafe-mysql" "eldersafe-fastapi" "eldersafe-socket")

for container in "${containers[@]}"; do
    status=$(docker ps --filter "name=$container" --format "{{.Status}}" 2>/dev/null | head -1)
    if [ -n "$status" ]; then
        echo "✓ $container: $status"
    else
        echo "✗ $container: not running"
    fi
done

echo ""
echo "╔═════════════════════════════════════════════════════╗"
echo "║              USEFUL COMMANDS                         ║"
echo "╚═════════════════════════════════════════════════════╝"
echo ""

cat << 'EOF'
# View service logs
journalctl -u eldersafe-setup-network.service -f
journalctl -u eldersafe-provisioner.service -f
journalctl -u eldersafe-docker.service -f

# Docker inspection
docker ps -a
docker logs eldersafe-fastapi -f
docker logs eldersafe-socket -f
docker logs eldersafe-mysql -f

# Service management
sudo systemctl restart eldersafe-provisioner.service
sudo systemctl restart eldersafe-docker.service

# Network debugging
ip addr show
iwconfig wlan0
ps aux | grep hostapd
ps aux | grep dnsmasq

# Database access
docker exec -it eldersafe-mysql mysql -u root -pPASSWORD eldersafe_db
SELECT * FROM iot_devices;
SELECT * FROM telemetry_data ORDER BY timestamp DESC LIMIT 20;

# API testing
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/iot-devices
curl http://127.0.0.1:8000/api/iot-devices/docs

# WiFi info
cat /etc/eldersafe/.env | grep WIFI
iwlist wlan0 scan 2>/dev/null | grep ESSID
EOF

echo ""
echo "╔═════════════════════════════════════════════════════╗"
echo "║           TROUBLESHOOTING TIPS                      ║"
echo "╚═════════════════════════════════════════════════════╝"
echo ""

cat << 'EOF'
1. ESP32 won't provision:
   - Check provisioner logs: journalctl -u eldersafe-provisioner.service -f
   - Verify hostapd is running: ps aux | grep hostapd
   - Scan for ESP32 APs: sudo iwlist wlan0 scan | grep ESSID

2. Socket connection fails:
   - Check socket server: docker logs eldersafe-socket
   - Verify FastAPI: curl http://127.0.0.1:8000/health
   - Check device registered: curl http://127.0.0.1:8000/api/iot-devices

3. No telemetry data:
   - Check MySQL: docker exec eldersafe-mysql mysqladmin ping
   - Verify device connection: netstat -tlnp | grep 9000
   - View MySQL data: docker exec -it eldersafe-mysql mysql -u root -p eldersafe_db

4. Services won't start:
   - Check syntax: systemd-analyze verify /etc/systemd/system/eldersafe-*.service
   - Reload systemd: sudo systemctl daemon-reload
   - Check disk space: df -h
   - Check memory: free -h

5. Docker containers crash:
   - View logs: docker logs eldersafe-fastapi
   - Check health: docker inspect eldersafe-mysql
   - Rebuild: docker-compose build && docker-compose up -d
EOF

echo ""
echo "✓ Verification complete!"
