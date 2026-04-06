from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid


class District(models.Model):
    name = models.CharField(max_length=100)
    division = models.CharField(max_length=100, blank=True)  # e.g. Dhaka, Chittagong
    latitude = models.FloatField()
    longitude = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class Device(models.Model):
    """
    Represents a physical ESP32 weather node.
    Owned by a user, assigned to a district.
    """
    STATUS_ONLINE  = 'online'
    STATUS_OFFLINE = 'offline'
    STATUS_CHOICES = [
        (STATUS_ONLINE,  'Online'),
        (STATUS_OFFLINE, 'Offline'),
    ]

    device_id   = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    api_key     = models.UUIDField(default=uuid.uuid4, unique=True)   # used by ESP32 to POST data
    name        = models.CharField(max_length=100)
    owner       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='devices')
    district    = models.ForeignKey(District, on_delete=models.SET_NULL, null=True, related_name='devices')

    # Physical location of this node (more precise than district centre)
    latitude    = models.FloatField(null=True, blank=True)
    longitude   = models.FloatField(null=True, blank=True)
    location_note = models.CharField(max_length=200, blank=True)   # e.g. "Rooftop, North Tangail"

    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OFFLINE)
    last_seen   = models.DateTimeField(null=True, blank=True)
    firmware_version = models.CharField(max_length=20, blank=True)

    registered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.owner.username})"

    def mark_online(self):
        self.status    = self.STATUS_ONLINE
        self.last_seen = timezone.now()
        self.save(update_fields=['status', 'last_seen'])

    class Meta:
        ordering = ['-last_seen']


class WeatherReading(models.Model):
    """
    A single reading pushed by an ESP32 node.
    All sensor fields are nullable — partial readings are accepted.
    """
    device      = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='readings')
    timestamp   = models.DateTimeField(default=timezone.now, db_index=True)

    # DHT22
    temperature = models.FloatField(null=True, blank=True)   # °C
    humidity    = models.FloatField(null=True, blank=True)   # %

    # BMP280
    pressure    = models.FloatField(null=True, blank=True)   # hPa

    # Rain sensor (0–1023 analogue value; higher = more rain)
    rain_value  = models.IntegerField(null=True, blank=True)
    is_raining  = models.BooleanField(null=True, blank=True)

    # LDR (0–1023; lower = darker / more cloud cover)
    light_value = models.IntegerField(null=True, blank=True)

    # Derived / computed on save
    heat_index  = models.FloatField(null=True, blank=True)   # °C (feels-like)
    dew_point   = models.FloatField(null=True, blank=True)   # °C

    class Meta:
        ordering = ['-timestamp']
        indexes  = [
            models.Index(fields=['device', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.device.name} @ {self.timestamp:%Y-%m-%d %H:%M}"

    # ------------------------------------------------------------------ #
    #  Derived sensor calculations                                         #
    # ------------------------------------------------------------------ #
    def save(self, *args, **kwargs):
        if self.temperature is not None and self.humidity is not None:
            self.heat_index = self._calc_heat_index(self.temperature, self.humidity)
            self.dew_point  = self._calc_dew_point(self.temperature, self.humidity)
        super().save(*args, **kwargs)

    @staticmethod
    def _calc_heat_index(T, RH):
        """Rothfusz regression (°C)."""
        import math
        if T < 27:
            return T
        hi = (-8.78469475556
              + 1.61139411    * T
              + 2.33854883889 * RH
              - 0.14611605    * T * RH
              - 0.012308094   * T**2
              - 0.0164248277778 * RH**2
              + 0.002211732   * T**2 * RH
              + 0.00072546    * T * RH**2
              - 0.000003582   * T**2 * RH**2)
        return round(hi, 2)

    @staticmethod
    def _calc_dew_point(T, RH):
        """Magnus formula (°C)."""
        import math
        a, b = 17.27, 237.7
        gamma = (a * T / (b + T)) + math.log(RH / 100.0)
        dp = (b * gamma) / (a - gamma)
        return round(dp, 2)


class DistrictAggregate(models.Model):
    """
    Hourly pre-computed averages per district.
    Populated by a management command / Celery task.
    Drives the public dashboard charts without hitting raw readings.
    """
    district    = models.ForeignKey(District, on_delete=models.CASCADE, related_name='aggregates')
    hour        = models.DateTimeField(db_index=True)   # truncated to the hour

    device_count     = models.IntegerField(default=0)
    reading_count    = models.IntegerField(default=0)

    avg_temperature  = models.FloatField(null=True, blank=True)
    min_temperature  = models.FloatField(null=True, blank=True)
    max_temperature  = models.FloatField(null=True, blank=True)

    avg_humidity     = models.FloatField(null=True, blank=True)
    avg_pressure     = models.FloatField(null=True, blank=True)
    avg_light        = models.FloatField(null=True, blank=True)

    rain_percentage  = models.FloatField(null=True, blank=True)  # % of devices reporting rain
    avg_heat_index   = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ('district', 'hour')
        ordering = ['-hour']

    def __str__(self):
        return f"{self.district.name} — {self.hour:%Y-%m-%d %H:00}"
