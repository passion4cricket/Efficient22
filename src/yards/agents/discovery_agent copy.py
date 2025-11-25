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

        # --- Scrape websites ---
        scraper_response = await get_multi_source_product_pages(product_titles)
        
        all_products = []

        # --- Process each product ---
        for product_detail in scraper_response:
            # SUPER STRICT prompt
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
                - Option1 Name MUST be "Size".
                - Option1 Value MUST include only the numeric or letter part (e.g., "Size 3" → "3", "Size L" → "L").
                - One object per variant. If no variants, create a single default variant.
                - Generate "Handle" from Title: lowercase, alphanumeric + hyphens, spaces → hyphens.
                - Wrap product description in "<p>...</p>".
                - "Image Src" must be absolute URLs. Assign "Image Position" sequentially for multiple images.

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

            try:
                # --- Call LLM ---
                extractor_response = await call_llm(
                    llm,
                    prompt,
                    sys_prompt,
                    user_prompt
                )

                raw_json = extractor_response.content.strip()
                raw_json = sanitize_json_online_llm(raw_json)

                # --- Parse JSON ---
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

                all_products.extend(extracted)
                print(all_products)

            except Exception as e:
                print(f"⚠️ Error extracting: {e}")

        # --- Write output CSV ---
        output_file = os.path.join(UPDATED_DIR, f"{filename_no_ext}.csv")
        
        manufacturer_details = {}
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHOPIFY_HEADERS)
            writer.writeheader()

            for item in all_products:
                if not isinstance(item, dict):
                    continue

                for key, value in list(item.items()):
                    if isinstance(value, list):
                        item[key] = ", ".join(map(str, value))

                clean_item = {}
                item_name = item.get("Title", "")
                brand_name = ''
                for k in SHOPIFY_HEADERS:
                    value = item.get(k, "")
                    if k == "Vendor":
                        print(f"VENDOR : {k}, Value: {value.lower()}")
                        price_info = {}
                        
                        MANUFACTURER_DIR = None
                        if "mrf" in value.lower():
                            MANUFACTURER_DIR = os.path.join("manufacturer", "mrf.xlsx")
                            brand_name = "mrf"
                        elif "moonwalkr" in value.lower():
                            MANUFACTURER_DIR = os.path.join("manufacturer", "moonwalkr.xlsx")
                            brand_name = "moonwalkr"
                        elif "sg" in value.lower():
                            MANUFACTURER_DIR = os.path.join("manufacturer", "sg.xlsx")
                            brand_name = "sg"

                        print(f"MANUFACTURER_DIR : {MANUFACTURER_DIR}, BRAND NAME: {brand_name}, VALUE: {not brand_name in manufacturer_details}")
                        
                        if brand_name and not brand_name in manufacturer_details:
                            if MANUFACTURER_DIR and os.path.exists(MANUFACTURER_DIR):
                                manufacturer_ext = os.path.splitext(MANUFACTURER_DIR)[1].lower()
                                if manufacturer_ext in [".xlsx", ".xls"]:
                                    manufacturer_info = pd.read_excel(MANUFACTURER_DIR)
                                    for row in manufacturer_info.to_dict(orient="records"):
                                        if pd.notna(row.get("Retailer Price in USD")):
                                            price_info[row['Sub Catergory'].lower()] = row['Retailer Price in USD']

                                    manufacturer_details[brand_name] = price_info
                                    print(manufacturer_details)

                    if brand_name and brand_name in manufacturer_details:
                        # print(f"item_name: {item_name}, {item_name.lower() in manufacturer_details[brand_name]}")
                        
                        if k in ["Variant Price", "Price / United States", "Price / International"]:
                            try:
                                print(f"Processing key: {k} with value: {value}")
                                cost_price = float(manufacturer_details[brand_name][item_name.lower()])
                                print(f"MINIMUM PRICE: {float(item.get(k))}")
                                lowest_price_websites = convert_inr_to_usd(float(item.get(k)))
                                calculated_price = price_conversion(cost_price, lowest_price_websites)
                                print(f"Price INFO: {lowest_price_websites}, {cost_price}, Calculated Price: {calculated_price}")
                                item[k] = round(calculated_price, 2)
                            except Exception as e:
                                print(f"PRICE CALCULATION ERROR: {e}")
                    
                    clean_item[k] = str(value)

                writer.writerow(clean_item)

        print(f"✅ Completed extraction for {filename_no_ext}, total products: {len(all_products)}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")
