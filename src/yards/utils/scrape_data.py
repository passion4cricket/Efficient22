import asyncio
import threading
import requests
import os
import json
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import extruct
from urllib.parse import urljoin
from rapidfuzz import process, fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yards.utils.utils import llm_init, call_llm, run_local_llm
from dotenv import load_dotenv
import tldextract
import config

load_dotenv(config.get_env_path())
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
llm, prompt = llm_init()

MAX_TOKENS_PER_REQUEST = 2500

official_sites = {
    "SG": "https://shop.teamsg.in/",
    "Kookaburra": "https://www.kookaburrasport.com.au/",
    "Gray-Nicolls": "https://www.gray-nicolls.co.uk/",
    "SS": "https://www.sstoncricket.com/",
    "Adidas": "https://www.adidas.co.in/cricket",
    "New Balance": "https://www.newbalance.co.uk/cricket/",
    "Gunn & Moore": "https://www.gm-cricket.com/",
    "DSC": "https://dsc-cricket.com/",
    "CA": "https://www.ca-sports.com.pk/",
    "Spartan": "https://www.spartansports.com/",
    "Puma": "https://in.puma.com/in/en/mens/mens-sports/cricket",
    "TON": "https://www.toncricket.com/",
    "SS TON": "https://www.sstoncricket.com/",
    "ASICS": "https://www.asics.com/in/en-in/cricket/c/cricket/",
    "Masuri": "https://www.masuri.com/",
    "Aero": "https://aerocricket.com/",
    "Shrey": "https://shreysports.com/",
    "Protos": "https://protoscricket.com/",
    "Payntr": "https://www.payntr.com/",
    "Moonwalkr": "https://moonwalkr.com/"
}

brands = list(official_sites.keys())


# ----------------------------------------------------------
# Utilities
# ----------------------------------------------------------
def get_base_url(html_content, page_url):
    base_href = re.search(r'<base\s+href=["\'](.*?)["\']', html_content, re.I)
    return urljoin(page_url, base_href.group(1)) if base_href else page_url


async def fetch_page_in_thread(url: str, timeout_ms: int = 40000) -> str:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _worker():
        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    await page.goto(url, timeout=timeout_ms)
                await page.wait_for_timeout(3000)
                html = await page.content()
                await browser.close()
                return html

        try:
            result = asyncio.run(_run())
        except Exception as e:
            loop.call_soon_threadsafe(future.set_exception, e)
        else:
            loop.call_soon_threadsafe(future.set_result, result)

    threading.Thread(target=_worker, daemon=True).start()
    return await future


async def extract_variants_generic(soup, data):
    variants = []
    for entry in data.get("json-ld", []):
        if entry.get("@type") == "Product":
            offers = entry.get("offers")
            if isinstance(offers, list):
                for offer in offers:
                    variants.append({
                        "Variant Name": entry.get("name"),
                        "Variant SKU": offer.get("sku"),
                        "Variant Price": offer.get("price"),
                        "Currency": offer.get("priceCurrency"),
                        "Size": offer.get("name") or offer.get("description"),
                    })
            elif isinstance(offers, dict):
                variants.append({
                    "Variant Name": entry.get("name"),
                    "Variant SKU": offers.get("sku"),
                    "Variant Price": offers.get("price"),
                    "Currency": offers.get("priceCurrency"),
                    "Size": offers.get("name") or offers.get("description"),
                })

    if not variants:
        for select in soup.find_all("select"):
            if re.search(r"size|variant|option|color", select.get("name", ""), re.I):
                for opt in select.find_all("option"):
                    value = opt.get_text(strip=True)
                    if value:
                        variants.append({"Variant Name": value})
    return variants

async def extract_variants_from_shopify(soup):
    variants = []
    script_tags = soup.find_all("script", string=re.compile(r"Shopify\.product|var meta"))
    for s in script_tags:
        s = s.string
        match = re.search(r"var\s+meta\s*=\s*(\{.*?\});", s, re.S)
        if match:
            try:
                shopify_json = json.loads(match.group(1))
                product_data = shopify_json.get("product", {})
                vendor = product_data.get("vendor")
                for variant in product_data.get("variants", []):                    
                    variants.append({
                        "Variant Name": variant.get("name") or product_data.get("title"),
                        "Variant SKU": variant.get("sku"),
                        "Variant Price": variant.get("price") / 100 if isinstance(variant.get("price"),
                                                                                  (int, float)) else variant.get(
                            "price"),
                        "Size": variant.get("public_title"),
                    })
            except Exception as e:
                print(f"[⚠️ Shopify JSON parse error] {e}")
    return variants

async def extract_product_info(url):
    try:
        html = await fetch_page_in_thread(url)
        if not html:
            return {}

        base_url = get_base_url(html, url)

        # print(f"base_url {base_url}")

        data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])
        soup = BeautifulSoup(html, "html.parser")

        # data = data['json-ld']

        meta_title = soup.find("meta", property="og:title")
        meta_desc = soup.find("meta", attrs={"name": "description"})
        data["SEO Title"] = meta_title["content"] if meta_title else None
        data["SEO Description"] = meta_desc["content"] if meta_desc else None

        variants = await extract_variants_from_shopify(soup)
        if not variants:
            variants = await extract_variants_generic(soup, data)
        data["Variants"] = variants or []

        return data
    except Exception as e:
        print("Extract product info")
        return data

def detect_region(url, snippet_text=""):
    url_lower = url.lower()
    snippet_text = snippet_text.lower()

    # ---------------------------
    # 1. Known retailer / brand rules
    # ---------------------------
    retailer_rules = {
        "amazon.in": "India",
        "amazon.com": "United States",
        "flipkart.com": "India",
        "croma.com": "India",
        "reliance": "India",
        "walmart.com": "United States",
        "bestbuy.com": "United States",
        "target.com": "United States",
        "argos.co.uk": "United Kingdom",
        "currys.co.uk": "United Kingdom",
        "amazon.co.uk": "United Kingdom",
        "amazon.ca": "Canada",
        "amazon.de": "Germany",
        "amazon.fr": "France"
    }
    for key, region in retailer_rules.items():
        if key in url_lower:
            return region

    # ---------------------------
    # 2. URL path region indicators
    # ---------------------------
    path_region_patterns = {
        "/in/": "India",
        "/hi-in/": "India",
        "/en-in/": "India",
        "/en-us/": "United States",
        "/en-gb/": "United Kingdom",
        "/uk/": "United Kingdom",
        "/ca/": "Canada",
        "/de/": "Germany",
        "/fr/": "France"
    }
    for key, region in path_region_patterns.items():
        if key in url_lower:
            return region

    # ---------------------------
    # 3. Currency detection
    # ---------------------------
    if "₹" in snippet_text:
        return "India"
    if "$" in snippet_text or "usd" in snippet_text.lower() or "us" in snippet_text.lower():
        # if url_lower.endswith(".com") or ".com/" in url_lower:
        return "United States"
    if "£" in snippet_text:
        return "United Kingdom"
    if "€" in snippet_text:
        return "Europe"

    # ---------------------------
    # 4. Phone number country codes
    # ---------------------------
    phone_patterns = {
        r"\+91": "India",
        r"\+1(?!\d{1,2})": "United States",
        r"\+44": "United Kingdom",
        r"\+61": "Australia"
    }
    for pattern, region in phone_patterns.items():
        if re.search(pattern, snippet_text):
            return region

    # ---------------------------
    # 5. TLD fallback
    # ---------------------------
    extracted = tldextract.extract(url)
    tld = extracted.suffix  # e.g., "com", "in", "co.uk"

    tld_mapping = {
        "in": "India",
        "co.in": "India",
        "co.uk": "United Kingdom",
        "uk": "United Kingdom",
        "ca": "Canada",
        "com.au": "Australia",
        "sg": "Singapore",
        "ae": "United Arab Emirates",
        "de": "Germany",
        "fr": "France",
    }

    if tld in tld_mapping:
        return tld_mapping[tld]

    # ---------------------------
    # Default
    # ---------------------------
    return "Unknown / Global"

async def get_multi_source_product_pages(product_names, region="us"):
    final_results = []
    for name in product_names:
        query = f"{name}"
        print(f"🔍 Searching for product: {query}")

        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": query, "num": 10}
        # payload = {"q": query, "num": 5, "gl": "us", "hl": "en"}

        try:
            res = requests.post(url, headers=headers, json=payload)
            data = res.json()
            # print(data)
            
            results = data.get("organic", [])            

            if not results:
                print(f"⚠️ No results found for {name}")
                continue

            product_data = []
            for result in results:
                link = result.get("link")
                if not link:
                    continue
                try:
                    region = detect_region(link, result.get("snippet", ""))
                    if region == "Unknown / Global" or region == "United States":
                        link = f"{link}/?currency=usd"
                    
                    print(f"base URL {link} and detected region: {region}")
                    
                    prod = await extract_product_info(link)
                    normalize = await normalize_info(prod)

                    if isinstance(normalize, dict):                
                        product_data.append(normalize)
                    else:                
                        if prod:
                            product_data.append(prod)
                except Exception as e:
                    print(f"[❌ Failed to extract from {link}] {e}")

            if product_data:
                merged = await summarize_product_info(name, product_data)
                
                print(f"✅ Final summarized product for {name} ready.")                
                final_results.append(merged)

        except Exception as e:
            print(f"[❌ Error fetching {name}] {e}")
    
    return final_results

async def summarize_product_info(product_name, product_values, region_info="us"):
    prod_description = []
    prod_variant = []
    prod_images = []
    prod_brand = []

    # print(f"After normalize: {product_values}")

    # Flatten incoming product data
    for p in product_values:
        if p.get("description"):
            prod_description.append(p["description"])

        if p.get("variants"):
            prod_variant.extend(p["variants"])

        if p.get("images"):
            prod_images.extend(p["images"])

        if p.get("brand"):
            prod_brand.append(p["brand"])

    summary_prompt = """
        You are an e-commerce catalog normalization engine.

        Your task:
        Given the input data, generate ONLY a single clean JSON object.
        Do NOT output anything except pure JSON.
        No explanations, no headings, no markdown, no comments.

        JSON FORMAT (MANDATORY):
        {
            "title": "",
            "brand": "",
            "official_sku": "",
            "description": "",
            "variants": [],
            "images": []
        }

        RULES:

        1. Use only the provided input. Do not invent any data.

        2. Title
        - Set "title" = product_name.

        3. Brand
        - Extract only if clearly present in the product name.
        - Otherwise brand = "".

        4. Description
        - Merge all descriptions.
        - Remove duplicates.
        - Keep only factual product information.

        5. Images
        - Include only valid http/https URLs.
        - Remove duplicates.
        - ALWAYS return ONLY ONE image:
            → the first valid http/https URL after deduplication.

        6. Variant Structure
        Each variant must be normalized into:
        {
            "name": "",
            "sku": "",
            "price": 0,
            "currency": "",
            "size": ""
        }

        FIELD MAPPING (case-insensitive):
        - Name → ("Variant Name", "name", "title")
        - SKU → ("Variant SKU", "sku", "id")
        - Price → ("Variant Price", "price", "amount")
        - Currency → ("currency")
        - Size → ("Size", "size")

        Variant Cleaning:
        - Remove NULL / empty / placeholder fields.
        - Price must be numeric. Missing or invalid price → exclude variant.
        - Only include variants where currency matches the region.
        - If currency is missing and cannot be inferred → EXCLUDE variant.

        7. Variant Source Rule:
        Variants may contain "source", "source_url", "url", or "product_url".
        If none exist → treat variant as NOT from an official source.

        OFFICIAL SOURCE SITES:
        - sgcricket.com
        - teamsg.in
        - sanspareils.co.in

        A variant is OFFICIAL ONLY if its source URL contains one of the above domains.

        8. Official SKU Selection:
        ***OFFICIAL SKU MUST BE SELECTED ONLY FROM VARIANTS THAT PASSED REGION-BASED CURRENCY FILTERING.***

        Valid conditions for SKU Selection:
        - Must come from an official source URL.
        - Prefer manufacturer-style patterns (e.g., SG****).
        - Prefer structured SKUs (4–12 alphanumeric characters).
        - Prefer stable names.
        - Ignore null, empty, short numeric-only SKUs.

        Tie-breakers:
        1. Most frequent SKU.
        2. Highest priced official variant.
        If none qualify → official_sku = "".

        9. Non-Official SKU Handling:
        If variant is NOT from an official source:
        - Set "sku" = "".

        10. SKU AND PRICE DIFFERENCE DO NOT CREATE VARIANTS:
        Only real attributes create variants:
        - size
        - color
        - material
        - quantity
        - pack-size

        Variant NAME/TITLE must NOT be considered a real attribute and must NEVER create separate variants.
        If variants differ ONLY by name/title, treat them as the SAME variant.

        11. VARIANT DEDUPLICATION:
        Normalize name:
        - lowercase
        - remove punctuation
        - remove filler words: "for", "the", "-", "_"

        If duplicates:
        - KEEP ONLY ONE
        - Choose the variant with lowest price
        - If retained variant SKU is not official → sku = ""

        12. Final Variant Selection:
        If no real variation exists:
        - Output ONLY ONE variant
        - Select variant with lowest price
        - If that SKU is not official → sku = ""

        FINAL OUTPUT:
        Return ONLY the final JSON object.
        Nothing else.


"""



    user_prompt = f"""
       ### INPUT DATA
        Product Name: "{product_name}"
        Descriptions: {json.dumps(prod_description)}
        Variants: {json.dumps(prod_variant)}
        Images: {json.dumps(prod_images)}
        Brands: {json.dumps(prod_brand)}
        Region: "{region_info}"

        -----------------------------------------------------
        REGION-BASED VARIANT FILTERING (MANDATORY)
        -----------------------------------------------------

        1. The region is "{region_info}".

        2. Region → Currency mapping:
            - us → USD
            - india → INR
            - in → INR
            - uk → GBP
            - eu → EUR

        3. Determine the correct region currency.

        4. HARD FILTER:
            - REMOVE every variant whose currency does NOT EXACTLY match the region currency.
            - No fallback, no inference.
            - No currency conversion.

        5. After filtering:
            - Keep only variant(s) with the LOWEST price.
            - If multiple share same lowest price:
                → keep only one unless they differ in real attribute.

        6. No assumptions.
        7. No multiple currencies allowed.

        -----------------------------------------------------
        REQUIRED OUTPUT FORMAT
        -----------------------------------------------------

        {{
        "title": "{product_name}",
        "brand": "",
        "official_sku": "",
        "description": "",
        "variants": [],
        "images": []
        }}

    """


    response = await call_llm(
        llm,
        prompt,
        summary_prompt,
        user_prompt
    )

    try:
        # print(f"After summerization: {response.content.strip()}")
        return json.loads(response.content.strip())
    except:
        return json.loads(sanitize_json(response.content.strip()))

def sanitize_json(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = text.replace("```", "")

    if "{" in text:
        text = text[text.index("{"):]

    return text.strip()

async def normalize_info(prod_detail):

    # print(f"After webscrapping: {prod_detail}")
    normalize_prompt = """
        You are a JSON-only generator.

        RULES:
        - Output ONLY a valid JSON object.
        - The output MUST start with "{" and end with "}".
        - Do NOT include markdown, headings, ###, ####, Output:, ```, ```json and code fences.
        - No explanations, no comments, no extra text.
        - If the input is incomplete, use empty strings or empty arrays.
        - Follow this exact structure and ensure every variant object has a "currency" field (string).

        Structure:
        {
            "title": "",
            "brand": "",
            "description": "",
            "price": {
                "value": 0.0,
                "currency": ""
            },
            "sku": "",
            "category": "",
            "images": [],
            "variants": [
                {
                    "Variant Name": "",
                    "Variant SKU": "",
                    "Variant Price": 0.0,
                    "Size": "",
                    "currency": ""
                }
            ],
            "attributes": {
                "Color": "",
                "Size": ""
            }
        }

        Instruction:
        - Analyze the input and set "price.currency" to the detected currency code (e.g., "INR", "USD").
        - Add the same currency code into each variant's "currency" field.
        - If you cannot detect currency, use empty string "".

    """

    clean_input = json.dumps(prod_detail, ensure_ascii=False, indent=2)

    user_prompt = f"""
        Convert the following product details into the JSON structure.

        Return ONLY the JSON.

        PRODUCT DATA:
        {clean_input}
    """

    try:
        # extractor_response = await run_local_llm(
        #     normalize_prompt,
        #     user_prompt,
        # )

        extractor_response = await call_llm(
            llm,
            prompt,
            normalize_prompt,
            user_prompt,
        )

        details = extractor_response.content.strip()
        # print(f"Narmalize: {details}")
        
        try:
            clean = sanitize_json(details)
            parsed = json.loads(clean)
        except Exception:
            parsed = json.loads(details)
    except Exception as e:
        print(f"⚠️ Normalization error: {e}")
        
    return parsed

def sanitize_json_online_llm(text: str) -> str:
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    if not text.startswith("[") and not text.startswith("{"):
        start = text.find("[")
        if start != -1:
            text = text[start:]
    return text