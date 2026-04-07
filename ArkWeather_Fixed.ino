/*
 * ArkWeather ESP32 Node Firmware  —  FIXED + I2C AUTO-DETECT
 * ────────────────────────────────────────────────────────────
 * OLED  : tries 0x3C → 0x3D → SDA/SCL swapped 0x3C → SDA/SCL swapped 0x3D
 * BMP280: tries 0x76 → 0x77
 * All fallback results printed to Serial for debugging.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Adafruit_BMP280.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ── Configuration ──────────────────────────────────────────────────────────
const char* WIFI_SSID     = "Name Error.";
const char* WIFI_PASSWORD = "12345678";
const char* SERVER_URL    = "https://arkweather.onrender.com/api/push/";
const char* API_KEY       = "d5ea1d2c-6b18-4c4a-87e5-22a209a98eab";

const unsigned long PUSH_INTERVAL_MS = 30000;

// ── Pin definitions ────────────────────────────────────────────────────────
#define DHT_PIN      4
#define DHT_TYPE     DHT22
#define RAIN_PIN     34
#define LDR_PIN      35
#define RAIN_THRESH  500

// ── I2C pins ───────────────────────────────────────────────────────────────
#define SDA_NORMAL  21
#define SCL_NORMAL  22
#define SDA_SWAPPED 22   // fallback: wires physically swapped
#define SCL_SWAPPED 21

// ── OLED ───────────────────────────────────────────────────────────────────
#define SCREEN_W   128
#define SCREEN_H    64
#define OLED_RESET  -1
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, OLED_RESET);
bool oledOk = false;

// ── Sensor objects ─────────────────────────────────────────────────────────
DHT             dht(DHT_PIN, DHT_TYPE);
Adafruit_BMP280 bmp;
bool            bmpOk = false;

// ── BLE UUIDs ──────────────────────────────────────────────────────────────
#define BLE_DEVICE_NAME      "ArkWeather"
#define BLE_SVC_ENV_SENSING  "0000181A-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_TEMPERATURE "00002A6E-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_HUMIDITY    "00002A6F-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_PRESSURE    "00002A6D-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_ILLUMINANCE "00002AFB-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_RAIN        "ArkW0001-0000-1000-8000-00805F9B34FB"

// ── BLE state ──────────────────────────────────────────────────────────────
BLEServer*         bleServer          = nullptr;
BLECharacteristic* charTemp           = nullptr;
BLECharacteristic* charHum            = nullptr;
BLECharacteristic* charPres           = nullptr;
BLECharacteristic* charLight          = nullptr;
BLECharacteristic* charRain           = nullptr;
bool               bleClientConnected = false;

// ── Loop state ─────────────────────────────────────────────────────────────
unsigned long lastPush = 0;


// ══════════════════════════════════════════════════════════════════════════
//  I2C AUTO-DETECT
//  Tries every combination of SDA/SCL pins and I2C addresses.
//  Returns true if OLED is found and initialised.
// ══════════════════════════════════════════════════════════════════════════

bool tryOLED(uint8_t sda, uint8_t scl, uint8_t addr) {
    Wire.begin(sda, scl);
    delay(100);

    // Check the address actually responds on the bus first
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() != 0) {
        // No device at this address on this pin combo
        return false;
    }

    // Device present — try full init
    if (display.begin(SSD1306_SWITCHCAPVCC, addr)) {
        Serial.printf("OLED found: SDA=%d SCL=%d addr=0x%02X\n", sda, scl, addr);
        return true;
    }
    return false;
}

void initOLED() {
    Serial.println("OLED: scanning all fallbacks...");

    // Try 1: Normal wiring, address 0x3C  (most common)
    if (tryOLED(SDA_NORMAL, SCL_NORMAL, 0x3C)) { oledOk = true; return; }
    Serial.println("OLED: 0x3C normal wiring — not found");

    // Try 2: Normal wiring, address 0x3D
    if (tryOLED(SDA_NORMAL, SCL_NORMAL, 0x3D)) { oledOk = true; return; }
    Serial.println("OLED: 0x3D normal wiring — not found");

    // Try 3: Swapped SDA/SCL, address 0x3C  (common mistake)
    if (tryOLED(SDA_SWAPPED, SCL_SWAPPED, 0x3C)) { oledOk = true; return; }
    Serial.println("OLED: 0x3C swapped wiring — not found");

    // Try 4: Swapped SDA/SCL, address 0x3D
    if (tryOLED(SDA_SWAPPED, SCL_SWAPPED, 0x3D)) { oledOk = true; return; }
    Serial.println("OLED: 0x3D swapped wiring — not found");

    // All 4 combinations failed
    Serial.println("OLED: ALL fallbacks failed — check VCC=3.3V and connections");
    oledOk = false;

    // Restore normal I2C for BMP280
    Wire.begin(SDA_NORMAL, SCL_NORMAL);
}

void initBMP() {
    Serial.println("BMP280: scanning...");

    if (bmp.begin(0x76)) {
        Serial.println("BMP280 found at 0x76");
        bmpOk = true;
        return;
    }
    Serial.println("BMP280: 0x76 not found");

    if (bmp.begin(0x77)) {
        Serial.println("BMP280 found at 0x77");
        bmpOk = true;
        return;
    }
    Serial.println("BMP280: 0x77 not found — check wiring");
    bmpOk = false;
}


// ══════════════════════════════════════════════════════════════════════════
//  BLE
// ══════════════════════════════════════════════════════════════════════════

class ArkWeatherBLECallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer* server) override {
        bleClientConnected = true;
        Serial.println("BLE: client connected");
        BLEDevice::getAdvertising()->stop();
    }
    void onDisconnect(BLEServer* server) override {
        bleClientConnected = false;
        Serial.println("BLE: client disconnected — restarting advertising");
        BLEDevice::startAdvertising();
    }
};

void initBLE() {
    BLEDevice::init(BLE_DEVICE_NAME);
    bleServer = BLEDevice::createServer();
    bleServer->setCallbacks(new ArkWeatherBLECallbacks());

    BLEService* svc = bleServer->createService(BLEUUID(BLE_SVC_ENV_SENSING), 20);

    charTemp = svc->createCharacteristic(BLE_CHAR_TEMPERATURE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    charTemp->addDescriptor(new BLE2902());

    charHum = svc->createCharacteristic(BLE_CHAR_HUMIDITY,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    charHum->addDescriptor(new BLE2902());

    charPres = svc->createCharacteristic(BLE_CHAR_PRESSURE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    charPres->addDescriptor(new BLE2902());

    charLight = svc->createCharacteristic(BLE_CHAR_ILLUMINANCE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    charLight->addDescriptor(new BLE2902());

    charRain = svc->createCharacteristic(BLE_CHAR_RAIN,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    charRain->addDescriptor(new BLE2902());

    svc->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BLE_SVC_ENV_SENSING);
    adv->setScanResponse(true);
    adv->setMinPreferred(0x06);
    adv->setMaxPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("BLE: advertising as '" BLE_DEVICE_NAME "'");
}

void updateBLE(float temp, float hum, float pres, bool raining, int light) {
    if (!charTemp) return;

    if (!isnan(temp)) {
        int16_t t = (int16_t)(temp * 100);
        charTemp->setValue((uint8_t*)&t, 2);
        if (bleClientConnected) charTemp->notify();
    }
    if (!isnan(hum)) {
        uint16_t h = (uint16_t)(hum * 100);
        charHum->setValue((uint8_t*)&h, 2);
        if (bleClientConnected) charHum->notify();
    }
    if (pres > 0) {
        uint32_t p = (uint32_t)(pres * 1000);
        charPres->setValue((uint8_t*)&p, 4);
        if (bleClientConnected) charPres->notify();
    }
    {
        uint32_t lux_raw = (uint32_t)((long)light * 10000000L / 4095L);
        uint8_t lux[3];
        lux[0] =  lux_raw        & 0xFF;
        lux[1] = (lux_raw >>  8) & 0xFF;
        lux[2] = (lux_raw >> 16) & 0xFF;
        charLight->setValue(lux, 3);
        if (bleClientConnected) charLight->notify();
    }
    {
        uint8_t r = raining ? 1 : 0;
        charRain->setValue(&r, 1);
        if (bleClientConnected) charRain->notify();
    }
}


// ══════════════════════════════════════════════════════════════════════════
//  SETUP
// ══════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(115200);
    delay(500);  // let Serial settle
    Serial.println("\n=== ArkWeather booting ===");

    // OLED — auto-detect all I2C combinations
    initOLED();

    if (oledOk) {
        display.clearDisplay();
        display.setTextSize(1);
        display.setTextColor(SSD1306_WHITE);
        displayMessage("ArkWeather", "Booting...");
    }

    // DHT22
    dht.begin();
    Serial.println("DHT22: started");

    // BMP280 — auto-detect address
    initBMP();

    // BLE
    initBLE();
    displayMessage("BLE", "Advertising...");
    delay(500);

    // WiFi
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    displayMessage("ArkWeather", "Connecting WiFi...");
    Serial.print("WiFi: connecting");
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
        displayMessage("WiFi OK", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\nWiFi FAILED — BLE only mode");
        displayMessage("WiFi FAILED", "BLE only mode");
    }

    delay(1500);
    Serial.println("=== Boot complete ===");
}


// ══════════════════════════════════════════════════════════════════════════
//  LOOP
// ══════════════════════════════════════════════════════════════════════════

void loop() {
    unsigned long now = millis();

    float temperature = dht.readTemperature();
    float humidity    = dht.readHumidity();
    float pressure    = bmpOk ? (bmp.readPressure() / 100.0F) : 0.0F;
    int   rainValue   = analogRead(RAIN_PIN);
    int   lightValue  = analogRead(LDR_PIN);
    bool  isRaining   = (rainValue < RAIN_THRESH);

    updateDisplay(temperature, humidity, pressure, isRaining, lightValue);
    updateBLE(temperature, humidity, pressure, isRaining, lightValue);

    if (now - lastPush >= PUSH_INTERVAL_MS) {
        if (WiFi.status() == WL_CONNECTED) {
            pushReading(temperature, humidity, pressure,
                        rainValue, isRaining, lightValue);
        } else {
            Serial.println("WiFi disconnected — attempting reconnect");
            WiFi.reconnect();
        }
        lastPush = now;
    }

    delay(2000);
}


// ══════════════════════════════════════════════════════════════════════════
//  WiFi PUSH
// ══════════════════════════════════════════════════════════════════════════

void pushReading(float temp, float hum, float pres,
                 int rainVal, bool raining, int lightVal)
{
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<256> doc;
    doc["api_key"]     = API_KEY;
    if (!isnan(temp))  doc["temperature"] = temp;
    if (!isnan(hum))   doc["humidity"]    = hum;
    if (pres > 0)      doc["pressure"]    = pres;
    doc["rain_value"]  = rainVal;
    doc["is_raining"]  = raining;
    doc["light_value"] = lightVal;

    String body;
    serializeJson(doc, body);

    int code = http.POST(body);
    if (code == 201) {
        Serial.println("WiFi push OK");
    } else {
        Serial.printf("WiFi push failed: HTTP %d\n", code);
        Serial.println(http.getString());
    }
    http.end();
}


// ══════════════════════════════════════════════════════════════════════════
//  OLED HELPERS
// ══════════════════════════════════════════════════════════════════════════

void updateDisplay(float temp, float hum, float pres, bool raining, int light) {
    if (!oledOk) return;

    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    char buf[32];

    display.setCursor(0, 0);
    display.println("-- ArkWeather Node --");

    if (isnan(temp)) display.println("Temp  : --");
    else { snprintf(buf, sizeof(buf), "Temp  : %.1f C", temp);  display.println(buf); }

    if (isnan(hum))  display.println("Humid : --");
    else { snprintf(buf, sizeof(buf), "Humid : %.1f %%", hum);  display.println(buf); }

    if (pres <= 0)   display.println("Press : --");
    else { snprintf(buf, sizeof(buf), "Press : %.1f hPa", pres); display.println(buf); }

    snprintf(buf, sizeof(buf), "Rain  : %s", raining ? "YES" : "No");
    display.println(buf);

    snprintf(buf, sizeof(buf), "Light : %d", light);
    display.println(buf);

    // Top-right: W=WiFi B=BLE X=none
    display.setCursor(110, 0);
    if      (WiFi.status() == WL_CONNECTED && bleClientConnected) display.print("WB");
    else if (WiFi.status() == WL_CONNECTED)                       display.print("W ");
    else if (bleClientConnected)                                   display.print(" B");
    else                                                           display.print(" X");

    display.display();
}

void displayMessage(const char* line1, const char* line2) {
    if (!oledOk) return;

    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0, 20);
    display.println(line1);
    display.println(line2);
    display.display();
}