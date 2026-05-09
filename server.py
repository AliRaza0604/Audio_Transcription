import os
import uuid
import subprocess
import time
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from faster_whisper import WhisperModel
from pymongo import MongoClient
import gridfs

PROCESSED_FOLDER = "processed"
os.makedirs(PROCESSED_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = [".mp3", ".wav", ".m4a", ".flac", ".aac"]
CHUNK_DURATION = 300  # 5 minutes

mongo = MongoClient("mongodb://localhost:27017")
db = mongo["transcription_db"]
jobs = db["jobs"]
fs = gridfs.GridFS(db)

model = WhisperModel("base", device="cpu", compute_type="int8")

app = FastAPI(title="Audio Transcription API", version="1.0")


# ── helpers ───────────────────────────────────────────────────────────────────

def validate_file(filename: str):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported format: {ext}")


def convert_to_wav(src: str, dst: str):
    subprocess.run(
        ["ffmpeg", "-i", src, "-ar", "16000", "-ac", "1", dst, "-y"],
        check=True, capture_output=True
    )


def get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True
    )
    return float(r.stdout.strip())


def split_into_chunks(path: str, job_id: str):
    duration = get_duration(path)
    if duration <= CHUNK_DURATION:
        return [(path, 0.0)]
    chunks = []
    start = 0.0
    i = 0
    while start < duration:
        chunk_path = os.path.join(PROCESSED_FOLDER, f"{job_id}_c{i}.wav")
        subprocess.run(
            ["ffmpeg", "-i", path, "-ss", str(start), "-t", str(CHUNK_DURATION),
             chunk_path, "-y"],
            check=True, capture_output=True
        )
        chunks.append((chunk_path, start))
        start += CHUNK_DURATION
        i += 1
    return chunks


def transcribe_file(path: str) -> dict:
    segments, info = model.transcribe(path, beam_size=5)
    return {
        "language": info.language,
        "duration": round(info.duration, 2),
        "segments": [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ]
    }


def transcribe_with_retry(path: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            return transcribe_file(path)
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(2)


# ── background worker ─────────────────────────────────────────────────────────

def process_job(job_id: str, file_id, original_ext: str):
    raw_tmp = os.path.join(PROCESSED_FOLDER, f"{job_id}_raw{original_ext}")
    processed_path = os.path.join(PROCESSED_FOLDER, f"{job_id}.wav")
    try:
        jobs.update_one({"job_id": job_id}, {"$set": {"status": "processing"}})

        # Pull raw file from GridFS to a temp path for FFmpeg
        with open(raw_tmp, "wb") as f:
            f.write(fs.get(file_id).read())

        convert_to_wav(raw_tmp, processed_path)
        os.remove(raw_tmp)

        chunks = split_into_chunks(processed_path, job_id)

        all_segments = []
        language = None
        total_duration = 0.0

        for chunk_path, offset in chunks:
            result = transcribe_with_retry(chunk_path)
            language = result["language"]
            total_duration += result["duration"]
            for seg in result["segments"]:
                all_segments.append({
                    "start": round(seg["start"] + offset, 2),
                    "end": round(seg["end"] + offset, 2),
                    "text": seg["text"]
                })
            if chunk_path != processed_path:
                os.remove(chunk_path)

        jobs.update_one({"job_id": job_id}, {"$set": {
            "status": "completed",
            "result": {
                "language": language,
                "duration": round(total_duration, 2),
                "segments": all_segments
            }
        }})

    except Exception as e:
        jobs.update_one({"job_id": job_id}, {"$set": {
            "status": "failed",
            "error": str(e)
        }})
    finally:
        for p in [raw_tmp, processed_path]:
            if os.path.exists(p):
                os.remove(p)


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Audio Transcription API Running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/transcribe")
async def transcribe(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    validate_file(file.filename)

    job_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1].lower()

    # Store uploaded file in GridFS
    file_id = fs.put(await file.read(), filename=f"{job_id}{ext}", content_type=file.content_type)

    jobs.insert_one({
        "job_id": job_id,
        "status": "pending",
        "filename": file.filename,
        "file_id": file_id,
        "created_at": datetime.now(timezone.utc),
        "result": None,
        "error": None
    })

    background_tasks.add_task(process_job, job_id, file_id, ext)

    return {"job_id": job_id, "status": "pending"}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = jobs.find_one({"job_id": job_id}, {"_id": 0, "job_id": 1, "status": 1, "error": 1})
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/result/{job_id}")
def get_result(job_id: str):
    job = jobs.find_one({"job_id": job_id}, {"_id": 0, "result": 1, "status": 1})
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "completed":
        raise HTTPException(400, f"Job status: {job['status']}")
    return {"job_id": job_id, "status": "completed", "result": job["result"]}
