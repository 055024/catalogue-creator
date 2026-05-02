"""
Product Catalogue Generator
============================
Extracts individual product cards from a Noise-style product PDF
(two-column layout with image + description per product, ending with "MRP. ₹XXXX")
and assembles them into a clean 4-column grid PDF.

Requirements:
    pip install pymupdf pillow

Usage:
    python catalogue_generator.py input.pdf
    python catalogue_generator.py input.pdf --output my_catalogue.pdf
    python catalogue_generator.py input.pdf --cols 3 --rows-per-page 5 --scale 4
    python catalogue_generator.py input.pdf --skip-pages 1
"""

import argparse
import sys
import os
import fitz                        # PyMuPDF
from PIL import Image, ImageDraw


# ─────────────────────────────────────────────
#  CONFIGURATION  (can also be set via CLI)
# ─────────────────────────────────────────────
DEFAULT_COLS = 4          # products per row
DEFAULT_ROWS_PER_PAGE = 6 # rows per output page
DEFAULT_SCALE = 5         # render DPI multiplier (page is 144 pt → 720 px per col at 5×)
DEFAULT_SKIP_PAGES = 1    # pages to skip from the start (intro / about-us pages)
DEFAULT_PAD_TOP = 30      # px whitespace at top of each output page
DEFAULT_PAD_BOTTOM = 30   # px whitespace at bottom of each output page
DEFAULT_DIVIDER_PX = 3    # thickness of grid dividers in px
DEFAULT_DIVIDER_COLOR = (210, 210, 210)   # RGB grey

# Section-header text to IGNORE when looking for the start of a product name
SECTION_HEADERS = {
    "S M A R T WAT C H", "S M A R T W A T C H", "SMARTWATCH",
    "AUDIO", "HEADPHONES", "ACCESSORIES", "NECKBAND",
}


# ─────────────────────────────────────────────
#  CORE LOGIC
# ─────────────────────────────────────────────

def extract_product_cells(pdf_path: str,
                           skip_pages: int = DEFAULT_SKIP_PAGES,
                           scale: int = DEFAULT_SCALE) -> list:
    """
    Open *pdf_path* and return a list of PIL Images, one per product.

    Detection strategy
    ------------------
    Every product in this catalogue style ends with a line that starts with
    "MRP." or "MRP ".  We sort all text blocks by vertical position, collect
    the Y-bottom of every MRP line, and use those as hard product boundaries.
    The top of each product is the first non-header text block that appears
    after the previous product's MRP line.
    """
    doc = fitz.open(pdf_path)
    cells = []

    for page_idx in range(skip_pages, len(doc)):
        page = doc[page_idx]
        page_h = page.rect.height

        # ── 1. Extract and sort text blocks ──────────────────────────────
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda b: b[1])          # sort by y-top

        # ── 2. Find MRP positions (product end anchors) ───────────────────
        mrp_y_bottoms = []
        for b in blocks:
            text = b[4].strip()
            if text.startswith("MRP.") or text.startswith("MRP "):
                mrp_y_bottoms.append(b[3])        # y-bottom of the MRP line

        if not mrp_y_bottoms:
            print(f"  [page {page_idx + 1}] no MRP lines found – skipped")
            continue

        # ── 3. Find product name tops (product start anchors) ─────────────
        # Collect y-tops of all non-header, non-bullet text blocks
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

        # ── 4. Render the full page once at the requested scale ───────────
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        page_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # ── 5. Crop one cell per product ──────────────────────────────────
        for y_name, y_mrp in zip(name_y_tops, mrp_y_bottoms):
            y0 = max(0.0, y_name - 12)          # small padding above name
            y1 = min(page_h, y_mrp + 10)        # small padding below MRP

            py0 = max(0, int(y0 * scale))
            py1 = min(page_img.height, int(y1 * scale))

            cell = page_img.crop((0, py0, page_img.width, py1))
            cells.append(cell)

        print(f"  [page {page_idx + 1}] {len(mrp_y_bottoms)} products extracted")

    doc.close()
    return cells


def build_catalogue_pdf(cells: list,
                         output_path: str,
                         cols: int = DEFAULT_COLS,
                         rows_per_page: int = DEFAULT_ROWS_PER_PAGE,
                         pad_top: int = DEFAULT_PAD_TOP,
                         pad_bottom: int = DEFAULT_PAD_BOTTOM,
                         divider_px: int = DEFAULT_DIVIDER_PX,
                         divider_color: tuple = DEFAULT_DIVIDER_COLOR):
    """
    Arrange *cells* in a grid of *cols* columns and save to *output_path*.
    """
    if not cells:
        print("No product cells to assemble – aborting.")
        return

    cell_w = cells[0].width          # all cells share the same width

    # Split into rows, then into page chunks
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
                # Centre shorter cells vertically within the row
                y_pad = (rh - cell.height) // 2
                canvas.paste(cell, (x_off, y_off + y_pad))
                x_off += cell_w

                # Vertical divider (except after last column)
                if col_idx < cols - 1:
                    draw.rectangle(
                        [x_off, y_off, x_off + divider_px - 1, y_off + rh],
                        fill=divider_color,
                    )
                    x_off += divider_px

            y_off += rh
            # Horizontal divider (except after last row)
            if row_idx < len(chunk) - 1:
                draw.rectangle(
                    [0, y_off, total_w, y_off + divider_px - 1],
                    fill=divider_color,
                )
                y_off += divider_px

        output_pages.append(canvas)

    # Save all pages as a single PDF
    output_pages[0].save(
        output_path,
        save_all=True,
        append_images=output_pages[1:],
        format="PDF",
        resolution=150,
    )
    print(f"\n✓  Saved {len(output_pages)}-page catalogue → {output_path}")
    print(f"   {len(cells)} products · {cols} columns · {rows_per_page} rows/page")


# ─────────────────────────────────────────────
#  CLI ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a product-grid PDF from a Noise-style catalogue PDF."
    )
    parser.add_argument("input", help="Path to the source PDF")
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output PDF path (default: <input_stem>_catalogue.pdf)",
    )
    parser.add_argument(
        "--cols", type=int, default=DEFAULT_COLS,
        help=f"Products per row (default: {DEFAULT_COLS})",
    )
    parser.add_argument(
        "--rows-per-page", type=int, default=DEFAULT_ROWS_PER_PAGE,
        help=f"Grid rows per output page (default: {DEFAULT_ROWS_PER_PAGE})",
    )
    parser.add_argument(
        "--scale", type=int, default=DEFAULT_SCALE,
        help=f"Render scale multiplier, higher = sharper (default: {DEFAULT_SCALE})",
    )
    parser.add_argument(
        "--skip-pages", type=int, default=DEFAULT_SKIP_PAGES,
        help=f"Pages to skip from the beginning, e.g. intro pages (default: {DEFAULT_SKIP_PAGES})",
    )
    parser.add_argument(
        "--divider", type=int, default=DEFAULT_DIVIDER_PX,
        help=f"Grid divider thickness in pixels (default: {DEFAULT_DIVIDER_PX})",
    )

    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found – {args.input}")
        sys.exit(1)

    if args.output is None:
        stem = os.path.splitext(args.input)[0]
        args.output = f"{stem}_catalogue.pdf"

    print(f"Source  : {args.input}")
    print(f"Output  : {args.output}")
    print(f"Settings: {args.cols} cols · {args.rows_per_page} rows/page · "
          f"scale={args.scale} · skip={args.skip_pages} pages\n")

    print("Extracting products …")
    cells = extract_product_cells(
        pdf_path=args.input,
        skip_pages=args.skip_pages,
        scale=args.scale,
    )

    if not cells:
        print("No products found. Check --skip-pages or verify the PDF format.")
        sys.exit(1)

    print(f"\nAssembling catalogue ({len(cells)} products) …")
    build_catalogue_pdf(
        cells=cells,
        output_path=args.output,
        cols=args.cols,
        rows_per_page=args.rows_per_page,
        divider_px=args.divider,
    )


if __name__ == "__main__":
    main()
