import json
import os, pandas as pd, csv, re, asyncio
from yards.utils.config import SHOPIFY_HEADERS, PROMPT_TEMPLATES
from yards.utils.utils import llm_init, call_llm
from yards.utils.scrape_data import get_multi_source_product_pages

llm, prompt = llm_init()

# -------- Helper Functions --------
MAX_TOKENS_PER_REQUEST = 4000  # stay well below Groq 6000 TPM limit

def chunk_text(text, max_length=MAX_TOKENS_PER_REQUEST):
    """Split text into manageable chunks (approx tokens)."""
    return [text[i:i + max_length] for i in range(0, len(text), max_length)]

def sanitize_json(text: str) -> str:
    """Try to fix malformed JSON returned by LLM."""
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    if not text.startswith("[") and not text.startswith("{"):
        start = text.find("[")
        if start != -1:
            text = text[start:]
    if text.count('"') % 2 != 0:
        text += '"'
    return text


# -------- Main Discovery Step --------
async def discovery_step(state):
    UPDATED_DIR = os.path.join("uploads", "updated_files")
    os.makedirs(UPDATED_DIR, exist_ok=True)

    try:
        file_path = state.get("file_path", "")
        filename = state.get("filename", "")
        if not os.path.exists(file_path):
            return {"status": 404, "message": "File not found..."}

        product_titles = []
        missing_info = []

        print(f"Processing file: {filename}")
        file_extension = os.path.splitext(filename)[1].lower()
        filename_no_ext = os.path.splitext(filename)[0]

        # --- Load product titles ---
        if file_extension == ".csv":
            file_info = pd.read_csv(file_path)
        elif file_extension in [".xlsx", ".xls"]:
            file_info = pd.read_excel(file_path)
        else:
            raise ValueError("Unsupported file format")

        for data in file_info.to_dict(orient="records"):
            title = data.get("Title", "")
            if pd.notna(title) and title.strip() != "":
                product_titles.append(title)
                missing_info.append(title)

        # --- Scrape product data ---
        scraper_response = await get_multi_source_product_pages(product_titles)

        if not scraper_response:
            chunks = [f"{PROMPT_TEMPLATES['user_prompt_prod_details']}\n\n(No scraped data found — skip processing)"]
        else:
            raw_scraped = str(scraper_response)
            chunks = chunk_text(raw_scraped)

        all_products = []

        # --- Process each chunk through LLM ---
        for i, chunk in enumerate(chunks, start=1):
            print(f"🧩 Processing chunk {i}/{len(chunks)} (length={len(chunk)})")
            user_prompt = (
                f"{PROMPT_TEMPLATES['user_prompt_prod_details']}\n\n"
                f"💡 Variant Expansion Rule (critical):\n"
                f"- Detect variant-defining fields such as Size, Grade, Weight, Model, or Color from scraped data.\n"
                f"- Map them to Shopify Option fields:\n"
                f"  * 'Size' → Option1 Name/Value\n"
                f"  * 'Color' → Option2 Name/Value\n"
                f"  * 'Grade', 'Edition', 'Profile' → Option1 Name/Value (if Size not present)\n"
                f"- Ensure each variant becomes a **separate JSON object** with its Option1 Value populated.\n\n"
                f"Scraped data (part {i} of {len(chunks)}):\n{chunk}"
            )


            try:
                extractor_response = await call_llm(
                    llm,
                    prompt,
                    PROMPT_TEMPLATES['get_column_details'],
                    user_prompt,
                )

                raw_json = extractor_response.content.strip()
                raw_json = sanitize_json(raw_json)                

                try:
                    extracted = json.loads(raw_json)
                except json.JSONDecodeError:
                    extracted = json.loads(sanitize_json(raw_json))

                if isinstance(extracted, dict):
                    extracted = [extracted]
                elif isinstance(extracted, str):
                    try:
                        extracted = json.loads(extracted)
                    except:
                        extracted = []

                all_products.extend(extracted)

            except Exception as e:
                print(f"⚠️ Error extracting JSON for chunk {i}: {e}")

            # Respect rate limit between calls
            await asyncio.sleep(1)

        # --- Write merged results to CSV ---
        output_file = os.path.join(UPDATED_DIR, f"{filename_no_ext}.csv")
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SHOPIFY_HEADERS)
            writer.writeheader()

            for item in all_products:
                if not isinstance(item, dict):
                    continue
                for key, value in list(item.items()):
                    if isinstance(value, list):
                        item[key] = ", ".join(map(str, value))
                writer.writerow(item)

        print(f"✅ Completed extraction for {filename_no_ext}, total products: {len(all_products)}")

    except Exception as e:
        print(f"❌ Error in discovery_step: {e}")

        
        
"""
    Handle, Title, Body (HTML), Vendor, Product Category, Type, Tags, Published, Option1 Name, Option1 Value, Option2 Name, Option2 Value, Option3 Name, Option3 Value,Variant SKU, Variant Grams, Variant Inventory Tracker, Variant Inventory Qty,Variant Inventory Policy, Variant Fulfillment Service, Variant Price, Variant Compare At Price, Variant Requires Shipping,	Variant Taxable,Variant Barcode, Image Src,	Image Position,	Image Alt Text,	Gift Card, SEO Title, SEO Description, Google Shopping / Google Product Category, Google Shopping / Gender, Google Shopping / Age Group, Google Shopping / MPN,	Google Shopping / Condition,	Google Shopping / Custom Product, Variant Image, Variant Weight Unit, Variant Tax Code,	Cost per item, Included / United States, Price / United States,	Compare At Price / United States, Included / International,	Price / International, Compare At Price / International, Status 
"""   