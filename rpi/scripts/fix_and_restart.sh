#!/bin/bash
# ============================================================
# Eldersafe - Fix and Restart Service
# Run this on your Raspberry Pi to apply the latest fixes
# ============================================================

set -e

echo "🚀 Arret des services Eldersafe..."
docker-compose -f /home/cmoi/ELderServer/rpi/docker/docker-compose.yml down

echo "🔧 Reconstruction et demarrage des services..."
echo "Note: Le build peut prendre une minute le temps d'installer 'cryptography'..."
docker-compose -f /home/cmoi/ELderServer/rpi/docker/docker-compose.yml up -d --build

echo "⏳ Attente du démarrage de FastAPI (Healthcheck)..."
MAX_RETRIES=30
COUNT=0
while [ $COUNT -lt $MAX_RETRIES ]; do
    STATE=$(docker inspect --format='{{json .State.Health.Status}}' eldersafe-fastapi 2>/dev/null || echo "unhealthy")
    if [ "$STATE" == "\"healthy\"" ]; then
        echo "✅ FastAPI est en ligne et healthy !"
        break
    fi
    echo -n "."
    sleep 2
    COUNT=$((COUNT+1))
done

if [ $COUNT -eq $MAX_RETRIES ]; then
    echo "❌ Timeout : FastAPI semble avoir des difficultes à démarrer."
fi

echo "📋 Logs récents de FastAPI :"
docker logs eldersafe-fastapi --tail 30

echo ""
echo "📊 Etat des conteneurs :"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
