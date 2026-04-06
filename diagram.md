# ArkWeather ESP32 Node — Wiring Diagram
> Firmware: ArkWeather Node v1.0  
> MCU: ESP32 (30-pin DevKit)

---

## System Overview

```
                        ┌─────────────────────────────────────┐
                        │           ESP32 DevKit              │
                        │                                     │
  ┌──────────┐          │  GPIO 4  ───────────── DHT22        │
  │  DHT22   │──DATA────┤                                     │
  │ Temp+Hum │──VCC─────┤  3.3V                               │
  │          │──GND─────┤  GND                                │
  └──────────┘          │                                     │
                        │                                     │
  ┌──────────┐          │  GPIO 21 (SDA) ─────┬── BMP280      │
  │  BMP280  │──SDA─────┤                     │              │
  │ Pressure │──SCL─────┤  GPIO 22 (SCL) ─────┤              │
  │          │──VCC─────┤  3.3V               │              │
  │          │──GND─────┤  GND                │              │
  └──────────┘          │                     │              │
                        │                     │              │
  ┌──────────┐          │  GPIO 21 (SDA) ─────┤── OLED       │
  │   OLED   │──SDA─────┤  (shared I2C bus)   │              │
  │ SSD1306  │──SCL─────┤  GPIO 22 (SCL) ─────┘              │
  │ 0.96"    │──VCC─────┤  3.3V                               │
  │          │──GND─────┤  GND                                │
  └──────────┘          │                                     │
                        │                                     │
  ┌──────────┐          │  GPIO 34 (ADC1) ──── Rain Sensor AO │
  │  Rain    │──AO──────┤                                     │
  │  Sensor  │──VCC─────┤  3.3V                               │
  │  Module  │──GND─────┤  GND                                │
  └──────────┘          │                                     │
                        │                                     │
  ┌──────────┐          │  GPIO 35 (ADC1) ──── LDR Module AO  │
  │   LDR    │──AO──────┤                                     │
  │  Module  │──VCC─────┤  3.3V                               │
  │ (Light)  │──GND─────┤  GND                                │
  └──────────┘          │                                     │
                        │  USB / Battery ──── 5V (VIN)        │
                        └─────────────────────────────────────┘
```

---

## Pin Reference Table

| GPIO  | Type     | Connected To              | Notes                              |
|-------|----------|---------------------------|------------------------------------|
| 4     | Digital  | DHT22 DATA                | 10kΩ pull-up resistor recommended  |
| 21    | I2C SDA  | BMP280 SDA + OLED SDA     | Shared I2C bus (3.3V logic)        |
| 22    | I2C SCL  | BMP280 SCL + OLED SCL     | Shared I2C bus (3.3V logic)        |
| 34    | ADC1     | Rain Sensor Module (AO)   | Input-only pin, no pull-up         |
| 35    | ADC1     | LDR Module (AO)           | Input-only pin, no pull-up         |
| 3.3V  | Power    | All sensor VCC            | Do NOT use 5V for sensors          |
| GND   | Ground   | All sensor GND            | Common ground                      |
| VIN   | Power In | Battery / USB 5V          | Onboard regulator steps to 3.3V    |

---

## I2C Device Addresses

| Device       | I2C Address | Notes                                    |
|--------------|-------------|------------------------------------------|
| OLED SSD1306 | `0x3C`      | Common default for 0.96" modules         |
| BMP280       | `0x76`      | Default; try `0x77` if not detected      |

> Both devices share GPIO 21 (SDA) and GPIO 22 (SCL).  
> This is safe — I2C is a multi-device bus, addresses distinguish them.

---

## Component Wiring Details

### DHT22 — Temperature & Humidity
```
DHT22 Pin 1 (VCC)  ──── 3.3V
DHT22 Pin 2 (DATA) ──── GPIO 4  ──┬── 10kΩ resistor ──── 3.3V
                                  └── (signal to ESP32)
DHT22 Pin 3 (NC)   ──── (not connected)
DHT22 Pin 4 (GND)  ──── GND
```
> ⚠️ The 10kΩ pull-up between DATA and VCC is required for reliable readings.

---

### BMP280 — Atmospheric Pressure
```
BMP280 VCC  ──── 3.3V
BMP280 GND  ──── GND
BMP280 SDA  ──── GPIO 21
BMP280 SCL  ──── GPIO 22
BMP280 CSB  ──── 3.3V   (forces I2C mode, not SPI)
BMP280 SDO  ──── GND    (sets I2C address to 0x76)
             or  3.3V   (sets I2C address to 0x77)
```

---

### OLED SSD1306 0.96" — Display
```
OLED VCC  ──── 3.3V
OLED GND  ──── GND
OLED SDA  ──── GPIO 21  (shared with BMP280)
OLED SCL  ──── GPIO 22  (shared with BMP280)
```

---

### Rain Sensor Module — Analogue Output
```
Module VCC  ──── 3.3V
Module GND  ──── GND
Module AO   ──── GPIO 34   (analogue read)
Module DO   ──── (not used — digital threshold output, optional)
```
> Threshold in firmware: `RAIN_THRESH = 500`  
> Value > 500 → `is_raining = true`  
> Dry = higher ADC value; Wet = lower ADC value (resistance drops when wet)

---

### LDR Module — Light Sensor
```
Module VCC  ──── 3.3V
Module GND  ──── GND
Module AO   ──── GPIO 35   (analogue read)
Module DO   ──── (not used — optional digital threshold output)
```
> Higher ADC value = brighter light  
> Used for cloud cover estimation

---

### Power — Battery Operation
```
Battery (+) ──── VIN (5V pin on ESP32 DevKit)
Battery (-) ──── GND

           OR

USB cable  ──── Micro-USB / USB-C port on ESP32 DevKit
```
> ESP32 DevKit has an onboard AMS1117 3.3V regulator.  
> All sensors run at 3.3V — do not wire sensor VCC to 5V/VIN.

---

## Shared I2C Bus Diagram

```
ESP32 GPIO 21 (SDA) ─────────────┬──────────────── BMP280 SDA
                                 │
                                 └──────────────── OLED SDA

ESP32 GPIO 22 (SCL) ─────────────┬──────────────── BMP280 SCL
                                 │
                                 └──────────────── OLED SCL

ESP32 3.3V ──────────────────────┬──────────────── BMP280 VCC
                                 │
                                 └──────────────── OLED VCC

ESP32 GND ───────────────────────┬──────────────── BMP280 GND
                                 │
                                 └──────────────── OLED GND
```
> I2C pull-ups (4.7kΩ) are usually built into the BMP280 and OLED breakout boards.  
> If you see communication errors, add external 4.7kΩ pull-ups from SDA/SCL to 3.3V.

---

## ADC Channels — GPIO 34 & 35

```
GPIO 34 ── ADC1 Channel 6 ── Rain Sensor AO
GPIO 35 ── ADC1 Channel 7 ── LDR Module AO
```
> ⚠️ GPIO 34 and 35 are **input-only** on ESP32 — no internal pull-up/pull-down.  
> ADC2 pins (GPIO 0, 2, 4, 12–15, 25–27) cannot be used while WiFi is active.  
> Always use ADC1 pins (GPIO 32–39) for analogue sensors when WiFi is needed.

---

## Quick Build Checklist

- [ ] DHT22 DATA → GPIO 4 with 10kΩ pull-up to 3.3V
- [ ] BMP280 on I2C (GPIO 21/22), SDO → GND for address 0x76
- [ ] OLED SSD1306 on same I2C bus (GPIO 21/22), address 0x3C
- [ ] Rain sensor AO → GPIO 34
- [ ] LDR module AO → GPIO 35
- [ ] All sensor VCC → 3.3V (never 5V)
- [ ] All GND connected to common GND
- [ ] Power via USB or battery to VIN/GND
- [ ] Flash firmware with correct `WIFI_SSID`, `WIFI_PASSWORD`, `API_KEY`
- [ ] Verify BMP280 found at 0x76 in Serial Monitor (try 0x77 if not)
- [ ] Verify OLED displays "ArkWeather / Booting..." on startup

---

*ArkWeather Node — ESP32 Wiring Reference*
