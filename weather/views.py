from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Avg, Min, Max, Count, Q
from django.contrib import messages
from datetime import timedelta
import json
import uuid

from .models import Device, WeatherReading, District, DistrictAggregate
from .forms import DeviceRegisterForm, DeviceEditForm


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC VIEWS
# ══════════════════════════════════════════════════════════════════════════════

def home(request):
    """
    Landing page: list all districts with their latest aggregate snapshot.
    """
    districts = District.objects.all()

    district_data = []
    for d in districts:
        latest_agg = d.aggregates.first()   # ordered by -hour
        device_count = d.devices.filter(status='online').count()
        district_data.append({
            'district': d,
            'latest':   latest_agg,
            'online_devices': device_count,
        })

    context = {
        'district_data': district_data,
        'total_online':  Device.objects.filter(status='online').count(),
        'total_devices': Device.objects.count(),
    }
    return render(request, 'weather/home.html', context)


def district_detail(request, district_id):
    """
    Public district dashboard:
    - Latest averaged readings
    - 24-hour chart data
    - 7-day trend
    - Rain forecast signal (pressure drop + rain %)
    - Active device map markers
    """
    district = get_object_or_404(District, pk=district_id)
    now = timezone.now()

    # ── Latest snapshot (most recent aggregate hour) ─────────────────────────
    latest = district.aggregates.first()

    # ── 24-hour chart data ────────────────────────────────────────────────────
    since_24h = now - timedelta(hours=24)
    hourly_24h = list(
        district.aggregates
        .filter(hour__gte=since_24h)
        .order_by('hour')
        .values('hour', 'avg_temperature', 'avg_humidity',
                'avg_pressure', 'rain_percentage', 'avg_heat_index',
                'avg_light', 'device_count')
    )

    # ── 7-day daily trend ─────────────────────────────────────────────────────
    since_7d = now - timedelta(days=7)
    daily_7d_raw = (
        district.aggregates
        .filter(hour__gte=since_7d)
        .order_by('hour')
        .values('hour', 'avg_temperature', 'avg_humidity',
                'avg_pressure', 'rain_percentage')
    )

    # Group by date
    from collections import defaultdict
    daily_buckets = defaultdict(list)
    for row in daily_7d_raw:
        day_key = row['hour'].date().isoformat()
        daily_buckets[day_key].append(row)

    daily_7d = []
    for day_key, rows in sorted(daily_buckets.items()):
        def safe_avg(field):
            vals = [r[field] for r in rows if r[field] is not None]
            return round(sum(vals) / len(vals), 2) if vals else None

        daily_7d.append({
            'date': day_key,
            'avg_temperature': safe_avg('avg_temperature'),
            'avg_humidity':    safe_avg('avg_humidity'),
            'avg_pressure':    safe_avg('avg_pressure'),
            'rain_percentage': safe_avg('rain_percentage'),
        })

    # ── Active devices (for map) ───────────────────────────────────────────────
    active_devices = (
        district.devices
        .filter(status='online', latitude__isnull=False, longitude__isnull=False)
        .values('name', 'latitude', 'longitude', 'last_seen', 'location_note')
    )

    # ── Rain forecast signal ───────────────────────────────────────────────────
    rain_alert = _compute_rain_signal(hourly_24h)

    context = {
        'district':      district,
        'latest':        latest,
        'hourly_24h':    json.dumps(hourly_24h, default=str),
        'daily_7d':      json.dumps(daily_7d,   default=str),
        'active_devices': json.dumps(list(active_devices), default=str),
        'rain_alert':    rain_alert,
        'device_count':  district.devices.filter(status='online').count(),
    }
    return render(request, 'weather/district_detail.html', context)


def _compute_rain_signal(hourly_data):
    """
    Simple rain forecast heuristic:
    - Pressure dropped > 2 hPa over last 3 hours → possible rain
    - rain_percentage > 50% in last reading → active rain
    Returns a dict with level ('none','watch','warning') and message.
    """
    if len(hourly_data) < 2:
        return {'level': 'none', 'message': 'Insufficient data'}

    recent = hourly_data[-1]
    older  = hourly_data[max(0, len(hourly_data) - 4)]   # ~3h back

    pressure_drop = None
    if recent.get('avg_pressure') and older.get('avg_pressure'):
        pressure_drop = older['avg_pressure'] - recent['avg_pressure']

    rain_pct = recent.get('rain_percentage') or 0

    if rain_pct > 60:
        return {'level': 'warning', 'message': f'Active rain — {rain_pct:.0f}% of stations reporting rain'}
    if pressure_drop and pressure_drop > 3:
        return {'level': 'watch', 'message': f'Pressure falling ({pressure_drop:.1f} hPa drop) — rain possible'}
    if rain_pct > 25:
        return {'level': 'watch', 'message': f'Light rain in parts of the district ({rain_pct:.0f}% stations)'}

    return {'level': 'none', 'message': 'No rain expected'}


# ══════════════════════════════════════════════════════════════════════════════
#  DEVICE OWNER VIEWS  (login required)
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def my_devices(request):
    """Dashboard for device owners — lists all their nodes."""
    devices = request.user.devices.select_related('district').order_by('-last_seen')

    device_data = []
    for dev in devices:
        latest_reading = dev.readings.first()
        device_data.append({'device': dev, 'latest': latest_reading})

    context = {
        'device_data': device_data,
    }
    return render(request, 'weather/my_devices.html', context)


@login_required
def device_detail(request, device_uuid):
    """
    Per-device live dashboard for the owner.
    Shows real-time readings + history charts for that specific node.
    """
    device = get_object_or_404(Device, device_id=device_uuid, owner=request.user)

    now = timezone.now()
    since_24h = now - timedelta(hours=24)

    latest_reading = device.readings.first()

    readings_24h = list(
        device.readings
        .filter(timestamp__gte=since_24h)
        .order_by('timestamp')
        .values('timestamp', 'temperature', 'humidity', 'pressure',
                'rain_value', 'is_raining', 'light_value', 'heat_index', 'dew_point')
    )

    context = {
        'device':       device,
        'latest':       latest_reading,
        'readings_24h': json.dumps(readings_24h, default=str),
        'api_key':      str(device.api_key),
    }
    return render(request, 'weather/device_detail.html', context)


@login_required
def register_device(request):
    """Register a new ESP32 node."""
    if request.method == 'POST':
        form = DeviceRegisterForm(request.POST)
        if form.is_valid():
            device = form.save(commit=False)
            device.owner = request.user
            device.save()
            messages.success(
                request,
                f'Device "{device.name}" registered! '
                f'Your API key: {device.api_key}'
            )
            return redirect('weather:device_detail', device_uuid=device.device_id)
    else:
        form = DeviceRegisterForm()

    return render(request, 'weather/register_device.html', {'form': form})


@login_required
def edit_device(request, device_uuid):
    device = get_object_or_404(Device, device_id=device_uuid, owner=request.user)
    if request.method == 'POST':
        form = DeviceEditForm(request.POST, instance=device)
        if form.is_valid():
            form.save()
            messages.success(request, 'Device updated.')
            return redirect('weather:device_detail', device_uuid=device.device_id)
    else:
        form = DeviceEditForm(instance=device)
    return render(request, 'weather/edit_device.html', {'form': form, 'device': device})


@login_required
def delete_device(request, device_uuid):
    device = get_object_or_404(Device, device_id=device_uuid, owner=request.user)
    if request.method == 'POST':
        device.delete()
        messages.success(request, 'Device removed.')
        return redirect('weather:my_devices')
    return render(request, 'weather/confirm_delete.html', {'device': device})


# ══════════════════════════════════════════════════════════════════════════════
#  ESP32 API ENDPOINTS  (no CSRF — authenticated via api_key header)
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@require_POST
def api_push_reading(request):
    """
    ESP32 POSTs JSON here every N seconds.

    Expected JSON body:
    {
        "api_key": "<uuid>",
        "temperature": 30.5,
        "humidity": 72.1,
        "pressure": 1008.3,
        "rain_value": 320,
        "is_raining": true,
        "light_value": 800
    }

    Returns: {"status": "ok", "reading_id": <int>}
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    api_key = data.get('api_key')
    if not api_key:
        return JsonResponse({'error': 'api_key required'}, status=401)

    try:
        device = Device.objects.get(api_key=api_key)
    except Device.DoesNotExist:
        return JsonResponse({'error': 'Unknown device'}, status=401)

    reading = WeatherReading.objects.create(
        device      = device,
        temperature = data.get('temperature'),
        humidity    = data.get('humidity'),
        pressure    = data.get('pressure'),
        rain_value  = data.get('rain_value'),
        is_raining  = data.get('is_raining'),
        light_value = data.get('light_value'),
    )

    device.mark_online()

    return JsonResponse({'status': 'ok', 'reading_id': reading.pk}, status=201)


@require_GET
def api_latest_reading(request, device_uuid):
    """
    Polling endpoint for the device-owner live view.
    Returns the most recent reading for a device.
    Query param: ?api_key=<uuid>
    """
    api_key = request.GET.get('api_key')
    try:
        device = Device.objects.get(device_id=device_uuid, api_key=api_key)
    except Device.DoesNotExist:
        return JsonResponse({'error': 'Unauthorised'}, status=401)

    reading = device.readings.first()
    if not reading:
        return JsonResponse({'status': 'no_data'})

    return JsonResponse({
        'status':      'ok',
        'timestamp':   reading.timestamp.isoformat(),
        'temperature': reading.temperature,
        'humidity':    reading.humidity,
        'pressure':    reading.pressure,
        'rain_value':  reading.rain_value,
        'is_raining':  reading.is_raining,
        'light_value': reading.light_value,
        'heat_index':  reading.heat_index,
        'dew_point':   reading.dew_point,
        'device_status': device.status,
    })


@require_GET
def api_district_latest(request, district_id):
    """
    Public API — returns latest aggregate for a district.
    Used by external consumers and the BLE bridge if needed.
    """
    district = get_object_or_404(District, pk=district_id)
    agg = district.aggregates.first()
    if not agg:
        return JsonResponse({'status': 'no_data'})

    return JsonResponse({
        'status':          'ok',
        'district':        district.name,
        'hour':            agg.hour.isoformat(),
        'avg_temperature': agg.avg_temperature,
        'avg_humidity':    agg.avg_humidity,
        'avg_pressure':    agg.avg_pressure,
        'rain_percentage': agg.rain_percentage,
        'device_count':    agg.device_count,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  BLE PAIRING VIEW  (progressive enhancement — WiFi first, BLE experimental)
# ══════════════════════════════════════════════════════════════════════════════

@login_required
def ble_pair(request):
    """
    Browser-side BLE pairing page.
    Uses the Web Bluetooth API (Chrome/Edge only).
    The page JS scans for ESP32 advertising 'ArkWeather' service,
    reads the characteristic and displays live data.
    Optionally binds the BLE device to an existing registered device record.
    """
    devices = request.user.devices.all()
    return render(request, 'weather/ble_pair.html', {'devices': devices})
