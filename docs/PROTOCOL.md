# Eldersafe IoT Protocol Documentation

## Architecture Overview

Eldersafe is a distributed IoT system designed to monitor elderly residents using ESP32-based sensors. Each Raspberry Pi acts as a central hub with WiFi provisioning, data collection, and persistent storage.

```
┌─────────────────────────────┐
│   ESP32 #1, #2, #3...       │
│   (Sensors in rooms)        │
│   - Temperature/Humidity    │
│   - Battery monitoring      │
└──────────┬──────────────────┘
           │ WiFi TCP (port 9000)
           ↓
┌─────────────────────────────┐
│  Raspberry Pi 4 (LOCAL HUB) │  192.168.10.1
├─────────────────────────────┤
│ hostapd (SSID: ELDERSAFE_SECURE)
│ dnsmasq (DHCP: .10.10-.249)
│ provisioner.py (discovery)
│ socket-server:9000 (asyncio)  ← TCP from ESP32
│ FastAPI:8000 (REST API)       ← From socket-server
│ MySQL (telemetry storage)
└─────────────────────────────┘
           │ Ethernet
           ↓
     [Internet / Cloud]
```

## Provisioning Flow

### 1. ESP32 Boot States

#### MODE 1: Setup (No WiFi Credentials)
- Boots without WiFi config stored in NVS
- Starts Access Point: `ELDERSAFE_SETUP_AABBCC` (last 6 MAC chars)
- No password (open network for provisioning)
- HTTP server on 192.168.4.1:80
- Awaits provisioning credentials

#### MODE 2: Normal (WiFi Credentials Available)
- Reads credentials from NVS
- Connects to `ELDERSAFE_SECURE` network
- Establishes TCP socket to 192.168.10.1:9000
- Authenticates with MAC address
- Sends sensor telemetry every 5 seconds
- Keepalive ping every 25 seconds

### 2. Provisioning Sequence

```
┌──────────────┐
│  RPI boots   │
│  hostapd ON  │
└──────┬───────┘
       │
       ├─ setup_network.sh (systemd)
       │  - Configure wlan0: 192.168.10.1/24
       │  - Start hostapd + dnsmasq
       │  - Configure NAT + iptables
       │
       └─ provisioner.py (service)
          - Scan WiFi every 30s
          - Detect: ELDERSAFE_SETUP_*
          │
          ├─ Stop hostapd temporarily
          ├─ Connect to ESP32 AP
          ├─ POST /provision with:
          │  {
          │  "ssid": "ELDERSAFE_SECURE",
          │  "password": "WPA2_PASSWORD",
          │  "server_ip": "192.168.10.1",
          │  "server_port": 9000
          │  }
          ├─ ESP32 saves to NVS → Reboots
          ├─ Restart hostapd
          │
          └─ Wait for ESP32 to join ELDERSAFE_SECURE
```

## Communication Protocol

### Socket Protocol (ESP32 ↔ Socket Server)

All messages are JSON-terminated with newline (`\n`).

#### 1. Authentication

```json
// ESP32 → Socket Server
{
  "type": "auth",
  "mac": "AA:BB:CC:DD:EE:FF"
}

// Socket Server → FastAPI
POST /api/iot-devices/socket-auth
{"mac": "AA:BB:CC:DD:EE:FF"}

// FastAPI → Socket Server (response)
{
  "status": "ok",
  "device_id": 123
}

// Socket Server → ESP32
{
  "status": "ok",
  "device_id": 123,
  "message": "Authenticated"
}
```

#### 2. Telemetry Data

```json
// ESP32 → Socket Server (every 5 seconds)
{
  "type": "data",
  "payload": {
    "temperature": 22.5,
    "humidity": 55.0,
    "battery_mv": 3700,
    "uptime_s": 1234567,
    "extra_data": {}
  }
}

// Socket Server → FastAPI
POST /api/iot-devices/telemetry/123
{
  "temperature": 22.5,
  "humidity": 55.0,
  "battery_mv": 3700,
  "uptime_s": 1234567,
  "extra_data": {}
}

// Socket Server → ESP32 (acknowledgement)
{
  "type": "ack"
}
```

#### 3. Keepalive Ping

```json
// ESP32 → Socket Server (every 25 seconds)
{
  "type": "ping"
}

// Socket Server → ESP32
{
  "type": "pong"
}
```

### REST API (FastAPI)

#### Device Management

```
POST   /api/iot-devices/register
GET    /api/iot-devices
GET    /api/iot-devices/{device_id}
PUT    /api/iot-devices/{device_id}
DELETE /api/iot-devices/{device_id}
GET    /api/iot-devices/check-mac/{mac_address}
GET    /api/iot-devices/telemetry/{device_id}/latest
```

#### Socket Authentication

```
POST /api/iot-devices/socket-auth
Content-Type: application/json

{
  "mac": "AA:BB:CC:DD:EE:FF"
}

Response:
{
  "status": "ok|error",
  "device_id": 123,
  "reason": "optional error message"
}
```

## Database Schema

### iot_devices
```sql
id              INT PRIMARY KEY
mac_address     VARCHAR(17) UNIQUE  -- AA:BB:CC:DD:EE:FF
device_name     VARCHAR(100)
device_type     VARCHAR(50)         -- "ESP32"
is_active       BOOLEAN DEFAULT TRUE
last_seen       DATETIME NULL
location        VARCHAR(255)
notes           TEXT
created_at      DATETIME
updated_at      DATETIME
```

### telemetry_data
```sql
id              INT PRIMARY KEY
device_id       INT FOREIGN KEY → iot_devices.id
temperature     FLOAT (Celsius)
humidity        FLOAT (%)
battery_mv      INT (millivolts)
uptime_s        INT (seconds)
extra_data      JSON
timestamp       DATETIME
```

### socket_sessions
```sql
id              INT PRIMARY KEY
device_id       INT FOREIGN KEY → iot_devices.id
remote_ip       VARCHAR(45)     -- IPv4 or IPv6
remote_port     INT
connected_at    DATETIME
disconnected_at DATETIME
```

## Service Architecture

### 1. Raspberry Pi Services (systemd)

#### eldersafe-setup-network.service
- **Type**: oneshot
- **Trigger**: boot (multi-user.target)
- **Action**: Configure wlan0 IP, hostapd, dnsmasq, iptables NAT
- **Script**: `/rpi/scripts/setup_network.sh`

#### eldersafe-provisioner.service
- **Type**: simple
- **Trigger**: after setup-network
- **Action**: Scan for ESP32 APs, provision new devices
- **Script**: `/rpi/provisioning/provisioner.py`
- **Restart**: on-failure (10s backoff)

#### eldersafe-docker.service
- **Type**: oneshot
- **Trigger**: after setup-network
- **Action**: docker-compose up (MySQL, FastAPI, Socket Server)
- **Dependencies**: docker.service

### 2. Docker Services

#### MySQL 8.0
- Port: 3306 (internal only, 127.0.0.1)
- Database: eldersafe_db
- Healthcheck: mysqladmin ping
- Volume: mysql_data (persistent)

#### FastAPI
- Port: 8000 (local 127.0.0.1)
- Framework: FastAPI + SQLAlchemy (aiomysql driver)
- Routes: /api/iot-devices/*
- Depends on: MySQL health check

#### Socket Server
- Port: 9000 (exposed on 192.168.10.1)
- Protocol: TCP + JSON (asyncio)
- Auth: MAC address verification
- Depends on: FastAPI

## Troubleshooting

### ESP32 Won't Provision
1. **Check hostapd is running**:
   ```bash
   ps aux | grep hostapd
   ```
   
2. **Verify dnsmasq DHCP**:
   ```bash
   ps aux | grep dnsmasq
   tailf /var/log/dnsmasq.log
   ```
   
3. **Check provisioner logs**:
   ```bash
   journalctl -u eldersafe-provisioner.service -f
   ```

### Socket Connection Fails
1. **Verify Socket Server is running**:
   ```bash
   docker logs eldersafe-socket
   ```
   
2. **Check MAC registered**:
   ```bash
   curl http://127.0.0.1:8000/api/iot-devices/check-mac/AABBCCDDEEFF
   ```
   
3. **Test TCP connectivity**:
   ```bash
   nc -zv 192.168.10.1 9000
   ```

### No Telemetry Data
1. **Check FastAPI logs**:
   ```bash
   docker logs eldersafe-fastapi
   ```
   
2. **Verify MySQL connection**:
   ```bash
   docker logs eldersafe-mysql
   ```
   
3. **Check device is active**:
   ```sql
   SELECT * FROM iot_devices WHERE is_active=1;
   SELECT * FROM telemetry_data ORDER BY timestamp DESC LIMIT 10;
   ```

## Environment Variables

Loaded from `/etc/eldersafe/.env` at runtime:

```bash
# WiFi
WIFI_SSID=ELDERSAFE_SECURE
WIFI_PASSWORD=<random_20_chars>

# Network
RPI_IP=192.168.10.1

# MySQL
MYSQL_ROOT_PASSWORD=<random>
MYSQL_USER=eldersafe
MYSQL_PASSWORD=<random>
MYSQL_DATABASE=eldersafe_db

# Services
SECRET_KEY=<random_32_hex>
SOCKET_PORT=9000
```

## Performance Notes

- **Max ESP32 per RPI**: 10-20 devices (socket server handles concurrent connections)
- **Data interval**: 5 seconds per device
- **Bandwidth**: ~100 bytes/message × 12 msg/min = 1.2 KB/min per device
- **Storage**: ~500 KB/device/month (1 month retention recommended)

## Security Considerations

1. **NVS Storage**: WiFi credentials stored unencrypted in ESP32 flash
2. **Socket Auth**: MAC-based only (not cryptographic)
3. **HTTP Provisioning**: Temporary, on open network (192.168.4.0/24)
4. **Network Isolation**: Docker containers on isolated bridge network

For production:
- Implement certificate-based device authentication
- Encrypt sensitive data in NVS
- Add firewall rules to block external access


---

## Phase 1 : Provisioning WiFi (première connexion)

### Séquence complète

```
ESP32 (neuf)          RPI Provisioner          RPI hostapd
     │                      │                       │
     │  Lance AP             │                       │
     │  SSID: ELDERSAFE_     │                       │
     │  SETUP_AABBCC         │                       │
     │                       │                       │
     │        ← Scan WiFi ───│                       │
     │        (toutes 30s)   │                       │
     │                       │  Stop hostapd ────────│
     │                       │  (maintenance ~30s)   │
     │                       │                       │
     │ ← Connexion TCP AP ───│                       │
     │                       │                       │
     │ ← POST /provision ────│                       │
     │   {ssid, password,    │                       │
     │    server_ip, port}   │                       │
     │                       │                       │
     │─── 200 OK ───────────►│                       │
     │                       │                       │
     │  Sauvegarde NVS       │                       │
     │  Redémarre            │  Start hostapd ───────│
     │                       │                       │
     │─── Rejoint ELDERSAFE_SECURE ─────────────────►│
     │                       │                       │
```

### Détail technique

| Étape | Acteur | Action |
|-------|--------|--------|
| 1 | ESP32 | Lance un AP WiFi ouvert `ELDERSAFE_SETUP_AABBCC` |
| 2 | RPI Provisioner | Scan `iwlist wlan0 scan` toutes les 30s |
| 3 | RPI Provisioner | Détecte le SSID avec le préfixe `ELDERSAFE_SETUP_` |
| 4 | RPI | Arrête `hostapd` temporairement |
| 5 | RPI | Se connecte à l'AP de l'ESP32 (réseau ouvert) |
| 6 | RPI | POST `http://192.168.4.1/provision` avec les credentials |
| 7 | ESP32 | Sauvegarde en NVS (flash persistante) et redémarre |
| 8 | RPI | Redémarre `hostapd` → réseau `ELDERSAFE_SECURE` restauré |
| 9 | ESP32 | Rejoindre `ELDERSAFE_SECURE` avec les credentials reçus |

---

## Phase 2 : Authentification (enregistrement MAC)

### Séquence

```
ESP32                    Socket Server               FastAPI/MySQL
  │                           │                           │
  │── TCP connect() ─────────►│                           │
  │                           │                           │
  │── {"type":"auth",         │                           │
  │    "mac":"AA:BB:CC:..."}─►│                           │
  │                           │── GET /authorized/MAC ───►│
  │                           │                           │── SELECT iot_devices
  │                           │                           │   WHERE mac_address=...
  │                           │◄── {authorized: false} ───│
  │                           │                           │
  │                           │── POST /register ─────────►│
  │                           │   {mac, ip_address}       │── INSERT iot_devices
  │                           │                           │   (nouveau device)
  │                           │◄── {id: 42, status:ok} ───│
  │                           │                           │
  │◄── {"type":"auth_response"│                           │
  │     "status":"ok",        │                           │
  │     "device_id":42} ──────│                           │
  │                           │                           │
```

### Règles d'authentification

- **MAC inconnue** → enregistrement automatique (status=`active`)
- **MAC connue + status=active** → autorisé
- **MAC connue + status=inactive** → refusé
- **Format MAC invalide** → refusé immédiatement
- **Premier message non-auth** → déconnexion immédiate

---

## Phase 3 : Communication normale (données)

### Format des messages TCP

Tous les messages sont en **JSON délimité par `\n`** (newline-delimited JSON).

**ESP32 → Serveur :**
```json
// Authentification
{"type": "auth", "mac": "AA:BB:CC:DD:EE:FF"}

// Données capteurs
{"type": "data", "payload": {"temperature": 22.5, "humidity": 55.0, "battery_mv": 3700}}

// Keepalive
{"type": "ping"}

// Déconnexion propre
{"type": "disconnect"}
```

**Serveur → ESP32 :**
```json
// Réponse auth (succès)
{"type": "auth_response", "status": "ok", "device_id": 42}

// Réponse auth (échec)
{"type": "auth_response", "status": "error", "reason": "invalid_mac"}

// ACK données
{"type": "ack", "status": "ok"}

// Pong
{"type": "pong", "ts": 1714000000}
```

---

## Réseau WiFi du RPI

| Paramètre | Valeur |
|-----------|--------|
| SSID | `ELDERSAFE_SECURE` |
| Sécurité | WPA2-PSK (AES) |
| IP du RPI (wlan0) | `192.168.10.1` (statique) |
| Plage DHCP ESP32 | `192.168.10.100` → `192.168.10.200` |
| Canal WiFi | 6 |
| Max ESP32 | 10 (configuré dans hostapd) |
| Interface Ethernet | `eth0` → modem/internet |

---

## Schéma de base de données

### Table `iot_devices`

| Colonne | Type | Description |
|---------|------|-------------|
| `id` | INT UNSIGNED PK | Identifiant auto-incrémenté |
| `mac_address` | VARCHAR(17) UNIQUE | Format `AA:BB:CC:DD:EE:FF` |
| `ip_address` | VARCHAR(45) | IP DHCP assignée par le RPI |
| `localserver_id` | INT FK | RPI auquel le device est rattaché |
| `resident_id` | INT FK | Résident surveillé (optionnel) |
| `status` | ENUM | `active` / `inactive` / `test` |
| `created_at` | DATETIME | Première connexion |
| `updated_at` | DATETIME | Dernière mise à jour |

---

## Installation sur un RPI vierge

```bash
# 1. Cloner le projet
git clone https://github.com/ton-org/eldersafe.git
cd eldersafe

# 2. Lancer l'installation (une seule fois, en root)
sudo bash rpi/scripts/install.sh

# 3. Lancer la stack Docker
cd rpi/docker
sudo docker-compose up -d

# 4. Redémarrer le RPI
sudo reboot
```

Après redémarrage, les credentials WiFi sont dans `/etc/eldersafe/.env`.

---

## Commandes utiles

```bash
# Voir les logs du provisioner
sudo journalctl -u eldersafe-provisioning -f

# Voir les logs du réseau
sudo journalctl -u eldersafe-network -f

# Voir les logs du socket server
sudo docker logs -f eldersafe-socket

# Voir les logs FastAPI
sudo docker logs -f eldersafe-fastapi

# Lister les ESP32 enregistrés
curl http://127.0.0.1:8000/api/iot-devices

# Désactiver un ESP32
curl -X PATCH http://127.0.0.1:8000/api/iot-devices/42/status?status=inactive

# Redémarrer le hotspot manuellement
sudo systemctl restart eldersafe-network
```

---

## Troubleshooting

| Problème | Cause probable | Solution |
|----------|---------------|----------|
| ESP32 ne passe pas en mode SETUP | Credentials déjà en NVS | Effacer NVS (appel `clearCredentials()` ou flash factory) |
| Provisioner ne détecte pas l'ESP32 | Trop loin / canal WiFi | Vérifier distance, changer canal dans hostapd.conf |
| Auth refusée après provisioning | IP du serveur incorrecte | Vérifier `server_ip` envoyé au `/provision` |
| Socket déconnecte souvent | ESP32 trop loin du RPI | Réduire `DATA_INTERVAL_MS`, vérifier antenne |
| `hostapd` ne démarre pas | `wpa_supplicant` en conflit | `sudo killall wpa_supplicant && sudo systemctl restart eldersafe-network` |
