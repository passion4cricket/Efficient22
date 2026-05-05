import json
import os
import sys
import pandas as pd
import csv
import re
import asyncio
from pathlib import Path
from openpyxl import Workbook

# Ensure src/ is on sys.path so package imports work from different entrypoints.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
        product_titles = []
        for row in file_info.to_dict(orient="records"):
            title = str(
                row.get("Title", "")
                or row.get("Product name in PI", "")
                or row.get("Product Name", "")
            ).strip()
            if title:
                product_titles.append(title)

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

        for item in scraper_response:
            print(item)
            ws.append([item.get(h, "") for h in SHOPIFY_HEADERS])
        wb.save(output_file)
        print(f"✅ Completed extraction for {filename_no_ext}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")   