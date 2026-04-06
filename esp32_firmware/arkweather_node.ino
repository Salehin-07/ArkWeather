/*
 * ArkWeather ESP32 Node Firmware
 * ─────────────────────────────
 * Sensors : DHT22 · BMP280 · Rain sensor (analogue) · LDR (module)
 * Display : 0.96" OLED SSD1306 (I2C)
 * Pushes  : JSON POST to /api/push/ every PUSH_INTERVAL_MS (WiFi)
 *         : BLE Environmental Sensing Service (standard SIG UUIDs)
 *         : Rain via one custom UUID (no SIG standard exists)
 *         : Light mapped to standard Illuminance characteristic
 *
 * Wiring
 * ──────
 * DHT22  DATA → GPIO 4
 * BMP280 SDA  → GPIO 21   SCL → GPIO 22
 * OLED   SDA  → GPIO 21   SCL → GPIO 22
 * Rain   AO   → GPIO 34   (ADC1)
 * LDR    AO   → GPIO 35   (ADC1)
 *
 * Libraries (Arduino Library Manager):
 *   DHT sensor library — Adafruit
 *   Adafruit BMP280 Library
 *   Adafruit SSD1306
 *   Adafruit GFX Library
 *   ArduinoJson
 *   ESP32 BLE Arduino (built into esp32 board package)
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
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL    = "https://arkweather.onrender.com/api/push/";
const char* API_KEY       = "YOUR-DEVICE-API-KEY-UUID";

const unsigned long PUSH_INTERVAL_MS = 30000;

// ── Pin definitions ────────────────────────────────────────────────────────
#define DHT_PIN      4
#define DHT_TYPE     DHT22
#define RAIN_PIN     34
#define LDR_PIN      35
#define RAIN_THRESH  500

// ── OLED ───────────────────────────────────────────────────────────────────
#define SCREEN_W   128
#define SCREEN_H    64
#define OLED_RESET  -1
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, OLED_RESET);

// ── Sensor objects ─────────────────────────────────────────────────────────
DHT             dht(DHT_PIN, DHT_TYPE);
Adafruit_BMP280 bmp;

// ── BLE UUIDs ──────────────────────────────────────────────────────────────
// Standard Bluetooth SIG — Environmental Sensing Service
#define BLE_DEVICE_NAME      "ArkWeather"
#define BLE_SVC_ENV_SENSING  "0000181A-0000-1000-8000-00805F9B34FB"
#define BLE_CHAR_TEMPERATURE "00002A6E-0000-1000-8000-00805F9B34FB"  // int16,  0.01 °C
#define BLE_CHAR_HUMIDITY    "00002A6F-0000-1000-8000-00805F9B34FB"  // uint16, 0.01 %
#define BLE_CHAR_PRESSURE    "00002A6D-0000-1000-8000-00805F9B34FB"  // uint32, 0.1 Pa
#define BLE_CHAR_ILLUMINANCE "00002AFB-0000-1000-8000-00805F9B34FB"  // uint24, 0.01 lux

// One custom UUID — only for rain (no SIG standard exists for this)
#define BLE_CHAR_RAIN        "ArkW0001-0000-1000-8000-00805F9B34FB"  // uint8: 0=dry 1=raining

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

    // 20 handles covers 5 characteristics × (char + descriptor + value) comfortably
    BLEService* svc = bleServer->createService(BLEUUID(BLE_SVC_ENV_SENSING), 20);

    charTemp = svc->createCharacteristic(
        BLE_CHAR_TEMPERATURE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    charTemp->addDescriptor(new BLE2902());

    charHum = svc->createCharacteristic(
        BLE_CHAR_HUMIDITY,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    charHum->addDescriptor(new BLE2902());

    charPres = svc->createCharacteristic(
        BLE_CHAR_PRESSURE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    charPres->addDescriptor(new BLE2902());

    charLight = svc->createCharacteristic(
        BLE_CHAR_ILLUMINANCE,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    charLight->addDescriptor(new BLE2902());

    charRain = svc->createCharacteristic(
        BLE_CHAR_RAIN,
        BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY
    );
    charRain->addDescriptor(new BLE2902());

    svc->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BLE_SVC_ENV_SENSING);
    adv->setScanResponse(true);
    adv->setMinPreferred(0x06);
    adv->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("BLE: advertising as '" BLE_DEVICE_NAME "'");
}

void updateBLE(float temp, float hum, float pres, bool raining, int light) {
    if (!charTemp) return;

    // Temperature — int16, unit 0.01 °C  (e.g. 3050 = 30.50 °C)
    if (!isnan(temp)) {
        int16_t t = (int16_t)(temp * 100);
        charTemp->setValue((uint8_t*)&t, 2);
        if (bleClientConnected) charTemp->notify();
    }

    // Humidity — uint16, unit 0.01 %  (e.g. 7210 = 72.10 %)
    if (!isnan(hum)) {
        uint16_t h = (uint16_t)(hum * 100);
        charHum->setValue((uint8_t*)&h, 2);
        if (bleClientConnected) charHum->notify();
    }

    // Pressure — uint32, unit 0.1 Pa  (e.g. 100830000 = 1008.3 hPa)
    if (pres > 0) {
        uint32_t p = (uint32_t)(pres * 10000);  // hPa → 0.1 Pa
        charPres->setValue((uint8_t*)&p, 4);
        if (bleClientConnected) charPres->notify();
    }

    // Illuminance — uint24, unit 0.01 lux
    // LDR gives 0–1023 ADC; map linearly to 0–100000 (0–1000 lux range)
    {
        uint32_t lux_raw = (uint32_t)map(light, 0, 1023, 0, 10000000); // 0.01 lux units
        uint8_t  lux[3];
        lux[0] =  lux_raw        & 0xFF;
        lux[1] = (lux_raw >>  8) & 0xFF;
        lux[2] = (lux_raw >> 16) & 0xFF;
        charLight->setValue(lux, 3);
        if (bleClientConnected) charLight->notify();
    }

    // Rain — uint8  (0 = dry, 1 = raining)
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

    // OLED
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println("OLED init failed");
    }
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    displayMessage("ArkWeather", "Booting...");

    // Sensors
    dht.begin();
    if (!bmp.begin(0x76)) {
        Serial.println("BMP280 not found — try 0x77");
    }

    // BLE — start before WiFi so device is discoverable immediately
    initBLE();
    displayMessage("BLE", "Advertising...");
    delay(500);

    // WiFi
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    displayMessage("ArkWeather", "Connecting WiFi...");
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
        displayMessage("WiFi FAILED", "BLE only mode");
    }

    delay(1500);
}


// ══════════════════════════════════════════════════════════════════════════
//  LOOP
// ══════════════════════════════════════════════════════════════════════════

void loop() {
    unsigned long now = millis();

    // Read sensors
    float temperature = dht.readTemperature();
    float humidity    = dht.readHumidity();
    float pressure    = bmp.readPressure() / 100.0F;   // Pa → hPa
    int   rainValue   = analogRead(RAIN_PIN);
    int   lightValue  = analogRead(LDR_PIN);
    bool  isRaining   = (rainValue > RAIN_THRESH);

    // Update OLED
    updateDisplay(temperature, humidity, pressure, isRaining, lightValue);

    // Update BLE characteristics — notifies connected client immediately
    updateBLE(temperature, humidity, pressure, isRaining, lightValue);

    // WiFi push every PUSH_INTERVAL_MS
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
    doc["api_key"]    = API_KEY;
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
    display.clearDisplay();
    display.setCursor(0, 0);
    display.setTextSize(1);

    display.println("-- ArkWeather Node --");
    display.printf("Temp  : %.1f C\n",   temp);
    display.printf("Humid : %.1f %%\n",  hum);
    display.printf("Press : %.1f hPa\n", pres);
    display.printf("Rain  : %s\n",       raining ? "YES" : "No");
    display.printf("Light : %d\n",       light);

    // Top-right: W = WiFi connected, B = BLE connected, X = neither
    display.setCursor(110, 0);
    if (WiFi.status() == WL_CONNECTED && bleClientConnected)
        display.print("WB");
    else if (WiFi.status() == WL_CONNECTED)
        display.print("W ");
    else if (bleClientConnected)
        display.print(" B");
    else
        display.print(" X");

    display.display();
}

void displayMessage(const char* line1, const char* line2) {
    display.clearDisplay();
    display.setCursor(0, 20);
    display.setTextSize(1);
    display.println(line1);
    display.println(line2);
    display.display();
}