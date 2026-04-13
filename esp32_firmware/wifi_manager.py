"""
wifi_manager.py  –  WiFi connection + AP provisioning portal.

Flow:
  1. Try to connect using saved SSID/password (up to CONNECT_TIMEOUT seconds).
  2. If that fails, start an Access Point called "ArkWeather-Setup".
  3. The AP runs a tiny HTTP config portal on 192.168.4.1.
  4. User connects phone to the AP, visits http://192.168.4.1 in browser,
     fills in new SSID/password (and optionally server_url/api_key).
  5. Config is saved; ESP32 reboots into normal mode.
"""

import network
import time
import socket
import json

CONNECT_TIMEOUT = 20   # seconds to wait for WiFi
AP_SSID         = "ArkWeather-Setup"
AP_PASSWORD     = "arkweather"   # WPA2; at least 8 chars
PORTAL_IP       = "192.168.4.1"


# ── STA (station) connection ──────────────────────────────────────────────────

def connect(ssid, password, timeout=CONNECT_TIMEOUT):
    """
    Try to join a WiFi network.
    Returns (wlan, ip_str) on success, (None, None) on failure.
    """
    if not ssid:
        print("[wifi] No SSID configured")
        return None, None

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    time.sleep_ms(500)

    if sta.isconnected():
        ip = sta.ifconfig()[0]
        print(f"[wifi] Already connected: {ip}")
        return sta, ip

    print(f"[wifi] Connecting to '{ssid}'...")
    sta.connect(ssid, password)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if sta.isconnected():
            ip = sta.ifconfig()[0]
            print(f"[wifi] Connected! IP: {ip}")
            return sta, ip
        time.sleep_ms(500)
        print(".", end="")

    print(f"\n[wifi] Failed to connect to '{ssid}'")
    sta.active(False)
    return None, None


# ── AP + Config Portal ────────────────────────────────────────────────────────

_PORTAL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ArkWeather Setup</title>
<style>
  body{{font-family:sans-serif;background:#0d1117;color:#e6edf3;
        display:flex;justify-content:center;padding:20px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:12px;
          padding:28px;width:100%;max-width:400px}}
  h1{{color:#58a6ff;margin:0 0 6px}}
  p{{color:#8b949e;font-size:.9em;margin:0 0 20px}}
  label{{display:block;margin-bottom:4px;font-size:.85em;color:#8b949e}}
  input{{width:100%;box-sizing:border-box;padding:10px;margin-bottom:16px;
         background:#0d1117;border:1px solid #30363d;border-radius:8px;
         color:#e6edf3;font-size:1em}}
  input:focus{{outline:none;border-color:#58a6ff}}
  button{{width:100%;padding:12px;background:#238636;border:none;
          border-radius:8px;color:#fff;font-size:1em;cursor:pointer;
          margin-top:4px}}
  button:hover{{background:#2ea043}}
  .saved{{background:#1f6feb;color:#fff;padding:14px;border-radius:8px;
           text-align:center;margin-top:16px;display:none}}
  .section{{border-top:1px solid #30363d;margin-top:20px;padding-top:16px}}
  .section h2{{font-size:1em;color:#8b949e;margin:0 0 12px}}
</style>
</head>
<body>
<div class="card">
  <h1>&#x1F4F6; ArkWeather</h1>
  <p>Configure your weather node. Changes take effect on reboot.</p>

  <form id="wf" onsubmit="save(event)">
    <label>WiFi Network (SSID)</label>
    <input name="ssid" placeholder="Your WiFi name" value="{ssid}" required>

    <label>WiFi Password</label>
    <input name="password" type="password" placeholder="Your WiFi password" value="{password}">

    <div class="section">
      <h2>Server Settings</h2>
      <label>Server URL (push endpoint)</label>
      <input name="server_url" value="{server_url}" placeholder="https://yourdomain.com/api/push/">

      <label>API Key</label>
      <input name="api_key" value="{api_key}" placeholder="Device API key">

      <label>Push Interval (seconds)</label>
      <input name="push_interval" type="number" value="{push_interval}" min="10" max="3600">
    </div>

    <button type="submit">&#x2713; Save &amp; Reboot</button>
  </form>
  <div class="saved" id="saved">Saved! Rebooting ESP32...</div>
</div>
<script>
function save(e){{
  e.preventDefault();
  const fd = new FormData(e.target);
  const data={{}};
  fd.forEach((v,k)=>data[k]=v);
  fetch('/save',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify(data)}})
  .then(r=>r.json()).then(()=>{{
    document.getElementById('saved').style.display='block';
    e.target.style.display='none';
  }}).catch(err=>alert('Error: '+err));
}}
</script>
</body>
</html>
"""


def _handle_request(client_sock, cfg, saved_flag):
    """Parse one HTTP request and send a response."""
    try:
        client_sock.settimeout(5)
        raw = b""
        while True:
            chunk = client_sock.recv(1024)
            if not chunk:
                break
            raw += chunk
            if b"\r\n\r\n" in raw:
                break

        request = raw.decode(errors="replace")
        first_line = request.split("\r\n")[0]
        method, path = first_line.split(" ")[:2]

        if method == "GET" and path in ("/", "/index.html"):
            html = _PORTAL_HTML.format(
                ssid          = cfg.get("wifi_ssid", ""),
                password      = "",   # never send back the password
                server_url    = cfg.get("server_url", ""),
                api_key       = cfg.get("api_key", ""),
                push_interval = cfg.get("push_interval", 60),
            )
            resp = (
                "HTTP/1.0 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(html)}\r\n\r\n"
                + html
            )
            client_sock.sendall(resp.encode())

        elif method == "POST" and path == "/save":
            # Find JSON body
            header_end = raw.find(b"\r\n\r\n")
            body_raw = raw[header_end + 4:]
            # If body incomplete, read more
            content_length = 0
            for line in request.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":")[1].strip())
            while len(body_raw) < content_length:
                chunk = client_sock.recv(512)
                if not chunk:
                    break
                body_raw += chunk

            new_cfg = json.loads(body_raw.decode())

            # Update config
            cfg["wifi_ssid"]      = new_cfg.get("ssid", "")
            cfg["wifi_password"]  = new_cfg.get("password", cfg.get("wifi_password", ""))
            cfg["server_url"]     = new_cfg.get("server_url", cfg.get("server_url", ""))
            cfg["api_key"]        = new_cfg.get("api_key", cfg.get("api_key", ""))
            try:
                cfg["push_interval"] = int(new_cfg.get("push_interval", 60))
            except Exception:
                pass

            import config as cfg_mod
            cfg_mod.save(cfg)
            saved_flag[0] = True

            resp_body = json.dumps({"status": "ok"})
            resp = (
                "HTTP/1.0 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(resp_body)}\r\n\r\n"
                + resp_body
            )
            client_sock.sendall(resp.encode())

        else:
            client_sock.sendall(b"HTTP/1.0 404 Not Found\r\n\r\nNot found")

    except Exception as e:
        print("[portal] request error:", e)
    finally:
        try:
            client_sock.close()
        except Exception:
            pass


def start_ap_portal(cfg: dict, on_display=None):
    """
    Start AP + config portal. Blocks until credentials are saved.
    on_display(ssid, ip) is called so you can update the OLED.
    Returns updated cfg dict.
    """
    # Disable STA
    try:
        sta = network.WLAN(network.STA_IF)
        sta.active(False)
    except Exception:
        pass

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    try:
        ap.config(essid=AP_SSID, password=AP_PASSWORD,
                  authmode=network.AUTH_WPA_WPA2_PSK)
    except Exception:
        try:
            ap.config(essid=AP_SSID, password=AP_PASSWORD)
        except Exception as e:
            print("[wifi] AP config error:", e)

    time.sleep(2)
    ap_ip = ap.ifconfig()[0]
    print(f"[wifi] AP up: SSID='{AP_SSID}' Password='{AP_PASSWORD}' IP={ap_ip}")

    if on_display:
        try:
            on_display(AP_SSID, ap_ip)
        except Exception:
            pass

    # Start TCP server on port 80
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", 80))
    server_sock.listen(3)
    server_sock.settimeout(1)   # non-blocking so we can check saved_flag

    saved_flag = [False]
    print("[wifi] Config portal running at http://192.168.4.1")

    while not saved_flag[0]:
        try:
            client, addr = server_sock.accept()
            print(f"[portal] connection from {addr}")
            _handle_request(client, cfg, saved_flag)
        except OSError:
            pass   # timeout — loop again
        except Exception as e:
            print("[portal] error:", e)

    server_sock.close()
    ap.active(False)
    print("[wifi] Config saved. Rebooting in 2s...")
    time.sleep(2)
    import machine
    machine.reset()

