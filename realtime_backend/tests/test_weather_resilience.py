import asyncio
import unittest
from unittest.mock import AsyncMock,patch
import aiohttp
from voicehub.mcp_tools import weather
from voicehub.common_tools import call_tool

GEO={'results':[{'name':'合肥','latitude':31.82,'longitude':117.22}]}
FORECAST={'current':{'temperature_2m':18},'timezone':'Asia/Shanghai','daily':{
 'time':['2026-10-29','2026-10-30','2026-10-31'],'weather_code':[3,53,3],
 'temperature_2m_max':[21,22,23],'temperature_2m_min':[14,15,16],'precipitation_probability_max':[20,70,10]}}

class WeatherResilienceTests(unittest.IsolatedAsyncioTestCase):
 async def test_geocoding_timeout_retries_once_and_still_returns_requested_day(self):
  fetch=AsyncMock(side_effect=[TimeoutError(),GEO,FORECAST])
  with patch.object(weather,'fetch',fetch),patch('asyncio.sleep',AsyncMock()):
   result=await weather.execute(None,'合肥',day_offset=1)
  self.assertEqual(fetch.await_count,3);self.assertEqual(result['forecast']['date'],'2026-10-30')
  self.assertEqual(result['forecast']['conditions'],'毛毛雨');self.assertNotIn('current',result)
 async def test_forecast_disconnect_retries_forecast_only(self):
  fetch=AsyncMock(side_effect=[GEO,aiohttp.ServerDisconnectedError(),FORECAST])
  with patch.object(weather,'fetch',fetch),patch('asyncio.sleep',AsyncMock()):result=await weather.execute(None,'合肥')
  self.assertEqual(fetch.await_count,3);self.assertEqual(result['source'],'Open-Meteo')
  self.assertEqual(fetch.call_args_list[1].args[1],fetch.call_args_list[2].args[1])
 async def test_permanent_errors_are_not_retried(self):
  for error,code in ((ValueError('upstream_http_400'),'weather_service_error'),(ValueError('upstream_http_429'),'weather_rate_limited'),(aiohttp.ClientSSLError(None,OSError('certificate')),'weather_certificate_error')):
   fetch=AsyncMock(side_effect=error)
   with self.subTest(code=code),patch.object(weather,'fetch',fetch):
    result=await call_tool(None,'weather',{'city':'合肥'})
   self.assertTrue(result['isError']);self.assertEqual(result['structuredContent']['error']['code'],code)
   self.assertEqual(fetch.await_count,1)
 async def test_two_timeouts_report_actual_stage(self):
  for responses,stage,count in (([TimeoutError(),TimeoutError()],'geocoding',2),([GEO,TimeoutError(),TimeoutError()],'forecast',3)):
   fetch=AsyncMock(side_effect=responses)
   with patch.object(weather,'fetch',fetch),patch('asyncio.sleep',AsyncMock()):result=await call_tool(None,'weather',{'city':'合肥'})
   self.assertEqual(result['structuredContent']['error']['code'],'weather_'+stage+'_timeout')
   self.assertEqual(fetch.await_count,count)
 async def test_temporary_http_failure_retries_once(self):
  fetch=AsyncMock(side_effect=[GEO,ValueError('upstream_http_503'),FORECAST])
  with patch.object(weather,'fetch',fetch),patch('asyncio.sleep',AsyncMock()):result=await weather.execute(None,'合肥')
  self.assertEqual(fetch.await_count,3);self.assertEqual(len(result['daily']),3)
 async def test_global_budget_covers_both_stages_and_retry(self):
  async def stuck(*args,**kwargs):await asyncio.Event().wait()
  with patch.object(weather,'fetch',stuck),patch.object(weather,'WEATHER_BUDGET',.01,create=True):
   result=await call_tool(None,'weather',{'city':'合肥'})
  self.assertEqual(result['structuredContent']['error']['code'],'weather_timeout')
 async def test_cancellation_is_not_retried_or_converted_to_weather_data(self):
  fetch=AsyncMock(side_effect=asyncio.CancelledError)
  with patch.object(weather,'fetch',fetch),self.assertRaises(asyncio.CancelledError):await weather.execute(None,'合肥')
  self.assertEqual(fetch.await_count,1)

 async def test_weather_failure_is_spoken_with_stage_and_never_summarized_as_success(self):
  import copy
  from types import SimpleNamespace
  from voicehub.config import DEFAULT
  from voicehub.tool_chat import chat
  from voicehub.tools import ToolNotice,ToolStatus
  cfg=copy.deepcopy(DEFAULT);cfg['tools']['planner']='rex'
  error={'content':[{'type':'text','text':'failed'}],'isError':True,
         'structuredContent':{'error':{'code':'weather_forecast_timeout'}}}
  class Client:
   def __init__(self,**kwargs):pass
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   async def list_tools(self):return [{'name':'weather'}]
   async def call(self,*args):return error
  planner=AsyncMock(return_value={'status':'ready','tool':'weather','arguments':{'city':'合肥'}})
  p=SimpleNamespace(config=cfg)
  with patch('voicehub.tool_chat.rex_plan',planner),patch('voicehub.tool_chat.CommonToolsClient',Client):
   result=[item async for item in chat(p,[{'role':'user','content':'合肥天气'}])]
  self.assertIsInstance(result[0],ToolNotice)
  failed=next(item for item in result if isinstance(item,ToolStatus) and item.phase=='failed')
  self.assertEqual(failed.code,'weather_forecast_timeout')
  self.assertEqual(result[-1],weather.ERROR_MESSAGES['weather_forecast_timeout'])
  self.assertFalse(any(isinstance(item,ToolStatus) and item.phase=='completed' for item in result))
