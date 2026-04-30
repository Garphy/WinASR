#!/usr/bin/env python3
"""WinASR 启动脚本"""
import os

# ── 性能优化: 必须在 import torch 之前设置 ──
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
