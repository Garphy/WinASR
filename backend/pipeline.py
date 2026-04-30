"""
WinASR 核心 Pipeline — 解耦式架构
音频 → VAD(fsmn-vad) → 逐段 ASR(SenseVoice) + 声纹(CAM++) → 聚类 → 结构化 JSON

FunASR AutoModel 不支持 SenseVoice + SPK 原生串联 (需要 Paraformer 才有 timestamp)，
因此采用解耦方案: VAD 先切分，再分别跑 ASR 和声纹，最后对齐融合。
"""

import os
import time
import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

import numpy as np
import torch
import torchaudio
from funasr import AutoModel

from backend.schema import (
    Segment,
    TranscriptionResult,
    parse_sensevoice_tags,
)

logger = logging.getLogger("winasr.pipeline")

# 声纹 embedding 最短时长 (秒), 低于此值跳过声纹提取
MIN_SPK_DURATION_S = 0.5


class ASRPipeline:
    """FunASR 解耦式多模型级联 Pipeline"""

    def __init__(
        self,
        device: str = "mps",
        max_segment_time: int = 30000,
        disable_update: bool = True,
    ):
        self.device = device
        self._max_segment_time = max_segment_time
        self._disable_update = disable_update

        self._vad_model: Optional[AutoModel] = None
        self._asr_model: Optional[AutoModel] = None
        self._spk_model: Optional[AutoModel] = None

    def load(self) -> None:
        """加载三个模型 (首次较慢, 约 30-60s, 后续走缓存)"""
        if self._vad_model is not None:
            return

        logger.info("Loading models on device=%s ...", self.device)
        t0 = time.time()

        logger.info("  Loading VAD (fsmn-vad)...")
        self._vad_model = AutoModel(
            model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": self._max_segment_time},
            device=self.device,
            disable_update=self._disable_update,
            trust_remote_code=True,
        )

        logger.info("  Loading ASR (SenseVoiceSmall)...")
        self._asr_model = AutoModel(
            model="iic/SenseVoiceSmall",
            device=self.device,
            disable_update=self._disable_update,
            trust_remote_code=True,
        )

        logger.info("  Loading Speaker (CAM++)...")
        self._spk_model = AutoModel(
            model="cam++",
            device=self.device,
            disable_update=self._disable_update,
            trust_remote_code=True,
        )

        logger.info("All models loaded in %.1fs", time.time() - t0)

    @property
    def is_loaded(self) -> bool:
        return self._vad_model is not None

    def transcribe(
        self,
        audio_path: Union[str, Path],
        language: str = "auto",
        batch_size_s: int = 300,
    ) -> TranscriptionResult:
        """
        转写音频文件, 返回结构化结果

        Args:
            audio_path: 音频文件路径
            language: 语种 (auto/zh/en/yue/ja/ko)
            batch_size_s: (保留参数, 解耦模式下未使用)

        Returns:
            TranscriptionResult
        """
        self.load()
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Transcribing: %s", audio_path.name)
        t0 = time.time()

        # Step 1: VAD 切分
        segments_ms = self._run_vad(audio_path)
        logger.info("VAD found %d segments", len(segments_ms))

        # 加载音频
        wav, sr = self._load_audio(audio_path)

        # Step 2: 逐段 ASR + 声纹提取
        raw_segments = []
        embeddings = []

        for i, (start_ms, end_ms) in enumerate(segments_ms):
            chunk_path = self._extract_chunk(wav, sr, start_ms, end_ms, i)

            # ASR
            text_raw = self._run_asr(chunk_path, language)
            tags = parse_sensevoice_tags(text_raw)

            # 声纹 (太短的片段跳过)
            duration_s = (end_ms - start_ms) / 1000.0
            emb = None
            if duration_s >= MIN_SPK_DURATION_S:
                emb = self._run_speaker_embedding(chunk_path)

            raw_segments.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "tags": tags,
                "emb_idx": len(embeddings) if emb is not None else None,
            })
            if emb is not None:
                embeddings.append(emb)

            # 清理临时文件
            os.unlink(chunk_path)

        # Step 3: 声纹聚类
        speaker_labels = self._cluster_speakers(embeddings, len(raw_segments), raw_segments)

        # Step 4: 组装最终结果
        segments = []
        for i, seg in enumerate(raw_segments):
            tags = seg["tags"]
            segments.append(Segment(
                start=seg["start_ms"] / 1000.0,
                end=seg["end_ms"] / 1000.0,
                text=tags["text"],
                raw_text=tags["raw_text"],
                speaker=speaker_labels[i],
                emotion=tags["emotion"],
                language=tags["language"],
                events=tags["events"],
            ))

        duration = segments[-1].end if segments else 0.0
        speakers = set(s.speaker for s in segments)

        elapsed = time.time() - t0
        logger.info("Transcription done in %.1fs: %d segments, %d speakers",
                     elapsed, len(segments), len(speakers))

        return TranscriptionResult(
            audio_file=audio_path.name,
            duration=duration,
            num_speakers=len(speakers),
            segments=segments,
        )

    # ── 内部方法 ──────────────────────────────────────────────

    def _run_vad(self, audio_path: Path) -> list[tuple[int, int]]:
        """VAD 切分, 返回 [(start_ms, end_ms), ...]"""
        res = self._vad_model.generate(input=str(audio_path))
        if res and "value" in res[0]:
            return [tuple(seg) for seg in res[0]["value"]]
        return []

    def _load_audio(self, audio_path: Path) -> tuple[torch.Tensor, int]:
        """加载音频, 返回 (waveform, sample_rate)"""
        wav, sr = torchaudio.load(str(audio_path))
        if sr != 16000:
            wav = torchaudio.transforms.Resample(sr, 16000)(wav)
            sr = 16000
        return wav, sr

    def _extract_chunk(self, wav: torch.Tensor, sr: int,
                       start_ms: int, end_ms: int, idx: int) -> str:
        """提取音频片段并保存为临时 wav 文件"""
        start_sample = int(start_ms / 1000 * sr)
        end_sample = int(end_ms / 1000 * sr)
        chunk = wav[:, start_sample:end_sample]
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", dir="/tmp", prefix=f"winasr_{idx}_"
        )
        torchaudio.save(tmp.name, chunk, sr)
        return tmp.name

    def _run_asr(self, chunk_path: str, language: str) -> str:
        """SenseVoice ASR 推理, 返回原始文本 (含标签)"""
        res = self._asr_model.generate(
            input=chunk_path,
            language=language,
            use_itn=False,
            ban_emo_unk=False,
        )
        if res and "text" in res[0]:
            return res[0]["text"]
        return ""

    def _run_speaker_embedding(self, chunk_path: str) -> Optional[np.ndarray]:
        """CAM++ 声纹特征提取, 返回 embedding ndarray"""
        try:
            res = self._spk_model.generate(input=chunk_path)
            if res and "spk_embedding" in res[0]:
                emb = res[0]["spk_embedding"]
                if isinstance(emb, torch.Tensor):
                    emb = emb.cpu().numpy()
                return emb
        except Exception as e:
            logger.warning("Speaker embedding failed for chunk: %s", e)
        return None

    def _cluster_speakers(
        self,
        embeddings: list[np.ndarray],
        num_segments: int,
        raw_segments: list[dict],
        max_speakers: int = 10,
    ) -> list[str]:
        """
        对声纹 embeddings 进行聚类, 返回每个 segment 的 speaker label

        如果 embedding 不足 2 个, 默认全部为 SPEAKER_0
        """
        labels = ["SPEAKER_0"] * num_segments

        if len(embeddings) < 2:
            return labels

        try:
            from sklearn.cluster import SpectralClustering

            # 构建 embedding 矩阵
            X = np.vstack([
                e.reshape(1, -1) if e.ndim == 1 else e for e in embeddings
            ])

            # 尝试不同聚类数, 选最优 (简单启发式: 2~min(n, max_speakers))
            n_candidates = min(len(embeddings), max_speakers)
            best_n = 2
            if n_candidates > 2:
                from sklearn.metrics import silhouette_score
                best_score = -1
                for n in range(2, n_candidates + 1):
                    try:
                        sc = SpectralClustering(n_clusters=n, affinity="cosine", random_state=42)
                        pred = sc.fit_predict(X)
                        score = silhouette_score(X, pred, metric="cosine")
                        if score > best_score:
                            best_score = score
                            best_n = n
                    except Exception:
                        break

            clustering = SpectralClustering(n_clusters=best_n, affinity="cosine", random_state=42)
            cluster_labels = clustering.fit_predict(X)

            # 将聚类结果映射回 segments
            emb_idx = 0
            for i, seg in enumerate(raw_segments):
                if seg["emb_idx"] is not None:
                    labels[i] = f"SPEAKER_{cluster_labels[emb_idx]}"
                    emb_idx += 1
                else:
                    # 太短没有 embedding 的片段, 使用前一个 segment 的 speaker
                    if i > 0:
                        labels[i] = labels[i - 1]

        except Exception as e:
            logger.warning("Speaker clustering failed, defaulting to SPEAKER_0: %s", e)

        return labels


# 全局单例
_pipeline: Optional[ASRPipeline] = None


def get_pipeline(device: str = "mps") -> ASRPipeline:
    """获取全局 Pipeline 单例"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ASRPipeline(device=device)
    return _pipeline
