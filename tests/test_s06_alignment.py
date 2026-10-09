"""测试 S06 跨说话人防撞与欠账偿还机制。

覆盖:
1. 真实生产任务 1ff0d1ea 回归:假重叠清零、同人零重叠、字幕单调。
2. 构造用例:两说话人顺序接话,前人配音超长,验证不撞车。
3. 构造用例:多说话人真源重叠组,非绑定成员加速,不吃主说话人放慢。
"""
import copy
import json
import unittest

from src.pipelines.video.units.alignment import plan_alignment
from src.pipelines.video.units.audio_unit import AudioUnit, SpeakerTrack, TranslationPair, UnitType


class TestS06Alignment(unittest.TestCase):
    def test_1ff0d1ea_regression(self):
        """生产 1ff0d1ea 任务:修改后假重叠清零,同人保持零重叠,字幕严格单调。"""
        with open("/root/.gemini/antigravity/brain/e3a4ca7a-d16d-49bf-bfd1-97c1ea1fdd26/scratch/ckpt/1ff0d1ea.json") as f:
            prod = json.load(f)
        raw = prod["speaker_tracks"]
        units = {t: SpeakerTrack.from_dict(copy.deepcopy(d)).units for t, d in raw.items()}
        src_dur = prod["video_adjust_plan"][-1]["src_end"]

        vplan, aplan, smap = plan_alignment(units, source_duration=src_dur)

        # 1) 假重叠应为 0
        speech_rows = sorted((r for r in smap if not r["is_singing"]), key=lambda r: r["out_start"])
        fake_ov = []
        same_ov = []
        for i, a in enumerate(speech_rows):
            for b in speech_rows[i + 1:]:
                if b["out_start"] >= a["out_end"] - 1e-3:
                    break
                ov = a["out_end"] - b["out_start"]
                if a["speaker"] == b["speaker"]:
                    same_ov.append(ov)
                elif min(a["src_end"], b["src_end"]) <= max(a["src_start"], b["src_start"]) + 1e-3:
                    fake_ov.append((ov, a, b))

        self.assertEqual(len(fake_ov), 0, f"存在假重叠: {fake_ov[:5]}")
        self.assertEqual(len(same_ov), 0, f"存在同人重叠: {same_ov[:5]}")

        # 2) 每说话人字幕严格单调
        by_spk = {}
        for r in speech_rows:
            by_spk.setdefault(r["speaker"], []).append(r)
        for spk, rows in by_spk.items():
            rows.sort(key=lambda r: r["src_start"])
            for a, b in zip(rows, rows[1:]):
                self.assertGreaterEqual(b["out_start"], a["out_end"] - 1e-3,
                                        f"说话人 {spk} 字幕逆序/交叠: {a['out_end']} > {b['out_start']}")

    def test_cross_speaker_spill_no_collision(self):
        """构造用例: A 说话人配音超长溢出,紧随其后 B 说话人接话,B 绝不能与 A 撞车。"""
        # A 说 2.0s, TTS 3.0s (溢出 1.0s); B 在 2.1s 说 2.0s, TTS 2.0s
        from src.pipelines.video.units.audio_unit import Speaker
        spk_a = Speaker(id="SPK_A")
        spk_b = Speaker(id="SPK_B")
        p_a = TranslationPair(start=0.0, end=2.0, src_text="Hello from A", translation="我是A",
                              tts_duration=3.0, tts_path="dummy_a.wav")
        u_a = AudioUnit(start=0.0, end=2.0, kind=UnitType.SPEECH, speaker=spk_a, pairs=[p_a])

        p_b = TranslationPair(start=2.1, end=4.1, src_text="Hello from B", translation="我是B",
                              tts_duration=2.0, tts_path="dummy_b.wav")
        u_b = AudioUnit(start=2.1, end=4.1, kind=UnitType.SPEECH, speaker=spk_b, pairs=[p_b])

        units = {"SPK_A": [u_a], "SPK_B": [u_b]}
        _, aplan, smap = plan_alignment(units, source_duration=5.0)

        rows = {r["speaker"]: r for r in smap}
        self.assertGreaterEqual(rows["SPK_B"]["out_start"], rows["SPK_A"]["out_end"] - 1e-3,
                                f"SPK_B 撞车: B_start={rows['SPK_B']['out_start']} < A_end={rows['SPK_A']['out_end']}")

    def test_non_binding_member_not_slowed(self):
        """构造用例: 真源重叠组内, 非绑定成员配音较长时不吃主说话人的放慢。"""
        from src.pipelines.video.units.audio_unit import Speaker
        spk_a = Speaker(id="SPK_A")
        spk_b = Speaker(id="SPK_B")
        # 主说话人 A 说了 10.0s, TTS 5.0s (极其充裕, A 会被放慢到 0.90)
        p_a = TranslationPair(start=0.0, end=10.0, src_text="Main long speech", translation="主说话人长句",
                              tts_duration=5.0, tts_path="dummy_a.wav")
        u_a = AudioUnit(start=0.0, end=10.0, kind=UnitType.SPEECH, speaker=spk_a, pairs=[p_a])

        # 次说话人 B 在 1.0s 处插话 1.0s, 但配音用了 1.8s (较紧迫)
        p_b = TranslationPair(start=1.0, end=2.0, src_text="Quick interjection", translation="短插话",
                              tts_duration=1.8, tts_path="dummy_b.wav")
        u_b = AudioUnit(start=1.0, end=2.0, kind=UnitType.SPEECH, speaker=spk_b, pairs=[p_b])

        units = {"SPK_A": [u_a], "SPK_B": [u_b]}
        _, _, smap = plan_alignment(units, source_duration=10.0)

        rows = {r["speaker"]: r for r in smap}
        # B 不能被放慢到 0.90, 应该根据自身需要加速
        self.assertGreater(rows["SPK_B"]["atempo"], 1.0,
                           f"非绑定成员 B 不应被放慢: atempo={rows['SPK_B']['atempo']}")


if __name__ == "__main__":
    unittest.main()
