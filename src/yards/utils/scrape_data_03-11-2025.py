import asyncio
import threading
import requests, os, json, re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import extruct
from urllib.parse import urljoin
from rapidfuzz import process, fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yards.utils.utils import llm_init, call_llm
from yards.utils.config import SERPER_API_KEY

llm, prompt = llm_init()


# ----------------------------------------------------------
# Utility: Base URL extraction
# ----------------------------------------------------------
def get_base_url(html_content, page_url):
    base_href = re.search(r'<base\s+href=["\'](.*?)["\']', html_content, re.I)
    if base_href:
        return urljoin(page_url, base_href.group(1))
    return page_url


# ----------------------------------------------------------
# Brand configuration
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
    "Payntr": "https://www.payntr.com/"
}


brands = ["SG", "Kookaburra", "Gray-Nicolls", "MRF", "SS", "Adidas", "New Balance", "Gunn & Moore", "DSC", "CA", "Spartan", "Puma", "TON", "SS TON", "ASICS", "Masuri", "Aero", "Shrey", "Protos", "Payntr"]


async def detect_brand(product_name, brands, groq_api_key=None):
    best_match, score, _ = process.extractOne(product_name, brands, scorer=fuzz.partial_ratio)
    if score >= 75:
        return best_match

    # TF-IDF fallback
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
# Thread-safe Playwright Fetch
# ----------------------------------------------------------
async def fetch_page_in_thread(url: str, timeout_ms: int = 30000) -> str:
    
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _worker():
        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception as e2:
                    print(f"⚠️ DOMContentLoaded also failed, retrying simple goto: {e2}")
                    await page.goto(url)  # final fallback
                await page.wait_for_timeout(4000)
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
# Extract Product Info (uses Playwright safely)
# ----------------------------------------------------------
async def extract_product_info(url):
    print(f"🔗 Visiting: {url}")

    html = await fetch_page_in_thread(url)  # ✅ Playwright runs in its own thread

    base_url = get_base_url(html, url)
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])
    print(f"Extracted data: {data}")

    product = {}
    variants = []

    # Structured data extraction
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

    # Fallback HTML parsing
    soup = BeautifulSoup(html, "html.parser")

    script_tags = soup.find_all("script", string=re.compile(r"Shopify\.product|var meta"))
    for s in script_tags: 
        s = s.string
        match = re.search(r"var\s+meta\s*=\s*(\{.*?\});", s, re.S)
        if match:
            try:
                shopify_json = json.loads(match.group(1))
                # print(shopify_json)
                products = shopify_json.get("product", {})
                """{'id': 8759295607072, 'gid': 'gid://shopify/Product/8759295607072', 'vendor': 'TeamSG', 'type': '', 'variants': [{'id': 46901732049184, 'price': 1115965, 'name': 'SG Century Classic English Willow Cricket Bat - 5', 'public_title': '5', 'sku': 'SG01CR130145'}, {'id': 46901732081952, 'price': 1115965, 'name': 'SG Century Classic English Willow Cricket Bat - 6', 'public_title': '6', 'sku': 'SG01CR130144'}, {'id': 46901732114720, 'price': 1593665, 'name': 'SG Century Classic English Willow Cricket Bat - SH', 'public_title': 'SH', 'sku': 'SG01CR130132'}], 'remote': False}"""
                product["Vendor"] = products.get("vendor")
                for variant in products.get("variants", []):
                    variant_info = {}
                    variant_info["Title"] = variant.get("public_title")
                    variant_info["sku"] = variant.get("sku")
                    variant_info["Variant Price"] = variant.get("price") / 100 if isinstance(variant.get("price"), (int, float)) else variant.get("price")
                    variant_info["Vendor"] = shopify_json.get("vendor")
                    variants.append(variant_info)                

                if "images" in shopify_json and shopify_json["images"]:
                    product["Image Src"] = shopify_json["images"][0].get("src")
            except Exception as e:
                print(f"⚠️ Error parsing Shopify JSON: {e}")
            break

    # Extract select-based options
    options = []
    for opt in soup.select("select"):
        name = opt.get("name") or opt.get("id")
        values = [o.text.strip() for o in opt.find_all("option") if o.text.strip()]
        if name and values:
            options.append({name: values})
    if options:
        product["Options"] = options

    meta_title = soup.find("meta", property="og:title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    product["SEO Title"] = meta_title["content"] if meta_title else product.get("Title")
    product["SEO Description"] = meta_desc["content"] if meta_desc else product.get("Body (HTML)")

    product["Variants"] = variants
    return product


# ----------------------------------------------------------
# Get Official Product Page (main entry)
# ----------------------------------------------------------
async def get_official_product_page(product_name, brand_domain=None):
    try:
        query = f"{product_name}"
        if brand_domain:
            query += f" site:{brand_domain}"
        else:
            brand = await detect_brand(product_name, brands)
            if brand:
                query += f" site:{official_sites[brand]}"

        url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        payload = {"q": query, "num": 5}

        res = requests.post(url, headers=headers, json=payload)
        data = res.json()

        results = data.get("organic", [])
        if brand_domain:
            results = [r for r in results if brand_domain in r.get("link", "")]

        if not results:
            print("⚠️ No search results found.")
            return None

        best = results[0]
        print(f"🔍 Found: {best.get('link')}")

        res_value = await extract_product_info(best.get("link"))

        return res_value
    except Exception as e:
        print(f"Error in get_official_product_page: {e}")
        return None
    
async def get_official_product_pages(product_names, brand_domain=None):
    product_info = []
    for product_name in product_names:
        info = await get_official_product_page(product_name, brand_domain)
        product_info.append(info)
    return product_info
