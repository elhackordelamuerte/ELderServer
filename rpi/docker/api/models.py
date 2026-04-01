"""
Eldersafe API - SQLAlchemy Models
==================================
Modèles ORM pour les appareils IoT et les connexions.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Index, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class IotDevice(Base):
    """Appareil IoT (ESP32) enregistré dans le système."""
    
    __tablename__ = "iot_devices"
    __table_args__ = (
        Index("idx_mac_address", "mac_address"),
        Index("idx_device_name", "device_name"),
    )

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(17), unique=True, nullable=False)  # AA:BB:CC:DD:EE:FF
    device_name = Column(String(100), nullable=False)
    device_type = Column(String(50), default="ESP32")
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
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
    """Données capteurs (température, humidité, batterie, etc.)"""
    
    __tablename__ = "telemetry_data"
    __table_args__ = (
        Index("idx_device_id", "device_id"),
        Index("idx_timestamp", "timestamp"),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("iot_devices.id"), nullable=False)
    
    # Données génériques
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    battery_mv = Column(Integer, nullable=True)
    uptime_s = Column(Integer, nullable=True)
    
    # JSON pour données customs
    extra_data = Column(JSON, nullable=True)
    
    # Métadonnées
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relations
    device = relationship("IotDevice", back_populates="telemetry_data")

    def __repr__(self):
        return f"<TelemetryData(device_id={self.device_id}, temp={self.temperature}, ts={self.timestamp})>"


class SocketSession(Base):
    """Session de connexion TCP d'un appareil."""
    
    __tablename__ = "socket_sessions"
    __table_args__ = (
        Index("idx_device_id", "device_id"),
        Index("idx_connected_at", "connected_at"),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("iot_devices.id"), nullable=False)
    
    # Adresse IP source
    remote_ip = Column(String(45), nullable=True)
    remote_port = Column(Integer, nullable=True)
    
    # Timestamps
    connected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    disconnected_at = Column(DateTime, nullable=True)
    
    # Relations
    device = relationship("IotDevice", back_populates="socket_sessions")

    def __repr__(self):
        return f"<SocketSession(device_id={self.device_id}, remote={self.remote_ip}:{self.remote_port})>"

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
