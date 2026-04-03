"""
Eldersafe API - SQLAlchemy Models
==================================
ORM models for IoT devices and telemetry.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Index, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()

class IotDevice(Base):
    """IoT Device (ESP32) registered in the system."""
    
    __tablename__ = "iot_devices"
    __table_args__ = (
        Index("idx_mac_address", "mac_address"),
        Index("idx_device_name", "device_name"),
    )

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(17), unique=True, nullable=False)  # AA:BB:CC:DD:EE:FF
    device_name = Column(String(100), nullable=False, default="Unnamed Device")
    device_type = Column(String(50), default="ESP32")
    ip_address = Column(String(45), nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
    status = Column(String(20), default="active") # active, inactive, test
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=True)
    
    # Metadata
    location = Column(String(255), nullable=True)
    notes = Column(String(500), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relations
    telemetry_data = relationship("TelemetryData", back_populates="device", cascade="all, delete-orphan")
    socket_sessions = relationship("SocketSession", back_populates="device", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<IotDevice(id={self.id}, mac={self.mac_address}, name={self.device_name})>"


class TelemetryData(Base):
    """Sensor data (temperature, humidity, battery, etc.)"""
    
    __tablename__ = "telemetry_data"
    __table_args__ = (
        Index("idx_device_id", "device_id"),
        Index("idx_timestamp", "timestamp"),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("iot_devices.id"), nullable=False)
    
    # Generic data fields
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    battery_mv = Column(Integer, nullable=True)
    uptime_s = Column(Integer, nullable=True)
    
    # JSON for custom/arbitrary data
    extra_data = Column(JSON, nullable=True)
    
    # Timestamps
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relations
    device = relationship("IotDevice", back_populates="telemetry_data")

    def __repr__(self):
        return f"<TelemetryData(device_id={self.device_id}, temp={self.temperature}, ts={self.timestamp})>"


class SocketSession(Base):
    """TCP connection session for a device."""
    
    __tablename__ = "socket_sessions"
    __table_args__ = (
        Index("idx_session_device_id", "device_id"),
        Index("idx_connected_at", "connected_at"),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("iot_devices.id"), nullable=False)
    
    # Remote address
    remote_ip = Column(String(45), nullable=True)
    remote_port = Column(Integer, nullable=True)
    
    # Timestamps
    connected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    disconnected_at = Column(DateTime, nullable=True)
    
    # Relations
    device = relationship("IotDevice", back_populates="socket_sessions")

    def __repr__(self):
        return f"<SocketSession(device_id={self.device_id}, remote={self.remote_ip}:{self.remote_port})>"
