"""S06 backlog planning baselines and downstream alignment regressions."""
import unittest

from src.pipelines.video.units.alignment import plan_alignment
from src.pipelines.video.units.audio_unit import AudioUnit, Speaker, TranslationPair, UnitType


def speech(speaker_id: str, start: float, end: float, tts_duration: float) -> AudioUnit:
    pair = TranslationPair(
        start=start,
        end=end,
        src_text=speaker_id,
        translation=speaker_id,
        tts_duration=tts_duration,
        tts_path=f"{speaker_id}.wav",
    )
    return AudioUnit(
        start=start,
        end=end,
        kind=UnitType.SPEECH,
        speaker=Speaker(id=speaker_id),
        pairs=[pair],
    )


class TestS06PlanBaseline(unittest.TestCase):
    def test_backlog_preserves_source_to_output_alignment(self):
        units = {
            "MAIN": [speech("MAIN", 0.0, 3.0, 3.0)],
            "SIDE": [speech("SIDE", 1.0, 2.0, 6.0)],
            "NEXT": [speech("NEXT", 3.1, 4.1, 1.0)],
        }

        video, audio, segment_map = plan_alignment(units, source_duration=4.1)

        def map_source_to_output(source_time: float) -> float:
            cursor = 0.0
            for row in video:
                if source_time <= row["src_end"] + 1e-9:
                    source_duration = row["src_end"] - row["src_start"]
                    fraction = (source_time - row["src_start"]) / source_duration
                    return cursor + fraction * row["out_dur"]
                cursor += row["out_dur"]
            return cursor

        fps = 30.0
        frame_durations = []
        cumulative_output = 0.0
        previous_cumulative_frames = 0
        for row in video:
            cumulative_output += row["out_dur"]
            cumulative_frames = round(cumulative_output * fps)
            frame_durations.append(max(1, cumulative_frames - previous_cumulative_frames) / fps)
            previous_cumulative_frames = cumulative_frames

        def map_rendered_source_to_output(source_time: float) -> float:
            cursor = 0.0
            for row, rendered_duration in zip(video, frame_durations):
                if source_time <= row["src_end"] + 1e-9:
                    source_duration = row["src_end"] - row["src_start"]
                    fraction = (source_time - row["src_start"]) / source_duration
                    return cursor + fraction * rendered_duration
                cursor += rendered_duration
            return cursor

        rows = {row["speaker"]: row for row in segment_map}
        voice_rows = {speaker: entries[0] for speaker, entries in audio["voice"].items()}

        self.assertGreaterEqual(voice_rows["NEXT"]["out_start"], rows["SIDE"]["out_end"] - 1e-4)
        for speaker in ("MAIN", "SIDE", "NEXT"):
            self.assertAlmostEqual(
                rows[speaker]["out_start"], map_source_to_output(rows[speaker]["src_start"]), places=3,
                msg=f"{speaker} 配音/字幕起点偏离视频时间映射",
            )
            self.assertLessEqual(
                abs(voice_rows[speaker]["out_start"]
                    - map_rendered_source_to_output(rows[speaker]["src_start"])),
                1 / fps,
                msg=f"{speaker} 配音起点偏离 S07 帧量化后画面超过一帧",
            )
            self.assertAlmostEqual(voice_rows[speaker]["out_dur"], rows[speaker]["out_end"]
                                   - rows[speaker]["out_start"], places=3)
        self.assertGreaterEqual(voice_rows["NEXT"]["out_start"], rows["SIDE"]["out_end"] - 1e-4)
        self.assertGreaterEqual(
            sum(row["out_dur"] for row in video),
            max(row["out_end"] for row in segment_map) - 1e-4,
        )

    def test_sequential_backlog_and_cross_speaker_guard(self):
        units = {
            "A": [speech("A", 0.0, 1.0, 2.0)],
            "B": [speech("B", 1.1, 2.1, 2.0)],
            "C": [speech("C", 2.2, 3.2, 2.0)],
        }

        video, _, segment_map = plan_alignment(units, source_duration=4.0)

        rows = {row["speaker"]: row for row in segment_map}
        self.assertEqual(
            [(rows[key]["out_start"], rows[key]["out_end"], rows[key]["atempo"])
             for key in ("A", "B", "C")],
            [(0.0, 1.62, 1.2346), (1.62, 3.24, 1.2346), (3.24, 5.1761, 1.033)],
        )
        self.assertAlmostEqual(sum(row["out_dur"] for row in video), 5.1761, places=4)
        self.assertGreaterEqual(rows["B"]["out_start"], rows["A"]["out_end"])
        self.assertGreaterEqual(rows["C"]["out_start"], rows["B"]["out_end"])

    def test_following_gap_absorbs_spill(self):
        units = {
            "A": [speech("A", 0.0, 1.0, 2.5)],
            "B": [speech("B", 2.0, 3.0, 1.0)],
        }

        video, _, segment_map = plan_alignment(units, source_duration=4.0)

        rows = {row["speaker"]: row for row in segment_map}
        self.assertEqual(
            [(rows[key]["out_start"], rows[key]["out_end"], rows[key]["atempo"])
             for key in ("A", "B")],
            [(0.0, 2.2, 1.1364), (2.2, 3.2, 1.0)],
        )
        self.assertAlmostEqual(sum(row["out_dur"] for row in video), 4.2, places=4)

    def test_true_source_overlap_remains_parallel(self):
        units = {
            "A": [speech("A", 0.0, 3.0, 2.0)],
            "B": [speech("B", 1.0, 2.0, 2.0)],
        }

        video, _, segment_map = plan_alignment(units, source_duration=3.0)

        rows = {row["speaker"]: row for row in segment_map}
        self.assertEqual(rows["A"]["overlap_group"], rows["B"]["overlap_group"])
        self.assertAlmostEqual(rows["A"]["out_start"], 0.0, places=4)
        self.assertAlmostEqual(rows["B"]["out_start"], 0.8, places=4)
        self.assertAlmostEqual(rows["A"]["atempo"], 0.9, places=4)
        self.assertAlmostEqual(rows["B"]["atempo"], 1.25, places=4)
        output_duration = sum(row["out_dur"] for row in video)
        self.assertAlmostEqual(output_duration, 2.4, places=4)
        self.assertGreaterEqual(output_duration, max(row["out_end"] for row in segment_map))


if __name__ == "__main__":
    unittest.main()
