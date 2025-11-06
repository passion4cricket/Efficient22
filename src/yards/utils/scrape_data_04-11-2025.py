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
brands = list(official_sites.keys())


# ----------------------------------------------------------
# Brand Detection
# ----------------------------------------------------------
async def detect_brand(product_name, brands):
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
async def fetch_page_in_thread(url: str, timeout_ms: int = 40000) -> str:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _worker():
        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                try:
                    await page.goto(url, wait_until="load", timeout=timeout_ms)
                except Exception:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                # Auto-scroll for lazy content
                try:
                    last_height = await page.evaluate("document.body.scrollHeight")
                    while True:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1.5)
                        new_height = await page.evaluate("document.body.scrollHeight")
                        if new_height == last_height:
                            break
                        last_height = new_height
                except Exception:
                    pass

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
# Resolve SS Listing Page → Product Page
# ----------------------------------------------------------
async def resolve_listing_to_product_url(url: str):
    if not re.search(r"/all-products/.*\.html", url):
        return url  # not a listing
    try:
        html = await fetch_page_in_thread(url)
        soup = BeautifulSoup(html, "html.parser")
        product_links = [a["href"] for a in soup.select("a.product-item-link[href]")]
        if product_links:
            resolved = urljoin(url, product_links[0])
            print(f"[🔗 Resolved listing → product: {resolved}]")
            return resolved
    except Exception as e:
        print(f"[WARN] Could not resolve listing page: {e}")
    return url


# ----------------------------------------------------------
# Product Info Extraction
# ----------------------------------------------------------
async def extract_product_info(url):
    html = await fetch_page_in_thread(url)
    base_url = get_base_url(html, url)
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])

    product = {}
    variants = []

    # Try structured data
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

    # Fallback HTML parse
    soup = BeautifulSoup(html, "html.parser")

    meta_title = soup.find("meta", property="og:title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    product["SEO Title"] = meta_title["content"] if meta_title else product.get("Title")
    product["SEO Description"] = meta_desc["content"] if meta_desc else product.get("Body (HTML)")

    # Collect text as fallback
    if not product.get("Body (HTML)"):
        paragraphs = soup.find_all("p")
        text_content = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        product["Body (HTML)"] = text_content[:2000] if text_content else ""

    product["Source URL"] = url
    return product


# ----------------------------------------------------------
# Serper + Multi-site Extraction + Summary
# ----------------------------------------------------------
async def get_multi_source_product_data(product_name, max_sites=5):
    """Search multiple sites via Serper, extract & summarize."""
    query = f"{product_name} cricket bat"
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": max_sites}

    res = requests.post(url, headers=headers, json=payload)
    data = res.json()
    results = data.get("organic", [])

    # Filter out junk links
    valid_links = []
    for r in results:
        link = r.get("link", "")
        if any(x in link for x in ["youtube", "facebook", "reddit", "pinterest", "instagram"]):
            continue
        valid_links.append(link)

    all_products = []
    for link in valid_links[:max_sites]:
        try:
            if "sstoncricket.com" in link:
                link = await resolve_listing_to_product_url(link)
            product_data = await extract_product_info(link)
            all_products.append(product_data)
        except Exception as e:
            print(f"[WARN] Failed to extract from {link}: {e}")

    # Combine all descriptions
    combined_desc = " ".join(p.get("Body (HTML)", "") for p in all_products if p.get("Body (HTML)"))

    # Summarize description
    summary_text = None
    if combined_desc:
        summary_prompt = """
        Summarize the following product descriptions into one clear, concise, and high-quality paragraph suitable for Shopify:
        Focus on key materials, design, performance, and suitability for players.
        """
        summary_resp = await call_llm(llm, prompt, summary_prompt, {"content": combined_desc})
        summary_text = summary_resp.content.strip()

    return {
        "Query": product_name,
        "Sources": valid_links,
        "Products": all_products,
        "Summary Description": summary_text,
    }


# ----------------------------------------------------------
# Batch Runner
# ----------------------------------------------------------
async def get_multi_source_product_pages(product_names):
    final_results = []
    for name in product_names:
        result = await get_multi_source_product_data(name)
        final_results.append(result)
    print(json.dumps(final_results, indent=2))
    return final_results
