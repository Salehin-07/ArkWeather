"""
sensors.py  –  All sensor reads with layered fallbacks.
Every sensor failure is caught; the reading dict always returns
a value (None if truly unavailable) so the rest of the firmware
never crashes because of a bad sensor.
"""

import math

# ── DHT22 ────────────────────────────────────────────────────────────────────
_dht_sensor = None

def _init_dht():
    global _dht_sensor
    if _dht_sensor is not None:
        return True
    try:
        import dht, machine
        _dht_sensor = dht.DHT22(machine.Pin(4))
        print("[sensors] DHT22 init OK on GPIO4")
        return True
    except Exception as e:
        print("[sensors] DHT22 init failed:", e)
        _dht_sensor = None
        return False


def read_dht():
    """Returns (temperature_C, humidity_%) or (None, None)."""
    if not _init_dht():
        return None, None
    for attempt in range(3):
        try:
            import time
            _dht_sensor.measure()
            time.sleep_ms(200)
            t = _dht_sensor.temperature()
            h = _dht_sensor.humidity()
            if t is not None and h is not None:
                # Sanity check
                if -40 <= t <= 80 and 0 <= h <= 100:
                    return round(t, 2), round(h, 2)
        except Exception as e:
            print(f"[sensors] DHT22 read attempt {attempt+1} failed:", e)
            import time
            time.sleep_ms(500)
    return None, None


# ── BMP280 ───────────────────────────────────────────────────────────────────
_bmp_sensor = None

def _init_bmp():
    global _bmp_sensor
    if _bmp_sensor is not None:
        return True
    try:
        # Try popular MicroPython BMP280 libraries in order
        try:
            import bmp280
            import machine
            i2c = machine.SoftI2C(scl=machine.Pin(22), sda=machine.Pin(21), freq=100000)
            _bmp_sensor = bmp280.BMP280(i2c)
            print("[sensors] BMP280 init OK (bmp280 lib) via SoftI2C")
            return True
        except ImportError:
            pass

        try:
            from machine import I2C, Pin
            i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)
            # Manual BMP280 read via raw I2C
            _bmp_sensor = ('raw_i2c', i2c)
            print("[sensors] BMP280 will use raw I2C fallback")
            return True
        except Exception as e:
            print("[sensors] BMP280 I2C fallback failed:", e)

    except Exception as e:
        print("[sensors] BMP280 init failed:", e)
    _bmp_sensor = None
    return False


def _read_bmp_raw_i2c(i2c):
    """Minimal BMP280 driver for when no library is available."""
    BMP_ADDR = 0x76

    # Try 0x76 then 0x77
    for addr in (0x76, 0x77):
        try:
            chip_id = i2c.readfrom_mem(addr, 0xD0, 1)[0]
            if chip_id in (0x58, 0x60):  # BMP280 or BME280
                BMP_ADDR = addr
                break
        except Exception:
            continue
    else:
        return None, None

    # Read calibration data
    calib = i2c.readfrom_mem(BMP_ADDR, 0x88, 24)
    T1 = calib[1] << 8 | calib[0]
    T2 = calib[3] << 8 | calib[2]
    T3 = calib[5] << 8 | calib[4]
    P1 = calib[7] << 8 | calib[6]
    P2 = calib[9] << 8 | calib[8]
    P3 = calib[11] << 8 | calib[10]
    P4 = calib[13] << 8 | calib[12]
    P5 = calib[15] << 8 | calib[14]
    P6 = calib[17] << 8 | calib[16]
    P7 = calib[19] << 8 | calib[18]
    P8 = calib[21] << 8 | calib[20]
    P9 = calib[23] << 8 | calib[22]

    # Sign extend
    def s16(v): return v if v < 32768 else v - 65536
    T2, T3 = s16(T2), s16(T3)
    P2, P3, P4, P5, P6, P8, P9 = (s16(x) for x in (P2, P3, P4, P5, P6, P8, P9))

    # Set oversampling: osrs_t=2, osrs_p=5, mode=3 (normal)
    i2c.writeto_mem(BMP_ADDR, 0xF4, bytes([0x57]))
    i2c.writeto_mem(BMP_ADDR, 0xF5, bytes([0x90]))

    import time
    time.sleep_ms(100)

    raw = i2c.readfrom_mem(BMP_ADDR, 0xF7, 6)
    adc_P = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
    adc_T = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)

    # Temperature compensation
    var1 = (adc_T / 16384.0 - T1 / 1024.0) * T2
    var2 = ((adc_T / 131072.0 - T1 / 8192.0) ** 2) * T3
    t_fine = var1 + var2
    temperature = t_fine / 5120.0

    # Pressure compensation
    var1 = t_fine / 2.0 - 64000.0
    var2 = var1 * var1 * P6 / 32768.0
    var2 += var1 * P5 * 2.0
    var2 = var2 / 4.0 + P4 * 65536.0
    var1 = (P3 * var1 * var1 / 524288.0 + P2 * var1) / 524288.0
    var1 = (1.0 + var1 / 32768.0) * P1
    if var1 == 0:
        return round(temperature, 2), None
    pressure = 1048576.0 - adc_P
    pressure = ((pressure - var2 / 4096.0) * 6250.0) / var1
    var1 = P9 * pressure * pressure / 2147483648.0
    var2 = pressure * P8 / 32768.0
    pressure += (var1 + var2 + P7) / 16.0

    return round(temperature, 2), round(pressure / 100.0, 2)  # hPa


def read_bmp():
    """Returns (temperature_C, pressure_hPa) or (None, None)."""
    if not _init_bmp():
        return None, None
    try:
        if isinstance(_bmp_sensor, tuple) and _bmp_sensor[0] == 'raw_i2c':
            return _read_bmp_raw_i2c(_bmp_sensor[1])
        # Library-based
        t = round(_bmp_sensor.temperature, 2)
        p = round(_bmp_sensor.pressure, 2)
        if -40 <= t <= 85 and 300 <= p <= 1100:
            return t, p
    except Exception as e:
        print("[sensors] BMP280 read failed:", e)
    return None, None


# ── ANALOG SENSORS (ADC) ─────────────────────────────────────────────────────
_rain_adc = None
_ldr_adc  = None

def _init_adcs():
    global _rain_adc, _ldr_adc
    try:
        from machine import ADC, Pin
        if _rain_adc is None:
            _rain_adc = ADC(Pin(34))
            _rain_adc.atten(ADC.ATTN_11DB)   # 0–3.3 V range
            _rain_adc.width(ADC.WIDTH_12BIT)  # 0–4095
            print("[sensors] Rain ADC init OK on GPIO34")
    except Exception as e:
        print("[sensors] Rain ADC init failed:", e)
        _rain_adc = None

    try:
        from machine import ADC, Pin
        if _ldr_adc is None:
            _ldr_adc = ADC(Pin(35))
            _ldr_adc.atten(ADC.ATTN_11DB)
            _ldr_adc.width(ADC.WIDTH_12BIT)
            print("[sensors] LDR ADC init OK on GPIO35")
    except Exception as e:
        print("[sensors] LDR ADC init failed:", e)
        _ldr_adc = None


def read_rain():
    """
    Returns (raw_value 0-4095, is_raining bool) or (None, False).
    Rain sensor: lower ADC = more water (conductive path).
    """
    _init_adcs()
    if _rain_adc is None:
        return None, False
    try:
        samples = []
        import time
        for _ in range(5):
            samples.append(_rain_adc.read())
            time.sleep_ms(10)
        val = sorted(samples)[2]  # median
        is_raining = val < 2000   # empirical threshold
        return val, is_raining
    except Exception as e:
        print("[sensors] Rain read failed:", e)
        return None, False


def read_light():
    """Returns raw LDR value 0-4095 or None. Higher = brighter (depends on module)."""
    _init_adcs()
    if _ldr_adc is None:
        return None
    try:
        samples = []
        import time
        for _ in range(5):
            samples.append(_ldr_adc.read())
            time.sleep_ms(10)
        return sorted(samples)[2]  # median
    except Exception as e:
        print("[sensors] LDR read failed:", e)
        return None


# ── DERIVED METRICS ──────────────────────────────────────────────────────────

def heat_index(temp_c, humidity):
    """Steadman heat index. Returns None if inputs unavailable."""
    if temp_c is None or humidity is None:
        return None
    try:
        T = temp_c * 9 / 5 + 32  # to Fahrenheit
        RH = humidity
        HI = (-42.379 + 2.04901523 * T + 10.14333127 * RH
              - 0.22475541 * T * RH - 6.83783e-3 * T ** 2
              - 5.481717e-2 * RH ** 2 + 1.22874e-3 * T ** 2 * RH
              + 8.5282e-4 * T * RH ** 2 - 1.99e-6 * T ** 2 * RH ** 2)
        return round((HI - 32) * 5 / 9, 2)  # back to Celsius
    except Exception:
        return None


def dew_point(temp_c, humidity):
    """Magnus formula dew point. Returns None if inputs unavailable."""
    if temp_c is None or humidity is None:
        return None
    try:
        a, b = 17.27, 237.7
        alpha = (a * temp_c) / (b + temp_c) + math.log(humidity / 100.0)
        dp = (b * alpha) / (a - alpha)
        return round(dp, 2)
    except Exception:
        return None


# ── UNIFIED READ ─────────────────────────────────────────────────────────────

def read_all():
    """
    Single call that returns a complete dict of all sensor data.
    Any unavailable sensor yields None for its fields — never raises.
    """
    dht_temp, dht_hum = read_dht()
    bmp_temp, bmp_pres = read_bmp()
    rain_val, is_rain  = read_rain()
    light_val          = read_light()

    # Prefer BMP280 for temp if DHT failed; average if both present
    if dht_temp is not None and bmp_temp is not None:
        temperature = round((dht_temp + bmp_temp) / 2, 2)
    elif dht_temp is not None:
        temperature = dht_temp
    elif bmp_temp is not None:
        temperature = bmp_temp
    else:
        temperature = None

    humidity = dht_hum
    pressure = bmp_pres

    hi = heat_index(temperature, humidity)
    dp = dew_point(temperature, humidity)

    return {
        "temperature": temperature,
        "humidity":    humidity,
        "pressure":    pressure,
        "rain_value":  rain_val,
        "is_raining":  is_rain,
        "light_value": light_val,
        "heat_index":  hi,
        "dew_point":   dp,
        # individual sensor temps for debug
        "_dht_temp":   dht_temp,
        "_bmp_temp":   bmp_temp,
    }

