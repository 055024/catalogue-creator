import json
import os
from typing import Optional, List

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from catalogue_generator import (
    extract_products,
    overlay_dealer_price,
    build_missing_models_pages,
    build_manual_cell,
    assemble_pdf,
    DEFAULT_COLS,
    DEFAULT_ROWS_PER_PAGE,
    DEFAULT_SCALE,
    DEFAULT_SKIP_PAGES,
    DEFAULT_DIVIDER_PX,
)
from matcher import parse_price_list, map_catalog, missing_items, normalize

app = FastAPI(title="Catalogue Generator API")

allowed = os.environ.get("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in allowed.split(",")] if allowed != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Product-Count", "X-Matched-Count",
                    "X-Unmatched-Count", "X-Missing-Models-Count",
                    "X-Manual-Cells-Count"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "catalogue-generator"}


def _read_pdf(file: UploadFile) -> bytes:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF file.")
    return None  # placeholder, FastAPI requires async read


def _read_xlsx(file: UploadFile) -> bytes:
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Price list must be an .xlsx file.")
    return None


@app.post("/preview")
async def preview(
    file: UploadFile = File(...),
    price_list: UploadFile = File(...),
    skip_pages: int = Form(DEFAULT_SKIP_PAGES),
    match_threshold: int = Form(100),
):
    """
    Lightweight peek: returns the list of products detected in the PDF
    and the list of price-list models that didn't match any product.
    Used by the frontend to populate the manual-entry section.
    Renders at scale=1 to stay cheap.
    """
    _read_pdf(file)
    _read_xlsx(price_list)

    pdf_bytes = await file.read()
    xlsx_bytes = await price_list.read()
    if not pdf_bytes or not xlsx_bytes:
        raise HTTPException(400, "Empty file.")

    try:
        price_items = parse_price_list(xlsx_bytes)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse price list: {e}")

    try:
        products = extract_products(pdf_bytes=pdf_bytes, skip_pages=skip_pages, scale=1)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse PDF: {e}")

    if not products:
        raise HTTPException(
            422,
            "No products detected. Try adjusting skip_pages or check the PDF format.",
        )

    names = [p["name"] for p in products]
    _results, matched_keys = map_catalog(names, price_items, threshold=match_threshold)
    miss = missing_items(price_items, matched_keys)

    return {
        "product_count": len(products),
        "matched_count": sum(1 for k in matched_keys if k),
        "missing_models": [
            {"model": it["model"], "dealer_price": it["dealer_price"],
             "category": it["category"], "normalized": it["normalized"]}
            for it in miss
        ],
    }


def _parse_manual_entries(raw_json: Optional[str]) -> list:
    if not raw_json:
        return []
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"manual_entries JSON invalid: {e}")
    if not isinstance(data, list):
        raise HTTPException(400, "manual_entries must be a JSON list.")
    out = []
    for i, e in enumerate(data):
        if not isinstance(e, dict) or "model" not in e:
            raise HTTPException(400, f"manual_entries[{i}] missing 'model'.")
        out.append({
            "model": str(e.get("model", "")).strip(),
            "features": str(e.get("features", "")),
            "mrp": float(e["mrp"]) if e.get("mrp") not in (None, "", 0) else 0.0,
            "image_index": e.get("image_index"),
        })
    return out


@app.post("/generate")
async def generate(
    file: UploadFile = File(...),
    price_list: Optional[UploadFile] = File(None),
    manual_entries: Optional[str] = Form(None),
    manual_images: List[UploadFile] = File(default=[]),
    cols: int = Form(DEFAULT_COLS),
    rows_per_page: int = Form(DEFAULT_ROWS_PER_PAGE),
    scale: int = Form(DEFAULT_SCALE),
    skip_pages: int = Form(DEFAULT_SKIP_PAGES),
    divider: int = Form(DEFAULT_DIVIDER_PX),
    match_threshold: int = Form(100),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF file.")

    scale = max(1, min(scale, 6))

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

    entries = _parse_manual_entries(manual_entries) if price_items else []
    image_blobs = []
    for img_file in manual_images:
        if img_file and img_file.filename:
            image_blobs.append(await img_file.read())
        else:
            image_blobs.append(b"")

    try:
        products = extract_products(pdf_bytes=pdf_bytes, skip_pages=skip_pages, scale=scale)
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
        miss = missing_items(price_items, matched_keys)
    else:
        miss = []

    cells = [p["image"] for p in products]
    cell_w = cells[0].width if cells else 144 * scale

    # Build manual cells; collect normalized keys that got a manual cell so
    # they're removed from the text-only "Additional Models" section.
    manual_cells = []
    manual_normalized = set()
    price_lookup = {it["normalized"]: it for it in price_items}

    for e in entries:
        model_name = e["model"]
        norm_key = normalize(model_name)
        item = price_lookup.get(norm_key)
        dp = item["dealer_price"] if item else 0.0
        image_bytes = b""
        idx = e.get("image_index")
        if isinstance(idx, int) and 0 <= idx < len(image_blobs):
            image_bytes = image_blobs[idx]
        if not (image_bytes or e["features"] or e["mrp"]):
            continue
        try:
            cell = build_manual_cell(
                name=model_name,
                image_bytes=image_bytes,
                features_raw=e["features"],
                mrp=e["mrp"],
                dealer_price=dp,
                cell_w=cell_w,
            )
            manual_cells.append(cell)
            manual_normalized.add(norm_key)
        except Exception as ex:
            raise HTTPException(400, f"Failed building manual cell for '{model_name}': {ex}")

    cells.extend(manual_cells)

    leftover_miss = [it for it in miss if it["normalized"] not in manual_normalized]
    extra_pages = []
    if leftover_miss:
        extra_pages = build_missing_models_pages(
            leftover_miss, cell_w, cols=cols, rows_per_page=rows_per_page,
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
            "X-Missing-Models-Count": str(len(leftover_miss)),
            "X-Manual-Cells-Count": str(len(manual_cells)),
        },
    )
