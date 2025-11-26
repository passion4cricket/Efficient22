import json
import os
import pandas as pd
import csv
import re
import asyncio
from yards.utils.config import SHOPIFY_HEADERS
from yards.utils.utils import llm_init, call_llm, price_conversion, convert_inr_to_usd
from yards.utils.scrape_data import get_multi_source_product_pages, sanitize_json, sanitize_json_online_llm

llm, prompt = llm_init()


async def discovery_step(state):
    UPDATED_DIR = os.path.join("uploads", "updated_files")    
    os.makedirs(UPDATED_DIR, exist_ok=True)

    try:
        file_path = state.get("file_path", "")
        filename = state.get("filename", "")
        if not os.path.exists(file_path):
            return {"status": 404, "message": "File not found..."}

        print(f"Processing file: {filename}")

        file_extension = os.path.splitext(filename)[1].lower()
        filename_no_ext = os.path.splitext(filename)[0]

        # --- Load input file ---
        if file_extension == ".csv":
            file_info = pd.read_csv(file_path)
        elif file_extension in [".xlsx", ".xls"]:
            file_info = pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file format")

        # --- Extract product titles ---
        product_titles = [
            row["Title"]
            for row in file_info.to_dict(orient="records")
            if pd.notna(row.get("Title", "") and row.get("Title", "").strip() != "")
        ]

        # --- Scrape data ---
        scraper_response = await get_multi_source_product_pages(product_titles)

        # --- Prepare CSV output file ---
        output_file = os.path.join(UPDATED_DIR, f"{filename_no_ext}.csv")
        manufacturer_details = {}

        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHOPIFY_HEADERS)
            writer.writeheader()

            # --------------------------------------------
            # PROCESS EACH PRODUCT AND WRITE IMMEDIATELY
            # --------------------------------------------
            for product_detail in scraper_response:

                # STRICT PROMPT
                user_prompt = f"""
                    Convert the following product data into a Shopify CSV-compatible JSON array.

                    RULES (STRICT):
                    1. Output ONLY a valid JSON array. No text, markdown, comments, or explanations.
                    2. Each array item MUST represent exactly ONE variant.
                    3. Each object MUST contain ALL Shopify CSV headers EXACTLY as listed. No extra keys.
                    4. Use ONLY flat string values. If a field is missing, use "".
                    5. Wrap all keys and string values in double quotes.
                    6. FINAL output MUST parse with json.loads() without corrections.
                    7. Vendor MUST be the product's brand value. If brand is missing, use "".

                    VARIANT RULES:
                    - Variants MUST be created ONLY based on these attributes:
                        • Color
                        • Size
                        • Material
                    - Do NOT generate variants based on image count, image URLs, price differences, or stock.
                    - If the product contains multiple values for any of the above (Color, Size, Material), generate variant combinations.
                    - If the product has none of these variant attributes, generate ONLY ONE variant.
                    - Option1 Name MUST be "Size" if size exists; otherwise use the next available attribute.
                    - Option2 Name MUST be "Color" if color exists.
                    - Option3 Name MUST be "Material" if material exists.
                    - Option values MUST contain only clean text (e.g., "Size 3" → "3", "Red Color" → "Red").
                    - Do NOT create a variant per image.
                    - Images must be duplicated across variants:
                        • Repeat the same variant record for each image
                        • Change ONLY:
                            - "Image Src"
                            - "Image Position"
                            - "Image Alt Text"
                    - Generate "Handle" from Title: lowercase, alphanumeric + hyphens, spaces → hyphens.
                    - Wrap product description in "<p>...</p>".
                    - "Image Src" must be absolute URLs.
                    - Assign "Image Position" sequentially for multiple images.

                    SEO RULES:
                    - If missing, generate intelligently:
                    - "SEO Title": short and keyword-rich
                    - "SEO Description": one sentence highlighting purpose/benefit
                    - "Image Alt Text": descriptive and SEO friendly
                    - "Tags": comma-separated keywords
                    - Google Shopping fields: infer if possible, else ""
                    - "Condition" defaults to "new"

                    SHOPIFY HEADERS:
                    {", ".join(SHOPIFY_HEADERS)}

                    Input product data:
                    {product_detail}

                """

                sys_prompt = """
                    You are an expert Shopify product data builder and eCommerce SEO specialist.

                    Strict output rule:
                    - Return **pure JSON only**.
                    - The output must **begin with `[` and end with `]`**.
                    - Do **not** include any explanations, text, labels, or markdown.
                    - Do **not** prefix with lines like “Here is the processed JSON array:” or “Output:”.
                    - Use empty strings ("") for missing text values and empty arrays ([]) for missing list values.
                    - Remove all newline characters inside the "Body (HTML)".
                    - Escape all double quotes (") as ".
                """

                # -------------------------
                # CALL LLM FOR THIS PRODUCT
                # -------------------------
                try:
                    extractor_response = await call_llm(
                        llm, prompt, sys_prompt, user_prompt
                    )

                    raw_json = extractor_response.content.strip()
                    raw_json = sanitize_json_online_llm(raw_json)

                    try:
                        extracted = json.loads(raw_json)
                    except:
                        extracted = json.loads(sanitize_json_online_llm(raw_json))

                    # Normalize to list
                    if isinstance(extracted, dict):
                        extracted = [extracted]
                    elif isinstance(extracted, str):
                        try:
                            extracted = json.loads(extracted)
                        except:
                            extracted = []

                    # ----------------------------------------------
                    # WRITE EACH VARIANT (each dict) TO CSV DIRECTLY
                    # ----------------------------------------------
                    for item in extracted:

                        if not isinstance(item, dict):
                            continue

                        # Convert list → string
                        for key, value in list(item.items()):
                            if isinstance(value, list):
                                item[key] = ", ".join(map(str, value))

                        clean_item = {}
                        item_name = item.get("Title", "").strip()
                        brand_name = ""

                        # --------------------------------------------
                        # POPULATE ALL SHOPIFY HEADERS
                        # --------------------------------------------
                        for k in SHOPIFY_HEADERS:
                            value = item.get(k, "")

                            # --------------------------------------------
                            # MANUFACTURER PRICE LOGIC — SAME AS YOUR CODE
                            # --------------------------------------------
                            if k == "Vendor":
                                vendor = value.lower()

                                MANUFACTURER_DIR = None
                                if "mrf" in vendor:
                                    MANUFACTURER_DIR = os.path.join("manufacturer", "mrf.xlsx")
                                    brand_name = "mrf"
                                elif "moonwalkr" in vendor:
                                    MANUFACTURER_DIR = os.path.join("manufacturer", "moonwalkr.xlsx")
                                    brand_name = "moonwalkr"
                                elif "sg" in vendor:
                                    MANUFACTURER_DIR = os.path.join("manufacturer", "sg.xlsx")
                                    brand_name = "sg"

                                # Load manufacturer details only once
                                if brand_name and brand_name not in manufacturer_details:
                                    if MANUFACTURER_DIR and os.path.exists(MANUFACTURER_DIR):
                                        df = pd.read_excel(MANUFACTURER_DIR)
                                        price_map = {}

                                        for row in df.to_dict(orient="records"):
                                            subcat = row.get("Sub Catergory")
                                            usd = row.get("Retailer Price in USD")
                                            if pd.notna(subcat) and pd.notna(usd):
                                                price_map[str(subcat).lower()] = usd

                                        manufacturer_details[brand_name] = price_map

                            # --------------------------------------------
                            # PRICE UPDATE LOGIC
                            # --------------------------------------------
                            if brand_name and brand_name in manufacturer_details:
                                if k in ["Variant Price", "Price / United States", "Price / International"]:
                                    try:
                                        cost_price = float(
                                            manufacturer_details[brand_name][item_name.lower()]
                                        )
                                        base_price = float(item.get(k))
                                        # lowest_price = convert_inr_to_usd(base_price)
                                        # value = round(
                                        #     price_conversion(cost_price, lowest_price), 2
                                        # )
                                    except Exception as e:
                                        print(f"Price calc error: {e}")

                            clean_item[k] = str(value)

                        # WRITE THE ROW
                        writer.writerow(clean_item)

                except Exception as e:
                    print(f"⚠️ Error extracting: {e}")

        print(f"✅ Completed extraction for {filename_no_ext}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")
