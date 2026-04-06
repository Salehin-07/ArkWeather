/*
 * ArkWeather ESP32 Node Firmware
 * ─────────────────────────────
 * Sensors : DHT22 · BMP280 · Rain sensor (analogue) · LDR (analogue)
 * Display : 0.96" OLED SSD1306 (I2C)
 * Pushes  : JSON POST to /api/push/ every PUSH_INTERVAL_MS
 *
 * Wiring
 * ──────
 * DHT22  DATA  → GPIO 4
 * BMP280 SDA   → GPIO 21   SCL → GPIO 22  (default I2C)
 * OLED   SDA   → GPIO 21   SCL → GPIO 22  (shared I2C bus)
 * Rain   AO    → GPIO 34   (ADC1)
 * LDR    AO    → GPIO 35   (ADC1)
 *
 * Libraries (install via Arduino Library Manager):
 *   DHT sensor library by Adafruit
 *   Adafruit BMP280 Library
 *   Adafruit SSD1306
 *   Adafruit GFX Library
 *   ArduinoJson
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Adafruit_BMP280.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

// ── Configuration ─────────────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL    = "https://arkweather.onrender.com/api/push/";
const char* API_KEY       = "YOUR-DEVICE-API-KEY-UUID";   // from Django device page

const unsigned long PUSH_INTERVAL_MS = 30000;  // push every 30 seconds

// ── Pin definitions ────────────────────────────────────────────────────────────
#define DHT_PIN       4
#define DHT_TYPE      DHT22
#define RAIN_PIN      34
#define LDR_PIN       35
#define RAIN_THRESH   500   // analogue value above which = raining

// ── OLED ───────────────────────────────────────────────────────────────────────
#define SCREEN_W   128
#define SCREEN_H    64
#define OLED_RESET  -1
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, OLED_RESET);

// ── Sensor objects ─────────────────────────────────────────────────────────────
DHT            dht(DHT_PIN, DHT_TYPE);
Adafruit_BMP280 bmp;

// ── State ──────────────────────────────────────────────────────────────────────
unsigned long lastPush = 0;

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
        Serial.println("BMP280 not found — check wiring (try 0x77)");
    }

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
        displayMessage("WiFi FAILED", "Offline mode");
    }

    delay(1500);
}

void loop() {
    unsigned long now = millis();

    // Read sensors
    float temperature = dht.readTemperature();
    float humidity    = dht.readHumidity();
    float pressure    = bmp.readPressure() / 100.0F;  // Pa → hPa
    int   rainValue   = analogRead(RAIN_PIN);
    int   lightValue  = analogRead(LDR_PIN);
    bool  isRaining   = (rainValue > RAIN_THRESH);

    // OLED display update
    updateDisplay(temperature, humidity, pressure, isRaining, lightValue);

    // Push to server every PUSH_INTERVAL_MS
    if (now - lastPush >= PUSH_INTERVAL_MS) {
        if (WiFi.status() == WL_CONNECTED) {
            pushReading(temperature, humidity, pressure, rainValue, isRaining, lightValue);
        } else {
            Serial.println("WiFi disconnected — attempting reconnect");
            WiFi.reconnect();
        }
        lastPush = now;
    }

    delay(2000);
}

// ── Push reading to Django ─────────────────────────────────────────────────────
void pushReading(float temp, float hum, float pres,
                 int rainVal, bool raining, int lightVal)
{
    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<256> doc;
    doc["api_key"]    = API_KEY;
    if (!isnan(temp))   doc["temperature"] = temp;
    if (!isnan(hum))    doc["humidity"]    = hum;
    if (pres > 0)       doc["pressure"]    = pres;
    doc["rain_value"]  = rainVal;
    doc["is_raining"]  = raining;
    doc["light_value"] = lightVal;

    String body;
    serializeJson(doc, body);

    int code = http.POST(body);
    if (code == 201) {
        Serial.println("Push OK");
    } else {
        Serial.printf("Push failed: HTTP %d\n", code);
        Serial.println(http.getString());
    }
    http.end();
}

// ── OLED helpers ──────────────────────────────────────────────────────────────
void updateDisplay(float temp, float hum, float pres, bool raining, int light) {
    display.clearDisplay();
    display.setCursor(0, 0);
    display.setTextSize(1);

    display.println("-- ArkWeather Node --");

    display.printf("Temp  : %.1f C\n", temp);
    display.printf("Humid : %.1f %%\n", hum);
    display.printf("Press : %.1f hPa\n", pres);
    display.printf("Rain  : %s\n", raining ? "YES" : "No");
    display.printf("Light : %d\n", light);

    // WiFi status dot
    display.setCursor(116, 0);
    display.print(WiFi.status() == WL_CONNECTED ? "W" : "X");

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
