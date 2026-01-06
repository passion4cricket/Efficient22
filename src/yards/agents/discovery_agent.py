import json
import os
import pandas as pd
import csv
import re
import asyncio
from openpyxl import Workbook
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
        # product_titles = [
        #     row["Title"]
        #     for row in file_info.to_dict(orient="records")
        #     if pd.notna(row.get("Title", "") and row.get("Title", "").strip() != "")
        # ]

        product_titles = [
            row["Product name in PI"]
            for row in file_info.to_dict(orient="records")
            if pd.notna(row.get("Product name in PI", "") and row.get("Product name in PI", "").strip() != "")
        ]

        # --- Scrape data ---
        scraper_response = await get_multi_source_product_pages(product_titles)

        # --- Prepare CSV output file ---
        output_file = os.path.join(UPDATED_DIR, f"{filename_no_ext}.xlsx")
        manufacturer_details = {}

        # with open(output_file, "w", newline="", encoding="utf-8") as f:
            # writer = csv.DictWriter(f, fieldnames=SHOPIFY_HEADERS)
            # writer.writeheader()

        wb = Workbook(write_only=True)

        ws = wb.create_sheet(title="Shopify Products")

        # Write header
        ws.append(SHOPIFY_HEADERS)

        for product_detail in scraper_response:

            user_prompt = f"""
               You are an expert Shopify product data builder and eCommerce SEO specialist.

                Strict output rule:
                - Return pure JSON only.
                - The output must begin with [ and end with ].
                - Do not include explanations, labels, markdown, or commentary.
                - Use empty strings ("") for missing text values.
                - Use empty arrays ([]) for missing list values.
                - Escape all double quotes (") as \".
                - Remove all newline characters inside "Body (HTML)".

                --------------------------------------------------
                CRITICAL OPTION ENFORCEMENT (HIGHEST PRIORITY)
                --------------------------------------------------
                - Option Names MUST ONLY appear if their corresponding Option Value is NON-EMPTY.
                - If an Option Value is "", the Option Name MUST also be "".
                - It is STRICTLY FORBIDDEN to return:
                • "Option2 Name": "Color" with "Option2 Value": ""
                • "Option3 Name": "Material" with "Option3 Value": ""
                - If ANY Option Name exists with an empty Option Value → DROP THE ENTIRE OBJECT.
                - Invalid objects MUST NOT be included in the output array.

                --------------------------------------------------
                VARIANT CREATION RULES
                --------------------------------------------------
                - Variants may ONLY be created using:
                • Size
                • Color
                • Material
                - DO NOT create variants from:
                • price
                • SKU
                • inventory
                • images
                - If NONE of Size, Color, Material exist → create EXACTLY ONE variant.
                - If ONLY Size exists → ONLY Option1 is allowed.
                - Option positions MUST NEVER SHIFT.

                --------------------------------------------------
                OPTION POSITION RULES (FIXED & STRICT)
                --------------------------------------------------
                - Option1 Name = "Size" ONLY if Size exists.
                - Option2 Name = "Color" ONLY if Color exists.
                - Option3 Name = "Material" ONLY if Material exists.
                - DO NOT output placeholder or inferred options.
                - Clean option values only:
                • "Harrow Size" → "Harrow"
                • "LB Size" → "LB"
                • "Red Color" → "Red"

                --------------------------------------------------
                TITLE & HANDLE RULES (VERY IMPORTANT)
                --------------------------------------------------
                - Title MUST be:
                • Proper case
                • Human-readable
                • Brand + Product Name
                • NO hyphens
                • NO lowercase-only formatting
                - Handle MUST be generated FROM Title using:
                • lowercase
                • spaces → hyphens
                • alphanumeric + hyphens only
                - Title MUST NEVER resemble a handle.
                - Handle MUST NEVER be reused as Title.

                --------------------------------------------------
                PRODUCT DESCRIPTION RULES
                --------------------------------------------------
                - "Body (HTML)" MUST:
                • be wrapped in <p>...</p>
                • contain NO newline characters
                - Preserve original marketing content.

                --------------------------------------------------
                IMAGE RULES
                --------------------------------------------------
                - Do NOT create variants per image.
                - Duplicate variant rows per image:
                • Change ONLY Image Src, Image Position, Image Alt Text
                - Image Src MUST be a valid absolute URL.
                - Image Position starts from 1.

                --------------------------------------------------
                SEO RULES
                --------------------------------------------------
                Generate ONLY if missing:
                - SEO Title (short, keyword-rich)
                - SEO Description (single sentence, benefit-driven)
                - Image Alt Text (descriptive)
                - Tags (comma-separated)
                - Condition defaults to "new"

                --------------------------------------------------
                FINAL VALIDATION (MANDATORY)
                --------------------------------------------------
                - If ANY rule is violated → DROP the object.
                - Output MUST be valid JSON and parse with json.loads().
                - Output MUST contain NOTHING except the JSON array.

                --------------------------------------------------
                SHOPIFY HEADERS:
                {", ".join(SHOPIFY_HEADERS)}

                --------------------------------------------------
                Input product data:
                {product_detail}



            """

            sys_prompt = """
                You are an expert Shopify product data builder and eCommerce SEO specialist.

                Strict output rules:
                - Return PURE JSON only.
                - The output MUST begin with "[" and end with "]".
                - Do NOT include explanations, text, labels, or markdown.
                - Do NOT prefix with lines like “Here is the processed JSON array:” or “Output:”.
                - Use empty strings ("") for missing text values and empty arrays ([]) for missing list values.
                - "Body (HTML)" must not contain newline characters (\n), but must preserve valid HTML tags.
                - Escape double quotes ONLY when they appear INSIDE string values.
                - The final output MUST parse successfully using json.loads().

            """


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


                    for k in SHOPIFY_HEADERS:
                        value = item.get(k, "")

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
                    # writer.writerow(clean_item)

                    print(clean_item)
                    ws.append([clean_item.get(h, "") for h in SHOPIFY_HEADERS])


            except Exception as e:
                print(f"⚠️ Error extracting: {e}")

        wb.save(output_file)
        print(f"✅ Completed extraction for {filename_no_ext}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")
