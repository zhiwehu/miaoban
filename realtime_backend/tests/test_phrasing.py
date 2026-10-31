import unittest
from voicehub import phrasing


class PhrasingTests(unittest.TestCase):
    def test_acknowledgement_is_varied_and_never_the_old_template(self):
        seen=[phrasing.acknowledge('weather') for _ in range(3)]
        self.assertEqual(len(set(seen)),3,seen)
        for line in seen:
            self.assertNotIn('收到，我来处理',line)
            self.assertTrue(line.endswith('。'),line)

    def test_unknown_tool_gets_a_natural_fallback(self):
        for _ in range(4):
            self.assertIn(phrasing.acknowledge('ext_demo__read_file'),phrasing._DEFAULT)

    def test_progress_names_the_subject_it_can_recognise(self):
        self.assertIn(phrasing.progress('上海今天天气怎么样'),phrasing._ACKS['weather'])
        self.assertIn(phrasing.progress('看看最新新闻'),phrasing._ACKS['news'])

    def test_announcement_never_uses_a_status_label(self):
        spoken=phrasing.announcement('上海今天多云，22 到 28 度。')
        self.assertIn('上海今天多云，22 到 28 度。',spoken)
        self.assertNotIn('后台任务结果',spoken)
        self.assertNotIn('后台处理完成',spoken)
        self.assertTrue(any(spoken.startswith(lead) for lead in phrasing._LEADS),spoken)

    def test_announcement_leads_rotate(self):
        leads={phrasing.announcement('结果。')[:3] for _ in range(5)}
        self.assertGreater(len(leads),1,leads)

    def test_long_result_is_capped_and_points_at_the_page(self):
        spoken=phrasing.announcement('长'*300)
        self.assertLessEqual(len(spoken),120+len(max(phrasing._LEADS,key=len))+len(max(phrasing._TAILS,key=len)))
        self.assertTrue(any(spoken.endswith(tail) for tail in phrasing._TAILS),spoken)

    def test_failures_are_spoken_as_written(self):
        text='后台任务未能完成，请在 MCP 页查看状态并检查工具配置。'
        self.assertEqual(phrasing.announcement(text,status='failed'),text)


if __name__=='__main__':
    unittest.main()
