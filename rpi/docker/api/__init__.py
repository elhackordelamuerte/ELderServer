# Eldersafe API Package
"""
Eldersafe API - FastAPI application for IoT device management
"""

from .main import app
from .database import get_db, init_db
from .models import IotDevice, TelemetryData, SocketSession

__version__ = "1.0.0"
__all__ = ["app", "get_db", "init_db", "IotDevice", "TelemetryData", "SocketSession"]
