"""WinASR FastAPI 服务"""

# ── 性能优化: 必须在 import torch 之前设置 ──
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import json
import uuid
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.pipeline import get_pipeline
from backend.schema import TranscriptionResult, TaskStatus, TaskState
from backend.file_store import get_file_store

# ── Debug 模式 ───────────────────────────────────────────────
DEBUG = os.getenv("WINASR_DEBUG", "0").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("winasr.app")
if DEBUG:
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug mode enabled (WINASR_DEBUG=1)")

# ── 常量 ──────────────────────────────────────────────────────
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB
MAX_TASKS_IN_MEMORY = 100            # 保留最近 N 条任务

app = FastAPI(
    title="WinASR",
    description="离线语音转文字 + 声纹识别服务",
    version="0.2.0",
)


@app.on_event("startup")
async def startup_event():
    """启动时加载持久化的任务数据"""
    _load_tasks()
    logger.info("Startup: loaded %d tasks", len(task_store))

# ── CORS 中间件 ──────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 输出目录
OUTPUT_DIR = Path(os.getenv("WINASR_OUTPUT_DIR", Path.home() / "WinASR" / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 任务管理 (内存 dict + 持久化 + 自动清理) ──────────────────
TASKS_FILE = OUTPUT_DIR / "tasks.json"
task_store: Dict[str, TaskStatus] = {}


def _save_tasks():
    """持久化任务元数据到 tasks.json"""
    data = []
    for task in task_store.values():
        d = {
            "task_id": task.task_id,
            "state": task.state.value,
            "filename": task.filename,
            "progress": task.progress,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error": task.error,
        }
        data.append(d)
    data.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    tmp = TASKS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(str(tmp), str(TASKS_FILE))


def _load_tasks():
    """从 tasks.json 加载任务元数据（不含 result，结果从磁盘 JSON 按需加载）"""
    if not TASKS_FILE.exists():
        return
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for d in data:
            task = TaskStatus(
                task_id=d["task_id"],
                state=TaskState(d["state"]),
                filename=d["filename"],
                progress=d.get("progress", 1.0),
                created_at=d.get("created_at"),
                completed_at=d.get("completed_at"),
                error=d.get("error"),
            )
            # 尝试从磁盘加载结果
            if task.state == TaskState.completed:
                result_file = OUTPUT_DIR / f"{task.task_id}.json"
                if result_file.exists():
                    task.result = {"_loaded": True}  # 标记有结果，按需加载
            task_store[task.task_id] = task
        logger.info("Loaded %d tasks from %s", len(task_store), TASKS_FILE)
    except Exception as e:
        logger.warning("Failed to load tasks: %s", e)


def _cleanup_old_tasks():
    """保留最近 MAX_TASKS_IN_MEMORY 条任务，清理旧的"""
    if len(task_store) <= MAX_TASKS_IN_MEMORY:
        return
    # 按 created_at 排序，删除最旧的
    sorted_ids = sorted(
        task_store.keys(),
        key=lambda tid: task_store[tid].created_at or "",
    )
    to_remove = sorted_ids[: len(sorted_ids) - MAX_TASKS_IN_MEMORY]
    for tid in to_remove:
        task = task_store.pop(tid, None)
        if task:
            logger.info("[task %s] Cleaned up (old task)", tid)
            # 清理结果 JSON
            result_file = OUTPUT_DIR / f"{tid}.json"
            if result_file.exists():
                result_file.unlink(missing_ok=True)
    _save_tasks()


def _process_task(task_id: str, file_path: str, filename: str, language: str) -> None:
    """后台任务: 执行转写并更新 task_store"""
    task = task_store.get(task_id)
    if task is None:
        logger.warning("[task %s] Not found in task_store, skipping", task_id)
        return

    task.state = TaskState.processing
    task.progress = 0.1
    logger.info("[task %s] Processing started: %s (lang=%s)", task_id, filename, language)
    if DEBUG:
        logger.debug("[task %s] file_path=%s, file_size=%s", task_id, file_path,
                     os.path.getsize(file_path) if os.path.exists(file_path) else "N/A")

    try:
        pipeline = get_pipeline()
        if DEBUG:
            logger.debug("[task %s] Pipeline loaded, device=%s", task_id, pipeline.device)

        result = pipeline.transcribe(audio_path=file_path, language=language)
        if DEBUG:
            logger.debug("[task %s] Transcription done: %d segments, %.1fs duration",
                         task_id, len(result.segments), result.duration)

        # 保存结果到文件
        output_file = OUTPUT_DIR / f"{task_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.to_json())

        now = datetime.now(timezone.utc).isoformat()
        task.state = TaskState.completed
        task.progress = 1.0
        task.result = result.to_dict()
        task.completed_at = now
        _save_tasks()
        logger.info("[task %s] Completed: %s (%d segments)", task_id, filename, len(result.segments))

    except Exception as e:
        logger.exception("[task %s] Failed: %s", task_id, e)
        task.state = TaskState.failed
        task.error = str(e)
        _save_tasks()

    finally:
        # pipeline.transcribe() 内部已清理转码临时文件
        # 音频文件由 FileStore 管理，不在这里删除
        _cleanup_old_tasks()


# ── 端点 ──────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "WinASR",
        "version": "0.2.0",
        "debug": DEBUG,
        "endpoints": {
            "POST /transcribe": "上传音频文件进行转写 (同步)",
            "POST /api/tasks": "创建异步转写任务",
            "GET /api/tasks": "列出所有任务",
            "GET /api/tasks/{task_id}": "查询任务状态",
            "GET /api/tasks/{task_id}/result": "获取转写结果",
            "GET /api/tasks/{task_id}/audio": "获取音频文件",
            "GET /api/files": "列出所有文件记录",
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
        "debug": DEBUG,
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(description="音频文件 (wav/mp3/m4a/flac)"),
    language: str = Query(default="zh", description="语种: zh/auto/en/yue/ja/ko (默认中文)"),
    output_format: str = Query(default="json", description="输出格式: json/text"),
):
    """
    上传音频文件, 返回转写结果 (同步)
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"文件过大，最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB")

    filename = file.filename or "unknown.wav"
    # 保存到 FileStore（MD5 去重）
    file_path = get_file_store().save_file(content, filename, "sync_" + uuid.uuid4().hex[:8])

    try:
        pipeline = get_pipeline()
        result = pipeline.transcribe(audio_path=str(file_path), language=language)

        output_file = OUTPUT_DIR / f"{Path(filename).stem}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.to_json())

        if output_format == "text":
            return PlainTextResponse(result.to_plain_text())

        return JSONResponse(content=result.to_dict())

    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── 异步任务 API ────────────────────────────────────────────

@app.post("/api/tasks")
async def create_task(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(description="音频文件 (wav/mp3/m4a/flac)"),
    language: str = Query(default="zh", description="语种: zh/auto/en/yue/ja/ko (默认中文)"),
):
    """
    创建异步转写任务: 上传文件 → 返回 task_id → 后台处理 → 前端轮询
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"文件过大，最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB")

    task_id = uuid.uuid4().hex[:12]
    filename = file.filename or "unknown.wav"

    # 保存文件（MD5 去重，永久保存）
    file_path = get_file_store().save_file(content, filename, task_id)

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
    _save_tasks()

    # 提交后台任务
    background_tasks.add_task(_process_task, task_id, str(file_path), filename, language)
    logger.info("[task %s] Created for file: %s (size=%d bytes, path=%s)",
                task_id, filename, len(content), file_path)

    return JSONResponse(status_code=202, content=task.to_dict())


@app.get("/api/tasks")
async def list_tasks():
    """列出所有任务"""
    _cleanup_old_tasks()
    tasks = [task.to_dict() for task in task_store.values()]
    tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return JSONResponse(content=tasks)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """查询单个任务状态"""
    task = task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if DEBUG:
        logger.debug("[task %s] Status query: state=%s, progress=%.2f", task_id, task.state.value, task.progress)
    return JSONResponse(content=task.to_dict())


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str):
    """获取任务转写结果"""
    task = task_store.get(task_id)
    if task is None or task.state != TaskState.completed:
        # 直接从磁盘加载
        output_file = OUTPUT_DIR / f"{task_id}.json"
        if output_file.exists():
            with open(output_file, "r", encoding="utf-8") as f:
                return JSONResponse(content=json.load(f))
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        raise HTTPException(status_code=400, detail=f"Task not completed (state={task.state.value})")

    # 优先从磁盘加载（result 可能是持久化标记或 None）
    output_file = OUTPUT_DIR / f"{task_id}.json"
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            return JSONResponse(content=json.load(f))

    # 回退到内存中的 result
    if task.result and "_loaded" not in task.result:
        return JSONResponse(content=task.result)

    raise HTTPException(status_code=404, detail="Result file not found")


@app.get("/api/tasks/{task_id}/audio")
async def get_task_audio(task_id: str):
    """获取任务对应的音频文件（用于波形播放）"""
    file_path = get_file_store().get_file_path(task_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")

    original_name = get_file_store().get_original_name(task_id) or "audio"
    suffix = file_path.suffix.lower()
    media_type = "audio/wav"
    if suffix == ".mp3":
        media_type = "audio/mpeg"
    elif suffix == ".m4a":
        media_type = "audio/mp4"
    elif suffix == ".flac":
        media_type = "audio/flac"

    if DEBUG:
        logger.debug("[task %s] Serving audio: %s", task_id, file_path)
    return FileResponse(path=str(file_path), media_type=media_type, filename=original_name)


@app.get("/api/files")
async def list_files():
    """列出所有文件记录"""
    files = get_file_store().list_files()
    return JSONResponse(content=files)


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
