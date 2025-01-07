from datetime import timedelta
import logging

import math

from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval, async_track_point_in_utc_time
from homeassistant.util import dt

from .const import AQI_BREAKPOINTS, DISPATCHER_PURPLE_AIR, PARTICLE_PROPS, LOCAL_SCAN_INTERVAL, LOCAL_URL_FORMAT, \
    TEMP_ADJUSTMENT, HUMIDITY_ADJUSTMENT

_LOGGER = logging.getLogger(__name__)


def calc_aqi(value, index):
    if index not in AQI_BREAKPOINTS:
        _LOGGER.debug('calc_aqi requested for unknown type: %s', index)
        return None

    bp = next((bp for bp in AQI_BREAKPOINTS[index] if bp['pm_low'] <= value <= bp['pm_high']), None)
    if not bp:
        _LOGGER.debug('value %s did not fall in valid range for type %s', value, index)
        return None

    aqi_range = bp['aqi_high'] - bp['aqi_low']
    pm_range = bp['pm_high'] - bp['pm_low']
    c = value - bp['pm_low']
    return round((aqi_range/pm_range) * c + bp['aqi_low'])


# LRAPA conversion using the same formula as used by PurpleAir's map as of 2020-09-06
def lrapa(value):
    return max(0, 0.5 * value - 0.66)


def calc_dewpoint(temp_f, humidity):
    """
    Calculate dewpoint using August-Roche-Magnus approximation.
    Note: Rounding twice for both F>C and C>F conversions simply because the purpleair website does this (so I
    do this too, to match the purpleair website).
    """
    temp_c = round((temp_f - 32) * 5.0/9.0)

    numerator = 243.04 * (math.log(humidity / 100) + ((17.625 * temp_c) / (243.04 + temp_c)))
    denominator = 17.625 - math.log(humidity / 100) - ((17.625 * temp_c) / (243.04 + temp_c))
    dew_point_c = numerator / denominator

    dew_point_f = round((dew_point_c * 9/5) + 32)
    return dew_point_f


def process_heat_adjustments(json_result):
    """Since the purple air devices are affected by heat from itself, modify readings to account for difference"""
    new_temp = json_result['current_temp_f'] + TEMP_ADJUSTMENT
    new_humid = min(100, json_result['current_humidity'] + HUMIDITY_ADJUSTMENT)

    if 'current_temp_f_680' in json_result:
        new_temp = round((new_temp + json_result['current_temp_f_680'] + TEMP_ADJUSTMENT) / 2)
    if 'current_humidity_680' in json_result:
        new_humid = round((new_humid + min(100, json_result['current_humidity_680'] + HUMIDITY_ADJUSTMENT)) / 2)

    dewpoint = calc_dewpoint(new_temp, new_humid)

    temp_in_c = round((new_temp - 32) * 5.0 / 9.0)
    dewpoint_in_c = round((dewpoint - 32) * 5.0 / 9.0)

    return {
        'current_temp': temp_in_c,
        'current_temp_in_f': new_temp,
        'current_humidity': new_humid,
        'current_dewpoint': dewpoint_in_c,
        'current_dewpoint_in_f': dewpoint
    }


def process_pm_readings(json_result, is_dual = False):
    """Processes Particle mass readings and confidence of said readings"""
    readings = {'pm2_5_aqi_raw': json_result['pm2.5_aqi']}
    if is_dual:
        readings['pm2_5_aqi_b_raw'] = json_result['pm2.5_aqi_b']

    for prop in PARTICLE_PROPS:
        if prop not in json_result:
            readings[prop] = None
            continue

        a = float(json_result[prop])
        prop_b = prop + '_b'  # Property name for sensor B
        if is_dual and prop_b in json_result:
            (value, confidence) = process_dual_sensor_readings(a, float(json_result[prop_b]))
        else:
            value = a
            confidence = 'Good'

        readings[prop] = value
        readings[f'{prop}_conf'] = confidence

    readings['aqi_epa'] = calc_aqi(readings['pm2_5_atm'], 'pm2_5')
    readings['aqi_lrapa'] = calc_aqi(lrapa(readings['pm2_5_atm']), 'pm2_5')
    return readings

def process_dual_sensor_readings(a, b):
    value = round((a + b) / 2, 1)

    if abs(a - b) < 45:
        confidence = 'Good'
    elif abs(a - b) > 300:
        # If the readings are so severly different, one is probably affected by a
        # physical obstuction (spider?), so just throw it away and use the smaller value.
        confidence = 'Severe'
        value = min(a, b)
    else:
        confidence = 'Questionable'

    return (value, confidence)


class PurpleAirApi:
    def __init__(self, hass, session):
        self._hass = hass
        self._session = session
        self._nodes = {}
        self._data = {}
        self._scan_interval = timedelta(seconds=LOCAL_SCAN_INTERVAL)
        self._shutdown_interval = None

    def is_node_registered(self, pa_sensor_id):
        return pa_sensor_id in self._data

    def get_reading(self, pa_sensor_id, prop):
        if pa_sensor_id not in self._data:
            return None

        node = self._data[pa_sensor_id]
        return node[prop] if prop in node else None

    def register_node(self, pa_sensor_id, ip_address):
        if pa_sensor_id in self._nodes:
            _LOGGER.debug('detected duplicate registration: %s', pa_sensor_id)
            return

        self._nodes[pa_sensor_id] = { 'ip_address': ip_address }
        _LOGGER.debug('registered new node: %s', pa_sensor_id)

        if not self._shutdown_interval:
            _LOGGER.debug('starting background poll: %s', self._scan_interval)
            self._shutdown_interval = async_track_time_interval(
                self._hass,
                self._update,
                self._scan_interval
            )

            async_track_point_in_utc_time(
                self._hass,
                self._update,
                dt.utcnow() + timedelta(seconds=5)
            )

    def unregister_node(self, pa_sensor_id):
        if pa_sensor_id not in self._nodes:
            _LOGGER.debug('detected non-existent unregistration: %s', pa_sensor_id)
            return

        del self._nodes[pa_sensor_id]
        _LOGGER.debug('unregistered node: %s', pa_sensor_id)

        if not self._nodes and self._shutdown_interval:
            _LOGGER.debug('no more nodes, shutting down interval')
            self._shutdown_interval()
            self._shutdown_interval = None


    async def _fetch_data(self, local_node_ips):
        if not local_node_ips:
            _LOGGER.debug('no nodes provided')
            return []

        urls = list(map(LOCAL_URL_FORMAT.format, local_node_ips))
        _LOGGER.debug('fetch url list: %s', urls)

        results = []
        for url in urls:
            _LOGGER.debug('fetching url: %s', url)

            try:
                async with self._session.get(url) as response:
                    if response.status != 200:
                        _LOGGER.error('bad API response for %s: %s', url, response.status)

                    json = await response.json()
                    results.append(json)
            except Exception:
                _LOGGER.error('Unable to connect to purple air device: ' + url)

        return results

    async def _update(self, now=None):
        local_node_ips = [n['ip_address'] for n in self._nodes.values()]
        _LOGGER.debug('Purple Air nodes: %s', local_node_ips)

        results = await self._fetch_data(local_node_ips)

        nodes = {}
        for result in results:
            pa_sensor_id = result['SensorId']
            is_dual = 'pm2.5_aqi_b' in result
            nodes[pa_sensor_id] = {
                'device_location': result['place'],
                'rssi': result['rssi'],
                'current_temp_raw': result['current_temp_f'],
                'current_humidity_raw': result['current_humidity'],
                'current_dewpoint_raw': result['current_dewpoint_f'],
                'pressure': round((result['pressure'] + result['pressure_680']) / 2, 2) if 'pressure_680' in result else result['pressure'],
                'voc': result['gas_680'] if 'gas_680' in result else result['gas'] if 'gas' in result else None,
                'is_dual': is_dual
            }
            nodes[pa_sensor_id].update(process_pm_readings(result, is_dual))
            nodes[pa_sensor_id].update(process_heat_adjustments(result))
            _LOGGER.debug('Json results for %s: %s', pa_sensor_id, result)
            _LOGGER.debug('Readings for %s: %s', pa_sensor_id, nodes[pa_sensor_id])

        self._data = nodes
        async_dispatcher_send(self._hass, DISPATCHER_PURPLE_AIR)
