"""
Eldersafe API - FastAPI Routes for IoT Devices
================================================
Endpoints for IoT device management and telemetry.
Called by: socket-server, frontend/dashboard.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
import re
from pydantic import BaseModel, validator

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from database import get_db
from models import IotDevice, TelemetryData, SocketSession, Base

# --- Pydantic Schemas ---

class MacAddressValidator:
    """Validate MAC address format."""
    
    @staticmethod
    def validate_mac(v: str) -> str:
        """Accept AA:BB:CC:DD:EE:FF, AA-BB-CC-DD-EE-FF and AABBCCDDEEFF formats."""
        mac = v.upper().replace("-", ":").strip()
        if len(mac) == 17 and all(c in '0123456789ABCDEF:' for c in mac):
            return mac
        elif len(mac) == 12 and all(c in '0123456789ABCDEF' for c in mac):
            return ':'.join(mac[i:i+2] for i in range(0, 12, 2))
        else:
            raise ValueError(f"Invalid MAC address format: {v}")

class IotDeviceCreate(BaseModel):
    mac_address: str
    device_name: Optional[str] = "Unnamed Device"
    device_type: Optional[str] = "ESP32"
    ip_address: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    
    @validator('mac_address')
    def validate_mac_address(cls, v):
        return MacAddressValidator.validate_mac(v)

class IotDeviceUpdate(BaseModel):
    device_name: Optional[str] = None
    is_active: Optional[bool] = None
    status: Optional[str] = None # active, inactive, test
    location: Optional[str] = None
    notes: Optional[str] = None

class IotDeviceOut(BaseModel):
    id: int
    mac_address: str
    device_name: str
    device_type: str
    is_active: bool
    status: str
    last_seen: Optional[datetime]
    ip_address: Optional[str]
    location: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class AuthorizedResponse(BaseModel):
    authorized: bool
    device_id: Optional[int] = None
    status: Optional[str] = None

class SensorDataRequest(BaseModel):
    device_id: int
    payload: Dict[str, Any]

class TelemetryDataIn(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    battery_mv: Optional[int] = None
    uptime_s: Optional[int] = None
    extra_data: Optional[dict] = None

# --- Router ---

router = APIRouter(prefix="/api", tags=["IoT Devices"])

@router.post("/iot-devices/register", response_model=IotDeviceOut, status_code=201)
async def register_device(device: IotDeviceCreate, db: AsyncSession = Depends(get_db)):
    """Register or update an IoT device via MAC address."""
    
    # Check if MAC already exists
    result = await db.execute(
        select(IotDevice).where(IotDevice.mac_address == device.mac_address)
    )
    db_device = result.scalar_one_or_none()
    
    if db_device:
        # Update existing device info (like IP)
        if device.ip_address:
            db_device.ip_address = device.ip_address
        db_device.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(db_device)
        return db_device
    
    # Create new device
    db_device = IotDevice(
        mac_address=device.mac_address,
        device_name=device.device_name or "New Device",
        device_type=device.device_type or "ESP32",
        ip_address=device.ip_address,
        location=device.location,
        notes=device.notes,
        status="active",
        is_active=True
    )
    db.add(db_device)
    await db.commit()
    await db.refresh(db_device)
    return db_device

@router.get("/iot-devices/authorized/{mac_address}", response_model=AuthorizedResponse)
async def check_mac_authorized(mac_address: str, db: AsyncSession = Depends(get_db)):
    """Check if a MAC address is authorized & active."""
    try:
        mac = MacAddressValidator.validate_mac(mac_address)
    except ValueError:
        return AuthorizedResponse(authorized=False)
        
    result = await db.execute(select(IotDevice).where(IotDevice.mac_address == mac))
    device = result.scalar_one_or_none()
    
    if not device:
        return AuthorizedResponse(authorized=False)
    
    return AuthorizedResponse(
        authorized=(device.is_active and device.status == "active"),
        device_id=device.id,
        status=device.status
    )

@router.post("/iot-devices/telemetry", status_code=201)
async def store_telemetry(data: SensorDataRequest, db: AsyncSession = Depends(get_db)):
    """Store sensor/telemetry data for a device (modern path)."""
    
    # Verify device exists
    result = await db.execute(select(IotDevice).where(IotDevice.id == data.device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    # Parse payload if it has structure, otherwise store as extra_data
    payload = data.payload
    
    telemetry = TelemetryData(
        device_id=data.device_id,
        temperature=payload.get("temperature"),
        humidity=payload.get("humidity"),
        battery_mv=payload.get("battery_mv"),
        uptime_s=payload.get("uptime_s"),
        extra_data=payload, # Keep everything in extra_data too
        timestamp=datetime.utcnow()
    )
    db.add(telemetry)
    
    # Update last_seen
    device.last_seen = datetime.utcnow()
    await db.commit()
    return {"status": "ok"}

# Compatible endpoint for old socket_server
@router.post("/sensor-data", status_code=201)
async def store_sensor_data_alt(data: SensorDataRequest, db: AsyncSession = Depends(get_db)):
    return await store_telemetry(data, db)

@router.get("/iot-devices", response_model=List[IotDeviceOut])
async def list_devices(db: AsyncSession = Depends(get_db)):
    """List all registered IoT devices."""
    result = await db.execute(select(IotDevice).order_by(IotDevice.updated_at.desc()))
    return result.scalars().all()

@router.get("/iot-devices/{device_id}", response_model=IotDeviceOut)
async def get_device_details(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(IotDevice).where(IotDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device

@router.patch("/iot-devices/{device_id}", response_model=IotDeviceOut)
async def update_device_info(device_id: int, data: IotDeviceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(IotDevice).where(IotDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    if data.device_name is not None: device.device_name = data.device_name
    if data.is_active is not None: device.is_active = data.is_active
    if data.status is not None: device.status = data.status
    if data.location is not None: device.location = data.location
    if data.notes is not None: device.notes = data.notes
    
    await db.commit()
    await db.refresh(device)
    return device

@router.delete("/iot-devices/{device_id}", status_code=204)
async def delete_device(device_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(IotDevice).where(IotDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    await db.delete(device)
    await db.commit()
    return None
