-- ============================================================
-- Eldersafe - Migration SQL initiale
-- Tables : local_servers, iot_devices, sensor_data
-- Moteur  : MySQL / MariaDB
-- ============================================================

CREATE DATABASE IF NOT EXISTS eldersafe_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE eldersafe_db;

-- ------------------------------------------------------------
-- Table : local_servers (Raspberry Pi)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS local_servers (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    hostname    VARCHAR(255)    NOT NULL COMMENT 'Nom d hôte du RPI (ex: eldersafe-rpi-01)',
    ip_address  VARCHAR(45)     NOT NULL COMMENT 'IP statique du RPI sur son réseau WiFi',
    location    VARCHAR(255)    NULL     COMMENT 'Localisation physique (ex: Chambre 12)',
    wifi_ssid   VARCHAR(64)     NULL     COMMENT 'SSID du réseau WiFi géré par ce RPI',
    status      ENUM('active','inactive','maintenance') NOT NULL DEFAULT 'active',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Serveurs locaux Raspberry Pi';

-- ------------------------------------------------------------
-- Table : iot_devices (ESP32)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS iot_devices (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    mac_address     VARCHAR(17)     NOT NULL COMMENT 'Format AA:BB:CC:DD:EE:FF',
    ip_address      VARCHAR(45)     NULL     COMMENT 'IP DHCP assignée par le RPI',
    localserver_id  INT UNSIGNED    NULL     COMMENT 'RPI auquel ce device est rattaché',
    resident_id     INT UNSIGNED    NULL     COMMENT 'Résident surveillé (optionnel)',
    status          ENUM('active','inactive','test') NOT NULL DEFAULT 'active',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Contraintes
    CONSTRAINT uq_mac_address UNIQUE (mac_address),
    CONSTRAINT fk_device_server
        FOREIGN KEY (localserver_id)
        REFERENCES local_servers(id)
        ON DELETE SET NULL
        ON UPDATE CASCADE,

    -- Index
    INDEX idx_mac_address (mac_address),
    INDEX idx_status (status),
    INDEX idx_localserver (localserver_id)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Devices IoT ESP32 enregistrés';

-- ------------------------------------------------------------
-- Table : sensor_data (données capteurs)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sensor_data (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    device_id   INT UNSIGNED    NOT NULL COMMENT 'ESP32 source',
    payload     JSON            NOT NULL COMMENT 'Données brutes (température, accéléromètre, etc.)',
    recorded_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Contraintes
    CONSTRAINT fk_sensor_device
        FOREIGN KEY (device_id)
        REFERENCES iot_devices(id)
        ON DELETE CASCADE,

    -- Index (optimisé pour les requêtes par device + temps)
    INDEX idx_device_time (device_id, recorded_at),
    INDEX idx_recorded_at (recorded_at)

) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Données capteurs des ESP32';

-- ------------------------------------------------------------
-- Données initiales : serveur local par défaut
-- (sera mis à jour par install.sh avec les vraies valeurs)
-- ------------------------------------------------------------
INSERT IGNORE INTO local_servers (id, hostname, ip_address, location, wifi_ssid, status)
VALUES (1, 'eldersafe-rpi-01', '192.168.10.1', 'Serveur principal', 'ELDERSAFE_SECURE', 'active');

-- ------------------------------------------------------------
-- Vue pratique : devices actifs avec infos serveur
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_active_devices AS
SELECT
    d.id,
    d.mac_address,
    d.ip_address,
    d.status,
    d.created_at,
    d.updated_at,
    s.hostname       AS server_hostname,
    s.location       AS server_location
FROM iot_devices d
LEFT JOIN local_servers s ON d.localserver_id = s.id
WHERE d.status = 'active';
