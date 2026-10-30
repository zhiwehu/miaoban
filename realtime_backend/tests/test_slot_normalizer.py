import unittest
from voicehub.slot_normalizer import normalize_spans


def spans(text,items):
    return [{'type':kind,'span':value,'offset':[text.index(value),text.index(value)+len(value)]}
            for kind,value in items]


class SlotTests(unittest.TestCase):
    def test_weather_city_and_day_are_normalized(self):
        text='查上海明天天气'
        plan=normalize_spans('weather',text,spans(text,[('城市','上海'),('时间','明天')]),{})
        self.assertEqual(plan['arguments'],{'city':'上海','day_offset':1})

    def test_missing_city_and_out_of_range_date_ask_instead_of_guessing(self):
        for text,items in [('明天天气',[('时间','明天')]),('上海下周天气',[('城市','上海'),('时间','下周')])]:
            self.assertEqual(normalize_spans('weather',text,spans(text,items),{})['status'],'clarify')

    def test_news_source_count_and_keyword(self):
        text='从中新网读两条人工智能新闻'
        result=normalize_spans('news',text,spans(text,[('新闻来源','中新网'),('条数','两'),('新闻关键词','人工智能')]),{})
        self.assertEqual(result['arguments'],{'source':'chinanews','limit':2,'keyword':'人工智能'})

    def test_ten_articles_not_silently_clamped(self):
        text='读十条新闻'
        self.assertEqual(normalize_spans('news',text,spans(text,[('条数','十')]),{})['status'],'clarify')

    def test_system_sections_and_false_spans(self):
        text='查CPU和内存'
        self.assertEqual(normalize_spans('system_info',text,spans(text,[('查询项','CPU'),('查询项','内存')]),{})['arguments'],
                         {'sections':['cpu','memory']})
        with self.assertRaises(ValueError):
            normalize_spans('weather',text,[{'type':'城市','span':'上海','offset':[0,2]}],{})

    def test_missing_city_keeps_day_for_followup(self):
        first='明天天气'
        pending=normalize_spans('weather',first,spans(first,[('时间','明天')]),{})
        text='上海'
        result=normalize_spans('weather',text,spans(text,[('城市','上海')]),{},pending)
        self.assertEqual(result['arguments'],{'city':'上海','day_offset':1})

    def test_explicit_quantity_cannot_be_ignored_if_model_missed_it(self):
        self.assertEqual(normalize_spans('news','来十条新闻',[],{})['status'],'clarify')
