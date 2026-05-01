# WinASR

离线语音转文字 + 声纹识别 + 情绪识别 + Web 编辑器

## 功能

- **语音转文字**：基于 FunASR SenseVoice 模型
- **声纹识别**：基于 CAM++ 模型 + 谱聚类
- **情绪/语种检测**：SenseVoice 自带情绪和语种标签
- **Web 编辑器**：波形可视化 + 在线校对 + 说话人改名 + 撤销/重做
- **多格式支持**：wav / mp3 / m4a / flac
- **文件去重**：MD5 哈希存储，相同文件不重复占用空间

## 技术架构

```
┌─────────────────────────────────────────────────┐
│  Frontend: React 19 + TypeScript + Vite          │
│  ┌──────┐  ┌──────────┐  ┌───────────────────┐  │
│  │Upload│→ │ TaskList │→ │ Workspace (编辑器)  │  │
│  └──────┘  └──────────┘  │ WaveSurfer 波形    │  │
│                          │ Virtuoso 虚拟滚动  │  │
│                          │ Zustand 状态管理   │  │
│                          └───────────────────┘  │
├─────────────────────────────────────────────────┤
│  Backend: FastAPI + Python                       │
│  ┌──────────────────────────────────────────┐   │
│  │ VAD → ASR(SenseVoice) + 声纹(CAM++) → 聚类│   │
│  │ ↓                                        │   │
│  │ FileStore(MD5去重) + TaskStore(内存)      │   │
│  └──────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│  Hardware: Apple Silicon MPS GPU 加速            │
└─────────────────────────────────────────────────┘
```

## 快速开始

详见 [INSTALL.md](INSTALL.md)

```bash
# 后端
cd ~/WinASR
uv sync
.venv/bin/python run_server.py

# 前端（新终端）
cd ~/WinASR/app
pnpm install
pnpm run dev
```

打开 `http://localhost:5173`，上传音频即可使用。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 创建异步转写任务 |
| GET | `/api/tasks` | 列出所有任务 |
| GET | `/api/tasks/{id}` | 查询任务状态 |
| GET | `/api/tasks/{id}/result` | 获取转写结果 |
| GET | `/api/tasks/{id}/audio` | 获取音频文件 |
| POST | `/transcribe` | 同步转写（直接返回结果） |
| GET | `/api/files` | 列出所有文件记录 |
| GET | `/health` | 健康检查 |

### POST /api/tasks

```bash
curl -X POST http://localhost:8000/api/tasks \
  -F "file=@meeting.m4a" \
  -F "language=zh"
# 返回: {"task_id": "abc123", "state": "pending", ...}
```

### POST /transcribe

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@meeting.m4a" \
  -F "output_format=text"
```

## 性能参考

| 音频时长 | 处理耗时 | RTF |
|----------|----------|-----|
| 4 min | ~15 s | ~0.06 |
| 103 min | ~14 min | ~0.14 |

> RTF < 1 表示快于实时。优化措施：ASR+声纹并行、MPS GPU 加速、m4a 预转码。

## 项目结构

```
WinASR/
├── backend/
│   ├── app.py           # FastAPI 服务入口
│   ├── pipeline.py      # 核心 Pipeline（VAD → ASR + 声纹 → 聚类）
│   ├── schema.py        # 数据模型定义
│   └── file_store.py    # 文件管理（MD5 去重 + 元数据持久化）
├── app/                 # React 前端
│   ├── src/
│   │   ├── components/  # 页面组件
│   │   ├── stores/      # Zustand 状态管理
│   │   ├── services/    # API 调用
│   │   └── types/       # TypeScript 类型
│   ├── vite.config.ts   # Vite 配置（含 API 代理）
│   └── package.json
├── output/              # 输出目录
│   ├── files/           # 上传文件（MD5 命名，永久保存）
│   └── *.json           # 转写结果
├── run_server.py        # 启动脚本（支持 --debug）
├── INSTALL.md           # 安装说明
└── pyproject.toml       # Python 依赖
```

## 常见问题

**Q: 模型加载很慢？**
首次运行需从 ModelScope 下载模型（~1GB），后续走缓存。

**Q: MPS 报错？**
CAM++ 声纹聚类部分算子在 MPS 上不兼容，程序会自动 fallback 到 CPU。

**Q: 音频播放器 404？**
确保后端未重启（任务在内存中），或文件仍在 `output/files/` 目录下。

**Q: Debug 模式？**
```bash
.venv/bin/python run_server.py --debug
# 或
WINASR_DEBUG=1 .venv/bin/python run_server.py
```
