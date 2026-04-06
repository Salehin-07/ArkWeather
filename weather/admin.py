from django.contrib import admin
from .models import District, Device, WeatherReading, DistrictAggregate


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ['name', 'division', 'latitude', 'longitude']
    search_fields = ['name', 'division']


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display  = ['name', 'owner', 'district', 'status', 'last_seen', 'firmware_version']
    list_filter   = ['status', 'district']
    search_fields = ['name', 'owner__username']
    readonly_fields = ['device_id', 'api_key', 'registered_at']


@admin.register(WeatherReading)
class WeatherReadingAdmin(admin.ModelAdmin):
    list_display  = ['device', 'timestamp', 'temperature', 'humidity',
                     'pressure', 'is_raining', 'heat_index']
    list_filter   = ['device__district', 'is_raining']
    search_fields = ['device__name']
    date_hierarchy = 'timestamp'
    readonly_fields = ['heat_index', 'dew_point']


@admin.register(DistrictAggregate)
class DistrictAggregateAdmin(admin.ModelAdmin):
    list_display = ['district', 'hour', 'avg_temperature', 'avg_humidity',
                    'avg_pressure', 'rain_percentage', 'device_count']
    list_filter  = ['district']
    date_hierarchy = 'hour'
