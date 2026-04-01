"""
Eldersafe API - FastAPI Routes for IoT Devices
================================================
Endpoints consumed by:
  - Socket server (auth, register device, store telemetry)
  - Provisioner (check if MAC registered)
  - Frontend/Dashboard (list, CRUD operations)
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, validator

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from .database import get_db
from .models import IotDevice, TelemetryData, SocketSession, Base

# --- Pydantic Schemas ---

class MacAddressValidator:
    """Validate MAC address format."""
    
    @staticmethod
    def validate_mac(mac: str) -> str:
        """Accept both AA:BB:CC:DD:EE:FF and AABBCCDDEEFF formats."""
        mac = mac.upper()
        if len(mac) == 17 and all(c in '0123456789ABCDEF:' for c in mac):
            return mac  # AA:BB:CC:DD:EE:FF format
        elif len(mac) == 12 and all(c in '0123456789ABCDEF' for c in mac):
            # Convert AABBCCDDEEFF to AA:BB:CC:DD:EE:FF
            return ':'.join(mac[i:i+2] for i in range(0, 12, 2))
        else:
            raise ValueError(f"Invalid MAC address format: {mac}")


class IotDeviceCreate(BaseModel):
    mac_address: str
    device_name: str
    device_type: str = "ESP32"
    location: Optional[str] = None
    notes: Optional[str] = None
    
    @validator('mac_address')
    def validate_mac_address(cls, v):
        return MacAddressValidator.validate_mac(v)


class IotDeviceUpdate(BaseModel):
    device_name: Optional[str] = None
    is_active: Optional[bool] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class IotDeviceOut(BaseModel):
    id: int
    mac_address: str
    device_name: str
    device_type: str
    is_active: bool
    last_seen: Optional[datetime]
    location: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TelemetryDataIn(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    battery_mv: Optional[int] = None
    uptime_s: Optional[int] = None
    extra_data: Optional[dict] = None


class SocketAuthRequest(BaseModel):
    mac: str
    
    @validator('mac')
    def validate_mac(cls, v):
        return MacAddressValidator.validate_mac(v)


class SocketAuthResponse(BaseModel):
    status: str  # "ok" or "error"
    device_id: Optional[int] = None
    reason: Optional[str] = None


# --- Router ---

router = APIRouter(prefix="/api/iot-devices", tags=["iot-devices"])


@router.post("/register", response_model=IotDeviceOut)
async def register_device(device: IotDeviceCreate, db: AsyncSession = Depends(get_db)):
    """Register a new IoT device (administratively)."""
    
    # Check if MAC already exists
    result = await db.execute(
        select(IotDevice).where(IotDevice.mac_address == device.mac_address)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Device with MAC {device.mac_address} already exists")
    
    # Create device
    db_device = IotDevice(
        mac_address=device.mac_address,
        device_name=device.device_name,
        device_type=device.device_type,
        location=device.location,
        notes=device.notes,
    )
    db.add(db_device)
    await db.commit()
    await db.refresh(db_device)
    return db_device


@router.post("/socket-auth", response_model=SocketAuthResponse)
async def socket_authenticate(auth: SocketAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Socket server authentication endpoint.
    Called when ESP32 connects via TCP - must verify MAC is registered.
    """
    mac = auth.mac
    
    # Find device by MAC
    result = await db.execute(
        select(IotDevice).where(IotDevice.mac_address == mac)
    )
    device = result.scalar_one_or_none()
    
    if not device:
        return SocketAuthResponse(
            status="error",
            reason=f"MAC {mac} not registered"
        )
    
    if not device.is_active:
        return SocketAuthResponse(
            status="error",
            reason=f"Device {device.device_name} is inactive"
        )
    
    # Update last_seen
    device.last_seen = datetime.utcnow()
    await db.commit()
    
    return SocketAuthResponse(
        status="ok",
        device_id=device.id,
    )


@router.post("/telemetry/{device_id}")
async def store_telemetry(
    device_id: int,
    data: TelemetryDataIn,
    db: AsyncSession = Depends(get_db)
):
    """Store sensor data from a device."""
    
    # Verify device exists
    result = await db.execute(
        select(IotDevice).where(IotDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Create telemetry record
    telemetry = TelemetryData(
        device_id=device_id,
        temperature=data.temperature,
        humidity=data.humidity,
        battery_mv=data.battery_mv,
        uptime_s=data.uptime_s,
        extra_data=data.extra_data,
        timestamp=datetime.utcnow(),
    )
    db.add(telemetry)
    
    # Update device last_seen
    device.last_seen = datetime.utcnow()
    
    await db.commit()
    return {"status": "ok", "telemetry_id": telemetry.id}


@router.get("/check-mac/{mac_address}")
async def check_mac_registered(mac_address: str, db: AsyncSession = Depends(get_db)):
    """Check if a MAC address is registered (used by provisioner)."""
    mac = MacAddressValidator.validate_mac(mac_address)
    
    result = await db.execute(
        select(IotDevice).where(IotDevice.mac_address == mac)
    )
    device = result.scalar_one_or_none()
    
    return {
        "registered": device is not None,
        "device_id": device.id if device else None,
    }


@router.get("/", response_model=List[IotDeviceOut])
async def list_devices(
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """List all devices."""
    query = select(IotDevice)
    if active_only:
        query = query.where(IotDevice.is_active == True)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{device_id}", response_model=IotDeviceOut)
async def get_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """Get a specific device."""
    result = await db.execute(
        select(IotDevice).where(IotDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.put("/{device_id}", response_model=IotDeviceOut)
async def update_device(
    device_id: int,
    update: IotDeviceUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a device."""
    result = await db.execute(
        select(IotDevice).where(IotDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    if update.device_name:
        device.device_name = update.device_name
    if update.is_active is not None:
        device.is_active = update.is_active
    if update.location:
        device.location = update.location
    if update.notes:
        device.notes = update.notes
    
    await db.commit()
    await db.refresh(device)
    return device


@router.delete("/{device_id}")
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a device."""
    result = await db.execute(
        select(IotDevice).where(IotDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    await db.delete(device)
    await db.commit()
    return {"status": "deleted"}


@router.get("/telemetry/{device_id}/latest")
async def get_latest_telemetry(
    device_id: int,
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get latest telemetry records for a device."""
    result = await db.execute(
        select(TelemetryData)
        .where(TelemetryData.device_id == device_id)
        .order_by(TelemetryData.timestamp.desc())
        .limit(limit)
    )
    return result.scalars().all()


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
