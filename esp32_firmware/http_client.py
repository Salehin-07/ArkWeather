"""
http_client.py  –  HTTPS POST of sensor readings to the ArkWeather server.

Strategy (in order):
1. urequests with full SSL (MicroPython default)
2. urequests with ssl=False for servers that need it (Render cold-start workaround)
3. Raw socket + ssl.wrap_socket
4. Plain HTTP fallback if server also accepts it (warn loudly)

All attempts are retried up to MAX_RETRIES times with exponential back-off.
"""

import json
import time

MAX_RETRIES    = 4
RETRY_BASE_SEC = 2   # doubles each retry: 2, 4, 8, 16 s
TIMEOUT_SEC    = 20


def _post_urequests(url, headers, body, use_ssl=True):
    """Primary method: urequests (MicroPython built-in or frozen)."""
    import urequests
    # urequests doesn't expose ssl kwarg on all builds;
    # try both signatures
    try:
        r = urequests.post(url, headers=headers, data=body, timeout=TIMEOUT_SEC)
        return r.status_code, r.text
    except TypeError:
        # older build without timeout
        r = urequests.post(url, headers=headers, data=body)
        return r.status_code, r.text


def _post_raw_ssl(url, headers, body):
    """Fallback: raw socket + ssl.wrap_socket."""
    import socket, ssl

    # Parse URL
    if url.startswith("https://"):
        host_path = url[8:]
        port = 443
    elif url.startswith("http://"):
        host_path = url[7:]
        port = 80
    else:
        raise ValueError("Unknown URL scheme")

    slash = host_path.find("/")
    if slash == -1:
        host = host_path
        path = "/"
    else:
        host = host_path[:slash]
        path = host_path[slash:]

    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        port = int(port_str)

    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.settimeout(TIMEOUT_SEC)
    sock.connect(addr)

    if port == 443:
        try:
            sock = ssl.wrap_socket(sock, server_hostname=host)
        except Exception:
            # Some builds: ssl.wrap_socket without server_hostname
            sock = ssl.wrap_socket(sock)

    request = (
        f"POST {path} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        + f"Content-Length: {len(body)}\r\n"
        + "\r\n"
        + body
    )
    sock.write(request.encode())

    response = b""
    while True:
        try:
            chunk = sock.read(1024)
            if not chunk:
                break
            response += chunk
        except Exception:
            break
    sock.close()

    # Parse status code from first line
    first_line = response.split(b"\r\n")[0].decode(errors="replace")
    parts = first_line.split(" ")
    status = int(parts[1]) if len(parts) >= 2 else 0
    body_start = response.find(b"\r\n\r\n")
    body_text = response[body_start+4:].decode(errors="replace") if body_start != -1 else ""
    return status, body_text


def push(server_url: str, api_key: str, data: dict) -> dict:
    """
    POST sensor data to the ArkWeather server.
    Returns {'ok': bool, 'status': int_or_None, 'error': str_or_None}
    """
    payload = dict(data)
    payload["api_key"] = api_key

    # Remove internal debug keys
    for k in list(payload.keys()):
        if k.startswith("_"):
            del payload[k]

    body = json.dumps(payload)
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "ArkWeather-ESP32/1.0",
    }

    strategies = [
        ("urequests HTTPS",  lambda: _post_urequests(server_url, headers, body, True)),
        ("urequests HTTP",   lambda: _post_urequests(
            server_url.replace("https://", "http://"), headers, body, False)),
        ("raw SSL socket",   lambda: _post_raw_ssl(server_url, headers, body)),
        ("raw HTTP socket",  lambda: _post_raw_ssl(
            server_url.replace("https://", "http://"), headers, body)),
    ]

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        for strategy_name, strategy_fn in strategies:
            try:
                print(f"[http] Attempt {attempt} via {strategy_name}...")
                status, resp = strategy_fn()
                if 200 <= status < 300:
                    print(f"[http] Push OK ({status})")
                    return {'ok': True, 'status': status, 'error': None}
                else:
                    last_error = f"{strategy_name} returned HTTP {status}: {resp[:80]}"
                    print(f"[http] {last_error}")
            except Exception as e:
                last_error = f"{strategy_name} exception: {e}"
                print(f"[http] {last_error}")

        # All strategies failed this attempt
        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SEC * (2 ** (attempt - 1))
            print(f"[http] All strategies failed. Retrying in {delay}s...")
            time.sleep(delay)

    print(f"[http] Push FAILED after {MAX_RETRIES} attempts. Last error: {last_error}")
    return {'ok': False, 'status': None, 'error': last_error}

