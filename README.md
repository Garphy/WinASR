# WinASR

离线语音转文字 + 声纹识别 + 情绪识别服务

## 功能

- **语音转文字**：基于 FunASR SenseVoice 模型
- **声纹识别**：基于 CAM++ 模型 + 谱聚类
- **情绪识别**：SenseVoice 自带情绪标签

## 快速开始

```bash
# 安装依赖
uv sync

# 启动服务
uv run python run_server.py
```

服务启动后访问 http://localhost:8000/docs 查看 API 文档。

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

## API

`POST /transcribe` - 上传音频文件，返回结构化转写结果。

```json
{
  "speakers": [...],
  "segments": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "你好",
      "speaker": "speaker_0",
      "emotion": "happy"
    }
  ]
}
```
