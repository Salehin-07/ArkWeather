"""
display.py  –  OLED SSD1306 manager (128×64, I2C on GPIO21/22).
Falls back gracefully to print() if display unavailable.
Supports multiple pages that auto-rotate every few seconds.
"""

import time

_display = None
_page    = 0
_pages   = 3

# ── Init ─────────────────────────────────────────────────────────────────────

def init():
    global _display
    if _display is not None:
        return True
    for addr in (0x3C, 0x3D):
        try:
            import machine, ssd1306
            i2c = machine.SoftI2C(scl=machine.Pin(22), sda=machine.Pin(21), freq=400000)
            _display = ssd1306.SSD1306_I2C(128, 64, i2c, addr=addr)
            _display.fill(0)
            _display.show()
            print(f"[display] SSD1306 init OK at 0x{addr:02X}")
            return True
        except Exception as e:
            print(f"[display] SSD1306 at 0x{addr:02X} failed:", e)

    # Try hardware I2C as fallback
    try:
        import machine, ssd1306
        i2c = machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21), freq=400000)
        _display = ssd1306.SSD1306_I2C(128, 64, i2c)
        _display.fill(0)
        _display.show()
        print("[display] SSD1306 init OK via hardware I2C")
        return True
    except Exception as e:
        print("[display] All OLED init attempts failed:", e)
        _display = None
        return False


def _safe_show():
    if _display is None:
        return
    try:
        _display.show()
    except Exception as e:
        print("[display] show() error:", e)


def _text(msg, x, y, colour=1):
    if _display is None:
        return
    try:
        _display.text(str(msg), x, y, colour)
    except Exception:
        pass


def _fill(colour=0):
    if _display is None:
        return
    try:
        _display.fill(colour)
    except Exception:
        pass


def _hline(x, y, w):
    if _display is None:
        return
    try:
        _display.hline(x, y, w, 1)
    except Exception:
        pass


# ── Screens ───────────────────────────────────────────────────────────────────

def _fmt(v, decimals=1, unit=""):
    if v is None:
        return "---"
    return f"{v:.{decimals}f}{unit}"


def show_boot(version="1.0"):
    init()
    _fill(0)
    _text("  ArkWeather", 0, 0)
    _hline(0, 10, 128)
    _text(f"  v{version}", 0, 18)
    _text("  Booting...", 0, 32)
    _safe_show()


def show_ap_mode(ssid, ip):
    init()
    _fill(0)
    _text("WiFi Setup Mode", 0, 0)
    _hline(0, 10, 128)
    _text(f"AP: {ssid[:14]}", 0, 16)
    _text(f"IP: {ip}", 0, 28)
    _text("Visit the AP IP", 0, 40)
    _text("to configure.", 0, 52)
    _safe_show()


def show_connecting(ssid):
    init()
    _fill(0)
    _text("Connecting...", 0, 0)
    _hline(0, 10, 128)
    ssid_str = ssid[:16] if ssid else "(none)"
    _text(f"SSID: {ssid_str}", 0, 20)
    _safe_show()


def show_status(wifi_ok, ble_ok, push_ok, ip=""):
    init()
    _fill(0)
    _text("ArkWeather", 0, 0)
    _hline(0, 10, 128)
    _text(f"WiFi: {'OK ' + ip[:9] if wifi_ok else 'OFFLINE'}", 0, 14)
    _text(f"BLE:  {'ON' if ble_ok else 'OFF'}", 0, 26)
    _text(f"Push: {'OK' if push_ok else 'FAIL'}", 0, 38)
    _safe_show()


def show_readings_page1(data):
    """Temperature / Humidity / Pressure."""
    init()
    _fill(0)
    _text("Temp  Hum  Press", 0, 0)
    _hline(0, 10, 128)
    _text(_fmt(data.get("temperature"), 1, "C"), 0, 16)
    _text(_fmt(data.get("humidity"), 0, "%"), 52, 16)
    _text(_fmt(data.get("pressure"), 1), 88, 16)
    _hline(0, 28, 128)
    _text(f"HI: {_fmt(data.get('heat_index'), 1, 'C')}", 0, 34)
    _text(f"DP: {_fmt(data.get('dew_point'), 1, 'C')}", 0, 46)
    rain_str = "RAIN" if data.get("is_raining") else "DRY"
    _text(f"Rain:{rain_str}", 70, 34)
    _safe_show()


def show_readings_page2(data):
    """Rain value / Light value."""
    init()
    _fill(0)
    _text("Rain    Light", 0, 0)
    _hline(0, 10, 128)
    rv = data.get("rain_value")
    lv = data.get("light_value")
    _text(_fmt(rv, 0), 0, 18)
    _text(_fmt(lv, 0), 72, 18)
    rain_bar = int((4095 - (rv or 4095)) / 4095 * 40)
    light_bar = int((lv or 0) / 4095 * 40)
    # Simple bar graphs
    for i in range(rain_bar):
        try:
            _display.pixel(i, 36, 1)
            _display.pixel(i, 37, 1)
            _display.pixel(i, 38, 1)
        except Exception:
            break
    for i in range(light_bar):
        try:
            _display.pixel(72 + i, 36, 1)
            _display.pixel(72 + i, 37, 1)
            _display.pixel(72 + i, 38, 1)
        except Exception:
            break
    _text(f"R:{_fmt(rv, 0)}  L:{_fmt(lv, 0)}", 0, 52)
    _safe_show()


def show_readings_page3(data, ts=""):
    """Compact all-in-one."""
    init()
    _fill(0)
    _text("-- ArkWeather --", 0, 0)
    _hline(0, 10, 128)
    _text(f"T:{_fmt(data.get('temperature'),1)}C H:{_fmt(data.get('humidity'),0)}%", 0, 14)
    _text(f"P:{_fmt(data.get('pressure'),1)}hPa", 0, 26)
    _text(f"Rain:{'YES' if data.get('is_raining') else 'NO '} L:{_fmt(data.get('light_value'),0)}", 0, 38)
    _text(ts[-8:] if ts else "", 0, 52)
    _safe_show()


def next_page(data, ts=""):
    """Cycle through display pages."""
    global _page
    try:
        if _page == 0:
            show_readings_page1(data)
        elif _page == 1:
            show_readings_page2(data)
        else:
            show_readings_page3(data, ts)
        _page = (_page + 1) % _pages
    except Exception as e:
        print("[display] page render error:", e)


def show_error(msg):
    init()
    _fill(0)
    _text("! ERROR !", 20, 0)
    _hline(0, 10, 128)
    # Word-wrap at 16 chars
    words = str(msg)[:64]
    for i, line_start in enumerate(range(0, len(words), 16)):
        _text(words[line_start:line_start+16], 0, 14 + i * 12)
    _safe_show()


