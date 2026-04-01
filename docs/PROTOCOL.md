# Eldersafe — Documentation protocole IoT
## Architecture de connexion ESP32 ↔ Raspberry Pi

---

## Vue d'ensemble

```
[ESP32] ←WiFi→ [RPI (hostapd)] ←TCP Socket→ [RPI (Docker: FastAPI + MySQL)]
                     ↑
               [RPI Ethernet → Modem/Internet]
```

Le Raspberry Pi joue **deux rôles réseau simultanément** :
- **eth0** : connecté au modem, accès internet normal
- **wlan0** : crée son propre réseau WiFi WPA2 (`ELDERSAFE_SECURE`)

---

## Structure des fichiers

```
eldersafe/
├── esp32/
│   └── eldersafe_firmware.ino      # Firmware Arduino ESP32
│
└── rpi/
    ├── scripts/
    │   ├── install.sh              # Installation initiale (à lancer 1 seule fois)
    │   └── setup_network.sh        # Lancé au démarrage par systemd
    │
    ├── hostapd/
    │   ├── hostapd.conf            # Config du hotspot WiFi
    │   └── dnsmasq.conf            # Config DHCP pour les ESP32
    │
    ├── provisioning/
    │   └── provisioner.py          # Détecte et configure les ESP32 neufs
    │
    ├── socket_server/
    │   └── socket_server.py        # Serveur TCP (auth MAC + données)
    │
    ├── docker/
    │   ├── docker-compose.yml      # FastAPI + MySQL + Socket Server
    │   └── api/
    │       ├── routes_iot_devices.py
    │       └── models.py
    │
    └── db/
        └── 001_init_iot_tables.sql # Migration SQL initiale
```

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
