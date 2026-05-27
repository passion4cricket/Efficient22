import json
import os
import pandas as pd
import csv
import re
import asyncio
from openpyxl import Workbook

from yards.utils.config import SHOPIFY_HEADERS, HOSTNAME, USERNAME, PASSWORD, DATABASE
from yards.utils.utils import llm_init, call_llm, price_conversion, convert_inr_to_usd
from yards.utils.scrape_data import get_multi_source_product_pages, sanitize_json, sanitize_json_online_llm
from yards.database.get_table_details import connect_db, upsert_product_details

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
        product_titles = []
        for row in file_info.to_dict(orient="records"):
            title = str(
                row.get("Title", "")
                or row.get("Product name in PI", "")
                or row.get("Product Name", "")
            ).strip()
            if title:
                product_titles.append(title)
        
        if len(product_titles) > 10:
            state['message'] = f"You have more than 10 products in the uploaded file. Please upload fewer than 10 products to get product details from online."
            return {
                "status": 400,
                "message": "You have more than 10 products. Please upload fewer than 10 products to get product details from online."
            }

        # --- Scrape data ---
        scraper_response = await get_multi_source_product_pages(product_titles, format_type="shopify")

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

        conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)

        for item in scraper_response:
            print(item)
            ws.append([item.get(h, "") for h in SHOPIFY_HEADERS])

            try:
                def safe_float(v):
                    if v in (None, ""):
                        return None
                    try:
                        return float(str(v).replace(',', '').strip())
                    except Exception:
                        return None

                product = {
                    'marketplace': 'shopify',
                    'marketplace_product_id': item.get('Handle') or item.get('Variant SKU') or '',
                    'sku': item.get('Variant SKU') or item.get('Variant SKU') or '',
                    'title': item.get('Title'),
                    'product_name': item.get('Title'),
                    'description': item.get('Body (HTML)'),
                    'vendor': item.get('Vendor'),
                    'product_type': item.get('Type'),
                    'product_category': item.get('Product Category'),
                    'tags': item.get('Tags'),
                    'mrp': safe_float(item.get('Variant Compare At Price')),
                    'selling_price': safe_float(item.get('Variant Price')),
                    'currency': 'INR',
                    'main_image_url': item.get('Image Src'),
                    'seo_title': item.get('SEO Title'),
                    'seo_description': item.get('SEO Description'),
                    'option1_name': item.get('Option1 Name'),
                    'option1_value': item.get('Option1 Value'),
                    'option2_name': item.get('Option2 Name'),
                    'option2_value': item.get('Option2 Value'),
                    'option3_name': item.get('Option3 Name'),
                    'option3_value': item.get('Option3 Value'),
                    'extra_attributes': item,
                }

                upsert_product_details(conn, product)
            except Exception as e:
                print(f"[discovery_agent] upsert failed for handle={item.get('Handle')} error={e}")

        if conn:
            conn.close()
        wb.save(output_file)
        print(f"✅ Completed extraction for {filename_no_ext}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")   