"""WinASR FastAPI 服务"""

# ── 性能优化: 必须在 import torch 之前设置 ──
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import json
import uuid
import shutil
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.pipeline import get_pipeline
from backend.schema import TranscriptionResult, TaskStatus, TaskState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("winasr.app")

app = FastAPI(
    title="WinASR",
    description="离线语音转文字 + 声纹识别服务",
    version="0.2.0",
)

# ── CORS 中间件 ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 输出目录
OUTPUT_DIR = Path(os.getenv("WINASR_OUTPUT_DIR", Path.home() / "WinASR" / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 上传文件暂存目录
UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ── 任务管理 (Phase 1: 内存 dict) ────────────────────────────
task_store: Dict[str, TaskStatus] = {}


def _process_task(task_id: str, tmp_path: str, filename: str, language: str) -> None:
    """后台任务: 执行转写并更新 task_store"""
    task = task_store.get(task_id)
    if task is None:
        return

    task.state = TaskState.processing
    task.progress = 0.1

    try:
        pipeline = get_pipeline()
        result = pipeline.transcribe(audio_path=tmp_path, language=language)

        # 保存结果到文件
        output_file = OUTPUT_DIR / f"{task_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.to_json())

        task.state = TaskState.completed
        task.progress = 1.0
        task.result = result.to_dict()
        logger.info("Task %s completed: %s", task_id, filename)

    except Exception as e:
        logger.exception("Task %s failed: %s", task_id, e)
        task.state = TaskState.failed
        task.error = str(e)

    finally:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── 原有端点 ─────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "WinASR",
        "version": "0.2.0",
        "endpoints": {
            "POST /transcribe": "上传音频文件进行转写 (同步)",
            "POST /api/tasks": "创建异步转写任务",
            "GET /api/tasks": "列出所有任务",
            "GET /api/tasks/{task_id}": "查询任务状态",
            "GET /health": "健康检查",
        },
    }


@app.get("/health")
async def health():
    pipeline = get_pipeline()
    return {
        "status": "ok",
        "model_loaded": pipeline.is_loaded,
        "device": pipeline.device,
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(description="音频文件 (wav/mp3/m4a/flac)"),
    language: str = Query(default="zh", description="语种: zh/auto/en/yue/ja/ko (默认中文)"),
    output_format: str = Query(default="json", description="输出格式: json/text"),
):
    """
    上传音频文件, 返回转写结果 (同步)

    返回包含: 每个语音段的时间戳、文本、说话人ID、情绪标签、语种、波形峰值
    """
    # 保存上传文件到临时目录
    suffix = Path(file.filename).suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="/tmp") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        pipeline = get_pipeline()
        result = pipeline.transcribe(audio_path=tmp_path, language=language)

        # 保存结果到文件
        output_file = OUTPUT_DIR / f"{Path(file.filename).stem}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.to_json())

        if output_format == "text":
            return PlainTextResponse(result.to_plain_text())

        return JSONResponse(content=result.to_dict())

    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        os.unlink(tmp_path)


# ── Phase 1: 异步任务 API ────────────────────────────────────

@app.post("/api/tasks")
async def create_task(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(description="音频文件 (wav/mp3/m4a/flac)"),
    language: str = Query(default="zh", description="语种: zh/auto/en/yue/ja/ko (默认中文)"),
):
    """
    创建异步转写任务: 上传文件 → 返回 task_id → 后台处理 → 前端轮询
    """
    task_id = uuid.uuid4().hex[:12]
    filename = file.filename or "unknown.wav"

    # 保存上传文件
    suffix = Path(filename).suffix or ".wav"
    tmp_path = str(UPLOAD_DIR / f"{task_id}{suffix}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)

    # 创建任务记录
    now = datetime.now(timezone.utc).isoformat()
    task = TaskStatus(
        task_id=task_id,
        state=TaskState.pending,
        filename=filename,
        progress=0.0,
        created_at=now,
    )
    task_store[task_id] = task

    # 提交后台任务
    background_tasks.add_task(_process_task, task_id, tmp_path, filename, language)
    logger.info("Task %s created for file: %s", task_id, filename)

    return JSONResponse(status_code=202, content=task.to_dict())


@app.get("/api/tasks")
async def list_tasks():
    """列出所有任务"""
    tasks = [task.to_dict() for task in task_store.values()]
    # 按创建时间降序排列
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return JSONResponse(content=tasks)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """查询单个任务状态"""
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return JSONResponse(content=task.to_dict())


# ── 静态文件服务 (前端 SPA) ──────────────────────────────────
_dist_dir = Path(__file__).parent.parent / "app" / "dist"
if _dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="static")
    logger.info("Serving static files from: %s", _dist_dir)
else:
    logger.warning("Static files directory not found: %s (frontend not built yet)", _dist_dir)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
