"""测试 S01 分离步骤中逐轨静音检测契约(pause_detect)。"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.engines.media.pause_detect import Pauses
from src.pipelines.video.steps.s01_separation import SeparationStep


class TestS01Silence(unittest.TestCase):
    def test_s01_silence_contract(self):
        """验证 S01 逐轨静音检测契约: vocals 走 syllable, 其他分轨走 phrase。"""
        step = SeparationStep()

        mock_pauses_vocals = Pauses(
            syllable=[(1.0, 2.0)],
            phrase=[(0.5, 2.5)],
            cores=[(1.0, 2.0)],
        )
        mock_pauses_drums = Pauses(
            syllable=[(3.0, 3.5)],
            phrase=[(3.0, 4.0)],
            cores=[(3.0, 4.0)],
        )

        def mock_detect(path):
            p = str(path)
            if "vocals" in p:
                return mock_pauses_vocals
            return mock_pauses_drums

        def mock_rms(path):
            p = str(path)
            # 模拟原混音 -20dB, 各分轨 -30dB (正常伴奏, 差距 10dB < 35dB)
            return (-20.0, 10.0) if "audio.wav" in p else (-30.0, 10.0)

        with patch("src.pipelines.video.steps.s01_separation.DemucsEngine") as mock_demucs, \
             patch("src.pipelines.video.steps.s01_separation.FfmpegTools"), \
             patch("src.pipelines.video.steps.s01_separation._track_rms_db", side_effect=mock_rms), \
             patch("src.pipelines.video.steps.s01_separation.detect_pauses_file", side_effect=mock_detect):

            mock_demucs_instance = MagicMock()
            mock_demucs.return_value.__enter__.return_value = mock_demucs_instance
            mock_demucs_instance.infer.return_value = {
                "vocals": "/tmp/dummy/audio_vocals.wav",
                "drums":  "/tmp/dummy/audio_drums.wav",
                "bass":   "/tmp/dummy/audio_bass.wav",
                "other":  "/tmp/dummy/audio_other.wav",
            }

            ctx = MagicMock()
            ctx.require.return_value = Path("/tmp/dummy_video.mp4")
            ctx.dirs = {"audio": Path("/tmp/audio"), "separation": Path("/tmp/sep")}
            ctx.cancelled = False

            out = step.run(ctx)

            ts = out.get("track_silence", {})
            self.assertEqual(ts["vocals"], [(1.0, 2.0)], "vocals 应取 syllable")
            self.assertEqual(ts["drums"], [(3.0, 4.0)], "drums 应取 phrase")
            self.assertEqual(ts["bass"], [(3.0, 4.0)], "bass 应取 phrase")
            self.assertEqual(ts["other"], [(3.0, 4.0)], "other 应取 phrase")

    def test_s01_stem_masked_silence(self):
        """验证伴奏分轨弱于原始音轨 35dB 时被判定为全片静音 [(0.0, total_dur)]。"""
        step = SeparationStep()

        def mock_rms(path):
            p = str(path)
            if "audio.wav" in p:
                return (-20.0, 15.0)  # 混音 -20dB, 总长 15.0s
            if "bass" in p:
                return (-60.0, 15.0)  # bass -60dB (比混音低 40dB > 35dB, 掩蔽残渣)
            return (-30.0, 15.0)      # 其余伴奏正常 (-30dB, 差距 10dB)

        mock_pauses = Pauses(syllable=[(1.0, 2.0)], phrase=[(2.0, 3.0)])

        with patch("src.pipelines.video.steps.s01_separation.DemucsEngine") as mock_demucs, \
             patch("src.pipelines.video.steps.s01_separation.FfmpegTools"), \
             patch("src.pipelines.video.steps.s01_separation._track_rms_db", side_effect=mock_rms), \
             patch("src.pipelines.video.steps.s01_separation.detect_pauses_file", return_value=mock_pauses):

            mock_demucs.return_value.__enter__.return_value.infer.return_value = {
                "vocals": "/tmp/dummy/audio_vocals.wav",
                "drums":  "/tmp/dummy/audio_drums.wav",
                "bass":   "/tmp/dummy/audio_bass.wav",
                "other":  "/tmp/dummy/audio_other.wav",
            }

            ctx = MagicMock()
            ctx.require.return_value = Path("/tmp/dummy_video.mp4")
            ctx.dirs = {"audio": Path("/tmp/audio"), "separation": Path("/tmp/sep")}
            ctx.cancelled = False

            out = step.run(ctx)
            ts = out.get("track_silence", {})
            self.assertEqual(ts["bass"], [(0.0, 15.0)], "弱于原混音 35dB 的 bass 轨应判定为全片静音")
            self.assertEqual(ts["drums"], [(2.0, 3.0)], "正常伴奏 drums 应走乐句路")


if __name__ == "__main__":
    unittest.main()
