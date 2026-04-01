# ============================================================
# Dockerfile for Eldersafe Socket Server
# ============================================================

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY socket_server.py .

# Expose port
EXPOSE 9000

# Run socket server
CMD ["python", "socket_server.py"]
