/*
 * ============================================================
 * Eldersafe - Firmware ESP32
 * ============================================================
 * Comportement :
 *
 * [MODE 1 - SETUP] Pas de credentials WiFi en mémoire :
 *   → Lance un Access Point "ELDERSAFE_SETUP_XXXX" (XXXX = fin MAC)
 *   → Héberge un serveur HTTP sur 192.168.4.1
 *   → Attend que le RPI se connecte et envoie les credentials via POST /provision
 *   → Sauvegarde les credentials en NVS (flash non-volatile)
 *   → Redémarre en MODE 2
 *
 * [MODE 2 - NORMAL] Credentials WiFi disponibles :
 *   → Se connecte au réseau ELDERSAFE_SECURE du RPI
 *   → Ouvre une connexion TCP persistante vers le socket server
 *   → S'authentifie avec son adresse MAC
 *   → Envoie les données capteurs toutes les DATA_INTERVAL_MS ms
 *
 * Librairies requises (Arduino IDE / PlatformIO) :
 *   - WiFi.h          (built-in ESP32)
 *   - WebServer.h     (built-in ESP32)
 *   - Preferences.h   (built-in ESP32 - NVS)
 *   - ArduinoJson     (v6+ via Library Manager)
 * ============================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>

// --- Configuration ---
#define SETUP_AP_PREFIX     "ELDERSAFE_SETUP_"
#define SETUP_AP_CHANNEL    6
#define SETUP_AP_IP         "192.168.4.1"
#define WIFI_CONNECT_TIMEOUT_MS  15000
#define WIFI_RETRY_COUNT    5
#define SOCKET_CONNECT_TIMEOUT_MS 10000
#define DATA_INTERVAL_MS    5000          // Envoi données toutes les 5s
#define PING_INTERVAL_MS    25000         // Keepalive toutes les 25s
#define NVS_NAMESPACE       "eldersafe"
#define SERIAL_BAUD         115200

// --- Variables globales ---
Preferences prefs;
WebServer   setupServer(80);
WiFiClient  socketClient;

String wifiSSID     = "";
String wifiPassword = "";
String serverIP     = "";
int    serverPort   = 9000;

String deviceMAC    = "";
String deviceID_str = "";

unsigned long lastDataSent = 0;
unsigned long lastPingSent = 0;

bool isProvisioned = false;
bool socketConnected = false;

// ============================================================
// UTILITAIRES
// ============================================================

String getDeviceMAC() {
    String mac = WiFi.macAddress();
    mac.replace(":", "");
    mac.toUpperCase();
    return mac;
}

String getMACFormatted() {
    return WiFi.macAddress();  // "AA:BB:CC:DD:EE:FF"
}

String getSetupSSID() {
    String mac = getDeviceMAC();
    return String(SETUP_AP_PREFIX) + mac.substring(6);  // 6 derniers chars
}

void logf(const char* fmt, ...) {
    char buf[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    Serial.println(buf);
}

// ============================================================
// NVS - Stockage des credentials (flash persistante)
// ============================================================

bool loadCredentials() {
    prefs.begin(NVS_NAMESPACE, true);  // read-only
    wifiSSID     = prefs.getString("wifi_ssid", "");
    wifiPassword = prefs.getString("wifi_pass", "");
    serverIP     = prefs.getString("server_ip", "");
    serverPort   = prefs.getInt("server_port", 9000);
    prefs.end();

    isProvisioned = (wifiSSID.length() > 0 && serverIP.length() > 0);
    return isProvisioned;
}

void saveCredentials(String ssid, String pass, String ip, int port) {
    prefs.begin(NVS_NAMESPACE, false);  // read-write
    prefs.putString("wifi_ssid", ssid);
    prefs.putString("wifi_pass", pass);
    prefs.putString("server_ip", ip);
    prefs.putInt("server_port", port);
    prefs.end();
    logf("[NVS] Credentials sauvegardés : SSID=%s, IP=%s:%d", ssid.c_str(), ip.c_str(), port);
}

void clearCredentials() {
    prefs.begin(NVS_NAMESPACE, false);
    prefs.clear();
    prefs.end();
    logf("[NVS] Credentials effacés.");
}

// ============================================================
// MODE 1 - ACCESS POINT + Portail de provisioning
// ============================================================

void handleProvisionPost() {
    if (setupServer.method() != HTTP_POST) {
        setupServer.send(405, "text/plain", "Method Not Allowed");
        return;
    }

    String body = setupServer.arg("plain");
    logf("[AP] Reçu POST /provision : %s", body.c_str());

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, body);

    if (err) {
        setupServer.send(400, "application/json", "{\"error\":\"JSON invalide\"}");
        return;
    }

    String ssid   = doc["ssid"]        | "";
    String pass   = doc["password"]    | "";
    String ip     = doc["server_ip"]   | "";
    int    port   = doc["server_port"] | 9000;

    if (ssid.length() == 0 || pass.length() == 0 || ip.length() == 0) {
        setupServer.send(400, "application/json", "{\"error\":\"Champs manquants: ssid, password, server_ip\"}");
        return;
    }

    // Répondre AVANT de sauvegarder/redémarrer
    setupServer.send(200, "application/json",
        "{\"status\":\"ok\",\"message\":\"Credentials reçus, redémarrage...\",\"mac\":\"" + getMACFormatted() + "\"}");

    delay(500);

    saveCredentials(ssid, pass, ip, port);

    logf("[AP] Provisioning terminé. Redémarrage dans 2s...");
    delay(2000);
    ESP.restart();
}

void handleProvisionGet() {
    // Page HTML simple pour debug manuel (accès navigateur)
    String html = R"(
<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Eldersafe - Setup</title>
<style>body{font-family:sans-serif;max-width:400px;margin:40px auto;padding:20px}
input{width:100%;margin:8px 0;padding:8px;box-sizing:border-box}
button{background:#1D9E75;color:white;padding:10px 20px;border:none;border-radius:4px;cursor:pointer;width:100%}
</style></head><body>
<h2>Eldersafe ESP32 Setup</h2>
<p>MAC: )" + getMACFormatted() + R"(</p>
<form id='f'>
<input name='ssid' placeholder='SSID WiFi du serveur' required>
<input name='password' type='password' placeholder='Mot de passe WiFi' required>
<input name='server_ip' placeholder='IP du serveur (ex: 192.168.10.1)' required>
<input name='server_port' placeholder='Port socket (defaut: 9000)' value='9000'>
<button type='submit'>Configurer</button>
</form>
<script>
document.getElementById('f').onsubmit=function(e){
  e.preventDefault();
  const d=Object.fromEntries(new FormData(e.target));
  d.server_port=parseInt(d.server_port)||9000;
  fetch('/provision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
  .then(r=>r.json()).then(j=>alert(j.message||JSON.stringify(j)));
};
</script></body></html>
)";
    setupServer.send(200, "text/html", html);
}

void handleStatus() {
    StaticJsonDocument<256> doc;
    doc["mac"]   = getMACFormatted();
    doc["mode"]  = "setup";
    doc["ssid"]  = getSetupSSID();
    String out;
    serializeJson(doc, out);
    setupServer.send(200, "application/json", out);
}

void startSetupMode() {
    logf("[SETUP] Démarrage du mode Access Point...");
    logf("[SETUP] SSID : %s", getSetupSSID().c_str());

    WiFi.mode(WIFI_AP);
    WiFi.softAP(getSetupSSID().c_str());  // Réseau OUVERT (pas de mot de passe)

    logf("[SETUP] AP IP : %s", WiFi.softAPIP().toString().c_str());

    setupServer.on("/provision", HTTP_GET,  handleProvisionGet);
    setupServer.on("/provision", HTTP_POST, handleProvisionPost);
    setupServer.on("/status",    HTTP_GET,  handleStatus);
    setupServer.onNotFound([]() {
        setupServer.sendHeader("Location", "/provision");
        setupServer.send(302);
    });
    setupServer.begin();

    logf("[SETUP] Serveur HTTP actif. En attente du provisioner RPI...");
}

// ============================================================
// MODE 2 - Connexion WiFi normale
// ============================================================

bool connectToWiFi() {
    logf("[WIFI] Connexion à %s...", wifiSSID.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.begin(wifiSSID.c_str(), wifiPassword.c_str());

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
            logf("[WIFI] Timeout de connexion !");
            return false;
        }
        delay(500);
        Serial.print(".");
    }
    Serial.println();
    logf("[WIFI] Connecté ! IP : %s", WiFi.localIP().toString().c_str());
    return true;
}

// ============================================================
// SOCKET - Authentification + données
// ============================================================

bool sendJSON(const StaticJsonDocument<512>& doc) {
    if (!socketClient.connected()) return false;
    String out;
    serializeJson(doc, out);
    out += "\n";
    socketClient.print(out);
    return true;
}

bool authenticateWithServer() {
    logf("[SOCKET] Authentification avec MAC : %s", getMACFormatted().c_str());

    StaticJsonDocument<256> authMsg;
    authMsg["type"] = "auth";
    authMsg["mac"]  = getMACFormatted();
    sendJSON(authMsg);

    // Attendre la réponse
    unsigned long start = millis();
    while (!socketClient.available()) {
        if (millis() - start > 5000) {
            logf("[SOCKET] Timeout auth !");
            return false;
        }
        delay(100);
    }

    String response = socketClient.readStringUntil('\n');
    StaticJsonDocument<256> resp;
    if (deserializeJson(resp, response)) {
        logf("[SOCKET] Réponse auth invalide");
        return false;
    }

    String status = resp["status"] | "error";
    if (status == "ok") {
        deviceID_str = resp["device_id"].as<String>();
        logf("[SOCKET] Auth OK ! device_id=%s", deviceID_str.c_str());
        return true;
    } else {
        logf("[SOCKET] Auth refusée : %s", resp["reason"].as<const char*>());
        return false;
    }
}

bool connectToServer() {
    logf("[SOCKET] Connexion à %s:%d...", serverIP.c_str(), serverPort);
    if (!socketClient.connect(serverIP.c_str(), serverPort)) {
        logf("[SOCKET] Échec de connexion TCP");
        return false;
    }
    logf("[SOCKET] Connecté au socket server.");
    socketConnected = authenticateWithServer();
    return socketConnected;
}

void sendSensorData() {
    // -------------------------------------------------------
    // Remplacer par les vraies lectures de capteurs
    // Exemple : température, humidité, accéléromètre, etc.
    // -------------------------------------------------------
    StaticJsonDocument<512> doc;
    doc["type"] = "data";

    JsonObject payload = doc.createNestedObject("payload");
    payload["temperature"] = 22.5 + (random(-10, 10) / 10.0);  // Simulé
    payload["humidity"]    = 55.0 + (random(-5, 5) / 10.0);    // Simulé
    payload["battery_mv"]  = 3700 + random(-50, 50);             // Simulé
    payload["uptime_s"]    = millis() / 1000;

    if (sendJSON(doc)) {
        logf("[DATA] Données capteurs envoyées.");
    }
}

void sendPing() {
    StaticJsonDocument<64> doc;
    doc["type"] = "ping";
    sendJSON(doc);
}

void handleServerMessage() {
    if (!socketClient.available()) return;

    String raw = socketClient.readStringUntil('\n');
    StaticJsonDocument<256> msg;
    if (deserializeJson(msg, raw)) return;

    String type = msg["type"] | "";

    if (type == "pong") {
        logf("[SOCKET] Pong reçu.");
    } else if (type == "ack") {
        logf("[SOCKET] ACK données.");
    } else if (type == "command") {
        // Traiter les commandes du serveur (future feature)
        logf("[SOCKET] Commande reçue : %s", raw.c_str());
    }
}

// ============================================================
// SETUP & LOOP
// ============================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000);

    logf("\n╔══════════════════════════════════╗");
    logf("║      Eldersafe ESP32 Firmware    ║");
    logf("╚══════════════════════════════════╝");

    deviceMAC = getMACFormatted();
    logf("[INFO] Adresse MAC : %s", deviceMAC.c_str());

    bool hasCredentials = loadCredentials();

    if (!hasCredentials) {
        logf("[INFO] Pas de credentials → Mode SETUP");
        startSetupMode();
    } else {
        logf("[INFO] Credentials trouvés → Mode NORMAL");
        logf("[INFO] SSID : %s | Serveur : %s:%d", wifiSSID.c_str(), serverIP.c_str(), serverPort);
    }
}

void loop() {
    // --- Mode SETUP : traiter les requêtes HTTP ---
    if (!isProvisioned) {
        setupServer.handleClient();
        return;
    }

    // --- Mode NORMAL ---

    // Reconnexion WiFi si perdu
    if (WiFi.status() != WL_CONNECTED) {
        socketConnected = false;
        socketClient.stop();
        logf("[WIFI] Connexion perdue, tentative de reconnexion...");

        int retries = 0;
        while (WiFi.status() != WL_CONNECTED && retries < WIFI_RETRY_COUNT) {
            connectToWiFi();
            retries++;
        }

        if (WiFi.status() != WL_CONNECTED) {
            logf("[WIFI] Impossible de se reconnecter. Attente 30s...");
            delay(30000);
            return;
        }
    }

    // Reconnexion socket si perdu
    if (!socketClient.connected()) {
        socketConnected = false;
        logf("[SOCKET] Déconnecté, tentative de reconnexion...");
        delay(5000);
        connectToServer();
        return;
    }

    // Messages entrants du serveur
    handleServerMessage();

    // Envoi données capteurs
    if (millis() - lastDataSent >= DATA_INTERVAL_MS) {
        sendSensorData();
        lastDataSent = millis();
    }

    // Keepalive ping
    if (millis() - lastPingSent >= PING_INTERVAL_MS) {
        sendPing();
        lastPingSent = millis();
    }
}
