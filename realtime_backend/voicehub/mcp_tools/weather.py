"""Weather tool: schema, acknowledgement and Open-Meteo implementation."""
import asyncio
import math
import aiohttp
from .shared import fetch, fetched_at

SCHEMA = ('查询指定城市未来三天的天气；城市不明确时先询问，不猜用户位置。',
 {'city': {'type': 'string', 'minLength': 1, 'maxLength': 80},
  'day_offset': {'type': 'integer',
                 'minimum': 0,
                 'maximum': 2,
                 'description': '要查询的日期：0今天，1明天，2后天。省略时返回三天概况。'}})
REQUIRED = ['city']
ACKNOWLEDGEMENT = '我查一下天气，请稍等。'


WEATHER_BUDGET=16
ERROR_MESSAGES={
    'weather_geocoding_timeout':'城市定位服务连接超时，请稍后重试。',
    'weather_forecast_timeout':'天气预报服务连接超时，请稍后重试。',
    'weather_geocoding_connection_failed':'暂时无法连接城市定位服务，请稍后重试。',
    'weather_forecast_connection_failed':'暂时无法连接天气预报服务，请稍后重试。',
    'weather_geocoding_unavailable':'城市定位服务暂时不可用，请稍后重试。',
    'weather_forecast_unavailable':'天气预报服务暂时不可用，请稍后重试。',
    'weather_certificate_error':'天气服务的安全连接验证失败，请检查后端证书和网络。',
    'weather_rate_limited':'天气服务请求过于频繁，请稍后再查。',
    'weather_service_error':'天气服务返回异常，本次未取得天气数据。',
    'weather_invalid_data':'天气服务返回的数据格式异常，本次无法确认天气。',
    'weather_timeout':'天气查询在限定时间内未完成，请稍后重试。',
}


async def weather_fetch(http,url,*,params,stage):
    """Retry only transient failures of fixed, read-only weather GETs, once."""
    for attempt in range(2):
        try:
            return await fetch(http,url,params=params,json_response=True,
                timeout=aiohttp.ClientTimeout(total=6,connect=3,sock_read=4))
        except aiohttp.ClientSSLError:
            raise ValueError('weather_certificate_error') from None
        except (TimeoutError,aiohttp.ClientConnectionError,aiohttp.ClientPayloadError) as exc:
            code='weather_'+stage+('_timeout' if isinstance(exc,TimeoutError) else '_connection_failed')
            if attempt:raise ValueError(code) from None
        except ValueError as exc:
            code=str(exc)
            if code=='upstream_http_429':raise ValueError('weather_rate_limited') from None
            if code not in ('upstream_http_502','upstream_http_503','upstream_http_504'):
                raise ValueError('weather_service_error' if code.startswith('upstream_http_') else 'weather_invalid_data') from None
            if attempt:raise ValueError('weather_'+stage+'_unavailable') from None
        except aiohttp.ClientError:
            raise ValueError('weather_service_error') from None
        await asyncio.sleep(.15)


def conditions(code):
    if code == 0: return '晴'
    if code in (1, 2): return '少云到多云'
    if code == 3: return '阴'
    if code in (45, 48): return '雾'
    if code in (51, 53, 55): return '毛毛雨'
    if code in (56, 57): return '冻毛毛雨'
    if code in (61, 63, 65): return '雨'
    if code in (66, 67): return '冻雨'
    if code in (71, 73, 75, 77): return '雪'
    if code in (80, 81, 82): return '阵雨'
    if code in (85, 86): return '阵雪'
    if code in (95, 96, 99): return '雷暴'
    return '未知天气'



async def _execute(http, city, day_offset=None):
    locations = await weather_fetch(http, 'https://geocoding-api.open-meteo.com/v1/search',
        params={'name':city, 'count':3, 'language':'zh', 'format':'json'}, stage='geocoding')
    choices = locations.get('results') or []
    if not choices:
        return {'status':'city_not_found', 'city':city, 'message':'未找到城市，请补充省份或英文名。'}
    location = choices[0]
    latitude, longitude = float(location['latitude']), float(location['longitude'])
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('invalid_location')
    report = await weather_fetch(http, 'https://api.open-meteo.com/v1/forecast', params={
        'latitude':latitude, 'longitude':longitude, 'timezone':'auto', 'forecast_days':3,
        'current':'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
        'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'},
        stage='forecast')
    daily = report['daily']
    days = [{'date':date, **{key:daily[key][i] for key in
            ('weather_code','temperature_2m_max','temperature_2m_min','precipitation_probability_max')},
            'conditions':conditions(daily['weather_code'][i])}
            for i, date in enumerate(daily['time'][:3])]
    result = {'source':'Open-Meteo', 'fetched_at':fetched_at(),
        'location':{k:location.get(k) for k in ('name','admin1','country')},
        'timezone':report.get('timezone'),
        'forecast_units':{'temperature':'°C','precipitation_probability':'%'},
        'location_candidates':[str(c.get('name',''))+', '+str(c.get('admin1',''))+', '+str(c.get('country',''))
                               for c in choices]}
    if day_offset is None:
        result.update(current=report['current'], units=report.get('current_units', {}), daily=days)
    else:
        result.update(requested_day=('今天','明天','后天')[day_offset], forecast=days[day_offset])
        if day_offset == 0:
            result.update(current=report['current'], units=report.get('current_units', {}))
    return result



async def execute(http, city, day_offset=None):
    try:
        async with asyncio.timeout(WEATHER_BUDGET):
            return await _execute(http,city,day_offset)
    except TimeoutError:
        raise ValueError('weather_timeout') from None
