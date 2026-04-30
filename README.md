# WinASR

离线语音转文字 + 声纹识别 + 情绪识别服务

## 功能

- **语音转文字**：基于 FunASR SenseVoice 模型
- **声纹识别**：基于 CAM++ 模型 + 谱聚类
- **情绪/语种检测**：SenseVoice 自带情绪和语种标签
- **多格式支持**：wav / mp3 / m4a / flac

## 快速开始

```bash
# 安装依赖
cd ~/WinASR
uv sync

# 启动服务
.venv/bin/python run_server.py
```

服务启动后监听 `http://0.0.0.0:8000`，模型首次加载约 30-60 秒。

## 转写音频

```bash
# 中文音频（默认）
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.m4a"

# 自动检测语种
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.m4a" \
  -F "language=auto"

# 纯文本输出
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.m4a" \
  -F "output_format=text"
```

## API 接口

### POST /transcribe

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `file` | file | （必填） | 音频文件，支持 wav/mp3/m4a/flac |
| `language` | string | `zh` | 语种：`zh`/`auto`/`en`/`yue`/`ja`/`ko` |
| `output_format` | string | `json` | 输出格式：`json`/`text` |

**返回示例：**

```json
{
  "audio_file": "meeting.m4a",
  "duration": 6187.9,
  "num_speakers": 8,
  "segments": [
    {
      "start": 0.4,
      "end": 2.38,
      "text": "诶 now嗯",
      "speaker": "SPEAKER_1",
      "emotion": "NEUTRAL",
      "language": "zh",
      "events": ["Speech"]
    }
  ]
}
```

### GET /health

```bash
curl http://localhost:8000/health
# {"status":"ok","model_loaded":true,"device":"mps"}
```

## 语种选择

| 场景 | 推荐设置 | 说明 |
|------|----------|------|
| 中文会议录音 | `language=zh`（默认） | 避免误识别为其他语种 |
| 中英混合 | `language=auto` | 自动检测每段语种 |
| 英文录音 | `language=en` | — |
| 粤语录音 | `language=yue` | — |

## 性能参考

| 音频时长 | 文件大小 | 处理耗时 | RTF |
|----------|----------|----------|-----|
| 4 min | 965 KB | ~15 s | ~0.06 |
| 103 min | 174 MB | ~14 min | ~0.14 |

> RTF（Real-Time Factor）= 处理耗时 / 音频时长，越小越快。RTF < 1 表示快于实时。

**优化措施：**
- ASR + 声纹并行处理（ThreadPoolExecutor）
- 禁用 tqdm 进度条，消除终端 I/O 阻塞
- MPS 设备自适应（Apple Silicon GPU 加速 + CPU fallback）
- 非 WAV 格式预转码（ffmpeg），消除运行时解码开销

## 技术架构

```
音频 → VAD (fsmn-vad) → 切分片段 → 并行处理:
                                  ├── ASR (SenseVoice) → 转写文本 + 情绪标签
                                  └── 声纹 (CAM++) → 说话人向量
                                                    ↓
                                              谱聚类 → 说话人ID
                                                    ↓
                                              结构化 JSON 输出
```

## 项目结构

```
WinASR/
├── backend/
│   ├── app.py          # FastAPI 服务入口
│   ├── pipeline.py     # 核心 Pipeline（VAD → ASR + 声纹 → 聚类）
│   └── schema.py       # 数据模型定义
├── samples/            # 测试音频样本
├── output/             # 转写结果输出
├── run_server.py       # 启动脚本
└── pyproject.toml      # 项目依赖
```

## 常见问题

**Q: 模型加载很慢？**
首次运行需从 ModelScope 下载模型（~1GB），后续走缓存。

**Q: MPS 报错？**
CAM++ 声纹聚类依赖 SVD 分解，部分算子在 MPS 上不兼容。程序会自动 fallback 到 CPU。

**Q: 内存占用很高？**
处理长音频时内存占用较高是正常的 — 音频数据 + 模型 + embedding 都在统一内存中。
