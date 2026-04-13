"""
ble_server.py  –  BLE GATT server for ArkWeather ESP32.

Advertises as "ArkWeather" and exposes sensor readings as
readable + notifiable characteristics under a custom service.

Service UUID:   6E400001-B5A3-F393-E0A9-E50E24DCCA9E  (ArkWeather)
Characteristics:
  6E400002 – Temperature   (float, 4 bytes, little-endian)
  6E400003 – Humidity      (float, 4 bytes)
  6E400004 – Pressure      (float, 4 bytes)
  6E400005 – Rain Value    (uint16, 2 bytes)
  6E400006 – Is Raining    (uint8, 1 byte: 0/1)
  6E400007 – Light Value   (uint16, 2 bytes)
  6E400008 – JSON Payload  (utf-8 string, up to 512 bytes)
"""

import struct
import json

try:
    import bluetooth
    _HAS_BLE = True
except ImportError:
    _HAS_BLE = False
    print("[ble] bluetooth module not available – BLE disabled")

# ── UUID helpers ──────────────────────────────────────────────────────────────

def _uuid(short_or_full):
    """Accept a 16-bit int or full string UUID."""
    if isinstance(short_or_full, int):
        return bluetooth.UUID(short_or_full)
    return bluetooth.UUID(short_or_full)


# ── BLE Server class ──────────────────────────────────────────────────────────

_FLAG_READ   = 0x0002
_FLAG_NOTIFY = 0x0010

_SVC_UUID  = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
_TEMP_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
_HUM_UUID  = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
_PRES_UUID = "6E400004-B5A3-F393-E0A9-E50E24DCCA9E"
_RAIN_UUID = "6E400005-B5A3-F393-E0A9-E50E24DCCA9E"
_IRAIN_UUID= "6E400006-B5A3-F393-E0A9-E50E24DCCA9E"
_LIGHT_UUID= "6E400007-B5A3-F393-E0A9-E50E24DCCA9E"
_JSON_UUID = "6E400008-B5A3-F393-E0A9-E50E24DCCA9E"


class BLEServer:
    def __init__(self, device_name="ArkWeather"):
        self._name     = device_name[:16]
        self._ble      = None
        self._handles  = {}
        self._conn     = None
        self._started  = False

    def start(self):
        if not _HAS_BLE:
            print("[ble] BLE not available")
            return False
        try:
            self._ble = bluetooth.BLE()
            self._ble.active(True)
            self._ble.irq(self._irq)
            self._register_services()
            self._advertise()
            self._started = True
            print("[ble] BLE server started, advertising as:", self._name)
            return True
        except Exception as e:
            print("[ble] start() failed:", e)
            self._ble    = None
            self._started = False
            return False

    def stop(self):
        if self._ble:
            try:
                self._ble.active(False)
            except Exception:
                pass
        self._started = False
        print("[ble] stopped")

    def is_started(self):
        return self._started

    def _register_services(self):
        """Register GATT service + all characteristics."""
        _NOTIFY_READ = _FLAG_READ | _FLAG_NOTIFY

        WEATHER_SERVICE = (
            _uuid(_SVC_UUID),
            (
                (_uuid(_TEMP_UUID),  _NOTIFY_READ),
                (_uuid(_HUM_UUID),   _NOTIFY_READ),
                (_uuid(_PRES_UUID),  _NOTIFY_READ),
                (_uuid(_RAIN_UUID),  _NOTIFY_READ),
                (_uuid(_IRAIN_UUID), _NOTIFY_READ),
                (_uuid(_LIGHT_UUID), _NOTIFY_READ),
                (_uuid(_JSON_UUID),  _NOTIFY_READ),
            ),
        )

        services = (WEATHER_SERVICE,)
        ((h_temp, h_hum, h_pres, h_rain, h_irain, h_light, h_json),) = \
            self._ble.gatts_register_services(services)

        self._handles = {
            'temp':   h_temp,
            'hum':    h_hum,
            'pres':   h_pres,
            'rain':   h_rain,
            'irain':  h_irain,
            'light':  h_light,
            'json':   h_json,
        }
        # Increase JSON buffer
        self._ble.gatts_set_buffer(h_json, 512, True)

    def _irq(self, event, data):
        _IRQ_CENTRAL_CONNECT    = 1
        _IRQ_CENTRAL_DISCONNECT = 2

        if event == _IRQ_CENTRAL_CONNECT:
            conn_handle, _, _ = data
            self._conn = conn_handle
            print("[ble] Client connected, handle:", conn_handle)
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._conn = None
            print("[ble] Client disconnected")
            # Re-advertise
            try:
                self._advertise()
            except Exception:
                pass

    def _advertise(self):
        """Start BLE advertisement."""
        import struct
        # Advertising payload: flags + name
        name_bytes = self._name.encode()
        adv_data = (
            bytes([2, 0x01, 0x06]) +                          # Flags: LE General Discoverable
            bytes([len(name_bytes) + 1, 0x09]) + name_bytes   # Complete Local Name
        )
        try:
            self._ble.gap_advertise(100_000, adv_data=adv_data)  # 100ms interval
            print("[ble] Advertising started")
        except Exception as e:
            print("[ble] gap_advertise failed:", e)

    def update(self, data: dict):
        """
        Push new sensor readings to all characteristics.
        Call this whenever you have fresh data.
        """
        if not self._started or self._ble is None:
            return

        def _write(handle, raw_bytes):
            try:
                self._ble.gatts_write(handle, raw_bytes)
                if self._conn is not None:
                    try:
                        self._ble.gatts_notify(self._conn, handle)
                    except Exception:
                        pass
            except Exception as e:
                print("[ble] write error:", e)

        h = self._handles

        # Float fields (4 bytes little-endian)
        temp = data.get("temperature")
        if temp is not None:
            _write(h['temp'], struct.pack('<f', float(temp)))

        hum = data.get("humidity")
        if hum is not None:
            _write(h['hum'], struct.pack('<f', float(hum)))

        pres = data.get("pressure")
        if pres is not None:
            _write(h['pres'], struct.pack('<f', float(pres)))

        # Uint16 fields
        rain = data.get("rain_value")
        if rain is not None:
            _write(h['rain'], struct.pack('<H', int(min(max(rain, 0), 65535))))

        light = data.get("light_value")
        if light is not None:
            _write(h['light'], struct.pack('<H', int(min(max(light, 0), 65535))))

        # Bool field
        _write(h['irain'], bytes([1 if data.get("is_raining") else 0]))

        # JSON summary (truncated to 510 bytes)
        payload = {
            "t":  temp,
            "h":  hum,
            "p":  pres,
            "rv": rain,
            "ir": data.get("is_raining", False),
            "lv": light,
            "hi": data.get("heat_index"),
            "dp": data.get("dew_point"),
        }
        json_bytes = json.dumps(payload).encode()[:510]
        _write(h['json'], json_bytes)


# ── Module-level singleton ─────────────────────────────────────────────────────

_server = None

def get_server(device_name="ArkWeather"):
    global _server
    if _server is None:
        _server = BLEServer(device_name)
    return _server

