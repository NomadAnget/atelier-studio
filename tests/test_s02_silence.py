"""测试 S02 说话人分轨步骤中单人轨静音检测契约(pause_detect)。"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np

from src.engines.media.pause_detect import Pauses
from src.pipelines.video.steps.s02_speaker_tracks import SpeakerTracksStep


class TestS02Silence(unittest.TestCase):
    def test_s02_vsil_fallback_contract(self):
        """当上游未传入 track_silence 时, S02 的 vsil 兜底应调用 detect_pauses.syllable。"""
        step = SpeakerTracksStep()
        mock_pauses = Pauses(syllable=[(0.5, 1.5)], phrase=[(0.5, 2.0)])

        with patch("librosa.load", return_value=(np.zeros(16000), 16000)), \
             patch("src.pipelines.video.steps.s02_speaker_tracks.detect_pauses", return_value=mock_pauses) as mock_p, \
             patch("src.pipelines.video.steps.s02_speaker_tracks.SileroVadEngine") as mock_vad, \
             patch("src.pipelines.video.steps.s02_speaker_tracks.vseg.segment_voice", return_value=[]):

            mock_vad.return_value.__enter__.return_value.frame_probs.return_value = (np.array([]), 0.032)

            ctx = MagicMock()
            ctx.require.return_value = Path("/tmp/dummy_vocals.wav")
            ctx.dirs = {"speaker": Path("/tmp/speaker")}
            ctx.get.side_effect = lambda k, default=None: None if k == "track_silence" else (False if k == "singing_detect" else default)
            ctx.cancelled = True

            out = step.run(ctx)
            mock_p.assert_called_once()
            args, _ = mock_p.call_args
            self.assertEqual(len(args[0]), 16000)


if __name__ == "__main__":
    unittest.main()
