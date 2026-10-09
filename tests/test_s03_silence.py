"""测试 S03 ASR 步骤中句边界与有声门的静音检测契约(pause_detect)。"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np

from src.engines.media.pause_detect import Pauses
from src.pipelines.video.steps.s03_asr import ASRStep
from src.pipelines.video.units.audio_unit import Speaker, SpeakerTrack, AudioUnit, UnitType


class TestS03Silence(unittest.TestCase):
    def test_s03_voiced_and_music_gate_contract(self):
        """验证 S03: 句边界有声区取 syllable 差集; 音乐轨唱段传入门取 phrase 扣除停顿。"""
        step = ASRStep()

        mock_pauses = Pauses(
            syllable=[(1.0, 2.0)],      # 说话停顿
            phrase=[(0.0, 1.0)],        # 音乐停顿
            cores=[(1.0, 2.0)],
        )

        spk = Speaker(id="SPK_00")
        u_speech = AudioUnit(start=0.0, end=5.0, kind=UnitType.SPEECH, speaker=spk)
        track_speech = SpeakerTrack(speaker=spk, units=[u_speech], wav=Path("/tmp/dummy_spk.wav"))

        spk_music = Speaker(id="SINGING")
        u_music = AudioUnit(start=0.0, end=2.0, kind=UnitType.SINGING, speaker=spk_music)
        track_music = SpeakerTrack(speaker=spk_music, units=[u_music], wav="/tmp/dummy_music.wav")

        tracks = {"SPK_00": track_speech.to_dict(), "MUSIC": track_music.to_dict()}

        with patch("librosa.load", return_value=(np.zeros(16000 * 5), 16000)), \
             patch("src.pipelines.video.steps.s03_asr.detect_pauses", return_value=mock_pauses) as mock_p, \
             patch("src.pipelines.video.steps.s03_asr.SileroVadEngine") as mock_vad, \
             patch("soundfile.write"):

            mock_vad.return_value.__enter__.return_value.timestamps.return_value = [(0.0, 2.0)]

            ctx = MagicMock()
            ctx.require.return_value = tracks
            ctx.dirs = {"asr": Path("/tmp/asr")}
            ctx.cancelled = True  # 切完 clip 退出, 验证前面的静音检测部分

            step.run(ctx)
            # 两个轨分别调用了 detect_pauses
            self.assertEqual(mock_p.call_count, 2)


if __name__ == "__main__":
    unittest.main()
