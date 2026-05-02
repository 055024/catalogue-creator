import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from catalogue_generator import (
    extract_product_cells,
    build_catalogue_pdf,
    DEFAULT_COLS,
    DEFAULT_ROWS_PER_PAGE,
    DEFAULT_SCALE,
    DEFAULT_SKIP_PAGES,
    DEFAULT_DIVIDER_PX,
)

app = FastAPI(title="Catalogue Generator API")

# CORS — set ALLOWED_ORIGINS env var on Render to your Netlify URL
# (comma-separated). Defaults to "*" so local dev works out of the box.
allowed = os.environ.get("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in allowed.split(",")] if allowed != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "catalogue-generator"}


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    cols: int = Form(DEFAULT_COLS),
    rows_per_page: int = Form(DEFAULT_ROWS_PER_PAGE),
    scale: int = Form(DEFAULT_SCALE),
    skip_pages: int = Form(DEFAULT_SKIP_PAGES),
    divider: int = Form(DEFAULT_DIVIDER_PX),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        cells = extract_product_cells(
            pdf_bytes=pdf_bytes,
            skip_pages=skip_pages,
            scale=scale,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")

    if not cells:
        raise HTTPException(
            status_code=422,
            detail="No products detected. Try adjusting skip_pages or check the PDF format.",
        )

    try:
        out_bytes = build_catalogue_pdf(
            cells=cells,
            cols=cols,
            rows_per_page=rows_per_page,
            divider_px=divider,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build PDF: {e}")

    stem = os.path.splitext(file.filename)[0]
    out_name = f"{stem}_catalogue.pdf"

    return Response(
        content=out_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Product-Count": str(len(cells)),
        },
    )
