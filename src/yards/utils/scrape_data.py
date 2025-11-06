import asyncio
import threading
import requests
import os
import json
import re
import csv
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import extruct
from urllib.parse import urljoin
from rapidfuzz import process, fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yards.utils.utils import llm_init, call_llm
from yards.utils.config import SERPER_API_KEY, PROMPT_TEMPLATES

llm, prompt = llm_init()

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------
official_sites = {
    "SG": "https://shop.teamsg.in/",
    "Kookaburra": "https://www.kookaburrasport.com.au/",
    "Gray-Nicolls": "https://www.gray-nicolls.co.uk/",
    "MRF": "https://www.mrfsports.com/",
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
    # SS, MRF, CEAT, SG, MOON.
}
brands = list(official_sites.keys())

SHOPIFY_HEADERS = [
    "Title", "Body (HTML)", "Image Src", "Variant Price",
    "Currency", "SEO Title", "SEO Description", "Source URL", "Variants"
]

UPDATED_DIR = "output"
os.makedirs(UPDATED_DIR, exist_ok=True)

# ----------------------------------------------------------
# Utility
# ----------------------------------------------------------
def get_base_url(html_content, page_url):
    base_href = re.search(r'<base\s+href=["\'](.*?)["\']', html_content, re.I)
    if base_href:
        return urljoin(page_url, base_href.group(1))
    return page_url

# ----------------------------------------------------------
# Brand Detection
# ----------------------------------------------------------
async def detect_brand(product_name, brands):
    best_match, score, _ = process.extractOne(product_name, brands, scorer=fuzz.partial_ratio)
    if score >= 75:
        return best_match

    vectorizer = TfidfVectorizer().fit(brands + [product_name])
    vectors = vectorizer.transform(brands + [product_name])
    sims = cosine_similarity(vectors[-1], vectors[:-1]).flatten()
    max_idx = sims.argmax()
    if sims[max_idx] >= 0.7:
        return brands[max_idx]

    prompt_text = """
    You are a product and brand expert.
    Identify the brand for the following product:
    Product: "{product_name}"
    Possible brands: {brand_list}
    Respond with ONLY the brand name from the list.
    """
    extractor_response = await call_llm(llm, prompt, prompt_text, {"product_name": product_name, "brand_list": ", ".join(brands)})
    brand = extractor_response.content.strip()
    return brand if brand in brands else None

# ----------------------------------------------------------
# Thread-safe Playwright
# ----------------------------------------------------------
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

# ----------------------------------------------------------
# Variant Extractor (from your first working code)
# ----------------------------------------------------------
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
                        "Variant Price": variant.get("price") / 100 if isinstance(variant.get("price"), (int, float)) else variant.get("price"),
                        "Currency": "INR",
                        "Size": variant.get("public_title"),
                        "Vendor": vendor
                    })
            except Exception as e:
                print(f"[⚠️ Error parsing Shopify JSON] {e}")
    return variants

# ----------------------------------------------------------
# Product Info
# ----------------------------------------------------------
async def extract_product_info(url):
    html = await fetch_page_in_thread(url)
    if not html:
        return {}

    base_url = get_base_url(html, url)
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])
    soup = BeautifulSoup(html, "html.parser")

    product = {}
    product["Source URL"] = url

    for entry in data.get("json-ld", []):
        if entry.get("@type") == "Product":
            product["Title"] = entry.get("name")
            product["Body (HTML)"] = entry.get("description")
            product["Image Src"] = entry.get("image")
            offers = entry.get("offers", {})
            if isinstance(offers, dict):
                product["Variant Price"] = offers.get("price")
                product["Currency"] = offers.get("priceCurrency")
            break

    meta_title = soup.find("meta", property="og:title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    product["SEO Title"] = meta_title["content"] if meta_title else product.get("Title")
    product["SEO Description"] = meta_desc["content"] if meta_desc else product.get("Body (HTML)")

    # ✅ Shopify variant extraction
    variants = await extract_variants_from_shopify(soup)
    if variants:
        product["Variants"] = variants
    else:
        product["Variants"] = []

    return product

# ----------------------------------------------------------
# Main: Search + Extract multiple sites
# ----------------------------------------------------------
async def get_multi_source_product_pages(product_names):
    final_results = []
    for name in product_names:
        print(f"🔍 Fetching data for: {name}")
        brand = await detect_brand(name, brands)
        domain = official_sites.get(brand)
        query = f"{name} site:{domain}" if domain else name

        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": query, "num": 3}
        try:
            res = requests.post(url, headers=headers, json=payload)
            data = res.json()
            results = data.get("organic", [])
            if results:
                link = results[0].get("link")
                prod = await extract_product_info(link)
                final_results.append(prod)
        except Exception as e:
            print(f"[❌ Error fetching {name}] {e}")
    return final_results
