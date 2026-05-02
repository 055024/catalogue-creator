"""
Product Catalogue Generator (backend-adapted)
=============================================
Same detection logic as the CLI version, but accepts/returns bytes
instead of file paths so it can be called from a web request.
"""

from io import BytesIO
import fitz
from PIL import Image, ImageDraw


DEFAULT_COLS = 4
DEFAULT_ROWS_PER_PAGE = 6
DEFAULT_SCALE = 5
DEFAULT_SKIP_PAGES = 1
DEFAULT_PAD_TOP = 30
DEFAULT_PAD_BOTTOM = 30
DEFAULT_DIVIDER_PX = 3
DEFAULT_DIVIDER_COLOR = (210, 210, 210)

SECTION_HEADERS = {
    "S M A R T WAT C H", "S M A R T W A T C H", "SMARTWATCH",
    "AUDIO", "HEADPHONES", "ACCESSORIES", "NECKBAND",
}


def extract_product_cells(pdf_bytes: bytes,
                          skip_pages: int = DEFAULT_SKIP_PAGES,
                          scale: int = DEFAULT_SCALE) -> list:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    cells = []

    for page_idx in range(skip_pages, len(doc)):
        page = doc[page_idx]
        page_h = page.rect.height

        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: b[1])

        mrp_y_bottoms = []
        for b in blocks:
            text = b[4].strip()
            if text.startswith("MRP.") or text.startswith("MRP "):
                mrp_y_bottoms.append(b[3])

        if not mrp_y_bottoms:
            continue

        usable_tops = []
        for b in blocks:
            txt = b[4].strip()
            if (txt
                    and txt not in SECTION_HEADERS
                    and not all(c in "•· \n" for c in txt)):
                usable_tops.append((b[1], txt))

        name_y_tops = []
        prev_mrp_y = 0.0
        for mrp_y in mrp_y_bottoms:
            candidates = [ty for ty, _ in usable_tops
                          if prev_mrp_y < ty < mrp_y]
            name_y_tops.append(candidates[0] if candidates else prev_mrp_y + 2)
            prev_mrp_y = mrp_y

        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        page_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        for y_name, y_mrp in zip(name_y_tops, mrp_y_bottoms):
            y0 = max(0.0, y_name - 12)
            y1 = min(page_h, y_mrp + 10)

            py0 = max(0, int(y0 * scale))
            py1 = min(page_img.height, int(y1 * scale))

            cell = page_img.crop((0, py0, page_img.width, py1))
            cells.append(cell)

    doc.close()
    return cells


def build_catalogue_pdf(cells: list,
                        cols: int = DEFAULT_COLS,
                        rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
                        pad_top: int = DEFAULT_PAD_TOP,
                        pad_bottom: int = DEFAULT_PAD_BOTTOM,
                        divider_px: int = DEFAULT_DIVIDER_PX,
                        divider_color: tuple = DEFAULT_DIVIDER_COLOR) -> bytes:
    if not cells:
        raise ValueError("No product cells extracted from the PDF.")

    cell_w = cells[0].width

    rows = [cells[i: i + cols] for i in range(0, len(cells), cols)]
    page_chunks = [rows[i: i + rows_per_page]
                   for i in range(0, len(rows), rows_per_page)]

    output_pages = []

    for chunk in page_chunks:
        total_w = cols * cell_w + (cols - 1) * divider_px
        row_heights = [max(c.height for c in row) for row in chunk]
        total_h = (pad_top
                   + sum(row_heights)
                   + (len(chunk) - 1) * divider_px
                   + pad_bottom)

        canvas = Image.new("RGB", (total_w, total_h), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        y_off = pad_top
        for row_idx, (row, rh) in enumerate(zip(chunk, row_heights)):
            x_off = 0
            for col_idx, cell in enumerate(row):
                y_pad = (rh - cell.height) // 2
                canvas.paste(cell, (x_off, y_off + y_pad))
                x_off += cell_w

                if col_idx < cols - 1:
                    draw.rectangle(
                        [x_off, y_off, x_off + divider_px - 1, y_off + rh],
                        fill=divider_color,
                    )
                    x_off += divider_px

            y_off += rh
            if row_idx < len(chunk) - 1:
                draw.rectangle(
                    [0, y_off, total_w, y_off + divider_px - 1],
                    fill=divider_color,
                )
                y_off += divider_px

        output_pages.append(canvas)

    buf = BytesIO()
    output_pages[0].save(
        buf,
        save_all=True,
        append_images=output_pages[1:],
        format="PDF",
        resolution=150,
    )
    return buf.getvalue()
