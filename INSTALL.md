# WinASR 安装说明

## 环境要求

| 依赖 | 版本要求 | 用途 |
|------|----------|------|
| macOS | 14+ (Apple Silicon) | MPS GPU 加速 |
| Python | 3.12+ | 后端运行时 |
| Node.js | 20+ (LTS) | 前端构建 |
| pnpm | 9+ | 前端包管理 |
| uv | 最新版 | Python 依赖管理 |
| ffmpeg | 最新版 | 音频格式转码 |

## 1. 安装基础工具

### Homebrew（如未安装）

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### ffmpeg

```bash
brew install ffmpeg
```

### uv（Python 依赖管理）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc   # 重新加载 shell
```

### nvm + Node.js

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.zshrc
nvm install --lts
nvm use --lts
```

### pnpm

```bash
npm install -g pnpm
```

## 2. 克隆项目

```bash
git clone git@github.com:Garphy/WinASR.git
cd WinASR
```

## 3. 后端安装

```bash
uv sync
```

uv 会自动创建 `.venv` 并安装 Python 3.12 + 所有依赖。

验证 MPS：

```bash
.venv/bin/python -c "import torch; print(torch.backends.mps.is_available())"
# 应输出 True
```

## 4. 前端安装

```bash
cd app
pnpm install
```

## 5. 启动服务

**终端 1 — 后端（端口 8000）：**

```bash
cd ~/WinASR
.venv/bin/python run_server.py
```

**Debug 模式**（输出详细日志）：

```bash
.venv/bin/python run_server.py --debug
```

**终端 2 — 前端（端口 5173）：**

```bash
cd ~/WinASR/app
pnpm run dev
```

打开浏览器访问 `http://localhost:5173`。

## 6. 构建生产版本

```bash
cd ~/WinASR/app
pnpm run build
```

构建产物输出到 `app/dist/`，后端 FastAPI 会自动托管。

## 内网访问

Vite 已配置 `host: true`，前端自动监听 `0.0.0.0`。局域网内通过 `http://<本机IP>:5173` 访问。

## 文件管理

- 上传文件存储在 `~/WinASR/output/files/`，以 MD5 哈希命名（去重）
- 元数据记录在 `~/WinASR/output/files/metadata.json`
- 文件永久保存，不会随任务清理而删除

## 常见问题

### Q: `uv sync` 报 Python 版本不满足？

uv 会自动下载 Python 3.12，无需手动安装。如遇问题可指定版本：

```bash
uv python install 3.12
uv sync
```

### Q: MPS 相关报错？

CAM++ 声纹模型部分算子在 MPS 上不兼容，程序会自动 fallback 到 CPU，不影响功能。

### Q: 首次加载模型很慢？

首次运行需下载模型文件（~1GB），后续走本地缓存。可提前手动下载：

```bash
cd ~/WinASR
.venv/bin/python -c "from funasr import AutoModel; AutoModel(model='iic/SenseVoiceSmall')"
```

### Q: 端口被占用？

```bash
lsof -ti:8000   # 查看后端端口占用
lsof -ti:5173   # 查看前端端口占用
kill $(lsof -ti:8000)  # 终止
```

### Q: 后端重启后任务列表为空？

任务存储在内存中，重启后清空。但转写结果（`output/*.json`）和音频文件（`output/files/`）持久保存在磁盘上，不受影响。
