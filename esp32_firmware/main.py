"""
main.py  –  ArkWeather ESP32 Firmware  v1.0
============================================
Boot flow:
  1. Show boot screen on OLED.
  2. Load config from flash.
  3. Init BLE server (non-blocking).
  4. Try WiFi with saved credentials.
     • Success → enter main loop.
     • Failure → start AP config portal (blocks until saved, then reboots).
  5. Main loop every push_interval seconds:
     a. Read all sensors.
     b. Update OLED (rotating pages).
     c. Update BLE characteristics.
     d. Push to server via HTTPS.

All component failures are caught and logged; the loop continues regardless.
"""

import time
import machine

VERSION = "1.0"

# ── Safe imports ──────────────────────────────────────────────────────────────

def _safe_import(name):
    try:
        return __import__(name)
    except Exception as e:
        print(f"[main] Failed to import {name}: {e}")
        return None


import config as cfg_mod
import sensors
import display
import ble_server as ble_mod
import wifi_manager
import http_client


# ── Boot screen ───────────────────────────────────────────────────────────────

print("=" * 40)
print(f"  ArkWeather Firmware v{VERSION}")
print("  YZ-ESP32-E")
print("=" * 40)

display.show_boot(VERSION)
time.sleep(2)


# ── Load config ───────────────────────────────────────────────────────────────

cfg = cfg_mod.load()
print("[main] Config loaded:")
print(f"  SSID:     {cfg.get('wifi_ssid') or '(not set)'}")
print(f"  Server:   {cfg.get('server_url')}")
print(f"  Interval: {cfg.get('push_interval')}s")


# ── BLE init (doesn't need WiFi) ──────────────────────────────────────────────

device_name = cfg.get("device_name", "ArkWeather")
ble_srv = ble_mod.get_server(device_name)
ble_ok = False
try:
    ble_ok = ble_srv.start()
except Exception as e:
    print("[main] BLE start error:", e)


# ── WiFi ──────────────────────────────────────────────────────────────────────

wifi_ok = False
device_ip = ""

ssid     = cfg.get("wifi_ssid", "")
password = cfg.get("wifi_password", "")

display.show_connecting(ssid)

wlan, ip = wifi_manager.connect(ssid, password)
if wlan and ip:
    wifi_ok   = True
    device_ip = ip
    print(f"[main] WiFi up: {ip}")
else:
    print("[main] WiFi failed — launching AP config portal")
    # This call blocks until user saves config and the ESP reboots
    wifi_manager.start_ap_portal(
        cfg,
        on_display=display.show_ap_mode,
    )
    # start_ap_portal ends with machine.reset() so code below only
    # runs if something went very wrong
    import sys
    sys.exit()


# ── NTP time sync (best-effort) ───────────────────────────────────────────────
try:
    import ntptime
    ntptime.settime()
    print("[main] NTP time synced")
except Exception as e:
    print("[main] NTP sync failed (non-fatal):", e)


# ── Status screen ─────────────────────────────────────────────────────────────

display.show_status(wifi_ok, ble_ok, False, device_ip)
time.sleep(2)


# ── Main loop ─────────────────────────────────────────────────────────────────

push_interval  = int(cfg.get("push_interval", 60))
server_url     = cfg.get("server_url", "")
api_key        = cfg.get("api_key", "")

last_push_time  = 0
last_page_time  = 0
PAGE_INTERVAL   = 5       # rotate OLED page every 5s

last_push_ok    = False
loop_errors     = 0
MAX_LOOP_ERRORS = 20      # reboot if something is very wrong

print(f"[main] Entering main loop (push every {push_interval}s)")

while True:
    try:
        now = time.time()

        # ── Read sensors (always, even if push not due) ────────────────────
        sensor_data = {}
        try:
            sensor_data = sensors.read_all()
        except Exception as e:
            print("[main] sensors.read_all() error:", e)

        # ── OLED page rotation ─────────────────────────────────────────────
        if now - last_page_time >= PAGE_INTERVAL:
            last_page_time = now
            try:
                ts = ""
                try:
                    import utime
                    t = utime.localtime()
                    ts = f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
                except Exception:
                    pass
                display.next_page(sensor_data, ts)
            except Exception as e:
                print("[main] display error:", e)

        # ── BLE update ────────────────────────────────────────────────────
        if sensor_data:
            try:
                ble_srv.update(sensor_data)
            except Exception as e:
                print("[main] BLE update error:", e)

        # ── WiFi keepalive check ──────────────────────────────────────────
        try:
            if wlan and not wlan.isconnected():
                print("[main] WiFi dropped — reconnecting...")
                display.show_connecting(ssid)
                wlan, ip = wifi_manager.connect(ssid, password, timeout=30)
                if wlan and ip:
                    wifi_ok   = True
                    device_ip = ip
                    print(f"[main] Reconnected: {ip}")
                else:
                    wifi_ok = False
                    print("[main] Reconnect failed")
        except Exception as e:
            print("[main] WiFi check error:", e)

        # ── HTTP push ─────────────────────────────────────────────────────
        if now - last_push_time >= push_interval:
            last_push_time = now

            if not sensor_data:
                print("[main] No sensor data — skipping push")
            elif not wifi_ok:
                print("[main] No WiFi — skipping push")
            elif not server_url or not api_key:
                print("[main] Server URL or API key missing — skipping push")
            else:
                try:
                    result = http_client.push(server_url, api_key, sensor_data)
                    last_push_ok = result["ok"]
                    if not result["ok"]:
                        print("[main] Push failed:", result.get("error"))
                except Exception as e:
                    last_push_ok = False
                    print("[main] http_client.push() exception:", e)

            # Brief status screen after push
            try:
                display.show_status(wifi_ok, ble_ok, last_push_ok, device_ip)
                time.sleep(1)
            except Exception:
                pass

        # ── Debug heartbeat ───────────────────────────────────────────────
        try:
            t   = sensor_data.get("temperature")
            h   = sensor_data.get("humidity")
            p   = sensor_data.get("pressure")
            rv  = sensor_data.get("rain_value")
            lv  = sensor_data.get("light_value")
            ir  = sensor_data.get("is_raining")
            print(f"[data] T={t}°C H={h}% P={p}hPa Rain={rv}({'WET' if ir else 'DRY'}) Light={lv}")
        except Exception:
            pass

        loop_errors = 0   # reset on successful loop
        time.sleep(1)     # 1-second granularity

    except KeyboardInterrupt:
        print("[main] KeyboardInterrupt — stopping")
        ble_srv.stop()
        break

    except Exception as e:
        loop_errors += 1
        print(f"[main] Unhandled loop error ({loop_errors}/{MAX_LOOP_ERRORS}):", e)
        try:
            display.show_error(str(e))
        except Exception:
            pass
        if loop_errors >= MAX_LOOP_ERRORS:
            print("[main] Too many errors — rebooting in 10s")
            time.sleep(10)
            machine.reset()
        time.sleep(5)

