import json
import logging
import os
import re
import pandas as pd
from bs4 import BeautifulSoup
from openpyxl import Workbook
from rapidfuzz import fuzz, process
from yards.utils.config import AMAZON_HEADERS
from yards.utils.scrape_data import get_multi_source_product_pages, format_products

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
    title = str(
        product_detail.get("Title")
        or product_detail.get("title")
        or product_detail.get("item-name")
        or product_detail.get("item_name")
        or product_detail.get("Official Site Title")
        or ""
    ).strip()
    title = expand_willow_abbreviations(title)

    brand = str(
        product_detail.get("brand", "")
        or product_detail.get("Brand", "")
        or product_detail.get("brand-name", "")
        or product_detail.get("brand_name", "")
        or ""
    ).strip()
    category = str(
        product_detail.get("category", "")
        or product_detail.get("Category", "")
        or product_detail.get("feed_product_type", "")
        or ""
    ).strip()
    item_type = str(
        product_detail.get("type", "")
        or product_detail.get("Type", "")
        or product_detail.get("item-type", "")
        or category
    ).strip()
    description = str(
        product_detail.get("description", "")
        or product_detail.get("Body HTML", "")
        or product_detail.get("Body (HTML)", "")
        or product_detail.get("body_html", "")
    )
    description_plain = clean_text(description)
    tags = product_detail.get("tags", [])
    if isinstance(tags, str):
        if tags.strip():
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
        else:
            tags = []
    if not tags:
        tags = product_detail.get("Tags") or product_detail.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
    price_info = product_detail.get("price", {})
    if not price_info:
        price_info = product_detail.get("Price", product_detail.get("Variant Price", {}))
    if isinstance(price_info, dict):
        price_value = price_info.get("value", "")
        currency = price_info.get("currency", "")
    else:
        price_value = price_info
        currency = ""

    if isinstance(price_value, str):
        price_text = price_value.strip()
        if price_text:
            match = re.search(r"([0-9]+(?:[\.,][0-9]{1,2})?)", price_text.replace(",", ""))
            if match:
                try:
                    price_value = float(match.group(1))
                except ValueError:
                    pass

    if not currency:
        currency = str(
            product_detail.get("currency", "")
            or (product_detail.get("price") or {}).get("currency", "")
            or product_detail.get("Price currency", "")
        ).strip()
        if not currency and isinstance(price_info, str):
            if "\u20b9" in price_info or "inr" in price_info.lower():
                currency = "INR"
            elif "$" in price_info or "usd" in price_info.lower():
                currency = "USD"
            elif "£" in price_info or "gbp" in price_info.lower():
                currency = "GBP"

    images = product_detail.get("images", []) or []
    if not images:
        images = product_detail.get("Image Src") or product_detail.get("images") or product_detail.get("image_src") or []
    if isinstance(images, str):
        images = [images]
    if isinstance(images, dict):
        images = [images]
    images = [str(img).strip() for img in images if str(img).strip()]

    bullet_points = extract_bullet_points(description_plain)
    material = ""
    for tag in tags:
        if tag.lower() in ["leather", "wood", "carbon", "inox", "polyester", "nylon", "rubber", "foam"]:
            material = tag
            break

    size_value = extract_first_variant_size(product_detail.get("variants", []))
    color_value = ""
    for tag in tags:
        if tag.lower() in ["red", "blue", "black", "white", "yellow", "green", "gray", "orange", "pink", "brown"]:
            color_value = tag
            break

    row = {header: "" for header in AMAZON_HEADERS}
    row.update({
        "sku": str(product_detail.get("official_sku", "") or product_detail.get("sku", "") or ""),
        "product-id": str(product_detail.get("official_sku", "") or ""),
        "product-id-type": "ASIN" if product_detail.get("official_sku", "") else "",
        "item-name": title,
        "brand-name": brand,
        "manufacturer": brand,
        "item-type": item_type,
        "feed_product_type": category or item_type,
        "product-description": description_plain,
        "search-terms": ", ".join([t.lower() for t in tags if t]),
        "department": "Sports",
        "sport-type": "Cricket",
        "material-type": material,
        "color": color_value,
        "size": size_value,
        "style-name": item_type or title,
        "outer-material-type": material,
        "price": str(price_value),
        "quantity": "1",
        "condition-type": "New",
        "main-image-url": images[0] if images else "",
        "other-image-url1": images[1] if len(images) > 1 else "",
        "other-image-url2": images[2] if len(images) > 2 else "",
        "other-image-url3": images[3] if len(images) > 3 else "",
        "item-weight": str(product_detail.get("weight", "") or ""),
        "item-weight-unit-of-measure": str(product_detail.get("weight_unit", "") or ""),
        "item-package-dimensions": "",
        "item-package-weight": "",
        "country-of-origin": str(product_detail.get("country_of_origin", "") or ""),
        "manufacturer-contact-information": "",
        "update_delete": "Update",
    })

    for idx, bullet in enumerate(bullet_points, start=1):
        row[f"bullet-point{idx}"] = bullet

    return row


async def amazon_step(state):
    UPDATED_DIR = os.path.join("uploads", "updated_files")
    os.makedirs(UPDATED_DIR, exist_ok=True)

    try:
        file_path = state.get("file_path", "")
        filename = state.get("filename", "")
        logging.info(f"[amazon_agent] amazon_step start file={filename} path={file_path} user_id={state.get('user_id')}")
        if not os.path.exists(file_path):
            logging.error(f"[amazon_agent] file not found path={file_path}")
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
        logging.info(f"[amazon_agent] loaded {len(input_rows)} rows from {filename}, predata_records={len(predata_records)}")

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
                # Clean abbreviations before passing to scraper
                cleaned_title = clean_title_for_scraper(title)
                titles_to_scrape.append(cleaned_title)
                logging.info(f"[amazon_agent] will scrape title='{title}' (cleaned: '{cleaned_title}')")
            else:
                logging.info(f"[amazon_agent] matched predata for title='{title}'")

            product_entries.append(entry)

        scraped_rows = []
        if titles_to_scrape:
            logging.info(f"[amazon_agent] looking up {len(titles_to_scrape)} titles via scraper")
            scraped_rows = await get_multi_source_product_pages(titles_to_scrape, format_type="amazon")
            logging.info(f"[amazon_agent] scraper returned {len(scraped_rows)} amazon rows")
            for i, row in enumerate(scraped_rows):
                logging.info(f"[amazon_agent] scraped_row[{i}]: title='{row.get('item-name', 'N/A')}' brand='{row.get('brand-name', 'N/A')}' price='{row.get('price', 'N/A')}'")
        else:
            logging.info(f"[amazon_agent] no titles to scrape")

        scrape_index = 0
        for entry in product_entries:
            if entry["detail"] is not None:
                amazon_rows = await format_products([entry["detail"]], "amazon")
                amazon_row = amazon_rows[0] if amazon_rows else {}
                # Always use original input title, not the predata title
                amazon_row["item-name"] = expand_willow_abbreviations(entry["title"])
                entry["amazon_row"] = amazon_row
                entry["source"] = "predata_formatted"
                predata_file = entry["detail"].get("_predata_file", "")
                logging.info(f"[amazon_agent] product='{entry['title']}' source=predata_formatted file={predata_file}")
            else:
                if scrape_index < len(scraped_rows):
                    scraped_row = dict(scraped_rows[scrape_index])  # copy, never mutate original
                    scraped_title = scraped_row.get("item-name", "")
                    original_title = entry["title"]

                    # Always restore original input title — scraper may return wrong product name
                    correct_title = expand_willow_abbreviations(entry["title"])
                    if scraped_title != correct_title:
                        logging.info(
                            f"[amazon_agent] title override: scraped='{scraped_title}' → '{correct_title}'"
                        )
                    scraped_row["item-name"] = correct_title

                    entry["amazon_row"] = scraped_row
                    entry["source"] = "scraped"
                    logging.info(f"[amazon_agent] product='{original_title}' source=scraped")
                else:
                    # fallback - create a more complete row with defaults
                    fallback_title = entry["title"]
                    fallback_detail = {
                        "Title": fallback_title,
                        "brand": "SS" if fallback_title.upper().startswith("SS ") else "",
                        "category": "Cricket Bat" if "bat" in fallback_title.lower() else "Cricket Equipment",
                        "type": "Cricket Bat" if "bat" in fallback_title.lower() else "",
                        "description": f"High-quality cricket equipment: {fallback_title}",
                        "images": [],
                        "tags": ["cricket"],
                        "price": "",
                        "variants": []
                    }
                    fallback_rows = await format_products([fallback_detail], "amazon")
                    entry["amazon_row"] = fallback_rows[0] if fallback_rows else {}
                    entry["source"] = "fallback"
                    logging.warning(f"[amazon_agent] no data found for title='{entry['title']}', using enhanced fallback row")
                    logging.info(f"[amazon_agent] product='{entry['title']}' source=fallback")
                scrape_index += 1

        logging.info(f"[amazon_agent] total product_entries={len(product_entries)}")
        output_file = os.path.join(UPDATED_DIR, f"{filename_no_ext}_amazon.xlsx")
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="Amazon Products")
        ws.append(AMAZON_HEADERS)

        for entry in product_entries:
            amazon_row = entry.get("amazon_row", {})
            ws.append([amazon_row.get(h, "") for h in AMAZON_HEADERS])

        wb.save(output_file)
        return {"status": 200, "output_file_path": output_file, "output_file_name": os.path.basename(output_file)}

    except Exception as e:
        logging.error(f"[amazon_agent] Error in amazon_step: {e}", exc_info=True)
        return {"status": 500, "message": str(e)}
