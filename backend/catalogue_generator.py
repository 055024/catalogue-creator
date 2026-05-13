"""
Product Catalogue Generator (backend-adapted, with dealer-price overlay)
========================================================================
Extracts product cards from a Noise-style PDF, optionally overlays a
dealer price beneath each MRP line, and appends extra pages listing
models that were in the price list but missing from the source PDF.
"""

import re
from io import BytesIO

import fitz
from PIL import Image, ImageDraw, ImageFont


DEFAULT_COLS = 4
DEFAULT_ROWS_PER_PAGE = 6
DEFAULT_SCALE = 5
DEFAULT_SKIP_PAGES = 1
DEFAULT_PAD_TOP = 30
DEFAULT_PAD_BOTTOM = 30
DEFAULT_DIVIDER_PX = 3
DEFAULT_DIVIDER_COLOR = (210, 210, 210)
DP_COLOR = (180, 30, 30)          # red-ish, distinct from MRP black
DP_FONT_SIZE = 28                 # px at scale=5
MISSING_TITLE_FONT_SIZE = 56
MISSING_NAME_FONT_SIZE = 32
MISSING_PRICE_FONT_SIZE = 30

# For manual entries that mimic the source-PDF cell layout
MANUAL_NAME_FONT_SIZE = 38
MANUAL_FEATURE_FONT_SIZE = 22
MANUAL_MRP_FONT_SIZE = 30
MANUAL_BULLET = "•"

SECTION_HEADERS = {
    "S M A R T WAT C H", "S M A R T W A T C H", "SMARTWATCH",
    "AUDIO", "HEADPHONES", "ACCESSORIES", "NECKBAND",
}

_FONT_PATH_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _load_font(size: int):
    for p in _FONT_PATH_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _clean_name(raw: str) -> str:
    s = raw.replace("\n", " ").replace("|", " ")
    return re.sub(r"\s+", " ", s).strip()


def extract_products(pdf_bytes: bytes,
                     skip_pages: int = DEFAULT_SKIP_PAGES,
                     scale: int = DEFAULT_SCALE) -> list:
    """
    Returns list of dicts:
        {name: str, image: PIL.Image, mrp_bottom_y: int}
    where mrp_bottom_y is the y-pixel inside the cell where the MRP line ends.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    products = []

    for page_idx in range(skip_pages, len(doc)):
        page = doc[page_idx]
        page_h = page.rect.height
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: b[1])

        mrp_bounds = []  # list of (y_top, y_bottom)
        for b in blocks:
            t = b[4].strip()
            if t.startswith("MRP.") or t.startswith("MRP "):
                mrp_bounds.append((b[1], b[3]))
        if not mrp_bounds:
            continue

        usable = []  # (y_top, text)
        for b in blocks:
            t = b[4].strip()
            if (t and t not in SECTION_HEADERS
                    and not all(c in "•· \n" for c in t)):
                usable.append((b[1], t))

        page_units = []
        prev_mrp_bottom = 0.0
        for mrp_top, mrp_bottom in mrp_bounds:
            cands = [(yt, txt) for yt, txt in usable
                     if prev_mrp_bottom < yt < mrp_top]
            if cands:
                y_name, name_raw = cands[0]
                name = _clean_name(name_raw)
            else:
                y_name = prev_mrp_bottom + 2
                name = "(unknown)"
            page_units.append((y_name, mrp_top, mrp_bottom, name))
            prev_mrp_bottom = mrp_bottom

        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        page_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        for y_name, mrp_top, mrp_bottom, name in page_units:
            y0 = max(0.0, y_name - 12)
            y1 = min(page_h, mrp_bottom + 10)
            py0 = max(0, int(y0 * scale))
            py1 = min(page_img.height, int(y1 * scale))
            cell = page_img.crop((0, py0, page_img.width, py1))
            mrp_bottom_y = int((mrp_bottom - y0) * scale)
            products.append({
                "name": name,
                "image": cell,
                "mrp_bottom_y": mrp_bottom_y,
            })

    doc.close()
    return products


def overlay_dealer_price(cell_img: Image.Image,
                         dealer_price: float,
                         mrp_bottom_y: int) -> Image.Image:
    """
    Returns a new image with 'DP. ₹X' drawn just below the MRP line.
    Extends the cell vertically so the new text doesn't overlap content.
    """
    font = _load_font(DP_FONT_SIZE)
    text = f"DP. ₹{int(dealer_price):,}"

    extra = DP_FONT_SIZE + 22
    new_h = cell_img.height + extra
    out = Image.new("RGB", (cell_img.width, new_h), (255, 255, 255))
    out.paste(cell_img, (0, 0))

    draw = ImageDraw.Draw(out)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    text_x = (cell_img.width - tw) // 2
    text_y = mrp_bottom_y + 8
    draw.text((text_x, text_y), text, fill=DP_COLOR, font=font)
    return out


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    if not words:
        return [""]
    lines, line = [], words[0]
    for w in words[1:]:
        trial = f"{line} {w}"
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            lines.append(line)
            line = w
    lines.append(line)
    return lines


def build_manual_cell(name: str,
                      image_bytes: bytes,
                      features_raw: str,
                      mrp: float,
                      dealer_price: float,
                      cell_w: int) -> Image.Image:
    """
    Construct a catalogue-style cell from manually-entered data:
      name (top, bold, centered)
      product image (centered)
      bullet features (left-padded, one per non-empty input line)
      MRP. ₹X (centered)
      DP. ₹Y (red, centered, below MRP)
    Cell width matches the catalogue cells; height grows with content.
    """
    name_font = _load_font(MANUAL_NAME_FONT_SIZE)
    feat_font = _load_font(MANUAL_FEATURE_FONT_SIZE)
    mrp_font = _load_font(MANUAL_MRP_FONT_SIZE)
    dp_font = _load_font(DP_FONT_SIZE)

    pad_x = 18
    pad_y = 22
    inner_w = cell_w - 2 * pad_x

    tmp = Image.new("RGB", (10, 10))
    td = ImageDraw.Draw(tmp)

    # Name lines
    name_lines = _wrap_text(td, name, name_font, inner_w) if name else []
    name_line_h = MANUAL_NAME_FONT_SIZE + 8
    name_block_h = len(name_lines) * name_line_h

    # Image (resize to fit cell width)
    img_block = None
    if image_bytes:
        try:
            from PIL import ImageOps
            img = Image.open(BytesIO(image_bytes))
            img = ImageOps.exif_transpose(img).convert("RGB")
            max_w = inner_w
            max_h = int(cell_w * 0.95)
            ratio = min(max_w / img.width, max_h / img.height, 1.0)
            new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
            img_block = img.resize(new_size, Image.LANCZOS)
        except Exception:
            img_block = None

    img_h = img_block.height + 14 if img_block else 0

    # Features (one bullet per non-empty line in user input)
    feature_lines = []
    for raw_line in (features_raw or "").splitlines():
        s = raw_line.strip().lstrip("-•·*").strip()
        if not s:
            continue
        wrapped = _wrap_text(td, s, feat_font, inner_w - 18)
        for i, w in enumerate(wrapped):
            feature_lines.append(("bullet" if i == 0 else "cont", w))
    feat_line_h = MANUAL_FEATURE_FONT_SIZE + 6
    feat_block_h = len(feature_lines) * feat_line_h
    if feature_lines:
        feat_block_h += 6

    # MRP + DP
    mrp_text = f"MRP. ₹{int(mrp):,}" if mrp else ""
    dp_text = f"DP. ₹{int(dealer_price):,}" if dealer_price else ""
    price_block_h = 0
    if mrp_text:
        price_block_h += MANUAL_MRP_FONT_SIZE + 10
    if dp_text:
        price_block_h += DP_FONT_SIZE + 12

    total_h = pad_y + name_block_h + img_h + feat_block_h + 8 + price_block_h + pad_y

    cell = Image.new("RGB", (cell_w, total_h), (255, 255, 255))
    d = ImageDraw.Draw(cell)
    y = pad_y

    for ln in name_lines:
        tw = d.textlength(ln, font=name_font)
        d.text(((cell_w - tw) // 2, y), ln, fill=(20, 20, 20), font=name_font)
        y += name_line_h

    if img_block:
        x = (cell_w - img_block.width) // 2
        cell.paste(img_block, (x, y))
        y += img_block.height + 14

    if feature_lines:
        y += 4
        bullet_x = pad_x + 4
        text_x = bullet_x + 18
        for kind, txt in feature_lines:
            if kind == "bullet":
                d.text((bullet_x, y), MANUAL_BULLET, fill=(80, 80, 80), font=feat_font)
            d.text((text_x, y), txt, fill=(40, 40, 40), font=feat_font)
            y += feat_line_h
        y += 4

    if mrp_text:
        tw = d.textlength(mrp_text, font=mrp_font)
        d.text(((cell_w - tw) // 2, y), mrp_text, fill=(0, 0, 0), font=mrp_font)
        y += MANUAL_MRP_FONT_SIZE + 10
    if dp_text:
        tw = d.textlength(dp_text, font=dp_font)
        d.text(((cell_w - tw) // 2, y), dp_text, fill=DP_COLOR, font=dp_font)

    return cell


def build_missing_models_cells(missing_items: list,
                               cell_w: int) -> list:
    """One text-only cell per missing model: name + DP centered."""
    name_font = _load_font(MISSING_NAME_FONT_SIZE)
    price_font = _load_font(MISSING_PRICE_FONT_SIZE)

    pad_x = 16
    pad_y = 18
    line_gap = 8
    inner_w = cell_w - 2 * pad_x

    tmp = Image.new("RGB", (10, 10))
    td = ImageDraw.Draw(tmp)

    cells = []
    for it in missing_items:
        name = it["model"]
        price_text = f"DP. ₹{int(it['dealer_price']):,}"

        name_lines = _wrap_text(td, name, name_font, inner_w)
        line_h = MISSING_NAME_FONT_SIZE + line_gap
        name_h = len(name_lines) * line_h
        price_h = MISSING_PRICE_FONT_SIZE + 4
        total_h = pad_y + name_h + 14 + price_h + pad_y

        cell = Image.new("RGB", (cell_w, total_h), (255, 255, 255))
        d = ImageDraw.Draw(cell)
        y = pad_y
        for ln in name_lines:
            tw = d.textlength(ln, font=name_font)
            d.text(((cell_w - tw) // 2, y), ln, fill=(20, 20, 20), font=name_font)
            y += line_h
        y += 6
        tw = d.textlength(price_text, font=price_font)
        d.text(((cell_w - tw) // 2, y), price_text, fill=DP_COLOR, font=price_font)
        cells.append(cell)
    return cells


def build_missing_title_image(total_w: int) -> Image.Image:
    title_font = _load_font(MISSING_TITLE_FONT_SIZE)
    sub_font = _load_font(MISSING_NAME_FONT_SIZE)
    title = "Additional Models"
    sub = "Available on order"

    pad = 30
    h = pad + MISSING_TITLE_FONT_SIZE + 14 + MISSING_NAME_FONT_SIZE + pad
    img = Image.new("RGB", (total_w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    tw = d.textlength(title, font=title_font)
    d.text(((total_w - tw) // 2, pad), title, fill=(20, 20, 20), font=title_font)
    sw = d.textlength(sub, font=sub_font)
    d.text(((total_w - sw) // 2, pad + MISSING_TITLE_FONT_SIZE + 14),
           sub, fill=(120, 120, 120), font=sub_font)
    return img


def build_missing_models_pages(missing_items: list,
                               cell_w: int,
                               cols: int,
                               rows_per_page: int,
                               pad_top: int = DEFAULT_PAD_TOP,
                               pad_bottom: int = DEFAULT_PAD_BOTTOM,
                               divider_px: int = DEFAULT_DIVIDER_PX,
                               divider_color: tuple = DEFAULT_DIVIDER_COLOR
                               ) -> list:
    """Returns full-page PIL images for the additional-models section."""
    if not missing_items:
        return []
    cells = build_missing_models_cells(missing_items, cell_w)
    total_w = cols * cell_w + (cols - 1) * divider_px

    rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
    page_chunks = [rows[i:i + rows_per_page]
                   for i in range(0, len(rows), rows_per_page)]

    pages = []
    for pi, chunk in enumerate(page_chunks):
        row_heights = [max(c.height for c in row) for row in chunk]
        title_img = build_missing_title_image(total_w) if pi == 0 else None
        title_h = (title_img.height + 10) if title_img else 0

        body_h = sum(row_heights) + (len(chunk) - 1) * divider_px
        total_h = pad_top + title_h + body_h + pad_bottom

        canvas = Image.new("RGB", (total_w, total_h), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        y_off = pad_top
        if title_img is not None:
            canvas.paste(title_img, (0, y_off))
            y_off += title_img.height + 10

        for row_idx, (row, rh) in enumerate(zip(chunk, row_heights)):
            x_off = 0
            for col_idx, c in enumerate(row):
                y_pad = (rh - c.height) // 2
                canvas.paste(c, (x_off, y_off + y_pad))
                x_off += cell_w
                if col_idx < cols - 1:
                    draw.rectangle(
                        [x_off, y_off, x_off + divider_px - 1, y_off + rh],
                        fill=divider_color)
                    x_off += divider_px
            y_off += rh
            if row_idx < len(chunk) - 1:
                draw.rectangle(
                    [0, y_off, total_w, y_off + divider_px - 1],
                    fill=divider_color)
                y_off += divider_px
        pages.append(canvas)
    return pages


def assemble_pdf(cells: list,
                 cols: int = DEFAULT_COLS,
                 rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
                 pad_top: int = DEFAULT_PAD_TOP,
                 pad_bottom: int = DEFAULT_PAD_BOTTOM,
                 divider_px: int = DEFAULT_DIVIDER_PX,
                 divider_color: tuple = DEFAULT_DIVIDER_COLOR,
                 extra_pages: list = None) -> bytes:
    """Lay out cells in a grid; append extra full-page images (e.g. missing-models pages)."""
    if not cells and not extra_pages:
        raise ValueError("Nothing to assemble.")

    output_pages = []
    if cells:
        cell_w = cells[0].width
        rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
        page_chunks = [rows[i:i + rows_per_page]
                       for i in range(0, len(rows), rows_per_page)]

        for chunk in page_chunks:
            total_w = cols * cell_w + (cols - 1) * divider_px
            row_heights = [max(c.height for c in row) for row in chunk]
            total_h = (pad_top + sum(row_heights)
                       + (len(chunk) - 1) * divider_px + pad_bottom)
            canvas = Image.new("RGB", (total_w, total_h), (255, 255, 255))
            draw = ImageDraw.Draw(canvas)

            y_off = pad_top
            for row_idx, (row, rh) in enumerate(zip(chunk, row_heights)):
                x_off = 0
                for col_idx, c in enumerate(row):
                    y_pad = (rh - c.height) // 2
                    canvas.paste(c, (x_off, y_off + y_pad))
                    x_off += cell_w
                    if col_idx < cols - 1:
                        draw.rectangle(
                            [x_off, y_off, x_off + divider_px - 1, y_off + rh],
                            fill=divider_color)
                        x_off += divider_px
                y_off += rh
                if row_idx < len(chunk) - 1:
                    draw.rectangle(
                        [0, y_off, total_w, y_off + divider_px - 1],
                        fill=divider_color)
                    y_off += divider_px
            output_pages.append(canvas)

    if extra_pages:
        output_pages.extend(extra_pages)

    buf = BytesIO()
    output_pages[0].save(
        buf, save_all=True, append_images=output_pages[1:],
        format="PDF", resolution=150)
    return buf.getvalue()


# Backwards-compatible thin wrappers
def extract_product_cells(pdf_bytes, skip_pages=DEFAULT_SKIP_PAGES, scale=DEFAULT_SCALE):
    return [p["image"] for p in extract_products(pdf_bytes, skip_pages, scale)]


def build_catalogue_pdf(cells, cols=DEFAULT_COLS, rows_per_page=DEFAULT_ROWS_PER_PAGE,
                        pad_top=DEFAULT_PAD_TOP, pad_bottom=DEFAULT_PAD_BOTTOM,
                        divider_px=DEFAULT_DIVIDER_PX,
                        divider_color=DEFAULT_DIVIDER_COLOR):
    return assemble_pdf(cells, cols, rows_per_page, pad_top, pad_bottom,
                        divider_px, divider_color)
