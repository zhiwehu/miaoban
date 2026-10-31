import unittest

import numpy as np

from voicehub.rediarize import (cluster, label_existing_text, smooth_labels,
                                turns_from_windows)


class RediarizeTests(unittest.TestCase):
    def test_smoothing_removes_single_window_flips(self):
        labels = np.array([0, 0, 1, 0, 0, 1, 1, 1])
        self.assertEqual(smooth_labels(labels, radius=2).tolist(),
                         [0, 0, 0, 0, 1, 1, 1, 1])

    def test_short_turns_are_merged_and_labels_renumbered(self):
        labels = [0, 0, 1, 0, 0, 0, 2, 2]
        turns = turns_from_windows(labels, hop=.8, min_turn=1.2)
        self.assertEqual(turns[0][2], 1)
        self.assertEqual(turns[-1][2], 2)
        self.assertTrue(all((end-start)*.8 >= 1.2 or len(turns) == 1
                            for start, end, _ in turns))

    def test_text_relabel_preserves_words_and_assigns_speakers(self):
        lines = label_existing_text('[00:00] 说话人1：甲。乙。',
                                    [(0, 5, 1), (5, 10, 2)], 10)
        self.assertEqual(''.join(line['text'] for line in lines), '甲。乙。')
        self.assertEqual([line['speaker_id'] for line in lines], [1, 2])

    def test_cluster_separates_orthogonal_embeddings(self):
        embeddings = [np.array([1., 0.]), np.array([.9, .1]),
                      np.array([0., 1.]), np.array([.1, .9])]
        labels = cluster(embeddings, 2)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], labels[3])
        self.assertNotEqual(labels[0], labels[2])
