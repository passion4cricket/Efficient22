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
        product_titles = [
            row["Title"]
            for row in file_info.to_dict(orient="records")
            if pd.notna(row.get("Title", "") and row.get("Title", "").strip() != "")
        ]

        #product_titles = [
        #    row["Product name in PI"]
        #    for row in file_info.to_dict(orient="records")
        #   if pd.notna(row.get("Product name in PI", "") and row.get("Product name in PI", "").strip() != "")
        # ]

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
                BODY HTML / PRODUCT DESCRIPTION RULES (MANDATORY)
                --------------------------------------------------
                - Use the value from "Body HTML" in the input product data as the
                  starting point. Do NOT rewrite it from scratch.
                - If "Body HTML" in the input is non-empty → clean it as described
                  below and use it directly as "Body (HTML)" in your output.
                - Output must use only these tags: <p>, <ul>, <li>, <strong>, <h2>, <h3>.
                - No newline characters (\n) inside the HTML string.

                SPELLING AND GRAMMAR — fix all of these without exception:
                • "Color may very"     → "Color may vary"
                • "Colour may very"    → "Colour may vary"
                • "Pride to made"      → "Proudly made"
                • "Signature bat"      → "Autograph bat"  (in autograph kit context)
                • Fix any other obvious spelling or grammar mistakes in the text.

                REMOVE — strip out these lines completely, they are NOT product details:
                • Any line containing "Manufactured and Marketed by:"
                • Any line containing "Manufactured by:"
                • Any line containing "Marketed by:"
                • Any full postal address (road name, city, district, PIN code, state, country)
                • Any company registration or legal disclaimer text
                • Shipping, return, or delivery policy text
                • Website navigation fragments

                KEEP — include only genuine product information:
                • Product features and specifications
                • Kit contents / what is included in the box / net quantity
                • Materials and construction details
                • Size, weight, color options
                • Performance or usage details

                --------------------------------------------------
                IMAGE RULES
                --------------------------------------------------
                - Use ONLY unique image URLs.
                - If the same URL appears multiple times → include it ONLY ONCE
                  at Image Position 1. Do NOT repeat the same URL in multiple rows.
                - Create a new row (duplicate) ONLY when Image Src URLs are
                  genuinely different from each other.
                - If only one unique image URL is available → output exactly ONE
                  row with Image Position = 1. Do NOT repeat it.
                - Image Src MUST be a valid absolute URL starting with https://.
                - Image Position starts at 1 and increments only for truly different URLs.
                - Image Alt Text format: "[Product Title] - [Brand Name]"

                --------------------------------------------------
                SEO RULES
                --------------------------------------------------
                - SEO Title: Copy the product Title exactly as-is. Do not modify it.
                - SEO Description:
                  • If "SEO Description" in the input product data is non-empty →
                    use it directly. Do NOT rewrite it.
                  • If empty → write ONE benefit-driven sentence, max 160 characters,
                    covering: what the product is, key contents or feature, brand name.
                  • Must NOT be a comma-joined list of words.
                  • Must NOT contain vague filler: "a well-known brand", "best quality",
                    "order today", "buy now", "free shipping".
                  • Must NOT contain any manufacturer address or company details.
                  • Good example: "SS Toe Guard Kit includes Fevibond, Toe Guard and
                    sandpaper for secure bat toe protection during cricket."
                - Tags: comma-separated, lowercase, relevant cricket keywords only.
                - Condition defaults to "new".

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
                ADDITIONAL REQUIRED FIELDS:
                These 2 fields MUST be included in every JSON object in the output array.
                They are NOT part of the Shopify headers but MUST appear alongside them in every row.

                1. "Official Site Title"
                   - Value: {product_detail.get("Official Site Title", "")}
                   - Copy this value exactly as-is. Do NOT modify, summarize, or rewrite it.
                   - If empty → use "".

                2. "Official Site Description"
                   - Value: {product_detail.get("Official Site Description", "")}
                   - Copy this value exactly as-is. Do NOT modify, summarize, or rewrite it.
                   - If empty → use "".

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
                - Do NOT prefix with lines like "Here is the processed JSON array:" or "Output:".
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
                except Exception:
                    try:
                        extracted = json.loads(sanitize_json_online_llm(raw_json))
                    except Exception as json_err:
                        print(f"⚠️ JSON parse failed, skipping product: {json_err}")
                        extracted = []   # skip this product gracefully, don't crash

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
                            # If LLM left Vendor empty, use brand from scraper output
                            if not value or str(value).strip() == "":
                                value = product_detail.get("brand", "") or item.get("brand", "")
                            vendor = str(value).lower()

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

                    # ── Fix missing/inconsistent fields before writing to Excel ──

                    # Vendor fallback
                    if not clean_item.get("Vendor") or clean_item["Vendor"] in ("", "None"):
                        clean_item["Vendor"] = product_detail.get("brand", "") or item.get("brand", "")

                    # Shopify required defaults
                    if not clean_item.get("Variant Inventory Tracker"):
                        clean_item["Variant Inventory Tracker"] = "shopify"
                    if not clean_item.get("Variant Inventory Policy"):
                        clean_item["Variant Inventory Policy"] = "deny"
                    if not clean_item.get("Variant Fulfillment Service"):
                        clean_item["Variant Fulfillment Service"] = "manual"
                    if not clean_item.get("Variant Weight Unit"):
                        clean_item["Variant Weight Unit"] = "kg"
                    if not clean_item.get("Status") or clean_item["Status"] in ("", "None"):
                        clean_item["Status"] = "active"
                    if not clean_item.get("Included / United States"):
                        clean_item["Included / United States"] = "true"
                    if not clean_item.get("Included / International"):
                        clean_item["Included / International"] = "true"

                    # Image Alt Text auto-generation
                    if not clean_item.get("Image Alt Text") or clean_item["Image Alt Text"] in ("", "None"):
                        title  = clean_item.get("Title", "")
                        vendor = clean_item.get("Vendor", "")
                        clean_item["Image Alt Text"] = f"{title} - {vendor}".strip(" -")

                    # ── BLANK OUT unwanted columns ─────────────────────────────
                    # These columns are intentionally left empty in every row.
                    # Option names/values, SKU, and Grams are not required.
                    for blank_col in [
                        "Option1 Name", "Option1 Value",
                        "Option2 Name", "Option2 Value",
                        "Option3 Name", "Option3 Value",
                        "Variant SKU",
                        "Variant Grams",
                    ]:
                        clean_item[blank_col] = ""
                    # ──────────────────────────────────────────────────────────

                    # Passthrough: Official Site Title and Description from scraper
                    clean_item["Official Site Title"] = str(
                        product_detail.get("Official Site Title", "")
                        or item.get("Official Site Title", "")
                    )
                    clean_item["Official Site Description"] = str(
                        product_detail.get("Official Site Description", "")
                        or item.get("Official Site Description", "")
                    )

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