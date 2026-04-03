#!/bin/bash
# ============================================================
# Eldersafe - Fix and Restart Service
# Run this on your Raspberry Pi to apply the latest fixes
# ============================================================

set -e

echo "🚀 Arret des services Eldersafe..."
docker-compose -f /home/cmoi/ELderServer/rpi/docker/docker-compose.yml down

echo "🧹 Nettoyage des volumes (optionnel, decommentez si besoin de reset DB)..."
# docker volume rm docker_mysql_data

echo "🔧 Reconstruction et demarrage des services..."
# On force le build pour appliquer les corrections de code dans l'image FastAPI
docker-compose -f /home/cmoi/ELderServer/rpi/docker/docker-compose.yml up -d --build

echo "✅ Services redemarres. Verification des logs FastAPI..."
sleep 5
docker logs eldersafe-fastapi --tail 20

echo ""
echo "📊 Etat des conteneurs :"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
