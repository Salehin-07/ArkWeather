"""
python manage.py aggregate_weather

Run this every hour via cron or Celery beat.
Computes DistrictAggregate for the last complete hour.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Avg, Min, Max, Count, Q
from datetime import timedelta

from weather.models import District, WeatherReading, DistrictAggregate


class Command(BaseCommand):
    help = 'Aggregate weather readings per district for the last hour'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours-back', type=int, default=1,
            help='How many past hours to (re-)aggregate. Default: 1'
        )

    def handle(self, *args, **options):
        now   = timezone.now().replace(minute=0, second=0, microsecond=0)
        hours = options['hours_back']

        for h in range(hours, 0, -1):
            hour_start = now - timedelta(hours=h)
            hour_end   = hour_start + timedelta(hours=1)
            self._aggregate_hour(hour_start, hour_end)

        self.stdout.write(self.style.SUCCESS(f'Aggregated {hours} hour(s) successfully.'))

    def _aggregate_hour(self, hour_start, hour_end):
        for district in District.objects.all():
            readings = WeatherReading.objects.filter(
                device__district=district,
                timestamp__gte=hour_start,
                timestamp__lt=hour_end,
            )

            count = readings.count()
            if count == 0:
                continue

            agg_vals = readings.aggregate(
                avg_temp  = Avg('temperature'),
                min_temp  = Min('temperature'),
                max_temp  = Max('temperature'),
                avg_hum   = Avg('humidity'),
                avg_pres  = Avg('pressure'),
                avg_light = Avg('light_value'),
                avg_hi    = Avg('heat_index'),
            )

            # Rain percentage: fraction of readings where is_raining=True
            raining_count = readings.filter(is_raining=True).count()
            total_rain_readings = readings.filter(is_raining__isnull=False).count()
            rain_pct = (raining_count / total_rain_readings * 100) if total_rain_readings else None

            device_count = readings.values('device').distinct().count()

            DistrictAggregate.objects.update_or_create(
                district=district,
                hour=hour_start,
                defaults={
                    'device_count':    device_count,
                    'reading_count':   count,
                    'avg_temperature': round(agg_vals['avg_temp'],  2) if agg_vals['avg_temp']  is not None else None,
                    'min_temperature': round(agg_vals['min_temp'],  2) if agg_vals['min_temp']  is not None else None,
                    'max_temperature': round(agg_vals['max_temp'],  2) if agg_vals['max_temp']  is not None else None,
                    'avg_humidity':    round(agg_vals['avg_hum'],   2) if agg_vals['avg_hum']   is not None else None,
                    'avg_pressure':    round(agg_vals['avg_pres'],  2) if agg_vals['avg_pres']  is not None else None,
                    'avg_light':       round(agg_vals['avg_light'], 2) if agg_vals['avg_light'] is not None else None,
                    'rain_percentage': round(rain_pct,              2) if rain_pct              is not None else None,
                    'avg_heat_index':  round(agg_vals['avg_hi'],    2) if agg_vals['avg_hi']    is not None else None,
                }
            )

            self.stdout.write(
                f'  {district.name} @ {hour_start:%H:00} — '
                f'{count} readings, {device_count} devices'
            )
