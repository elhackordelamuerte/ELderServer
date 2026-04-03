#!/usr/bin/env python3
"""
Eldersafe - Socket Server (TCP asyncio + aiohttp Dashboard)
===========================================================
Runs in Docker.
TCP Socket listens on 192.168.10.1:9000
HTTP Dashboard listens on 0.0.0.0:8080
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional, Dict, Set

import httpx
from aiohttp import web
import aiohttp

# --- Configuration ---
SOCKET_PORT = int(os.environ.get("SOCKET_PORT", "9000"))
SOCKET_HOST = "0.0.0.0"
WEB_PORT = 8080
WEB_HOST = "0.0.0.0"
FASTAPI_BASE_URL = os.environ.get("FASTAPI_BASE_URL", "http://fastapi:8000")
WIFI_SSID = os.environ.get("WIFI_SSID", "ELDERSAFE_SECURE")
WIFI_PASSWORD = os.environ.get("WIFI_PASSWORD", "Eldersafe123!")
READ_TIMEOUT = 60.0
HEARTBEAT_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SERVER] %(levelname)s - %(message)s",
)
log = logging.getLogger(__name__)

# Global state for web dashboard
active_ws_clients: Set[web.WebSocketResponse] = set()
esp32_connections: Dict[str, 'Esp32Connection'] = {}

def normalize_mac(mac: str) -> Optional[str]:
    """Normalises MAC to XX:XX:XX:XX:XX:XX."""
    mac = mac.upper().replace("-", ":").strip()
    if re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac):
        return mac
    return None

class Esp32Connection:
    """Represents an active ESP32 connection."""
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
        self.conn_id = f"{self.remote_ip}:{self.remote_port}"
        self.msg_count = 0

    def __str__(self):
        return f"ESP32[{self.mac_address or self.remote_ip}]"

    async def send(self, data: dict):
        try:
            msg = json.dumps(data) + "\n"
            self.writer.write(msg.encode())
            await self.writer.drain()
        except Exception as e:
            log.error(f"{self} - Send error: {e}")
            raise

    async def recv(self) -> Optional[dict]:
        try:
            raw = await asyncio.wait_for(
                self.reader.readline(),
                timeout=READ_TIMEOUT,
            )
            if not raw:
                return None
            return json.loads(raw.decode().strip())
        except asyncio.TimeoutError:
            log.warning(f"{self} - Timeout")
            return None
        except json.JSONDecodeError as e:
            log.error(f"{self} - Invalid JSON: {e}")
            return None

    def close(self):
        try:
            self.writer.close()
        except:
            pass

# --- Web UI Setup ---
async def broadcast_ws(event: dict):
    """Broadast event to all connected dashboard websockets."""
    if not active_ws_clients:
        return
    
    msg = json.dumps(event)
    for ws in list(active_ws_clients):
        try:
            await ws.send_str(msg)
        except Exception:
            active_ws_clients.discard(ws)

def emit_event(event_type: str, conn: Esp32Connection, message: str = "", level: str = "", data: dict = None):
    """Wrapper to emit UI events."""
    event = {
        "event_type": event_type,
        "conn_id": conn.conn_id,
        "mac": conn.mac_address,
        "ip": conn.remote_ip,
        "port": conn.remote_port,
        "ts": time.time(),
        "message": message,
        "level": level,
        "data": data or {}
    }
    asyncio.create_task(broadcast_ws(event))

async def init_web_app():
    app = web.Application()
    
    async def handle_index(request):
        try:
            with open("dashboard.html", "r") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html")
        except FileNotFoundError:
            return web.Response(text="dashboard.html not found", status=404)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        active_ws_clients.add(ws)
        log.info(f"Dashboard WS Client connected. Total: {len(active_ws_clients)}")
        
        # Send initial sync state
        sync_payload = []
        for c in esp32_connections.values():
            sync_payload.append({
                "conn_id": c.conn_id,
                "mac": c.mac_address,
                "ip": c.remote_ip,
                "port": c.remote_port,
                "device_id": c.device_id,
                "uptime": time.time() - c.connected_at,
                "msg_count": c.msg_count
            })
            
        await ws.send_json({
            "event_type": "sync",
            "data": sync_payload,
            "wifi": {
                "ssid": WIFI_SSID,
                "password": WIFI_PASSWORD
            }
        })

        try:
            async for msg in ws:
                pass # We don't expect messages from dashboard yet
        finally:
            active_ws_clients.discard(ws)
            log.info("Dashboard WS Client disconnected.")
        return ws

    app.router.add_get('/', handle_index)
    app.router.add_get('/ws', handle_ws)
    return app


# --- API / FastAPI Layer ---
async def api_register_device(mac: str, ip: str) -> Optional[dict]:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{FASTAPI_BASE_URL}/api/iot-devices/register",
                json={"mac_address": mac, "ip_address": ip},
                timeout=5.0,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            return None
        except Exception:
            return None

async def api_is_mac_authorized(mac: str) -> bool:
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{FASTAPI_BASE_URL}/api/iot-devices/authorized/{mac}",
                timeout=5.0,
            )
            return resp.status_code == 200 and resp.json().get("authorized", False)
        except Exception:
            return False

async def api_store_sensor_data(device_id: int, data: dict):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{FASTAPI_BASE_URL}/api/sensor-data",
                json={"device_id": device_id, "payload": data},
                timeout=5.0,
            )
        except Exception:
            pass


# --- TCP Socket Handlers ---
async def handle_auth(conn: Esp32Connection, msg: dict) -> bool:
    raw_mac = msg.get("mac", "")
    mac = normalize_mac(raw_mac)

    if not mac:
        log.warning(f"{conn} - Invalid MAC")
        emit_event("error", conn, f"Invalid MAC: {raw_mac}", "error")
        await conn.send({"type": "auth_response", "status": "error", "reason": "invalid_mac"})
        return False

    conn.mac_address = mac
    authorized = await api_is_mac_authorized(mac)

    if not authorized:
        log.info(f"{conn} - Unregistered MAC, attempting register...")
        device = await api_register_device(mac, conn.remote_ip)
        if device:
            conn.device_id = device["id"]
            conn.authenticated = True
            log.info(f"Registered new device: {mac} (ID: {conn.device_id})")
            emit_event("auth_success", conn, f"Device registered.", "success", {"device_id": conn.device_id})
            await conn.send({"type": "auth_response", "status": "ok", "device_id": conn.device_id})
            return True
        else:
            emit_event("error", conn, f"Registration failed", "error")
            await conn.send({"type": "auth_response", "status": "error", "reason": "registration_failed"})
            return False
    else:
        device = await api_register_device(mac, conn.remote_ip) # Update IP
        conn.device_id = device["id"] if device else None
        conn.authenticated = True
        log.info(f"Auth OK: {mac} (ID: {conn.device_id})")
        emit_event("auth_success", conn, f"Device authenticated.", "success", {"device_id": conn.device_id})
        await conn.send({"type": "auth_response", "status": "ok", "device_id": conn.device_id})
        return True


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    conn = Esp32Connection(reader, writer)
    esp32_connections[conn.conn_id] = conn
    
    log.info(f"New connection: {conn.remote_ip}:{conn.remote_port}")
    emit_event("connect", conn, f"Device connected from {conn.remote_ip}", "info")

    try:
        auth_msg = await conn.recv()
        if not auth_msg or auth_msg.get("type") != "auth":
            log.warning(f"{conn} - Disconnecting, first msg not auth.")
            emit_event("error", conn, "Failed auth requirement", "error")
            await conn.send({"type": "error", "reason": "auth_required"})
            return

        conn.msg_count += 1
        auth_ok = await handle_auth(conn, auth_msg)
        if not auth_ok:
            return

        while True:
            msg = await conn.recv()
            if msg is None:
                break
            
            conn.msg_count += 1
            msg_type = msg.get("type", "")

            if msg_type == "data":
                payload = msg.get("payload", {})
                if payload:
                    await api_store_sensor_data(conn.device_id, payload)
                    emit_event("data", conn, f"Data rx: {len(str(payload))} chars", "data")
                    await conn.send({"type": "ack", "status": "ok"})
                    
            elif msg_type == "ping":
                emit_event("ping", conn)
                await conn.send({"type": "pong", "ts": int(time.time())})
                
            elif msg_type == "disconnect":
                break

    except ConnectionResetError:
        log.info(f"{conn} - Reset by peer.")
    except Exception as e:
        log.error(f"{conn} - Error: {e}")
    finally:
        del esp32_connections[conn.conn_id]
        emit_event("disconnect", conn, "Device disconnected.", "disconnect")
        conn.close()


async def main():
    log.info("╔════════════════════════════════════════════════╗")
    log.info("║ Eldersafe Socket & Dashboard Server Starting... ║")
    log.info("╚════════════════════════════════════════════════╝")

    # Start TCP Server
    tcp_server = await asyncio.start_server(
        handle_client,
        host=SOCKET_HOST,
        port=SOCKET_PORT,
    )
    log.info(f"TCP socket server active on {SOCKET_HOST}:{SOCKET_PORT}")

    # Start AIOHTTP Server
    web_app = await init_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()
    log.info(f"Dashboard web server active on http://{WEB_HOST}:{WEB_PORT}")

    async with tcp_server:
        await tcp_server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
