"""
ArkWeather Forecasting Engine
==============================
Algorithms:
  1. Holt-Winters Triple Exponential Smoothing  — temperature, humidity, pressure trends
  2. WMO Pressure Tendency Rule                 — 3-hour Δpressure → rain probability
  3. Dew-Point Depression                       — (T − Td) < 2°C signals saturation / rain
  4. Persistence + Trend Blend                  — best-in-class for <6 h forecasts
  5. Diurnal Correction                         — adjusts for daily temperature cycle

All functions are pure Python (no numpy/pandas needed at runtime).
They accept plain lists of dicts (from Django ORM .values()) and return
JSON-serialisable dicts so views can dump them straight to the template.
"""

from datetime import datetime, timedelta, timezone as _tz
import math
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# 1.  HOLT-WINTERS  (additive, no seasonal component for hourly data)
#     α = level, β = trend  — values tuned for weather series
# ─────────────────────────────────────────────────────────────────────────────

def holt_winters_forecast(values, steps=24, alpha=0.3, beta=0.1):
    """
    Double exponential smoothing (Holt's linear method).
    Suitable for temperature, humidity, pressure — any series with trend.

    Args:
        values: list of floats (chronological, most-recent LAST)
        steps:  number of future periods to project
        alpha:  level smoothing (0.3 is typical for weather)
        beta:   trend smoothing (0.1 damps trend drift)

    Returns:
        dict with 'fitted' (same length as input) and 'forecast' (length = steps)
    """
    vals = [v for v in values if v is not None]
    if len(vals) < 4:
        return None

    # Initialise
    level  = vals[0]
    trend  = (vals[1] - vals[0]) if len(vals) > 1 else 0.0
    fitted = []

    for v in vals:
        prev_level = level
        level = alpha * v + (1 - alpha) * (level + trend)
        trend = beta  * (level - prev_level) + (1 - beta) * trend
        fitted.append(round(level, 2))

    # Forecast
    forecasted = []
    for h in range(1, steps + 1):
        forecasted.append(round(level + h * trend, 2))

    return {'fitted': fitted, 'forecast': forecasted, 'level': level, 'trend': trend}


# ─────────────────────────────────────────────────────────────────────────────
# 2.  PRESSURE TENDENCY  (WMO standard: 3-hour change)
# ─────────────────────────────────────────────────────────────────────────────

PRESSURE_THRESHOLDS = [
    # (Δp hPa,  label,               rain_prob_addition)
    (-6.0,  'Rapidly falling — severe weather risk',  0.70),
    (-3.0,  'Falling — rain likely within 6 h',       0.50),
    (-1.5,  'Slowly falling — rain possible',         0.25),
    ( 0.0,  'Steady — conditions stable',             0.00),
    ( 1.5,  'Slowly rising — clearing likely',       -0.10),
    ( 3.0,  'Rising — fair weather improving',       -0.20),
    ( 6.0,  'Rapidly rising — settled conditions',   -0.30),
]

def pressure_tendency(hourly_rows, lookback_hours=3):
    """
    Compute 3-h pressure tendency and return a rain probability modifier.
    hourly_rows: list of dicts with keys 'hour' and 'avg_pressure' (or 'pressure')
    """
    if len(hourly_rows) < 2:
        return {'delta': None, 'label': 'Insufficient data', 'rain_mod': 0.0}

    pkey = 'avg_pressure' if 'avg_pressure' in hourly_rows[0] else 'pressure'
    recent = next((r for r in reversed(hourly_rows) if r.get(pkey)), None)
    if not recent:
        return {'delta': None, 'label': 'No pressure data', 'rain_mod': 0.0}

    # Find a row ~lookback_hours earlier
    target_dt = None
    if isinstance(recent.get('hour'), str):
        recent_dt = datetime.fromisoformat(recent['hour'])
    elif isinstance(recent.get('timestamp'), str):
        recent_dt = datetime.fromisoformat(recent['timestamp'])
    else:
        recent_dt = recent.get('hour') or recent.get('timestamp')

    if recent_dt:
        if recent_dt.tzinfo is None:
            recent_dt = recent_dt.replace(tzinfo=_tz.utc)
        target_dt = recent_dt - timedelta(hours=lookback_hours)

    older = None
    if target_dt:
        for r in hourly_rows:
            dt_key = 'hour' if 'hour' in r else 'timestamp'
            dt = r.get(dt_key)
            if dt:
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_tz.utc)
                if abs((dt - target_dt).total_seconds()) <= 3600:
                    if r.get(pkey) is not None:
                        older = r
                        break

    if not older:
        older = next((r for r in hourly_rows if r.get(pkey)), None)

    if not older or older is recent:
        return {'delta': None, 'label': 'Steady', 'rain_mod': 0.0}

    delta = float(recent[pkey]) - float(older[pkey])

    label   = PRESSURE_THRESHOLDS[-1][1]
    rain_mod = PRESSURE_THRESHOLDS[-1][2]
    for threshold, lbl, mod in PRESSURE_THRESHOLDS:
        if delta <= threshold:
            label    = lbl
            rain_mod = mod
            break

    return {
        'delta':    round(delta, 2),
        'label':    label,
        'rain_mod': rain_mod,
        'current':  round(float(recent[pkey]), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  DEW-POINT DEPRESSION  →  saturation / fog / rain signal
# ─────────────────────────────────────────────────────────────────────────────

def dew_point_depression_signal(temp, dew_point, humidity=None):
    """
    When T − Td < 2°C the air is near-saturated.
    If we don't have dew_point directly, derive from humidity via Magnus formula.
    Returns rain_mod in [0, 0.4].
    """
    if dew_point is None and humidity is not None and temp is not None:
        # Magnus approximation
        try:
            gamma = math.log(humidity / 100.0) + (17.625 * temp) / (243.04 + temp)
            dew_point = (243.04 * gamma) / (17.625 - gamma)
        except (ValueError, ZeroDivisionError):
            dew_point = None

    if temp is None or dew_point is None:
        return {'depression': None, 'rain_mod': 0.0, 'label': 'No data'}

    depression = temp - dew_point
    if depression <= 0:
        return {'depression': round(depression, 1), 'rain_mod': 0.4, 'label': 'Saturated air — fog or rain very likely'}
    elif depression <= 2:
        return {'depression': round(depression, 1), 'rain_mod': 0.3, 'label': 'Near-saturated — rain likely'}
    elif depression <= 5:
        return {'depression': round(depression, 1), 'rain_mod': 0.15, 'label': 'Moist air — showers possible'}
    elif depression <= 10:
        return {'depression': round(depression, 1), 'rain_mod': 0.05, 'label': 'Comfortable humidity'}
    else:
        return {'depression': round(depression, 1), 'rain_mod': -0.05, 'label': 'Dry air'}


# ─────────────────────────────────────────────────────────────────────────────
# 4.  DIURNAL TEMPERATURE MODEL  (sinusoidal correction)
#     Standard NWP post-processing technique
# ─────────────────────────────────────────────────────────────────────────────

def diurnal_correction(base_temp, hour_of_day, amplitude=4.0, peak_hour=14):
    """
    Apply a sinusoidal diurnal cycle correction to a forecast temperature.
    amplitude: typical daily range / 2  (4°C is conservative for Bangladesh)
    peak_hour: hour of daily max (14:00 = 2 PM local)
    """
    if base_temp is None:
        return None
    phase = 2 * math.pi * (hour_of_day - peak_hour) / 24
    correction = amplitude * math.sin(phase)
    return round(base_temp + correction, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 5.  RAIN PROBABILITY  (combine all signals)
# ─────────────────────────────────────────────────────────────────────────────

def compute_rain_probability(
    rain_pct_stations,   # % of stations currently reporting rain (0–100)
    pressure_mod,        # from pressure_tendency()
    dewpoint_mod,        # from dew_point_depression_signal()
    base_climatology=0.15,  # Bangladesh avg rain probability ~15%
):
    """
    Combine signals into a calibrated rain probability using additive blending.
    Clamps result to [0, 1].
    """
    station_signal = (rain_pct_stations or 0) / 100.0

    # Weighted blend: station rain is strongest signal
    prob = (
        0.50 * station_signal +
        0.25 * max(0.0, base_climatology + pressure_mod) +
        0.25 * max(0.0, base_climatology + dewpoint_mod)
    )
    return round(min(1.0, max(0.0, prob)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# 6.  MAIN FORECAST BUILDER  for DISTRICT (uses DistrictAggregate rows)
# ─────────────────────────────────────────────────────────────────────────────

def build_district_forecast(hourly_rows, steps=24):
    """
    Args:
        hourly_rows: list of dicts from DistrictAggregate.values()
                     keys: hour, avg_temperature, avg_humidity, avg_pressure,
                           rain_percentage, avg_heat_index, avg_light
        steps: forecast horizon in hours (default 24)

    Returns:
        dict with keys:
            forecast_hours  — list of ISO strings
            temperature     — Holt-Winters projected temps with diurnal
            humidity        — projected humidity
            pressure        — projected pressure
            rain_probability— combined rain prob per hour [0..1]
            pressure_signal — WMO tendency dict
            now_summary     — plain-language current + near-term outlook
            confidence      — 'high' / 'medium' / 'low'
    """
    if not hourly_rows:
        return None

    rows = sorted(hourly_rows, key=lambda r: r.get('hour') or r.get('timestamp') or '')

    temps     = [r.get('avg_temperature') for r in rows]
    humids    = [r.get('avg_humidity')    for r in rows]
    pressures = [r.get('avg_pressure')    for r in rows]
    rains     = [r.get('rain_percentage') or 0 for r in rows]

    # Holt-Winters for each variable
    temp_hw  = holt_winters_forecast(temps,     steps=steps, alpha=0.3, beta=0.08)
    hum_hw   = holt_winters_forecast(humids,    steps=steps, alpha=0.25, beta=0.05)
    pres_hw  = holt_winters_forecast(pressures, steps=steps, alpha=0.2,  beta=0.04)
    rain_hw  = holt_winters_forecast(rains,     steps=steps, alpha=0.4,  beta=0.1)

    if not temp_hw:
        return None

    # Pressure tendency
    pres_sig = pressure_tendency(rows, lookback_hours=3)

    # Latest values for dew-point depression
    latest = rows[-1]
    latest_temp  = latest.get('avg_temperature')
    latest_humid = latest.get('avg_humidity')

    # Get latest dew_point — district aggregates don't store it directly,
    # derive from Magnus formula
    dd_sig = dew_point_depression_signal(latest_temp, None, latest_humid)

    # Determine reference datetime
    last_dt_raw = latest.get('hour') or latest.get('timestamp')
    if isinstance(last_dt_raw, str):
        last_dt = datetime.fromisoformat(last_dt_raw)
    else:
        last_dt = last_dt_raw or datetime.now(_tz.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=_tz.utc)

    # Build forecast timeline
    forecast_hours = []
    forecast_temps = []
    forecast_humids = []
    forecast_pressures = []
    forecast_rain_probs = []

    for i, (ft, fh, fp, fr) in enumerate(zip(
        temp_hw['forecast'],
        hum_hw['forecast'] if hum_hw else [None]*steps,
        pres_hw['forecast'] if pres_hw else [None]*steps,
        rain_hw['forecast'] if rain_hw else [None]*steps,
    )):
        future_dt = last_dt + timedelta(hours=i + 1)
        forecast_hours.append(future_dt.isoformat())

        # Apply diurnal correction to temperature
        corrected_temp = diurnal_correction(ft, future_dt.hour)
        forecast_temps.append(corrected_temp)

        # Clamp humidity
        forecast_humids.append(round(min(100.0, max(0.0, fh)), 1) if fh else None)
        forecast_pressures.append(round(fp, 1) if fp else None)

        # Rain probability from combined signals
        rain_base = max(0, fr or 0)
        rain_prob = compute_rain_probability(
            rain_pct_stations=rain_base,
            pressure_mod=pres_sig['rain_mod'],
            dewpoint_mod=dd_sig['rain_mod'],
        )
        forecast_rain_probs.append(rain_prob)

    # Confidence: degrade with sparse data or high variability
    n = len([t for t in temps if t is not None])
    if n >= 18:
        confidence = 'high'
    elif n >= 8:
        confidence = 'medium'
    else:
        confidence = 'low'

    # Plain-language now_summary
    next_6h_rain = [p for p in forecast_rain_probs[:6] if p is not None]
    avg_6h_rain  = sum(next_6h_rain) / len(next_6h_rain) if next_6h_rain else 0

    if avg_6h_rain >= 0.6:
        outlook = 'Rain expected in the next 6 hours'
    elif avg_6h_rain >= 0.35:
        outlook = 'Showers possible in the next 6 hours'
    elif pres_sig['delta'] and pres_sig['delta'] < -2:
        outlook = 'Pressure falling — weather may deteriorate'
    elif temp_hw['trend'] > 0.3:
        outlook = 'Warming trend over the next 24 hours'
    elif temp_hw['trend'] < -0.3:
        outlook = 'Cooling trend over the next 24 hours'
    else:
        outlook = 'Conditions expected to remain similar'

    return {
        'forecast_hours':    forecast_hours,
        'temperature':       forecast_temps,
        'humidity':          forecast_humids,
        'pressure':          forecast_pressures,
        'rain_probability':  forecast_rain_probs,
        'pressure_signal':   pres_sig,
        'dew_signal':        dd_sig,
        'now_summary':       outlook,
        'confidence':        confidence,
        'data_points_used':  n,
        'trend_temp':        round(temp_hw['trend'], 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.  MAIN FORECAST BUILDER  for DEVICE (uses WeatherReading rows)
# ─────────────────────────────────────────────────────────────────────────────

def build_device_forecast(reading_rows, steps=12):
    """
    Per-device forecast from raw WeatherReading rows.
    Uses higher alpha (more reactive) since individual sensors are noisier.

    reading_rows: list of dicts with keys:
        timestamp, temperature, humidity, pressure,
        rain_value, is_raining, heat_index, dew_point
    steps: forecast horizon (default 12 hours)
    """
    if not reading_rows:
        return None

    rows = sorted(reading_rows, key=lambda r: r.get('timestamp') or '')

    # Downsample to hourly means to reduce noise before Holt-Winters
    hourly_buckets = defaultdict(list)
    for r in rows:
        ts = r.get('timestamp')
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_tz.utc)
            bucket = ts.replace(minute=0, second=0, microsecond=0)
            hourly_buckets[bucket].append(r)

    def bucket_avg(bucket_rows, field):
        vals = [r[field] for r in bucket_rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    hourly = []
    for dt in sorted(hourly_buckets):
        brows = hourly_buckets[dt]
        hourly.append({
            'hour':        dt.isoformat(),
            'temperature': bucket_avg(brows, 'temperature'),
            'humidity':    bucket_avg(brows, 'humidity'),
            'pressure':    bucket_avg(brows, 'pressure'),
            'is_raining':  any(r.get('is_raining') for r in brows),
            'rain_pct':    100 * sum(1 for r in brows if r.get('is_raining')) / len(brows),
            'dew_point':   bucket_avg(brows, 'dew_point'),
            'heat_index':  bucket_avg(brows, 'heat_index'),
        })

    if len(hourly) < 3:
        return None

    # Use same pipeline as district but with device-tuned alphas
    temps     = [r['temperature'] for r in hourly]
    humids    = [r['humidity']    for r in hourly]
    pressures = [r['pressure']    for r in hourly]
    rains     = [r['rain_pct']    for r in hourly]

    temp_hw  = holt_winters_forecast(temps,     steps=steps, alpha=0.35, beta=0.10)
    hum_hw   = holt_winters_forecast(humids,    steps=steps, alpha=0.3,  beta=0.06)
    pres_hw  = holt_winters_forecast(pressures, steps=steps, alpha=0.25, beta=0.05)
    rain_hw  = holt_winters_forecast(rains,     steps=steps, alpha=0.45, beta=0.12)

    if not temp_hw:
        return None

    pres_sig = pressure_tendency(hourly, lookback_hours=3)

    latest    = hourly[-1]
    lt        = latest.get('temperature')
    ld        = latest.get('dew_point')
    lh        = latest.get('humidity')
    dd_sig    = dew_point_depression_signal(lt, ld, lh)

    last_dt_raw = latest.get('hour')
    if isinstance(last_dt_raw, str):
        last_dt = datetime.fromisoformat(last_dt_raw)
    else:
        last_dt = last_dt_raw or datetime.now(_tz.utc)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=_tz.utc)

    forecast_hours      = []
    forecast_temps      = []
    forecast_humids     = []
    forecast_pressures  = []
    forecast_rain_probs = []
    forecast_heat_index = []

    for i, (ft, fh, fp, fr) in enumerate(zip(
        temp_hw['forecast'],
        hum_hw['forecast']  if hum_hw  else [None]*steps,
        pres_hw['forecast'] if pres_hw else [None]*steps,
        rain_hw['forecast'] if rain_hw else [None]*steps,
    )):
        future_dt = last_dt + timedelta(hours=i + 1)
        forecast_hours.append(future_dt.isoformat())

        corrected_temp = diurnal_correction(ft, future_dt.hour)
        forecast_temps.append(corrected_temp)

        clamped_hum = round(min(100.0, max(0.0, fh)), 1) if fh else None
        forecast_humids.append(clamped_hum)
        forecast_pressures.append(round(fp, 1) if fp else None)

        # Approximate heat index for forecast (Steadman formula)
        if corrected_temp and clamped_hum:
            hi = _heat_index(corrected_temp, clamped_hum)
            forecast_heat_index.append(hi)
        else:
            forecast_heat_index.append(None)

        rain_prob = compute_rain_probability(
            rain_pct_stations=max(0, fr or 0),
            pressure_mod=pres_sig['rain_mod'],
            dewpoint_mod=dd_sig['rain_mod'],
        )
        forecast_rain_probs.append(rain_prob)

    # Confidence
    n = len([t for t in temps if t is not None])
    confidence = 'high' if n >= 12 else ('medium' if n >= 5 else 'low')

    next_6h = [p for p in forecast_rain_probs[:6] if p is not None]
    avg_6h  = sum(next_6h) / len(next_6h) if next_6h else 0

    if avg_6h >= 0.6:
        outlook = 'Rain expected in the next 6 hours'
    elif avg_6h >= 0.35:
        outlook = 'Showers possible in the next 6 hours'
    elif pres_sig['delta'] and pres_sig['delta'] < -2:
        outlook = 'Pressure falling — weather may deteriorate'
    else:
        outlook = 'No significant change expected'

    return {
        'forecast_hours':    forecast_hours,
        'temperature':       forecast_temps,
        'humidity':          forecast_humids,
        'pressure':          forecast_pressures,
        'rain_probability':  forecast_rain_probs,
        'heat_index':        forecast_heat_index,
        'pressure_signal':   pres_sig,
        'dew_signal':        dd_sig,
        'now_summary':       outlook,
        'confidence':        confidence,
        'data_points_used':  n,
        'trend_temp':        round(temp_hw['trend'], 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _heat_index(temp_c, rh):
    """Rothfusz / NWS heat index. Input °C, output °C."""
    try:
        t = temp_c * 9/5 + 32  # to °F
        hi = (-42.379
              + 2.04901523  * t
              + 10.14333127 * rh
              - 0.22475541  * t * rh
              - 6.83783e-3  * t**2
              - 5.481717e-2 * rh**2
              + 1.22874e-3  * t**2 * rh
              + 8.5282e-4   * t * rh**2
              - 1.99e-6     * t**2 * rh**2)
        return round((hi - 32) * 5/9, 1)  # back to °C
    except Exception:
        return None


def rain_level_from_prob(prob):
    """Map probability [0,1] to display level string."""
    if prob >= 0.70:
        return 'heavy'
    elif prob >= 0.45:
        return 'moderate'
    elif prob >= 0.25:
        return 'light'
    else:
        return 'none'


def forecast_to_summary_cards(forecast_dict, tz_offset_hours=6):
    """
    Produce 6-hourly summary cards for template rendering.
    tz_offset_hours: Bangladesh is UTC+6
    """
    if not forecast_dict:
        return []

    cards = []
    hours = forecast_dict['forecast_hours']
    temps = forecast_dict['temperature']
    humids = forecast_dict['humidity']
    rains  = forecast_dict['rain_probability']

    # Bucket into 6-h windows
    windows = [(0, 6), (6, 12), (12, 18), (18, 24)]
    window_labels = ['Next 6h', '6–12h', '12–18h', '18–24h']

    for (start, end), label in zip(windows, window_labels):
        window_temps  = [temps[i]  for i in range(start, min(end, len(temps)))  if temps[i] is not None]
        window_humids = [humids[i] for i in range(start, min(end, len(humids))) if humids[i] is not None] if humids else []
        window_rains  = [rains[i]  for i in range(start, min(end, len(rains)))  if rains[i] is not None]

        if not window_temps:
            continue

        avg_temp  = round(sum(window_temps) / len(window_temps), 1)
        avg_humid = round(sum(window_humids) / len(window_humids), 0) if window_humids else None
        max_rain  = max(window_rains) if window_rains else 0
        avg_rain  = round(sum(window_rains) / len(window_rains), 2) if window_rains else 0

        level  = rain_level_from_prob(max_rain)
        icon   = {'heavy': '🌧', 'moderate': '🌦', 'light': '🌤', 'none': '☀️'}[level]

        cards.append({
            'label':    label,
            'icon':     icon,
            'temp':     avg_temp,
            'humidity': avg_humid,
            'rain_pct': round(avg_rain * 100),
            'rain_prob':max_rain,
            'level':    level,
        })

    return cards
