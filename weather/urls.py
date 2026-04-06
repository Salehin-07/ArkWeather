from django.urls import path
from . import views

app_name = 'weather'

urlpatterns = [

    # ── Public ────────────────────────────────────────────────────────────────
    path('',
         views.home,
         name='home'),

    path('district/<int:district_id>/',
         views.district_detail,
         name='district_detail'),

    # ── Device owner (login required) ─────────────────────────────────────────
    path('my-devices/',
         views.my_devices,
         name='my_devices'),

    path('my-devices/register/',
         views.register_device,
         name='register_device'),

    path('my-devices/<uuid:device_uuid>/',
         views.device_detail,
         name='device_detail'),

    path('my-devices/<uuid:device_uuid>/edit/',
         views.edit_device,
         name='edit_device'),

    path('my-devices/<uuid:device_uuid>/delete/',
         views.delete_device,
         name='delete_device'),

    # ── BLE pairing (experimental) ─────────────────────────────────────────────
    path('my-devices/ble-pair/',
         views.ble_pair,
         name='ble_pair'),

    # ── ESP32 REST API ─────────────────────────────────────────────────────────
    # POST  /api/push/            ← ESP32 pushes a reading
    path('api/push/',
         views.api_push_reading,
         name='api_push_reading'),

    # GET   /api/device/<uuid>/latest/?api_key=<key>  ← live poll from owner page
    path('api/device/<uuid:device_uuid>/latest/',
         views.api_latest_reading,
         name='api_latest_reading'),

    # GET   /api/district/<id>/latest/  ← public aggregate snapshot
    path('api/district/<int:district_id>/latest/',
         views.api_district_latest,
         name='api_district_latest'),
]
