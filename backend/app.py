"""WinASR FastAPI 服务"""

import os
import json
import tempfile
import logging
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.pipeline import get_pipeline
from backend.schema import TranscriptionResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(
    title="WinASR",
    description="离线语音转文字 + 声纹识别服务",
    version="0.1.0",
)

# 输出目录
OUTPUT_DIR = Path(os.getenv("WINASR_OUTPUT_DIR", Path.home() / "WinASR" / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def root():
    return {
        "service": "WinASR",
        "version": "0.1.0",
        "endpoints": {
            "POST /transcribe": "上传音频文件进行转写",
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
    上传音频文件, 返回转写结果

    返回包含: 每个语音段的时间戳、文本、说话人ID、情绪标签、语种
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
        logging.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
