"""
config.py  –  Load and save persistent config from config.json
"""
import json

CONFIG_FILE = "config.json"

_DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "api_key": "",
    "server_url": "https://your-arkweather-domain.com/api/push/",
    "device_name": "ArkWeather-Node",
    "push_interval": 60,
}


def load():
    """Return config dict, falling back to defaults for missing keys."""
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        # Merge with defaults so new keys are always present
        for k, v in _DEFAULTS.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return dict(_DEFAULTS)


def save(cfg: dict):
    """Persist config dict to flash."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
        return True
    except Exception as e:
        print("[config] save error:", e)
        return False


def update(key, value):
    """Update a single key and persist."""
    cfg = load()
    cfg[key] = value
    return save(cfg)

