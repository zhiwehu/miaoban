import unittest
from voicehub.client_verification import evidence


class VerificationTests(unittest.TestCase):
    def event(self,kind,turn,**payload):return dict(type=kind,turn_id=turn,payload=payload)
    def test_unrelated_turns_do_not_prove_a_complete_voice_turn(self):
        result=evidence([self.event('transcript.final','one'),self.event('reply.final','two')])
        self.assertFalse(result['recognized'] and result['replied'])
    def test_generated_audio_does_not_prove_playback(self):
        result=evidence([self.event('transcript.final','one'),self.event('reply.final','one')])
        self.assertTrue(result['recognized'] and result['replied'])
        self.assertFalse(result['playback_reported'])
    def test_playback_requires_increasing_counter_and_output_enabled(self):
        events=[self.event('transcript.final','one'),self.event('reply.final','one'),
            self.event('device.playback_state','one',state='ready',played_audio_packets=10,output_enabled=True),
            self.event('device.playback_state',None,reported_turn_id='one',state='stopped',played_audio_packets=12,output_enabled=True)]
        self.assertTrue(evidence(events)['playback_reported'])
        events[-1]['payload']['played_audio_packets']=10
        self.assertFalse(evidence(events)['playback_reported'])
