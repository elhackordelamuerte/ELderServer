"""
Eldersafe - SQLAlchemy Models
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, JSON, Enum
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class DeviceStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    test = "test"


class LocalServer(Base):
    """Représente un Raspberry Pi (serveur local)."""
    __tablename__ = "local_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hostname = Column(String(255), nullable=False)
    ip_address = Column(String(45), nullable=False)
    location = Column(String(255), nullable=True)       # ex: "Chambre 12 - Aile B"
    status = Column(String(50), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    devices = relationship("IoTDevice", back_populates="local_server")


class IoTDevice(Base):
    """Représente un ESP32 enregistré."""
    __tablename__ = "iot_devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mac_address = Column(String(17), unique=True, nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    localserver_id = Column(Integer, ForeignKey("local_servers.id"), nullable=True)
    resident_id = Column(Integer, ForeignKey("residents.id"), nullable=True)
    status = Column(
        Enum("active", "inactive", "test", name="device_status"),
        default="active",
        nullable=False,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    local_server = relationship("LocalServer", back_populates="devices")
    sensor_data = relationship("SensorData", back_populates="device")


class SensorData(Base):
    """Données capteurs reçues depuis un ESP32."""
    __tablename__ = "sensor_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey("iot_devices.id"), nullable=False, index=True)
    payload = Column(JSON, nullable=False)      # données flexibles (température, mouvement, etc.)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relations
    device = relationship("IoTDevice", back_populates="sensor_data")
