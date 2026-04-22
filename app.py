from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageDraw
import shutil
import os
import json
import math

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")


def safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        else:
            keep.append("-")
    cleaned = "".join(keep).strip("-")
    return cleaned or "upload.bin"


def make_url(filename: str) -> str:
    return f"{BASE_URL}/files/{filename}" if BASE_URL else f"/files/{filename}"


def polar_point(cx, cy, r, deg):
    rad = math.radians(deg)
    return (cx + math.cos(rad) * r, cy + math.sin(rad) * r)


def build_gear_polygon(cx, cy, diameter, notch_count, notch_width_deg, notch_depth_ratio, steps_per_arc=12):
    r_outer = diameter / 2
    r_inner = r_outer * (1 - notch_depth_ratio)
    half_notch = notch_width_deg / 2

    centers = [-90 + (360 / notch_count) * i for i in range(notch_count)]

    points = []

    def append_outer_arc(start_deg, end_deg):
        delta = end_deg - start_deg
        while delta < 0:
          delta += 360
        steps = max(2, int(steps_per_arc * delta / 30))
        for i in range(1, steps + 1):
            t = i / steps
            deg = start_deg + delta * t
            points.append(polar_point(cx, cy, r_outer, deg))

    first_left_deg = centers[0] - half_notch
    points.append(polar_point(cx, cy, r_outer, first_left_deg))

    previous_right_deg = None

    for i, center_deg in enumerate(centers):
        left_deg = center_deg - half_notch
        right_deg = center_deg + half_notch

        if i > 0:
            append_outer_arc(previous_right_deg, left_deg)

        points.append(polar_point(cx, cy, r_inner, left_deg))
        points.append(polar_point(cx, cy, r_inner, right_deg))
        points.append(polar_point(cx, cy, r_outer, right_deg))

        previous_right_deg = right_deg

    append_outer_arc(previous_right_deg, first_left_deg)

    return points


def render_final_png(source_path: Path, output_path: Path, mask_info: dict, export_size: int = 1600):
    img = Image.open(source_path).convert("RGBA")

    canvas = Image.new("RGBA", (export_size, export_size), (0, 0, 0, 0))

    source_x_percent = mask_info["sourceImageXPercent"]
    source_y_percent = mask_info["sourceImageYPercent"]
    scale_x_percent = mask_info["sourceImageScaleXPercent"]
    scale_y_percent = mask_info["sourceImageScaleYPercent"]
    rotation_deg = mask_info["sourceImageRotationDeg"]

    crop_center_x_percent = mask_info["cropCenterXPercent"]
    crop_center_y_percent = mask_info["cropCenterYPercent"]
    crop_diameter_percent = mask_info["cropDiameterPercent"]

    notch_count = mask_info["notchCount"]
    notch_width_deg = mask_info["notchWidthDeg"]
    notch_depth_ratio = mask_info["notchDepthRatio"]

    center_x = export_size * (source_x_percent / 100.0)
    center_y = export_size * (source_y_percent / 100.0)

    crop_cx = export_size * (crop_center_x_percent / 100.0)
    crop_cy = export_size * (crop_center_y_percent / 100.0)
    crop_diameter = export_size * (crop_diameter_percent / 100.0)

    base_w = export_size
    base_h = export_size * (img.height / img.width)

    draw_w = max(1, round(base_w * (scale_x_percent / 100.0)))
    draw_h = max(1, round(base_h * (scale_y_percent / 100.0)))

    resized = img.resize((draw_w, draw_h), Image.LANCZOS)

    rotated = resized.rotate(-rotation_deg, expand=True, resample=Image.BICUBIC)

    paste_x = round(center_x - rotated.width / 2)
    paste_y = round(center_y - rotated.height / 2)

    canvas.alpha_composite(rotated, (paste_x, paste_y))

    mask = Image.new("L", (export_size, export_size), 0)
    draw = ImageDraw.Draw(mask)

    polygon = build_gear_polygon(
        crop_cx,
        crop_cy,
        crop_diameter,
        notch_count,
        notch_width_deg,
        notch_depth_ratio
    )
    draw.polygon(polygon, fill=255)

    final_img = Image.new("RGBA", (export_size, export_size), (0, 0, 0, 0))
    final_img.paste(canvas, (0, 0), mask)

    final_img.save(output_path, format="PNG")


@app.get("/")
def root():
    return {"ok": True}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    upload_id = uuid4().hex[:12]
    original_name = safe_filename(file.filename)
    stored_name = f"{upload_id}-{original_name}"
    destination = UPLOAD_DIR / stored_name

    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return JSONResponse(
        {
            "upload_id": upload_id,
            "filename": stored_name,
            "original_filename": file.filename,
            "url": make_url(stored_name),
        }
    )


@app.post("/process")
async def process_file(payload: dict):
    upload_id = payload.get("upload_id")
    source_filename = payload.get("source_filename")
    mask_info = payload.get("mask_info")

    if not upload_id or not source_filename or not mask_info:
        raise HTTPException(status_code=400, detail="Missing upload_id, source_filename, or mask_info")

    source_path = UPLOAD_DIR / source_filename
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Source file not found")

    original_stem = Path(source_filename).stem
    final_name = f"{upload_id}-{original_stem}-final.png"
    final_path = UPLOAD_DIR / final_name

    try:
        if isinstance(mask_info, str):
            mask_info = json.loads(mask_info)
        render_final_png(source_path, final_path, mask_info)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    return JSONResponse(
        {
            "upload_id": upload_id,
            "final_filename": final_name,
            "final_url": make_url(final_name),
        }
    )


@app.get("/files/{filename}")
def get_file(filename: str):
    path = UPLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)