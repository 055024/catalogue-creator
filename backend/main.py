import os
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from catalogue_generator import (
    extract_products,
    overlay_dealer_price,
    build_missing_models_pages,
    assemble_pdf,
    DEFAULT_COLS,
    DEFAULT_ROWS_PER_PAGE,
    DEFAULT_SCALE,
    DEFAULT_SKIP_PAGES,
    DEFAULT_DIVIDER_PX,
)
from matcher import parse_price_list, map_catalog, missing_items

app = FastAPI(title="Catalogue Generator API")

allowed = os.environ.get("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in allowed.split(",")] if allowed != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Product-Count", "X-Matched-Count",
                    "X-Unmatched-Count", "X-Missing-Models-Count"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "catalogue-generator"}


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    price_list: Optional[UploadFile] = File(None),
    cols: int = Form(DEFAULT_COLS),
    rows_per_page: int = Form(DEFAULT_ROWS_PER_PAGE),
    scale: int = Form(DEFAULT_SCALE),
    skip_pages: int = Form(DEFAULT_SKIP_PAGES),
    divider: int = Form(DEFAULT_DIVIDER_PX),
    match_threshold: int = Form(72),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Empty PDF file.")

    price_items = []
    if price_list is not None and price_list.filename:
        if not price_list.filename.lower().endswith((".xlsx", ".xls")):
            raise HTTPException(400, "Price list must be an .xlsx file.")
        xlsx_bytes = await price_list.read()
        if xlsx_bytes:
            try:
                price_items = parse_price_list(xlsx_bytes)
            except Exception as e:
                raise HTTPException(400, f"Failed to parse price list: {e}")

    try:
        products = extract_products(
            pdf_bytes=pdf_bytes,
            skip_pages=skip_pages,
            scale=scale,
        )
    except Exception as e:
        raise HTTPException(400, f"Failed to parse PDF: {e}")

    if not products:
        raise HTTPException(
            422,
            "No products detected. Try adjusting skip_pages or check the PDF format.",
        )

    matched_count = 0
    if price_items:
        names = [p["name"] for p in products]
        results, matched_keys = map_catalog(names, price_items, threshold=match_threshold)
        for prod, (item, _score) in zip(products, results):
            if item is not None:
                prod["image"] = overlay_dealer_price(
                    prod["image"], item["dealer_price"], prod["mrp_bottom_y"])
                matched_count += 1
        miss_items = missing_items(price_items, matched_keys)
    else:
        miss_items = []

    cells = [p["image"] for p in products]
    extra_pages = []
    if miss_items:
        cell_w = cells[0].width
        extra_pages = build_missing_models_pages(
            miss_items, cell_w, cols=cols, rows_per_page=rows_per_page,
            divider_px=divider)

    try:
        out_bytes = assemble_pdf(
            cells=cells,
            cols=cols,
            rows_per_page=rows_per_page,
            divider_px=divider,
            extra_pages=extra_pages,
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to build PDF: {e}")

    stem = os.path.splitext(file.filename)[0]
    out_name = f"{stem}_catalogue.pdf"

    return Response(
        content=out_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Product-Count": str(len(products)),
            "X-Matched-Count": str(matched_count),
            "X-Unmatched-Count": str(len(products) - matched_count) if price_items else "0",
            "X-Missing-Models-Count": str(len(miss_items)),
        },
    )
