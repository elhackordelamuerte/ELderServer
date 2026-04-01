#!/usr/bin/env python3
"""
Eldersafe - Socket Server (TCP)
================================
Ce serveur tourne dans Docker (service 'socket-server').
Rôle :
  1. Accepter les connexions TCP des ESP32
  2. Authentifier chaque ESP32 par son adresse MAC
  3. Rejeter les MAC non enregistrées
  4. Recevoir les données capteurs et les transmettre à FastAPI
  5. Maintenir les connexions persistantes (1-5 ESP32 simultanés)

Port : 9000 (configurable via SOCKET_PORT)
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional
import httpx

# --- Configuration ---
SOCKET_PORT = int(os.environ.get("SOCKET_PORT", "9000"))
SOCKET_HOST = "0.0.0.0"
FASTAPI_BASE_URL = os.environ.get("FASTAPI_BASE_URL", "http://fastapi:8000")
READ_TIMEOUT = 60.0          # secondes sans données avant déconnexion
MAX_MSG_SIZE = 4096          # bytes max par message
HEARTBEAT_INTERVAL = 30      # secondes entre les pings de keepalive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SOCKET] %(levelname)s - %(message)s",
)
log = logging.getLogger(__name__)


def normalize_mac(mac: str) -> Optional[str]:
    """
    Normalise une adresse MAC en format XX:XX:XX:XX:XX:XX (majuscules).
    Retourne None si le format est invalide.
    """
    mac = mac.upper().replace("-", ":").strip()
    if re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac):
        return mac
    return None


class Esp32Connection:
    """Représente une connexion active d'un ESP32."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.mac_address: Optional[str] = None
        self.device_id: Optional[int] = None
        self.authenticated = False
        self.connected_at = time.time()
        peer = writer.get_extra_info("peername")
        self.remote_ip = peer[0] if peer else "unknown"
        self.remote_port = peer[1] if peer else 0

    def __str__(self):
        return f"ESP32[{self.mac_address or self.remote_ip}]"

    async def send(self, data: dict):
        """Envoie un message JSON à l'ESP32."""
        try:
            msg = json.dumps(data) + "\n"
            self.writer.write(msg.encode())
            await self.writer.drain()
        except Exception as e:
            log.error(f"{self} - Erreur envoi : {e}")
            raise

    async def recv(self) -> Optional[dict]:
        """Reçoit et parse un message JSON de l'ESP32."""
        try:
            raw = await asyncio.wait_for(
                self.reader.readline(),
                timeout=READ_TIMEOUT,
            )
            if not raw:
                return None
            return json.loads(raw.decode().strip())
        except asyncio.TimeoutError:
            log.warning(f"{self} - Timeout (pas de données depuis {READ_TIMEOUT}s)")
            return None
        except json.JSONDecodeError as e:
            log.error(f"{self} - Message JSON invalide : {e}")
            return None

    def close(self):
        try:
            self.writer.close()
        except Exception:
            pass


# --- Couche HTTP vers FastAPI ---

async def api_register_device(mac: str, ip: str) -> Optional[dict]:
    """
    Enregistre ou récupère un device IoT dans FastAPI.
    POST /api/iot-devices/register
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{FASTAPI_BASE_URL}/api/iot-devices/register",
                json={"mac_address": mac, "ip_address": ip},
                timeout=5.0,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            log.error(f"API register failed: {resp.status_code} - {resp.text}")
            return None
        except Exception as e:
            log.error(f"Erreur API register_device : {e}")
            return None


async def api_is_mac_authorized(mac: str) -> bool:
    """
    Vérifie si la MAC est autorisée (enregistrée et status=active).
    GET /api/iot-devices/authorized/{mac}
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{FASTAPI_BASE_URL}/api/iot-devices/authorized/{mac}",
                timeout=5.0,
            )
            return resp.status_code == 200 and resp.json().get("authorized", False)
        except Exception as e:
            log.error(f"Erreur API is_mac_authorized : {e}")
            return False


async def api_store_sensor_data(device_id: int, data: dict):
    """
    Envoie les données capteurs à FastAPI pour stockage.
    POST /api/sensor-data
    """
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{FASTAPI_BASE_URL}/api/sensor-data",
                json={"device_id": device_id, "payload": data},
                timeout=5.0,
            )
        except Exception as e:
            log.error(f"Erreur API store_sensor_data : {e}")


# --- Handlers de messages ---

async def handle_auth(conn: Esp32Connection, msg: dict) -> bool:
    """
    Authentifie l'ESP32 via son adresse MAC.
    Message attendu : {"type": "auth", "mac": "AA:BB:CC:DD:EE:FF"}
    """
    raw_mac = msg.get("mac", "")
    mac = normalize_mac(raw_mac)

    if not mac:
        log.warning(f"{conn} - MAC invalide reçue : '{raw_mac}'")
        await conn.send({"type": "auth_response", "status": "error", "reason": "invalid_mac"})
        return False

    conn.mac_address = mac

    # Vérifier si la MAC est autorisée
    authorized = await api_is_mac_authorized(mac)

    if not authorized:
        # Première connexion : tenter d'enregistrer
        log.info(f"{conn} - MAC {mac} inconnue, tentative d'enregistrement...")
        device = await api_register_device(mac, conn.remote_ip)

        if device:
            conn.device_id = device["id"]
            conn.authenticated = True
            log.info(f"✓ Nouveau device enregistré : {mac} (id={conn.device_id})")
            await conn.send({
                "type": "auth_response",
                "status": "ok",
                "device_id": conn.device_id,
                "message": "Enregistrement réussi",
            })
            return True
        else:
            log.error(f"✗ Échec enregistrement MAC {mac}")
            await conn.send({"type": "auth_response", "status": "error", "reason": "registration_failed"})
            return False
    else:
        # MAC connue et autorisée
        device = await api_register_device(mac, conn.remote_ip)  # met à jour l'IP
        conn.device_id = device["id"] if device else None
        conn.authenticated = True
        log.info(f"✓ Auth OK : {mac} (id={conn.device_id})")
        await conn.send({
            "type": "auth_response",
            "status": "ok",
            "device_id": conn.device_id,
        })
        return True


async def handle_data(conn: Esp32Connection, msg: dict):
    """
    Traite les données capteurs envoyées par l'ESP32.
    Message attendu : {"type": "data", "payload": {...}}
    """
    payload = msg.get("payload", {})
    if not payload:
        log.warning(f"{conn} - Données capteurs vides")
        return

    log.debug(f"{conn} - Données reçues : {payload}")
    await api_store_sensor_data(conn.device_id, payload)
    await conn.send({"type": "ack", "status": "ok"})


async def handle_ping(conn: Esp32Connection):
    """Répond au ping de l'ESP32 pour maintenir la connexion."""
    await conn.send({"type": "pong", "ts": int(time.time())})


# --- Loop principale par connexion ---

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Gère une connexion ESP32 de bout en bout."""
    conn = Esp32Connection(reader, writer)
    log.info(f"Nouvelle connexion : {conn.remote_ip}:{conn.remote_port}")

    try:
        # --- Étape 1 : Authentification (obligatoire en premier) ---
        auth_msg = await conn.recv()

        if not auth_msg or auth_msg.get("type") != "auth":
            log.warning(f"{conn} - Premier message non-auth, déconnexion.")
            await conn.send({"type": "error", "reason": "auth_required"})
            return

        auth_ok = await handle_auth(conn, auth_msg)
        if not auth_ok:
            log.warning(f"{conn} - Authentification échouée, déconnexion.")
            return

        # --- Étape 2 : Boucle de réception des données ---
        log.info(f"{conn} - Session active.")

        while True:
            msg = await conn.recv()

            if msg is None:
                log.info(f"{conn} - Connexion fermée ou timeout.")
                break

            msg_type = msg.get("type", "")

            if msg_type == "data":
                await handle_data(conn, msg)
            elif msg_type == "ping":
                await handle_ping(conn)
            elif msg_type == "disconnect":
                log.info(f"{conn} - Déconnexion propre demandée.")
                break
            else:
                log.warning(f"{conn} - Type de message inconnu : '{msg_type}'")

    except ConnectionResetError:
        log.info(f"{conn} - Connexion réinitialisée par l'ESP32.")
    except Exception as e:
        log.error(f"{conn} - Erreur inattendue : {e}", exc_info=True)
    finally:
        duration = int(time.time() - conn.connected_at)
        log.info(f"{conn} - Déconnecté après {duration}s.")
        conn.close()


async def main():
    log.info("╔══════════════════════════════════════╗")
    log.info("║   Eldersafe Socket Server - Start    ║")
    log.info(f"║   Port : {SOCKET_PORT}                        ║")
    log.info("╚══════════════════════════════════════╝")

    server = await asyncio.start_server(
        handle_client,
        host=SOCKET_HOST,
        port=SOCKET_PORT,
    )

    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    log.info(f"Serveur TCP en écoute sur {addrs}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
