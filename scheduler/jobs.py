"""
ArkWeather background scheduler.
Uses django-apscheduler — lightweight, no broker required.

Install:  pip install django-apscheduler
"""

import logging
from django.conf import settings

logger = logging.getLogger(__name__)

_scheduler = None   # module-level singleton so start() is idempotent


def aggregate_weather_job():
    """Runs every 15 minutes — aggregates WeatherReadings into DistrictAggregates."""
    from django.utils import timezone
    from datetime import timedelta
    from weather.models import District, WeatherReading, DistrictAggregate
    from django.db.models import Avg, Min, Max

    now        = timezone.now().replace(minute=0, second=0, microsecond=0)
    hour_start = now - timedelta(hours=1)
    hour_end   = now

    logger.info(f'[ArkWeather] Aggregating {hour_start:%H:%M} -> {hour_end:%H:%M}')

    for district in District.objects.all():
        readings = WeatherReading.objects.filter(
            device__district=district,
            timestamp__gte=hour_start,
            timestamp__lt=hour_end,
        )
        count = readings.count()
        if count == 0:
            continue

        agg = readings.aggregate(
            avg_temp  = Avg('temperature'),
            min_temp  = Min('temperature'),
            max_temp  = Max('temperature'),
            avg_hum   = Avg('humidity'),
            avg_pres  = Avg('pressure'),
            avg_light = Avg('light_value'),
            avg_hi    = Avg('heat_index'),
        )

        raining_count       = readings.filter(is_raining=True).count()
        total_rain_readings = readings.filter(is_raining__isnull=False).count()
        rain_pct     = (raining_count / total_rain_readings * 100) if total_rain_readings else None
        device_count = readings.values('device').distinct().count()

        def r(v): return round(v, 2) if v is not None else None

        DistrictAggregate.objects.update_or_create(
            district=district,
            hour=hour_start,
            defaults={
                'device_count':    device_count,
                'reading_count':   count,
                'avg_temperature': r(agg['avg_temp']),
                'min_temperature': r(agg['min_temp']),
                'max_temperature': r(agg['max_temp']),
                'avg_humidity':    r(agg['avg_hum']),
                'avg_pressure':    r(agg['avg_pres']),
                'avg_light':       r(agg['avg_light']),
                'rain_percentage': r(rain_pct),
                'avg_heat_index':  r(agg['avg_hi']),
            }
        )
        logger.info(f'  ok {district.name}: {count} readings, {device_count} devices')


def delete_old_job_executions(max_age_seconds=604_800):
    """Prune APScheduler execution log older than 7 days."""
    from django_apscheduler.models import DjangoJobExecution
    DjangoJobExecution.objects.delete_old_job_executions(max_age_seconds)


def _tables_exist():
    """Return True only if the apscheduler DB tables are present."""
    from django.db import connection
    existing = connection.introspection.table_names()
    return 'django_apscheduler_djangojob' in existing


def start():
    global _scheduler
    if _scheduler is not None:
        return  # already running

    # Guard: skip if migrations have not been run yet.
    # This prevents the crash during `makemigrations` / `migrate` / any
    # management command that boots Django before the tables exist.
    if not _tables_exist():
        logger.warning(
            '[ArkWeather] Scheduler skipped — django_apscheduler tables not found. '
            'Run `python manage.py migrate` then restart the server.'
        )
        return

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from django_apscheduler.jobstores import DjangoJobStore

    _scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
    _scheduler.add_jobstore(DjangoJobStore(), 'default')

    _scheduler.add_job(
        aggregate_weather_job,
        trigger=IntervalTrigger(minutes=15),
        id='aggregate_weather',
        name='Aggregate district weather readings',
        jobstore='default',
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.add_job(
        delete_old_job_executions,
        trigger=IntervalTrigger(weeks=1),
        id='delete_old_job_executions',
        name='Prune APScheduler execution log',
        jobstore='default',
        replace_existing=True,
        misfire_grace_time=3600,
    )

    try:
        logger.info('[ArkWeather] Starting background scheduler...')
        _scheduler.start()
        logger.info('[ArkWeather] Scheduler running — aggregate_weather every 15 min.')
    except Exception as e:
        logger.error(f'[ArkWeather] Scheduler failed to start: {e}')
        _scheduler = None
