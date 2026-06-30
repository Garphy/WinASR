#!/usr/bin/env python3
"""WinASR 启动脚本"""
import os
import sys

# ── 性能优化: 必须在 import torch 之前设置 ──
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
# MPS GPU 内存: 用完立即归还 OS（默认行为是缓存不释放）
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

# ── Debug 模式: python run_server.py --debug ──
if "--debug" in sys.argv:
    os.environ["WINASR_DEBUG"] = "1"
    sys.argv.remove("--debug")

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
