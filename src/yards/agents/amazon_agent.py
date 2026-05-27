import json
import logging
import os
from datetime import datetime
import re
import pandas as pd
from bs4 import BeautifulSoup
from openpyxl import Workbook
from rapidfuzz import fuzz, process
from yards.utils.config import AMAZON_HEADERS, HOSTNAME, USERNAME, PASSWORD, DATABASE
from yards.utils.scrape_data import get_multi_source_product_pages, format_products
from yards.database.get_table_details import connect_db, upsert_product_details

from yards.utils.scrape_data import (
    get_multi_source_product_pages,
    format_products,
    _enrich_cricket_fields,      # ← ADD
    _extract_warranty,           # ← ADD
    _fill_package_dims,          # ← ADD
    convert_weight_to_range,     # ← ADD
    BAT_ALLOWED,                 # ← ADD
    BRAND_TO_MANUFACTURER,       # ← ADD
)

PREDATA_DIR = os.path.join("uploads", "predata")
PREDATA_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# Common cricket/sports equipment plural → singular normalization
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
    "spartan"
}


def clean_text(text: str) -> str:
    if text is None:
        return ""
    parsed = BeautifulSoup(str(text), "html.parser")
    text = parsed.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    if not text.strip():
        return []
    parts = re.split(r",|;|\n|\r", text)
    return [p.strip() for p in parts if p.strip()]


def normalize_predata_row(raw_row: dict) -> dict:
    row = {str(k).strip(): v for k, v in raw_row.items()}
    product_detail = {
        "Title": str(row.get("Title") or row.get("title") or row.get("Product name in PI") or row.get("Product Name") or row.get("product_name") or "").strip(),
        "brand": str(row.get("Brand") or row.get("brand") or "").strip(),
        "category": str(row.get("Category") or row.get("category") or "").strip(),
        "type": str(row.get("Type") or row.get("type") or row.get("item-type") or "").strip(),
        "description": str(row.get("Description") or row.get("description") or row.get("Product Description") or row.get("product-description") or "").strip(),
        "Body HTML": str(row.get("Body HTML") or row.get("body_html") or row.get("BodyHtml") or row.get("description") or "").strip(),
        "official_sku": str(row.get("Official SKU") or row.get("SKU") or row.get("sku") or "").strip(),
        "price": row.get("Price") or row.get("price") or row.get("Variant Price") or row.get("variant_price") or "",
        "images": parse_list_field(row.get("Images") or row.get("Image Src") or row.get("images") or row.get("image_src") or ""),
        "tags": row.get("Tags") or row.get("tags") or "",
        "weight": row.get("Weight") or row.get("weight") or row.get("Variant Weight") or "",
        "weight_unit": row.get("Weight Unit") or row.get("weight_unit") or row.get("Variant Weight Unit") or "",
        "country_of_origin": str(row.get("Country of Origin") or row.get("country_of_origin") or "").strip(),
        "variants": row.get("variants") or [],
    }

    if isinstance(product_detail["tags"], str):
        product_detail["tags"] = [t.strip() for t in product_detail["tags"].split(",") if t.strip()]

    if isinstance(product_detail["price"], str) and product_detail["price"].strip():
        try:
            product_detail["price"] = float(product_detail["price"])
        except ValueError:
            product_detail["price"] = product_detail["price"].strip()

    if not product_detail["images"] and "Image" in row:
        product_detail["images"] = parse_list_field(row.get("Image"))

    # Preserve the predata filename for logging
    product_detail["_predata_file"] = row.get("_predata_file", "")

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
                # Detect Shopify-style export: real headers are in row 2
                if _is_shopify_export(df):
                    logging.info(f"[amazon_agent] Shopify export detected in {filename}, re-reading with header=1")
                    df = pd.read_csv(path, dtype=str, header=1)
                rows = df.fillna("").to_dict(orient="records")
            else:
                sheets = pd.read_excel(path, sheet_name=None, dtype=str)
                rows = []
                for sheet_name, sheet_df in sheets.items():
                    if _is_shopify_export(sheet_df):
                        logging.info(f"[amazon_agent] Shopify export detected in {filename} sheet '{sheet_name}', re-reading with header=1")
                        sheet_df = pd.read_excel(path, sheet_name=sheet_name, dtype=str, header=1)
                    rows.extend(sheet_df.fillna("").to_dict(orient="records"))

            for row in rows:
                row["_predata_file"] = filename
                records.append(row)

            logging.info(f"[amazon_agent] loaded {len(rows)} records from {filename}, columns={list(pd.DataFrame(rows).columns[:5]) if rows else []}")

        except Exception as e:
            logging.warning(f"[amazon_agent] Failed to load predata file {filename}: {e}")

    return records


def _is_shopify_export(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    unnamed_count = sum(1 for col in df.columns if str(col).startswith("Unnamed:"))
    # If more than half the columns are unnamed, the real header is one row down
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
    expanded = re.sub(r"\s+", " ", expanded).strip()
    return expanded


def _strip_leading_brand_prefix(text: str) -> str:
    """Remove common leading brand tokens before matching titles."""
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
    """Clean title by expanding willow abbreviations before passing to scraper."""
    cleaned = expand_willow_abbreviations(str(text).strip())
    # Keep the product wording intact and only normalize common willow shorthand.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned



def extract_input_title(row: dict) -> str:
    return str(
        row.get("Title")
        or row.get("Product name in PI")
        or row.get("Product Name")
        or row.get("product_name")
        or row.get("Name")
        or row.get("name")
        or ""
    ).strip()


def find_predata_entry(title: str, predata_records: list[dict]) -> dict | None:
    """Find predata entry by title match, using exact and fuzzy matching."""
    normalized_title = normalize_title(title)
    logging.info(f"[amazon_agent] find_predata_entry: input='{title}' normalized='{normalized_title}'")

    candidates = []
    rows = []

    for raw_row in predata_records:
        raw_candidate = (
            raw_row.get("Title")
            or raw_row.get("title")
            or raw_row.get("Product name in PI")
            or raw_row.get("Product Name")
            or raw_row.get("product_name")
            or raw_row.get("Name")
            or raw_row.get("name")
            or raw_row.get("product-title")
            or raw_row.get("Product Title")
            or ""
        )
        candidate = normalize_title(raw_candidate)
        candidates.append(candidate)
        rows.append(raw_row)

        if candidate == normalized_title:
            logging.info(f"[amazon_agent] EXACT match for '{title}' → '{raw_candidate}'")
            return normalize_predata_row(raw_row)

    # Log a sample of non-empty candidates to verify column detection
    non_empty = [(c, i) for i, c in enumerate(candidates) if c][:5]
    logging.info(f"[amazon_agent] sample non-empty candidates (first 5): {[c for c,_ in non_empty]}")
    logging.info(f"[amazon_agent] total candidates={len(candidates)}, non-empty={sum(1 for c in candidates if c)}")

    if not any(candidates):
        logging.warning(f"[amazon_agent] ALL candidates are empty — check predata title column name!")
        logging.warning(f"[amazon_agent] Available columns in first predata row: {list(predata_records[0].keys()) if predata_records else '[]'}")
        return None

    best_match = process.extractOne(
        normalized_title,
        candidates,
        scorer=fuzz.token_set_ratio,
        score_cutoff=75
    )

    if best_match:
        _, score, index = best_match
        sort_score = fuzz.token_sort_ratio(normalized_title, candidates[index])
        combined = (score + sort_score) / 2

        matched_row = rows[index]
        matched_raw = (
            matched_row.get("Title") or matched_row.get("title")
            or matched_row.get("Product name in PI") or matched_row.get("Product Name")
            or matched_row.get("product_name") or matched_row.get("Name")
            or matched_row.get("name") or ""
        )

        logging.info(
            f"[amazon_agent] fuzzy candidate: '{title}' → '{matched_raw}' "
            f"(token_set={score:.1f}, token_sort={sort_score:.1f})"
        )

        if score >= 75 and sort_score >= 75:
            logging.info(f"[amazon_agent] FUZZY match accepted: '{title}' → '{matched_raw}'")
            return normalize_predata_row(matched_row)
        else:
            logging.info(
                f"[amazon_agent] FUZZY match REJECTED "
                f"(token_set={score:.1f}, token_sort={sort_score:.1f}, need both >= 75): "
                f"'{title}' vs '{matched_raw}' — will scrape instead"
            )


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


def build_amazon_row(product_detail: dict) -> dict:
    # Step 1: Pre-enrich with all cricket extraction helpers
    product_detail = _enrich_cricket_fields(product_detail)

    title = str(
        product_detail.get("Title") or product_detail.get("title")
        or product_detail.get("item-name") or product_detail.get("item_name") or ""
    ).strip()
    title = expand_willow_abbreviations(title)

    brand = str(
        product_detail.get("brand", "") or product_detail.get("Brand", "")
        or product_detail.get("brand-name", "") or product_detail.get("brand_name", "") or ""
    ).strip()

    category = str(
        product_detail.get("category", "") or product_detail.get("Category", "")
        or product_detail.get("feed_product_type", "") or ""
    ).strip()

    item_type = str(
        product_detail.get("type", "") or product_detail.get("Type", "")
        or product_detail.get("item-type", "") or category
    ).strip()

    description = str(
        product_detail.get("description", "") or product_detail.get("Body HTML", "")
        or product_detail.get("Body (HTML)", "") or product_detail.get("body_html", "")
    )
    description_plain = clean_text(description)

    tags = product_detail.get("tags", [])
    if isinstance(tags, str):
        if tags.strip():
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
        else:
            tags = []
    if not tags:
        tags = product_detail.get("Tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]

    # Price
    price_info = product_detail.get("price", {}) or product_detail.get("Price", {})
    if isinstance(price_info, dict):
        price_value = price_info.get("value", "")
        currency = price_info.get("currency", "")
    else:
        price_value = price_info
        currency = ""

    if isinstance(price_value, str) and price_value.strip():
        match = re.search(r"([0-9]+(?:[\.,][0-9]{1,2})?)", price_value.replace(",", ""))
        if match:
            try:
                price_value = float(match.group(1))
            except ValueError:
                pass

    # Images
    images = product_detail.get("Official Images") or product_detail.get("images", []) or []
    if not images:
        images = product_detail.get("Image Src") or product_detail.get("image_src") or []
    if isinstance(images, str):
        images = [images]
    if isinstance(images, dict):
        images = [images]
    images = [str(img).strip() for img in images if str(img).strip()]

    # Cricket-specific enriched fields
    blade_material = str(product_detail.get("Blade Material", "")).strip()
    weight_range   = str(product_detail.get("Weight Range", "")).strip()
    sport_type     = str(product_detail.get("Sport Type", "Cricket")).strip() or "Cricket"
    size_value     = str(product_detail.get("Size", "")).strip() or extract_first_variant_size(
        product_detail.get("variants", [])
    )
    manufacturer   = str(product_detail.get("Manufacturer Details", "")).strip()
    if not manufacturer:
        brand_key = brand.upper()
        manufacturer = BRAND_TO_MANUFACTURER.get(brand_key, brand)

    playing_level  = str(product_detail.get("Playing Level", "")).strip()
    handle_grip    = str(product_detail.get("Handle Grip Type", "")).strip()
    handle_mat     = str(product_detail.get("Handle Material", "")).strip()
    sku            = str(product_detail.get("official_sku", "") or product_detail.get("sku", "") or "").strip()
    country        = str(product_detail.get("country_of_origin", "India") or "India").strip()

    # Color
    color_value = str(product_detail.get("Color", "")).strip()
    if not color_value:
        allowed_colors = [c.lower() for c in BAT_ALLOWED.get("Color", [])]
        for tag in tags:
            if tag.lower() in allowed_colors:
                color_value = tag.capitalize()
                break

    # Material (blade material or generic)
    material = blade_material or ""
    if not material:
        for tag in tags:
            if tag.lower() in ["leather", "wood", "carbon", "polyester", "nylon", "rubber", "foam"]:
                material = tag.capitalize()
                break

    # Warranty
    warranty_text = ""
    w_data = product_detail.get("Warranty Summary", "") or product_detail.get("Domestic Warranty", "")
    if w_data and str(w_data).strip() not in ("", "No Warranty"):
        warranty_text = str(w_data).strip()

    # Package dims (already filled by _fill_package_dims inside _enrich_cricket_fields)
    pkg_length  = str(product_detail.get("Length (CM)", "")).strip()
    pkg_breadth = str(product_detail.get("Breadth (CM)", "")).strip()
    pkg_height  = str(product_detail.get("Height (CM)", "")).strip()
    pkg_weight  = str(product_detail.get("Weight (KG)", "")).strip()

    bullet_points = extract_bullet_points(description_plain)

    # Build row — initialise all headers to ""
    row = {header: "" for header in AMAZON_HEADERS}

    # ── Core identity ────────────────────────────────────────────────────────
    row["SKU"]                = sku
    row["Product Id"]         = sku
    row["Product Id Type"]    = "ASIN" if sku else ""
    row["Item Name"]          = title
    row["Brand Name"]         = brand
    row["Model Name"]         = str(product_detail.get("model_name", "") or title).strip()
    row["Model Number"]       = sku
    row["Part Number"]        = sku or "NA"
    row["Manufacturer"]       = manufacturer or brand

    # ── Product type / category ──────────────────────────────────────────────
    row["Item Type Name"]     = item_type
    row["Style"]              = item_type or title
    row["Department Name"]    = "Sports"

    # ── Description & keywords ───────────────────────────────────────────────
    row["Product Description"] = description_plain
    row["Bullet Point"]       = bullet_points[0] if bullet_points else ""
    row["_bullet_points"]     = bullet_points  # consumed by amazon_step

    row["Generic Keywords"]   = ", ".join([t.lower() for t in tags if t])
    row["Special Features"]   = "; ".join(bullet_points[1:4]) if len(bullet_points) > 1 else ""

    # ── Sport / cricket-specific ─────────────────────────────────────────────
    row["Sport Type"]              = sport_type
    row["Sport Bat Willow Type"]   = blade_material   # "English Willow" / "Kashmir Willow"
    row["Bat Construction"]        = "Hand Crafted" if blade_material == "English Willow" else "Machine Made"
    row["Skill Level"]             = playing_level or ("Advanced" if size_value in ("Short Handle", "Long Handle", "Harrow") else "Intermediate")
    row["Grip Type"]               = handle_grip
    row["Handle Material"]         = handle_mat
    row["Recommended Uses For Product"] = "Cricket"

    # ── Physical attributes ───────────────────────────────────────────────────
    row["Material"]           = material
    row["Color"]              = color_value
    row["Size"]               = size_value
    row["Item Weight"]        = weight_range
    row["Item Weight Unit"]   = "grams" if weight_range else ""

    # Package dims (cm → need unit cols)
    row["Item Package Length"]  = pkg_length
    row["Package Length Unit"]  = "centimeters" if pkg_length else ""
    row["Item Package Width"]   = pkg_breadth
    row["Package Width Unit"]   = "centimeters" if pkg_breadth else ""
    row["Item Package Height"]  = pkg_height
    row["Package Height Unit"]  = "centimeters" if pkg_height else ""
    row["Package Weight"]       = pkg_weight
    row["Package Weight Unit"]  = "kilograms" if pkg_weight else ""

    # ── Images ────────────────────────────────────────────────────────────────
    row["Main Image URL"]      = images[0] if images else ""
    row["Main Image Location"] = images[0] if images else ""
    row["Other Image URL"]     = images[1] if len(images) > 1 else ""
    row["Other Image Location"]= images[1] if len(images) > 1 else ""
    row["_extra_images"]       = images     # consumed by amazon_step for all slots

    # ── Pricing ───────────────────────────────────────────────────────────────
    row["Your Price INR (Sell on Amazon, IN)"]       = str(price_value) if price_value else ""
    row["Maximum Retail Price (Sell on Amazon, IN)"] = str(price_value) if price_value else ""

    # ── Fulfilment / inventory ────────────────────────────────────────────────
    row["Quantity (IN)"]               = "1"
    row["Fulfillment Channel Code (IN)"]= "DEFAULT"
    row["Handling Time (IN)"]          = "3"
    row["Item Condition"]              = "New"

    # ── Compliance / origin ───────────────────────────────────────────────────
    row["Country of Origin"]              = country
    row["Manufacturer Contact Information"]= manufacturer or brand
    row["Importer Contact Information"]   = manufacturer or brand
    row["Packer Contact Information"]     = "Twenty2Yards The Milange Jakat Naka Virar West"
    row["Warranty Description"]           = warranty_text

    return row


async def amazon_step(state):
    UPDATED_DIR = os.path.join("uploads", "updated_files")
    os.makedirs(UPDATED_DIR, exist_ok=True)

    try:
        file_path = state.get("file_path", "")
        filename = state.get("filename", "")
        logging.info(f"[amazon_agent] amazon_step start file={filename} path={file_path}")
        if not os.path.exists(file_path):
            return {"status": 404, "message": "File not found..."}

        file_extension = os.path.splitext(filename)[1].lower()
        filename_no_ext = os.path.splitext(filename)[0]

        if file_extension == ".csv":
            file_info = pd.read_csv(file_path)
        elif file_extension in [".xlsx", ".xls"]:
            file_info = pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file format")

        input_rows = file_info.to_dict(orient="records")
        predata_records = load_predata_records(PREDATA_DIR)
        logging.info(f"[amazon_agent] loaded {len(input_rows)} rows, predata={len(predata_records)}")

        product_entries = []
        titles_to_scrape = []

        for row in input_rows:
            title = extract_input_title(row)
            if not title:
                continue
            entry = {
                "title": title,
                "source": "predata",
                "detail": find_predata_entry(title, predata_records),
            }
            if entry["detail"] is None:
                entry["source"] = "scrape_pending"
                cleaned_title = clean_title_for_scraper(title)
                titles_to_scrape.append(cleaned_title)
            product_entries.append(entry)
            

        if len(product_entries) > 10:
            logging.warning(
                f"[amazon_agent] too many products: {len(product_entries)} titles"
            )
            state['message'] = f"You have more than 10 products in the uploaded file. Please upload fewer than 10 products to get product details from online."
            return {
                "status": 400,
                "message": "You have more than 10 products. Please upload fewer than 10 products to get product details from online."
            }

        scraped_rows = []
        if titles_to_scrape:
            scraped_rows = await get_multi_source_product_pages(titles_to_scrape, format_type="amazon")

        scrape_index = 0
        for entry in product_entries:
            if entry["detail"] is not None:
                amazon_rows = await format_products([entry["detail"]], "amazon")
                amazon_row = amazon_rows[0] if amazon_rows else {}
                amazon_row["Item Name"] = expand_willow_abbreviations(entry["title"])
                entry["amazon_row"] = amazon_row
                entry["source"] = "predata_formatted"
            else:
                if scrape_index < len(scraped_rows):
                    scraped_row = dict(scraped_rows[scrape_index])
                    scraped_row["Item Name"] = expand_willow_abbreviations(entry["title"])
                    entry["amazon_row"] = scraped_row
                    entry["source"] = "scraped"
                else:
                    fallback_detail = {
                        "Title": entry["title"],
                        "brand": "SS" if entry["title"].upper().startswith("SS ") else "",
                        "category": "Cricket Bat" if "bat" in entry["title"].lower() else "Cricket Equipment",
                        "type": "Cricket Bat" if "bat" in entry["title"].lower() else "",
                        "description": f"High-quality cricket equipment: {entry['title']}",
                        "images": [], "tags": ["cricket"], "price": "", "variants": []
                    }
                    fallback_rows = await format_products([fallback_detail], "amazon")
                    entry["amazon_row"] = fallback_rows[0] if fallback_rows else {}
                    entry["source"] = "fallback"
                scrape_index += 1

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        output_file = os.path.join(
            UPDATED_DIR,
            f"{filename_no_ext}_amazon_{timestamp}.xlsx"
        )
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="Amazon Products")
        ws.append(AMAZON_HEADERS)
        # open DB connection for upserts
        conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)

        for entry in product_entries:
            amazon_row = entry.get("amazon_row", {})
            extra_images  = amazon_row.pop("_extra_images", [])
            bullet_points = amazon_row.pop("_bullet_points", [])

            # Build the output row respecting AMAZON_HEADERS order and duplicates
            bullet_idx  = 0
            img_url_idx = 1   # index into extra_images for "Other Image URL" slots
            img_loc_idx = 1   # index into extra_images for "Other Image Location" slots
            sf_idx      = 0   # Special Features index

            output_cells = []
            for header in AMAZON_HEADERS:
                if header == "Bullet Point":
                    val = bullet_points[bullet_idx] if bullet_idx < len(bullet_points) else ""
                    bullet_idx += 1
                elif header == "Other Image URL":
                    val = extra_images[img_url_idx] if img_url_idx < len(extra_images) else ""
                    img_url_idx += 1
                elif header == "Other Image Location":
                    val = extra_images[img_loc_idx] if img_loc_idx < len(extra_images) else ""
                    img_loc_idx += 1
                elif header == "Special Features":
                    bullets_tail = bullet_points[1:] if len(bullet_points) > 1 else []
                    val = bullets_tail[sf_idx] if sf_idx < len(bullets_tail) else ""
                    sf_idx += 1
                else:
                    val = amazon_row.get(header, "")
                output_cells.append(val)

            ws.append(output_cells)

            # UPSERT into product_details
            try:
                def safe_float(v):
                    if v in (None, ""):
                        return None
                    try:
                        return float(str(v).replace(',', '').strip())
                    except Exception:
                        return None

                product = {
                    'marketplace': 'amazon',
                    'marketplace_product_id': amazon_row.get('Product Id') or amazon_row.get('SKU') or '',
                    'sku': amazon_row.get('SKU') or amazon_row.get('Product Id') or '',
                    'title': amazon_row.get('Item Name'),
                    'product_name': amazon_row.get('Item Name'),
                    'description': amazon_row.get('Product Description'),
                    'brand': amazon_row.get('Brand Name'),
                    'manufacturer': amazon_row.get('Manufacturer'),
                    'product_type': amazon_row.get('Item Type Name'),
                    'category': amazon_row.get('Department Name'),
                    'tags': amazon_row.get('Generic Keywords'),
                    'mrp': safe_float(amazon_row.get('Maximum Retail Price (Sell on Amazon, IN)')),
                    'selling_price': safe_float(amazon_row.get('Your Price INR (Sell on Amazon, IN)')),
                    'currency': 'INR',
                    'main_image_url': amazon_row.get('Main Image URL'),
                    'other_image_url_1': amazon_row.get('Other Image URL') or '',
                    'bullet_points': json.dumps(bullet_points) if bullet_points else None,
                    'browse_nodes': amazon_row.get('Recommended Browse Nodes'),
                    'extra_attributes': amazon_row,
                }

                upsert_product_details(conn, product)
            except Exception as e:
                logging.warning(f"[amazon_agent] upsert failed for sku={amazon_row.get('SKU')} error={e}")

        wb.save(output_file)
        if conn:
            conn.close()
        return {"status": 200, "output_file_path": output_file,
                "output_file_name": os.path.basename(output_file)}

    except Exception as e:
        logging.error(f"[amazon_agent] Error: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}
