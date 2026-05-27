import json
import logging
import os
from datetime import datetime
import re
import pandas as pd
from bs4 import BeautifulSoup
from openpyxl import Workbook
from rapidfuzz import fuzz, process
from yards.utils.config import FLIPKART_HEADERS, HOSTNAME, USERNAME, PASSWORD, DATABASE
from yards.utils.scrape_data import get_multi_source_product_pages, format_products
from yards.database.get_table_details import connect_db, upsert_product_details

PREDATA_DIR = os.path.join("uploads", "predata")
PREDATA_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# ── Common cricket/sports equipment plural → singular normalization ───────────
EQUIPMENT_PLURAL_MAP = {
    "bats": "bat", "gloves": "glove", "pads": "pad",
    "guards": "guard", "helmets": "helmet", "balls": "ball",
    "boots": "boot", "bags": "bag", "kits": "kit",
    "spikes": "spike", "grips": "grip", "stumps": "stump",
}

BRAND_PREFIXES = {
    "ss", "sg", "dsc", "ca", "ton", "ss ton", "gunn", "gm",
    "gray-nicolls", "gray", "kookaburra", "adidas", "puma",
    "new balance", "asics", "masuri", "aero", "shrey",
    "protos", "payntr", "moonwalkr", "hundred", "tyka", "cosco",
    "spartan",
}


def clean_text(text: str) -> str:
    if text is None:
        return ""
    parsed = BeautifulSoup(str(text), "html.parser")
    text = parsed.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    if not text.strip():
        return []
    return [p.strip() for p in re.split(r",|;|\n|\r", text) if p.strip()]


# =============================================================================
#  BUG 1 + BUG 2 FIX: normalize_predata_row
#  ─────────────────────────────────────────
#  BUG 1: Now reads ALL Flipkart column names directly (blade material, size,
#          age group, weight range, manufacturer details, HSN, etc.).
#          Previously these fields were silently dropped even when the predata
#          file was a prior Flipkart export.
#  BUG 2: Price is now mapped to both "price" AND "MRP (INR)" / "Your selling
#          price (INR)" so build_flipkart_row can read it without relying on
#          _extract_inr_price matching a currency symbol.
# =============================================================================

def _coerce_price(val) -> str:
    """Return a plain numeric string for a price value, or empty string."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    # Strip currency symbols and whitespace
    s = re.sub(r"[₹$£€\s,]", "", s)
    # Extract a number
    m = re.search(r"\d[\d.]*", s)
    return m.group(0) if m else ""


def normalize_predata_row(raw_row: dict) -> dict:
    row = {str(k).strip(): v for k, v in raw_row.items()}

    # ── Title ─────────────────────────────────────────────────────────────────
    title = str(
        row.get("Title") or row.get("title")
        or row.get("Model Name") or row.get("model_name")
        or row.get("Product name in PI") or row.get("Product Name")
        or row.get("product_name") or ""
    ).strip()

    # ── Brand ─────────────────────────────────────────────────────────────────
    brand = str(row.get("Brand") or row.get("brand") or "").strip()

    # ── Category / type ───────────────────────────────────────────────────────
    category = str(row.get("Category") or row.get("category") or "").strip()
    item_type = str(row.get("Type") or row.get("type") or row.get("item-type") or "").strip()

    # ── Description / Body HTML ───────────────────────────────────────────────
    description = str(
        row.get("Description") or row.get("description")
        or row.get("Product Description") or row.get("product-description") or ""
    ).strip()
    body_html = str(
        row.get("Body HTML") or row.get("body_html") or row.get("BodyHtml")
        or description or ""
    ).strip()

    # ── SKU ───────────────────────────────────────────────────────────────────
    official_sku = str(
        row.get("Seller SKU ID") or row.get("Official SKU")
        or row.get("SKU") or row.get("sku") or row.get("Part Number") or ""
    ).strip()

    # ── BUG 2 FIX: Price — map to MRP (INR) explicitly ───────────────────────
    raw_price = (
        row.get("MRP (INR)") or row.get("mrp_inr") or row.get("mrp")
        or row.get("Price") or row.get("price")
        or row.get("Variant Price") or row.get("variant_price") or ""
    )
    selling_price_raw = (
        row.get("Your selling price (INR)") or row.get("selling_price")
        or row.get("Sale Price") or row.get("sale_price") or ""
    )
    mrp_str          = _coerce_price(raw_price)
    selling_price_str = _coerce_price(selling_price_raw) or mrp_str

    # ── Images ────────────────────────────────────────────────────────────────
    images = parse_list_field(
        row.get("Main Image URL") or row.get("Images") or row.get("Image Src")
        or row.get("images") or row.get("image_src") or ""
    )
    other_images = []
    for col in ("Other Image URL 1", "Other Image URL 2", "Other Image URL 3", "Other Image URL 4"):
        v = str(row.get(col, "")).strip()
        if v:
            other_images.append(v)
    all_images = images + other_images

    # ── Tags ──────────────────────────────────────────────────────────────────
    tags = row.get("Tags") or row.get("tags") or row.get("Search Keywords") or ""
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    # ── Weight ────────────────────────────────────────────────────────────────
    weight      = row.get("Weight Range") or row.get("Weight") or row.get("weight") or row.get("Variant Weight") or ""
    weight_unit = row.get("Weight Range - Measuring Unit") or row.get("Weight Unit") or row.get("weight_unit") or row.get("Variant Weight Unit") or ""

    # ── Country of origin ─────────────────────────────────────────────────────
    country_of_origin = str(
        row.get("Country Of Origin") or row.get("Country of Origin")
        or row.get("country_of_origin") or "India"
    ).strip() or "India"

    # ── Variants ──────────────────────────────────────────────────────────────
    variants = row.get("variants") or []

    # ── BUG 1 FIX: Cricket / Flipkart-specific fields read directly ──────────
    #   If the predata is a prior Flipkart export (or has matching column names),
    #   these values are preserved and passed through to build_flipkart_row so
    #   they are never re-extracted or lost.
    blade_material     = str(row.get("Blade Material")     or row.get("blade_material")     or "").strip()
    sport_type         = str(row.get("Sport Type")         or row.get("sport_type")          or "").strip()
    size               = str(row.get("Size")               or row.get("size")                or "").strip()
    age_group          = str(row.get("Age Group")          or row.get("age_group")           or "").strip()
    ideal_for          = str(row.get("Ideal For")          or row.get("ideal_for")           or "").strip()
    playing_level      = str(row.get("Playing Level")      or row.get("playing_level")       or "").strip()
    bat_grade          = str(row.get("Bat Grade")          or row.get("bat_grade")           or "").strip()
    cover_included     = str(row.get("Cover Included")     or row.get("cover_included")      or "").strip()
    color              = str(row.get("Color")              or row.get("color")               or row.get("Colour") or "").strip()
    manufacturer       = str(row.get("Manufacturer Details") or row.get("Manufacturer")     or row.get("manufacturer") or "").strip()
    packer_details     = str(row.get("Packer Details")     or row.get("packer_details")      or "").strip()
    hsn                = str(row.get("HSN")                or row.get("hsn")                 or "").strip()
    tax_code           = str(row.get("Tax Code")           or row.get("tax_code")            or "").strip()
    fulfillment_by     = str(row.get("Fullfilment by")     or row.get("fulfillment_by")      or "").strip()
    width              = str(row.get("Width")              or row.get("width")               or "").strip()
    height             = str(row.get("Height")             or row.get("height")              or "").strip()
    depth              = str(row.get("Depth")              or row.get("depth")               or "").strip()
    stock              = str(row.get("Stock")              or row.get("stock")               or "").strip()
    listing_status     = str(row.get("Listing Status")     or row.get("listing_status")      or "").strip()

    product_detail = {
        # ── Standard scraper keys ─────────────────────────────────────────
        "Title":                        title,
        "brand":                        brand,
        "category":                     category,
        "type":                         item_type,
        "description":                  description,
        "Body HTML":                    body_html,
        "official_sku":                 official_sku,
        "images":                       all_images,
        "Official Images":              all_images,
        "tags":                         tags,
        "weight":                       weight,
        "weight_unit":                  weight_unit,
        "country_of_origin":            country_of_origin,
        "variants":                     variants,

        # ── BUG 2 FIX: Price mapped to Flipkart column names ──────────────
        "price":                        mrp_str,          # scraper helper fallback
        "MRP (INR)":                    mrp_str,          # Flipkart column — direct
        "Your selling price (INR)":     selling_price_str,

        # ── BUG 1 FIX: All Flipkart cricket fields passed through ─────────
        "Blade Material":               blade_material,
        "Sport Type":                   sport_type,
        "Size":                         size,
        "Age Group":                    age_group,
        "Ideal For":                    ideal_for,
        "Playing Level":                playing_level,
        "Bat Grade":                    bat_grade,
        "Cover Included":               cover_included,
        "Color":                        color,
        "Weight Range":                 weight,
        "Manufacturer Details":         manufacturer,
        "Packer Details":               packer_details,
        "HSN":                          hsn,
        "Tax Code":                     tax_code,
        "Fullfilment by":               fulfillment_by,
        "Width":                        width,
        "Height":                       height,
        "Depth":                        depth,
        "Stock":                        stock,
        "Listing Status":               listing_status,

        # ── Logging metadata ──────────────────────────────────────────────
        "_predata_file": row.get("_predata_file", ""),
    }

    return product_detail


def load_predata_records(predata_dir: str) -> list[dict]:
    records = []
    if not os.path.isdir(predata_dir):
        return records

    for filename in os.listdir(predata_dir):
        path = os.path.join(predata_dir, filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in PREDATA_EXTENSIONS:
            continue

        try:
            if ext == ".csv":
                df = pd.read_csv(path, dtype=str)
                if _is_shopify_export(df):
                    logging.info(f"[flipkart_agent] Shopify export detected in {filename}, re-reading with header=1")
                    df = pd.read_csv(path, dtype=str, header=1)
                rows = df.fillna("").to_dict(orient="records")
            else:
                sheets = pd.read_excel(path, sheet_name=None, dtype=str)
                rows = []
                for sheet_name, sheet_df in sheets.items():
                    if _is_shopify_export(sheet_df):
                        logging.info(f"[flipkart_agent] Shopify export detected in {filename} sheet '{sheet_name}', re-reading with header=1")
                        sheet_df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, header=1)
                    rows.extend(sheet_df.fillna("").to_dict(orient="records"))

            for row in rows:
                row["_predata_file"] = filename
                records.append(row)

            logging.info(
                f"[flipkart_agent] loaded {len(rows)} records from {filename}, "
                f"columns={list(pd.DataFrame(rows).columns[:5]) if rows else []}"
            )

        except Exception as e:
            logging.warning(f"[flipkart_agent] Failed to load predata file {filename}: {e}")

    return records


def _is_shopify_export(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    unnamed_count = sum(1 for col in df.columns if str(col).startswith("Unnamed:"))
    return unnamed_count > len(df.columns) / 2


def expand_willow_abbreviations(text: str) -> str:
    if not text:
        return ""
    expanded = str(text)
    expanded = re.sub(r"\b(e\.w|e\.w\.|ew)\b", "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(k\.w|k\.w\.|kw)\b", "Kashmir Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(cr\.?)(?!\w)", "Cricket", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(english\s+willow)\b", "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(kashmir\s+willow)\b", "Kashmir Willow", expanded, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", expanded).strip()


def _strip_leading_brand_prefix(text: str) -> str:
    if not text:
        return ""
    tokens = text.strip().split()
    if not tokens:
        return ""
    first_two = " ".join(tokens[:2]).lower()
    first_one = tokens[0].lower()
    if first_two in BRAND_PREFIXES:
        return " ".join(tokens[2:]).strip()
    if first_one in BRAND_PREFIXES:
        return " ".join(tokens[1:]).strip()
    return text.strip()


def normalize_title(text: str) -> str:
    normalized = expand_willow_abbreviations(str(text))
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = _strip_leading_brand_prefix(normalized)
    normalized = re.sub(r"\s+", " ", normalized.strip().lower())
    tokens = normalized.split()
    tokens = [EQUIPMENT_PLURAL_MAP.get(t, t) for t in tokens]
    return " ".join(tokens).strip()


def clean_title_for_scraper(text: str) -> str:
    """Expand willow abbreviations before passing to scraper."""
    cleaned = expand_willow_abbreviations(str(text).strip())
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_input_title(row: dict) -> str:
    return str(
        row.get("Title") or row.get("Product name in PI")
        or row.get("Product Name") or row.get("product_name")
        or row.get("Name") or row.get("name") or ""
    ).strip()


def find_predata_entry(title: str, predata_records: list[dict]) -> dict | None:
    """Find predata entry by title — exact match first, then fuzzy."""
    normalized_title = normalize_title(title)
    logging.info(f"[flipkart_agent] find_predata_entry: input='{title}' normalized='{normalized_title}'")

    candidates = []
    rows = []

    for raw_row in predata_records:
        raw_candidate = (
            raw_row.get("Title") or raw_row.get("title")
            or raw_row.get("Model Name") or raw_row.get("model_name")
            or raw_row.get("Product name in PI") or raw_row.get("Product Name")
            or raw_row.get("product_name") or raw_row.get("Name")
            or raw_row.get("name") or raw_row.get("product-title")
            or raw_row.get("Product Title") or ""
        )
        candidate = normalize_title(raw_candidate)
        candidates.append(candidate)
        rows.append(raw_row)

        if candidate == normalized_title:
            logging.info(f"[flipkart_agent] EXACT match for '{title}' → '{raw_candidate}'")
            return normalize_predata_row(raw_row)

    non_empty = [(c, i) for i, c in enumerate(candidates) if c][:5]
    logging.info(f"[flipkart_agent] sample non-empty candidates (first 5): {[c for c, _ in non_empty]}")
    logging.info(
        f"[flipkart_agent] total candidates={len(candidates)}, "
        f"non-empty={sum(1 for c in candidates if c)}"
    )

    if not any(candidates):
        logging.warning("[flipkart_agent] ALL candidates are empty — check predata title column name!")
        logging.warning(
            f"[flipkart_agent] Available columns in first predata row: "
            f"{list(predata_records[0].keys()) if predata_records else '[]'}"
        )
        return None

    best_match = process.extractOne(
        normalized_title, candidates,
        scorer=fuzz.token_set_ratio,
        score_cutoff=75,
    )

    if best_match:
        _, score, index = best_match
        sort_score = fuzz.token_sort_ratio(normalized_title, candidates[index])

        matched_row = rows[index]
        matched_raw = (
            matched_row.get("Title") or matched_row.get("title")
            or matched_row.get("Model Name") or matched_row.get("Product name in PI")
            or matched_row.get("Product Name") or matched_row.get("product_name")
            or matched_row.get("Name") or matched_row.get("name") or ""
        )

        logging.info(
            f"[flipkart_agent] fuzzy candidate: '{title}' → '{matched_raw}' "
            f"(token_set={score:.1f}, token_sort={sort_score:.1f})"
        )

        if score >= 75 and sort_score >= 75:
            logging.info(f"[flipkart_agent] FUZZY match accepted: '{title}' → '{matched_raw}'")
            return normalize_predata_row(matched_row)
        else:
            logging.info(
                f"[flipkart_agent] FUZZY match REJECTED "
                f"(token_set={score:.1f}, token_sort={sort_score:.1f}, need both >= 75): "
                f"'{title}' vs '{matched_raw}' — will scrape instead"
            )

    return None


def extract_bullet_points(text: str, max_points: int = 5) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    fragments = re.split(r"[\n\r]+|\.\s+", text)
    bullets = []
    for fragment in fragments:
        fragment = fragment.strip().rstrip(".")
        if len(fragment) < 20:
            continue
        bullets.append(fragment)
        if len(bullets) >= max_points:
            break
    if not bullets and text:
        bullets = [text[:200].rstrip(".")]
    return bullets[:max_points]


def extract_first_variant_size(variants) -> str:
    if not isinstance(variants, list):
        return ""
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        for key in ("size", "Size", "variant_size", "Variant Size"):
            size = variant.get(key, "")
            if size and str(size).strip():
                return str(size).strip()
    return ""


def _is_predata_complete(detail: dict) -> bool:
    """
    BUG 4 FIX (helper): Returns True when the predata entry already has all the
    fields that would otherwise need a web scrape to fill.

    If True, format_products() skips fetch_missing_data_from_official() so we
    do not re-scrape products that already have complete information.
    """
    has_description = bool(
        str(detail.get("description", "") or detail.get("Body HTML", "")).strip()
    )
    has_images = bool(
        detail.get("images") or detail.get("Official Images")
    )
    has_price = bool(
        str(detail.get("MRP (INR)", "") or detail.get("price", "")).strip()
    )
    has_title = bool(str(detail.get("Title", "") or detail.get("title", "")).strip())

    return has_title and has_description and has_images and has_price


async def flipkart_step(state):
    UPDATED_DIR = os.path.join("uploads", "updated_files")
    os.makedirs(UPDATED_DIR, exist_ok=True)

    try:
        file_path = state.get("file_path", "")
        filename  = state.get("filename", "")
        logging.info(
            f"[flipkart_agent] flipkart_step start file={filename} path={file_path} "
            f"user_id={state.get('user_id')}"
        )

        if not os.path.exists(file_path):
            logging.error(f"[flipkart_agent] file not found path={file_path}")
            return {"status": 404, "message": "File not found..."}

        file_extension  = os.path.splitext(filename)[1].lower()
        filename_no_ext = os.path.splitext(filename)[0]

        if file_extension == ".csv":
            file_info = pd.read_csv(file_path)
        elif file_extension in [".xlsx", ".xls"]:
            file_info = pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file format")

        input_rows      = file_info.to_dict(orient="records")
        predata_records = load_predata_records(PREDATA_DIR)
        logging.info(
            f"[flipkart_agent] loaded {len(input_rows)} rows from {filename}, "
            f"predata_records={len(predata_records)}"
        )

        groq_api_key   = state.get("groq_token")   or os.getenv("GROQ_API_KEY")
        serper_api_key = state.get("serper_token")  or os.getenv("SERPER_API_KEY")

        product_entries   = []
        titles_to_scrape  = []      # cleaned title strings for the scraper
        scrape_title_map  = {}      # cleaned_title → entry index (BUG 3 fix anchor)

        for row in input_rows:
            title = extract_input_title(row)
            if not title:
                continue

            entry = {
                "title":  title,
                "source": "predata",
                "detail": find_predata_entry(title, predata_records),
            }

            if entry["detail"] is None:
                entry["source"]  = "scrape_pending"
                cleaned_title    = clean_title_for_scraper(title)
                # BUG 3 FIX: store the index so we can look up by title later
                scrape_title_map[cleaned_title] = len(product_entries)
                titles_to_scrape.append(cleaned_title)
                logging.info(f"[flipkart_agent] will scrape title='{title}' (cleaned: '{cleaned_title}')")
            else:
                logging.info(f"[flipkart_agent] matched predata for title='{title}'")

            product_entries.append(entry)
        
            if len(product_entries) > 10:
                logging.warning(
                    f"[flipkart_agent] too many products: {len(product_entries)} titles"
                )
                state['message'] = f"You have more than 10 products in the uploaded file. Please upload fewer than 10 products to get product details from online."
                return {
                    "status": 400,
                    "message": "You have more than 10 products. Please upload fewer than 10 products to get product details from online."
                }
            
        scraped_by_title: dict[str, dict] = {}

        if titles_to_scrape:
            logging.info(f"[flipkart_agent] looking up {len(titles_to_scrape)} titles via scraper")
            raw_scraped_rows = await get_multi_source_product_pages(
                titles_to_scrape,
                format_type="flipkart",
                groq_api_key=groq_api_key,
                serper_api_key=serper_api_key,
            )
            logging.info(f"[flipkart_agent] scraper returned {len(raw_scraped_rows)} flipkart rows")

            # Map each returned row back to the title that produced it.
            # get_multi_source_product_pages processes titles in order, so
            # index N in the output corresponds to titles_to_scrape[N] — but
            # only when the result is not None.  We skip None entries and log.
            scrape_result_index = 0
            for i, cleaned_title in enumerate(titles_to_scrape):
                if scrape_result_index >= len(raw_scraped_rows):
                    logging.warning(
                        f"[flipkart_agent] no scraped row for title='{cleaned_title}' "
                        f"(scraper returned {len(raw_scraped_rows)} rows for "
                        f"{len(titles_to_scrape)} titles)"
                    )
                    break
                scraped_row = raw_scraped_rows[scrape_result_index]
                if scraped_row is None:
                    logging.warning(f"[flipkart_agent] scraper returned None for title='{cleaned_title}'")
                    # do not advance result index — None means this title failed
                    # and the next result belongs to the next title
                    scraped_by_title[cleaned_title] = None
                else:
                    scraped_by_title[cleaned_title] = scraped_row
                    logging.info(
                        f"[flipkart_agent] scraped_row for '{cleaned_title}': "
                        f"model='{scraped_row.get('Model Name', 'N/A')}' "
                        f"brand='{scraped_row.get('Brand', 'N/A')}' "
                        f"price='{scraped_row.get('MRP (INR)', 'N/A')}'"
                    )
                    scrape_result_index += 1
        else:
            logging.info("[flipkart_agent] no titles to scrape")

        # ── Step 2: Format predata entries and attach scraped rows ────────────
        for entry in product_entries:

            if entry["detail"] is not None:
                # ── Predata path ──────────────────────────────────────────────
                #
                # BUG 4 FIX: Only call the full format_products pipeline (which
                # triggers fetch_missing_data_from_official) when the predata
                # entry is genuinely incomplete.  If it already has title,
                # description, images, and price we call format_products with
                # a flag that suppresses the web-scrape enrichment step.
                #
                detail = entry["detail"]
                predata_file = detail.get("_predata_file", "")

                if _is_predata_complete(detail):
                    # Pass a sentinel so format_products can skip online fetch
                    detail["_skip_online_enrichment"] = True
                    logging.info(
                        f"[flipkart_agent] product='{entry['title']}' predata complete "
                        f"— skipping online enrichment (file={predata_file})"
                    )
                else:
                    logging.info(
                        f"[flipkart_agent] product='{entry['title']}' predata incomplete "
                        f"— will enrich online (file={predata_file})"
                    )

                flipkart_rows = await format_products(
                    [detail],
                    "flipkart",
                    groq_api_key=groq_api_key,
                    serper_api_key=serper_api_key,
                )
                flipkart_row = flipkart_rows[0] if flipkart_rows else {}

                # Always use the original input title, not the predata title
                flipkart_row["Model Name"] = expand_willow_abbreviations(entry["title"])
                entry["flipkart_row"] = flipkart_row
                entry["source"] = "predata_formatted"
                logging.info(
                    f"[flipkart_agent] product='{entry['title']}' source=predata_formatted "
                    f"file={predata_file}"
                )

            else:
                # ── Scraped path ──────────────────────────────────────────────
                #
                # BUG 3 FIX: Look up by title, not by counter.
                #
                original_title = entry["title"]
                cleaned_title  = clean_title_for_scraper(original_title)
                scraped_row    = scraped_by_title.get(cleaned_title)
                correct_title  = expand_willow_abbreviations(original_title)

                if scraped_row is not None:
                    scraped_row = dict(scraped_row)  # copy — never mutate original
                    scraped_title = scraped_row.get("Model Name", "")
                    if scraped_title != correct_title:
                        logging.info(
                            f"[flipkart_agent] title override: "
                            f"scraped='{scraped_title}' → '{correct_title}'"
                        )
                    scraped_row["Model Name"] = correct_title
                    entry["flipkart_row"] = scraped_row
                    entry["source"] = "scraped"
                    logging.info(f"[flipkart_agent] product='{original_title}' source=scraped")

                else:
                    # ── Fallback: minimal detail → format_products ────────────
                    logging.warning(
                        f"[flipkart_agent] no scraped data for title='{original_title}', "
                        f"using enhanced fallback row"
                    )
                    fallback_detail = {
                        "Title":       correct_title,
                        "brand":       "SS" if correct_title.upper().startswith("SS ") else "",
                        "category":    "Cricket Bat" if "bat" in correct_title.lower() else "Cricket Equipment",
                        "type":        "Cricket Bat" if "bat" in correct_title.lower() else "",
                        "description": f"High-quality cricket equipment: {correct_title}",
                        "images":      [],
                        "tags":        ["cricket"],
                        "price":       "",
                        "variants":    [],
                    }
                    fallback_rows = await format_products(
                        [fallback_detail],
                        "flipkart",
                        groq_api_key=groq_api_key,
                        serper_api_key=serper_api_key,
                    )
                    fallback_row = fallback_rows[0] if fallback_rows else {}
                    fallback_row["Model Name"] = correct_title
                    entry["flipkart_row"] = fallback_row
                    entry["source"] = "fallback"
                    logging.info(f"[flipkart_agent] product='{original_title}' source=fallback")

        # ── Step 3: Write Excel output ────────────────────────────────────────
        logging.info(f"[flipkart_agent] total product_entries={len(product_entries)}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        output_file = os.path.join(
            UPDATED_DIR,
            f"{filename_no_ext}_flipkart_{timestamp}.xlsx"
        )
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="Flipkart Products")
        ws.append(FLIPKART_HEADERS)

        conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)

        for entry in product_entries:
            flipkart_row = entry.get("flipkart_row", {})
            ws.append([flipkart_row.get(h, "") for h in FLIPKART_HEADERS])

            try:
                def safe_float(v):
                    if v in (None, ""):
                        return None
                    try:
                        return float(str(v).replace(',', '').strip())
                    except Exception:
                        return None

                product = {
                    'marketplace': 'flipkart',
                    'marketplace_product_id': flipkart_row.get('Seller SKU ID') or flipkart_row.get('Seller SKU ID') or '',
                    'sku': flipkart_row.get('Seller SKU ID') or flipkart_row.get('Seller SKU ID') or '',
                    'title': flipkart_row.get('Model Name') or flipkart_row.get('Title'),
                    'product_name': flipkart_row.get('Model Name') or flipkart_row.get('Title'),
                    'description': flipkart_row.get('Description'),
                    'brand': flipkart_row.get('Brand'),
                    'manufacturer': flipkart_row.get('Manufacturer Details'),
                    'product_type': flipkart_row.get('Model Name'),
                    'category': flipkart_row.get('Sport Type'),
                    'tags': flipkart_row.get('Search Keywords'),
                    'mrp': safe_float(flipkart_row.get('MRP (INR)')),
                    'selling_price': safe_float(flipkart_row.get('Your selling price (INR)')),
                    'currency': 'INR',
                    'main_image_url': flipkart_row.get('Main Image URL'),
                    'other_image_url_1': flipkart_row.get('Other Image URL 1'),
                    'other_image_url_2': flipkart_row.get('Other Image URL 2'),
                    'hsn': flipkart_row.get('HSN'),
                    'stock': safe_float(flipkart_row.get('Stock')) if flipkart_row.get('Stock') else None,
                    'listing_status': flipkart_row.get('Listing Status'),
                    'size': flipkart_row.get('Size'),
                    'color': flipkart_row.get('Color'),
                    'Blade Material': flipkart_row.get('Blade Material'),
                    'extra_attributes': flipkart_row,
                }

                upsert_product_details(conn, product)
            except Exception as e:
                logging.warning(f"[flipkart_agent] upsert failed for title={flipkart_row.get('Model Name')} error={e}")

        wb.save(output_file)
        if conn:
            conn.close()
        return {
            "status":           200,
            "output_file_path": output_file,
            "output_file_name": os.path.basename(output_file),
        }

    except Exception as e:
        logging.error(f"[flipkart_agent] Error in flipkart_step: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}