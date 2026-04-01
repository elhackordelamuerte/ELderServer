#!/usr/bin/env python3
"""
Eldersafe - Provisioner ESP32
==============================
Service tournant sur l'HÔTE du Raspberry Pi (pas dans Docker).
Rôle :
  1. Scanner les réseaux WiFi visibles via wlan0
  2. Détecter les ESP32 en mode Access Point (SSID: ELDERSAFE_SETUP_XXXX)
  3. S'y connecter temporairement (en coupant hostapd le temps du provisioning)
  4. Envoyer les credentials WiFi du réseau sécurisé via HTTP
  5. Redémarrer hostapd pour revenir au réseau normal

Dépendances :
  pip install requests
  Accès root requis (pour gérer wlan0)
"""

import os
import re
import time
import logging
import subprocess
import requests
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# --- Configuration ---
SCAN_INTERVAL_SECONDS = 30          # Fréquence de scan
ESP32_SSID_PREFIX = "ELDERSAFE_SETUP_"
ESP32_AP_IP = "192.168.4.1"         # IP par défaut de l'ESP32 en mode AP
ESP32_PROVISION_PORT = 80
PROVISION_ENDPOINT = f"http://{ESP32_AP_IP}/provision"
PROVISION_TIMEOUT = 15              # secondes
WIFI_IFACE = "wlan0"
FASTAPI_BASE_URL = "http://127.0.0.1:8000"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PROVISIONER] %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/eldersafe/provisioner.log"),
    ],
)
log = logging.getLogger(__name__)


@dataclass
class Esp32Network:
    ssid: str
    mac_hint: str   # extrait du SSID (6 derniers chars hex)
    signal: int     # dBm


def run(cmd: str, check=True) -> subprocess.CompletedProcess:
    """Exécute une commande shell et retourne le résultat."""
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=check
    )


def get_wifi_credentials() -> tuple[str, str]:
    """Lit les credentials WiFi depuis le fichier .env Eldersafe."""
    env_path = Path("/etc/eldersafe/.env")
    ssid, password = None, None
    for line in env_path.read_text().splitlines():
        if line.startswith("WIFI_SSID="):
            ssid = line.split("=", 1)[1].strip()
        elif line.startswith("WIFI_PASSWORD="):
            password = line.split("=", 1)[1].strip()
    if not ssid or not password:
        raise ValueError("WIFI_SSID ou WIFI_PASSWORD manquant dans .env")
    return ssid, password


def scan_for_esp32_aps() -> list[Esp32Network]:
    """
    Scanne les réseaux WiFi visibles et retourne les ESP32 en mode AP.
    Utilise 'iwlist scan' (compatible tous RPI).
    """
    try:
        result = run(f"iwlist {WIFI_IFACE} scan 2>/dev/null", check=False)
        output = result.stdout

        networks = []
        current_ssid = None
        current_signal = -100

        for line in output.splitlines():
            line = line.strip()

            signal_match = re.search(r"Signal level=(-\d+) dBm", line)
            if signal_match:
                current_signal = int(signal_match.group(1))

            ssid_match = re.search(r'ESSID:"(.+)"', line)
            if ssid_match:
                current_ssid = ssid_match.group(1)
                if current_ssid.startswith(ESP32_SSID_PREFIX):
                    mac_hint = current_ssid.replace(ESP32_SSID_PREFIX, "")
                    networks.append(Esp32Network(
                        ssid=current_ssid,
                        mac_hint=mac_hint,
                        signal=current_signal,
                    ))
                    log.info(f"ESP32 détecté : {current_ssid} (signal: {current_signal} dBm)")

        return networks

    except Exception as e:
        log.error(f"Erreur lors du scan WiFi : {e}")
        return []


def check_esp32_already_registered(mac_hint: str) -> bool:
    """Vérifie via l'API FastAPI si cet ESP32 est déjà enregistré."""
    try:
        resp = requests.get(
            f"{FASTAPI_BASE_URL}/api/iot-devices/check-mac-hint/{mac_hint}",
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("registered", False)
    except Exception:
        pass
    return False


def stop_hostapd():
    """Arrête temporairement le hotspot pour libérer wlan0."""
    log.info("Arrêt temporaire de hostapd pour provisioning...")
    run("systemctl stop hostapd", check=False)
    run("systemctl stop dnsmasq", check=False)
    time.sleep(2)


def start_hostapd():
    """Redémarre le hotspot Eldersafe après provisioning."""
    log.info("Redémarrage de hostapd...")
    run("systemctl start dnsmasq", check=False)
    run("systemctl start hostapd", check=False)
    time.sleep(3)
    log.info("Hotspot Eldersafe restauré.")


def connect_to_esp32_ap(ssid: str) -> bool:
    """
    Connecte wlan0 au réseau AP de l'ESP32.
    L'ESP32 en mode AP n'a pas de mot de passe (réseau ouvert pour le provisioning).
    """
    log.info(f"Connexion au réseau ESP32 : {ssid}")
    try:
        # Configurer wpa_supplicant pour réseau ouvert
        wpa_conf = f"""
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={{
    ssid="{ssid}"
    key_mgmt=NONE
}}
"""
        tmp_conf = "/tmp/esp32_provision_wpa.conf"
        Path(tmp_conf).write_text(wpa_conf)

        run(f"wpa_supplicant -B -i {WIFI_IFACE} -c {tmp_conf}", check=False)
        time.sleep(3)

        # Obtenir une IP via DHCP (le serveur DHCP de l'ESP32 fournit 192.168.4.x)
        run(f"dhclient {WIFI_IFACE}", check=False)
        time.sleep(2)

        # Vérifier la connectivité
        result = run(f"ping -c 2 -W 3 {ESP32_AP_IP}", check=False)
        if result.returncode == 0:
            log.info(f"Connecté à l'ESP32 AP ({ESP32_AP_IP})")
            return True
        else:
            log.warning("Pas de réponse ping vers l'ESP32")
            return False

    except Exception as e:
        log.error(f"Erreur connexion à l'ESP32 AP : {e}")
        return False


def disconnect_from_esp32_ap():
    """Déconnecte proprement wlan0 du réseau ESP32."""
    run("killall wpa_supplicant", check=False)
    run(f"ip addr flush dev {WIFI_IFACE}", check=False)
    time.sleep(1)


def send_credentials_to_esp32(wifi_ssid: str, wifi_password: str) -> bool:
    """
    Envoie les credentials WiFi à l'ESP32 via son portail HTTP.
    L'ESP32 expose un endpoint POST /provision.
    """
    log.info(f"Envoi des credentials WiFi vers {PROVISION_ENDPOINT}...")
    try:
        payload = {
            "ssid": wifi_ssid,
            "password": wifi_password,
            "server_ip": os.environ.get("RPI_IP", "192.168.10.1"),
            "server_port": int(os.environ.get("SOCKET_PORT", "9000")),
        }
        resp = requests.post(
            PROVISION_ENDPOINT,
            json=payload,
            timeout=PROVISION_TIMEOUT,
        )
        if resp.status_code == 200:
            log.info("Credentials envoyés avec succès à l'ESP32.")
            return True
        else:
            log.error(f"Réponse inattendue de l'ESP32 : {resp.status_code} - {resp.text}")
            return False

    except requests.exceptions.Timeout:
        log.error("Timeout lors de l'envoi des credentials à l'ESP32")
        return False
    except Exception as e:
        log.error(f"Erreur envoi credentials : {e}")
        return False


def provision_esp32(network: Esp32Network):
    """
    Pipeline complet de provisioning d'un ESP32.
    1. Stopper hostapd
    2. Se connecter au réseau AP de l'ESP32
    3. Envoyer les credentials
    4. Relancer hostapd
    """
    log.info(f"=== Début provisioning : {network.ssid} ===")

    wifi_ssid, wifi_password = get_wifi_credentials()

    stop_hostapd()

    try:
        connected = connect_to_esp32_ap(network.ssid)
        if not connected:
            log.error(f"Impossible de se connecter à {network.ssid}")
            return

        success = send_credentials_to_esp32(wifi_ssid, wifi_password)

        if success:
            log.info(f"✓ ESP32 {network.ssid} provisionné avec succès.")
            log.info("  L'ESP32 va redémarrer et rejoindre ELDERSAFE_SECURE.")
        else:
            log.error(f"✗ Échec du provisioning de {network.ssid}")

        disconnect_from_esp32_ap()

    finally:
        # Toujours restaurer hostapd, même en cas d'erreur
        start_hostapd()

    log.info(f"=== Fin provisioning : {network.ssid} ===")


def main():
    log.info("╔══════════════════════════════════════╗")
    log.info("║  Eldersafe Provisioner - Démarrage   ║")
    log.info("╚══════════════════════════════════════╝")

    # Attendre que le hotspot soit bien démarré
    time.sleep(10)

    provisioned_ssids: set[str] = set()

    while True:
        log.info("Scan des réseaux WiFi en cours...")
        esp32_networks = scan_for_esp32_aps()

        for network in esp32_networks:
            if network.ssid in provisioned_ssids:
                log.debug(f"Déjà provisionné cette session : {network.ssid}")
                continue

            if check_esp32_already_registered(network.mac_hint):
                log.info(f"ESP32 déjà enregistré en DB : {network.mac_hint}, skip.")
                provisioned_ssids.add(network.ssid)
                continue

            provision_esp32(network)
            provisioned_ssids.add(network.ssid)

            # Pause après un provisioning pour laisser l'ESP32 rejoindre le réseau
            time.sleep(15)

        if not esp32_networks:
            log.debug("Aucun ESP32 en mode setup détecté.")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
