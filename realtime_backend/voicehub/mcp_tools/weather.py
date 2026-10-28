"""Weather tool: schema, acknowledgement and Open-Meteo implementation."""
import math
from .shared import fetch, fetched_at

SCHEMA = ('查询指定城市未来三天的天气；城市不明确时先询问，不猜用户位置。',
 {'city': {'type': 'string', 'minLength': 1, 'maxLength': 80},
  'day_offset': {'type': 'integer',
                 'minimum': 0,
                 'maximum': 2,
                 'description': '要查询的日期：0今天，1明天，2后天。省略时返回三天概况。'}})
REQUIRED = ['city']
ACKNOWLEDGEMENT = '我查一下天气，请稍等。'


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



async def execute(http, city, day_offset=None):
    locations = await fetch(http, 'https://geocoding-api.open-meteo.com/v1/search',
        params={'name':city, 'count':3, 'language':'zh', 'format':'json'}, json_response=True)
    choices = locations.get('results') or []
    if not choices:
        return {'status':'city_not_found', 'city':city, 'message':'未找到城市，请补充省份或英文名。'}
    location = choices[0]
    latitude, longitude = float(location['latitude']), float(location['longitude'])
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('invalid_location')
    report = await fetch(http, 'https://api.open-meteo.com/v1/forecast', params={
        'latitude':latitude, 'longitude':longitude, 'timezone':'auto', 'forecast_days':3,
        'current':'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m',
        'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'},
        json_response=True)
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
