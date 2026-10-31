import numpy as np
import unittest

from voicehub.speaker import SpeakerTracker


class SpeakerTrackerTests(unittest.TestCase):
    def test_distinct_embeddings_create_separate_speakers(self):
        tracker = SpeakerTracker(threshold=.25, min_seconds=1, max_speakers=6)
        self.assertEqual(tracker.assign([1, 0, 0], 2), (1, '说话人1'))
        self.assertEqual(tracker.assign([0, 1, 0], 2), (2, '说话人2'))
        self.assertEqual(tracker.assign([.9, .1, 0], 2), (1, '说话人1'))
        self.assertEqual(tracker.count, 2)

    def test_short_segment_joins_best_speaker_instead_of_creating_one(self):
        tracker = SpeakerTracker(threshold=.25, min_seconds=1.2, max_speakers=6)
        tracker.assign([1, 0, 0], 2)
        tracker.assign([0, 1, 0], 2)
        self.assertEqual(tracker.assign([0, 0, 1], .5), (1, '说话人1'))
        self.assertEqual(tracker.count, 2)
        self.assertEqual(tracker.assign([0, 0, -1], 2), (3, '说话人3'))

    def test_refine_folds_single_segment_outliers(self):
        tracker = SpeakerTracker(threshold=.25, min_seconds=.5, max_speakers=6)
        tracker.assign([1, 0], 2)
        tracker.assign([.95, .05], 2)
        tracker.assign([0, 1], 2)
        mapping, count = tracker.refine()
        self.assertEqual(count, 1)
        self.assertEqual(mapping[1], 1)
        self.assertEqual(mapping[2], 1)

    def test_refine_keeps_two_multi_segment_speakers(self):
        tracker = SpeakerTracker(threshold=.25, min_seconds=.5, max_speakers=6)
        for vector in ([1, 0], [.95, .05], [0, 1], [.05, .95]):
            tracker.assign(vector, 2)
        mapping, count = tracker.refine()
        self.assertEqual(count, 2)
        self.assertEqual(mapping, {1: 1, 2: 2})

    def test_all_singletons_become_one_conservative_speaker(self):
        tracker = SpeakerTracker(threshold=.9, min_seconds=.5, max_speakers=6)
        tracker.assign([1, 0], 2)
        tracker.assign([0, 1], 2)
        mapping, count = tracker.refine()
        self.assertEqual(count, 1)
        self.assertEqual(set(mapping.values()), {1})

    def test_threshold_and_limits_are_validated(self):
        tracker = SpeakerTracker(threshold=.0, min_seconds=-1, max_speakers=100)
        self.assertGreaterEqual(tracker.threshold, .05)
        self.assertGreaterEqual(tracker.min_seconds, .2)
        self.assertLessEqual(tracker.max_speakers, 12)

    def test_embedding_shape_is_normalized_internally(self):
        tracker = SpeakerTracker(threshold=.25, min_seconds=1, max_speakers=6)
        self.assertEqual(tracker.assign(np.array([3., 4.]), 2), (1, '说话人1'))
