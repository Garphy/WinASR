"""
WinASR 核心 Pipeline — 解耦式架构
音频 → VAD(fsmn-vad) → 逐段 ASR(SenseVoice) + 声纹(CAM++) → 聚类 → 结构化 JSON

FunASR AutoModel 不支持 SenseVoice + SPK 原生串联 (需要 Paraformer 才有 timestamp)，
因此采用解耦方案: VAD 先切分，再分别跑 ASR 和声纹，最后对齐融合。

跨平台支持: macOS (MPS), Windows/Linux (CUDA), CPU fallback
"""

import os
import subprocess
import sys
import time
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
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


def _detect_device(requested: str = "auto") -> str:
    """
    自动检测最佳设备, 优先级: CUDA → MPS → CPU

    - CUDA (Windows/Linux NVIDIA GPU) → 单线程, GPU 加速
    - MPS (macOS Apple Silicon) → 单线程, GPU 加速
    - CPU fallback → 多核并行

    Args:
        requested: "auto" 自动检测, 或 "cuda"/"mps"/"cpu" 指定
    """
    if requested == "auto":
        # 优先 CUDA (Windows/Linux)
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            logger.info("CUDA 可用: %s, 使用 GPU 加速", gpu_name)
            os.environ["OMP_NUM_THREADS"] = "1"
            os.environ["MKL_NUM_THREADS"] = "1"
            return "cuda"
        # 其次 MPS (macOS)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            logger.info("MPS (Apple Silicon GPU) 可用, 使用 GPU 加速")
            os.environ["OMP_NUM_THREADS"] = "1"
            os.environ["MKL_NUM_THREADS"] = "1"
            return "mps"
        # CPU fallback
        import multiprocessing
        ncores = multiprocessing.cpu_count()
        os.environ["OMP_NUM_THREADS"] = str(ncores)
        os.environ["MKL_NUM_THREADS"] = str(ncores)
        logger.warning("无可用 GPU, 回退到 CPU (%d 核)", ncores)
        return "cpu"

    # 用户指定了具体设备
    if requested == "cuda" and not torch.cuda.is_available():
        logger.warning("请求 CUDA 但不可用, 自动降级")
        return _detect_device("auto")
    if requested == "mps":
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            os.environ["OMP_NUM_THREADS"] = "1"
            os.environ["MKL_NUM_THREADS"] = "1"
            return "mps"
        logger.warning("请求 MPS 但不可用, 自动降级")
        return _detect_device("auto")

    return requested


def _ensure_wav(audio_path: Path) -> Path:
    """
    非 WAV 格式预转码为 16kHz 单声道 WAV,
    消除 torchaudio 运行时解码 m4a/mp3 的 CPU 开销
    """
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    # 不指定 dir — 使用系统临时目录（跨平台兼容）
    wav_path = Path(tempfile.mktemp(suffix=".wav", prefix="winasr_conv_"))
    logger.info("Converting %s → WAV (16kHz mono)...", audio_path.name)
    t0 = time.time()

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1",
            "-loglevel", "error",
            str(wav_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {result.stderr}")

    elapsed = time.time() - t0
    size_mb = wav_path.stat().st_size / 1024 / 1024
    logger.info("转码完成: %.1fMB, %.1fs", size_mb, elapsed)
    return wav_path


class ASRPipeline:
    """FunASR 解耦式多模型级联 Pipeline"""

    def __init__(
        self,
        device: str = "auto",
        max_segment_time: int = 30000,
        disable_update: bool = True,
    ):
        self.device = _detect_device(device)
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
        language: str = "zh",
        batch_size_s: int = 1200,
    ) -> TranscriptionResult:
        """
        转写音频文件, 返回结构化结果

        Args:
            audio_path: 音频文件路径 (支持 wav/mp3/m4a/flac)
            language: 语种 (auto/zh/en/yue/ja/ko)
            batch_size_s: ASR 批处理秒数 (大内存可拉高到 1200+)

        Returns:
            TranscriptionResult
        """
        self.load()
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info("Transcribing: %s (language=%s, batch=%ds)", audio_path.name, language, batch_size_s)
        t0 = time.time()

        # Step 0: 非 WAV 预转码
        wav_path = _ensure_wav(audio_path)
        converted = wav_path != audio_path

        try:
            # Step 1: VAD 切分
            segments_ms = self._run_vad(wav_path)
            total = len(segments_ms)
            logger.info("VAD found %d segments", total)

            if total == 0:
                logger.warning("No speech segments found")
                peaks = self._generate_peaks(wav_path)
                return TranscriptionResult(
                    audio_file=audio_path.name,
                    duration=0.0,
                    num_speakers=0,
                    segments=[],
                    peaks=peaks,
                )

            # 加载音频
            wav, sr = self._load_audio(wav_path)

            # Step 2: 逐段 ASR + 声纹提取 (并行)
            raw_segments = []
            embeddings = []
            t_seg = time.time()
            _executor = ThreadPoolExecutor(max_workers=2)

            for i, (start_ms, end_ms) in enumerate(segments_ms):
                chunk_path = self._extract_chunk(wav, sr, start_ms, end_ms, i)

                # 声纹时长判断
                duration_s = (end_ms - start_ms) / 1000.0
                need_spk = duration_s >= MIN_SPK_DURATION_S

                # ASR + 声纹 并行提交
                future_asr = _executor.submit(self._run_asr, chunk_path, language, batch_size_s)
                future_spk = _executor.submit(self._run_speaker_embedding, chunk_path) if need_spk else None

                # 等待结果
                text_raw = future_asr.result()
                tags = parse_sensevoice_tags(text_raw)
                emb = future_spk.result() if future_spk is not None else None

                raw_segments.append({
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "tags": tags,
                    "emb_idx": len(embeddings) if emb is not None else None,
                })
                if emb is not None:
                    embeddings.append(emb)

                # 清理临时文件 (Windows 兼容: 忽略 PermissionError)
                try:
                    os.unlink(chunk_path)
                except PermissionError:
                    logger.debug("Could not delete temp file (locked): %s", chunk_path)
                except OSError:
                    pass

                # 进度日志 (每 50 段或最后一段)
                if (i + 1) % 50 == 0 or i == total - 1:
                    elapsed_s = time.time() - t_seg
                    rtf = elapsed_s / (segments_ms[i][1] / 1000.0) if segments_ms[i][1] > 0 else 0
                    logger.info(
                        "  [%d/%d] %d%% | %.1fs elapsed | RTF=%.3f | last: %.20s",
                        i + 1, total,
                        int((i + 1) / total * 100),
                        elapsed_s, rtf,
                        tags["text"][:20],
                    )

            _executor.shutdown(wait=False)

            # Step 3: 声纹聚类
            logger.info("Clustering %d speaker embeddings...", len(embeddings))
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

            # Step 5: 生成波形峰值数据
            peaks = self._generate_peaks(wav_path)

            elapsed = time.time() - t0
            logger.info(
                "Done in %.1fs (%.1fx real-time): %d segments, %d speakers, %d peaks",
                elapsed, duration / elapsed if elapsed > 0 else 0,
                len(segments), len(speakers), len(peaks),
            )

            return TranscriptionResult(
                audio_file=audio_path.name,
                duration=duration,
                num_speakers=len(speakers),
                segments=segments,
                peaks=peaks,
            )

        finally:
            # 清理转码临时文件
            if converted and wav_path.exists():
                try:
                    wav_path.unlink()
                except PermissionError:
                    logger.debug("Could not delete converted WAV (locked): %s", wav_path)

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _generate_peaks(wav_path: Path, chunk_size: int = 1024) -> list[float]:
        """
        从 WAV 文件读取音频数据, 按 chunk_size 采样点计算每段的 max 幅度值.
        返回 [float, ...] flat array, 用于前端波形绘制.
        """
        try:
            wav, sr = torchaudio.load(str(wav_path))
            # 取第一个声道
            samples = wav[0].numpy()
            peaks = []
            for i in range(0, len(samples), chunk_size):
                chunk = samples[i:i + chunk_size]
                if len(chunk) == 0:
                    break
                peaks.append(float(abs(chunk).max()))
            return peaks
        except Exception as e:
            logger.warning("Failed to generate peaks: %s", e)
            return []

    def _run_vad(self, audio_path: Path) -> list[tuple[int, int]]:
        """VAD 切分, 返回 [(start_ms, end_ms), ...]"""
        res = self._vad_model.generate(input=str(audio_path), disable_pbar=True)
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
        # 不指定 dir — 使用系统临时目录（跨平台兼容）
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", prefix=f"winasr_{idx}_"
        )
        torchaudio.save(tmp.name, chunk, sr)
        tmp.close()
        return tmp.name

    def _run_asr(self, chunk_path: str, language: str, batch_size_s: int = 1200) -> str:
        """SenseVoice ASR 推理, 返回原始文本 (含标签)"""
        res = self._asr_model.generate(
            input=chunk_path,
            language=language,
            use_itn=False,
            ban_emo_unk=False,
            batch_size_s=batch_size_s,
            disable_pbar=True,
        )
        if res and "text" in res[0]:
            return res[0]["text"]
        return ""

    def _run_speaker_embedding(self, chunk_path: str) -> Optional[np.ndarray]:
        """CAM++ 声纹特征提取, 返回 embedding ndarray"""
        try:
            res = self._spk_model.generate(input=chunk_path, disable_pbar=True)
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


def get_pipeline(device: str = "auto") -> ASRPipeline:
    """获取全局 Pipeline 单例"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ASRPipeline(device=device)
    return _pipeline
