"""
Eldersafe - FastAPI Routes : IoT Devices
==========================================
Endpoints consommés par :
  - Le socket server (auth, register, sensor data)
  - Le provisioner (check MAC hint)
  - Le frontend/dashboard (liste des devices)
"""

from datetime import datetime
from typing import Optional
import re

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from ..database import get_db
from ..models import IoTDevice, SensorData

router = APIRouter(prefix="/api", tags=["IoT Devices"])


# --- Schémas Pydantic ---

class DeviceRegisterRequest(BaseModel):
    mac_address: str
    ip_address: Optional[str] = None

    @validator("mac_address")
    def normalize_mac(cls, v):
        mac = v.upper().replace("-", ":").strip()
        if not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac):
            raise ValueError(f"Adresse MAC invalide : {v}")
        return mac


class DeviceResponse(BaseModel):
    id: int
    mac_address: str
    status: str
    ip_address: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SensorDataRequest(BaseModel):
    device_id: int
    payload: dict


class AuthorizedResponse(BaseModel):
    authorized: bool
    device_id: Optional[int] = None
    status: Optional[str] = None


# --- Endpoints ---

@router.post("/iot-devices/register", response_model=DeviceResponse, status_code=201)
async def register_or_update_device(
    req: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Enregistre un nouveau device IoT ou met à jour son IP si déjà connu.
    Appelé par le socket server à chaque nouvelle connexion TCP.
    """
    # Chercher si la MAC existe déjà
    result = await db.execute(
        select(IoTDevice).where(IoTDevice.mac_address == req.mac_address)
    )
    device = result.scalar_one_or_none()

    if device:
        # Mettre à jour l'IP et le timestamp
        device.ip_address = req.ip_address
        device.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(device)
        return device
    else:
        # Nouveau device
        device = IoTDevice(
            mac_address=req.mac_address,
            ip_address=req.ip_address,
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(device)
        await db.commit()
        await db.refresh(device)
        return device


@router.get("/iot-devices/authorized/{mac_address}", response_model=AuthorizedResponse)
async def check_mac_authorized(
    mac_address: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Vérifie si une adresse MAC est enregistrée et active.
    Appelé par le socket server avant d'accepter les données.
    """
    mac = mac_address.upper().replace("-", ":").strip()

    result = await db.execute(
        select(IoTDevice).where(IoTDevice.mac_address == mac)
    )
    device = result.scalar_one_or_none()

    if not device:
        return AuthorizedResponse(authorized=False)

    return AuthorizedResponse(
        authorized=(device.status == "active"),
        device_id=device.id,
        status=device.status,
    )


@router.get("/iot-devices/check-mac-hint/{mac_hint}")
async def check_mac_hint(mac_hint: str, db: AsyncSession = Depends(get_db)):
    """
    Vérifie si un ESP32 (identifié par les 6 derniers chars de son MAC)
    est déjà enregistré. Utilisé par le provisioner pour éviter
    de re-provisionner un device déjà connu.
    """
    result = await db.execute(
        select(IoTDevice).where(
            IoTDevice.mac_address.like(f"%{mac_hint.upper()}")
        )
    )
    device = result.scalar_one_or_none()
    return {"registered": device is not None}


@router.get("/iot-devices", response_model=list[DeviceResponse])
async def list_devices(db: AsyncSession = Depends(get_db)):
    """Liste tous les devices IoT enregistrés."""
    result = await db.execute(select(IoTDevice).order_by(IoTDevice.created_at.desc()))
    return result.scalars().all()


@router.patch("/iot-devices/{device_id}/status")
async def update_device_status(
    device_id: int,
    status: str,
    db: AsyncSession = Depends(get_db),
):
    """Met à jour le statut d'un device (active / inactive / test)."""
    if status not in ("active", "inactive", "test"):
        raise HTTPException(status_code=400, detail="Statut invalide")

    result = await db.execute(
        select(IoTDevice).where(IoTDevice.id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device introuvable")

    device.status = status
    device.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": device_id, "status": status}


@router.post("/sensor-data", status_code=201)
async def store_sensor_data(
    req: SensorDataRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Stocke les données capteurs envoyées par un ESP32.
    Appelé par le socket server après validation auth.
    """
    # Vérifier que le device existe
    result = await db.execute(
        select(IoTDevice).where(IoTDevice.id == req.device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device introuvable")

    entry = SensorData(
        device_id=req.device_id,
        payload=req.payload,
        recorded_at=datetime.utcnow(),
    )
    db.add(entry)
    await db.commit()
    return {"status": "ok"}
