-- ============================================================
-- Eldersafe - Database initialization (MySQL 8.0)
-- Run automatically by Docker (initdb.d/001_init.sql)
-- ============================================================

-- Create database if not exists
CREATE DATABASE IF NOT EXISTS `eldersafe_db` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `eldersafe_db`;

-- ============================================================
-- IoT DEVICES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS `iot_devices` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `mac_address` VARCHAR(17) NOT NULL UNIQUE COMMENT 'AA:BB:CC:DD:EE:FF format',
    `device_name` VARCHAR(100) NOT NULL,
    `device_type` VARCHAR(50) DEFAULT 'ESP32' COMMENT 'Device type (ESP32, sensor, etc.)',
    `is_active` BOOLEAN DEFAULT TRUE,
    `last_seen` DATETIME NULL COMMENT 'Last successful connection',
    `location` VARCHAR(255) NULL,
    `notes` TEXT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX `idx_mac_address` (`mac_address`),
    INDEX `idx_device_name` (`device_name`),
    INDEX `idx_is_active` (`is_active`),
    INDEX `idx_last_seen` (`last_seen`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Registered IoT devices (ESP32 sensors)';

-- ============================================================
-- TELEMETRY DATA TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS `telemetry_data` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `device_id` INT NOT NULL,
    `temperature` FLOAT NULL COMMENT 'Temperature in Celsius',
    `humidity` FLOAT NULL COMMENT 'Humidity %',
    `battery_mv` INT NULL COMMENT 'Battery voltage in mV',
    `uptime_s` INT NULL COMMENT 'Device uptime in seconds',
    `extra_data` JSON NULL COMMENT 'Additional custom data',
    `timestamp` DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT `fk_telemetry_device` 
        FOREIGN KEY (`device_id`) 
        REFERENCES `iot_devices`(`id`) 
        ON DELETE CASCADE,
    
    INDEX `idx_device_id` (`device_id`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_device_timestamp` (`device_id`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Sensor readings from devices';

-- ============================================================
-- SOCKET SESSIONS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS `socket_sessions` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `device_id` INT NOT NULL,
    `remote_ip` VARCHAR(45) NULL COMMENT 'IPv4 or IPv6 address',
    `remote_port` INT NULL,
    `connected_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `disconnected_at` DATETIME NULL,
    
    CONSTRAINT `fk_socket_device` 
        FOREIGN KEY (`device_id`) 
        REFERENCES `iot_devices`(`id`) 
        ON DELETE CASCADE,
    
    INDEX `idx_device_id` (`device_id`),
    INDEX `idx_connected_at` (`connected_at`),
    INDEX `idx_remote_ip` (`remote_ip`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='TCP socket connection history';

-- ============================================================
-- VIEWS
-- ============================================================

-- Active devices (with latest telemetry)
CREATE OR REPLACE VIEW `v_active_devices` AS
SELECT 
    d.id,
    d.mac_address,
    d.device_name,
    d.device_type,
    d.location,
    d.last_seen,
    d.created_at,
    COALESCE(t.temperature, -999) AS last_temperature,
    COALESCE(t.humidity, -999) AS last_humidity,
    COALESCE(t.battery_mv, 0) AS last_battery_mv,
    t.timestamp AS last_telemetry_time
FROM `iot_devices` d
LEFT JOIN `telemetry_data` t ON d.id = t.device_id
WHERE d.is_active = TRUE
AND (t.timestamp = (
    SELECT MAX(timestamp) FROM `telemetry_data` 
    WHERE device_id = d.id
) OR t.timestamp IS NULL)
ORDER BY d.created_at DESC;

-- ============================================================
-- INITIAL DATA (optional - remove if not needed)
-- ============================================================

-- Insert example device for testing
INSERT IGNORE INTO `iot_devices` 
    (`mac_address`, `device_name`, `device_type`, `location`) 
VALUES 
    ('AA:BB:CC:DD:EE:FF', 'Test Device', 'ESP32', 'Lab');

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
