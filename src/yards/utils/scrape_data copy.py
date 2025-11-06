import requests, os, json, re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import extruct
from urllib.parse import urljoin
from rapidfuzz import process, fuzz
from langchain_groq import ChatGroq
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yards.utils.utils import llm_init, call_llm
from yards.utils.config import SERPER_API_KEY

llm, prompt = llm_init()

def get_base_url(html_content, page_url):
    """Mimic extruct/w3lib get_base_url behavior."""
    # Finds <base href="..."> tag if exists
    base_href = re.search(r'<base\s+href=["\'](.*?)["\']', html_content, re.I)
    if base_href:
        return urljoin(page_url, base_href.group(1))
    return page_url


official_sites = {
    "SG": "https://shop.teamsg.in/",
    "Kookaburra": "https://www.kookaburra.biz/",
    "Gray-Nicolls": "https://www.gray-nicolls.co.uk/",
    "MRF": "https://www.mrfsports.com/",
    "SS": "https://www.sstoncricket.com/"
}

brands = ["SG", "Kookaburra", "Gray-Nicolls", "MRF", "SS"]



# def detect_brand(product_name, official_sites):
#     for brand in official_sites.keys():
#         if brand.lower() in product_name.lower():
#             return brand
#     return None

async def detect_brand(product_name, brands):
    best_match, score, _ = process.extractOne(product_name, brands, scorer=fuzz.partial_ratio)
    if score >= 75:
        return best_match

    # 2️⃣ Semantic fallback using TF-IDF similarity (lightweight alternative to embeddings)
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
    print(f"Brand detection response: {extractor_response}")  
    return extractor_response.content.strip()


async def get_official_product_page(product_name, brand_domain=None):
    query = f"{product_name}"
    if brand_domain:
        query += f" site:{brand_domain}"
    else:
        brand = await detect_brand("SG RP 17 English Willow Cricket Bat", brands)
        if brand:
            query += f" site:{official_sites[brand]}"

    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": 5}

    res = requests.post(url, headers=headers, json=payload)
    data = res.json()

    # Filter only brand domain results
    results = data.get("organic", [])
    if brand_domain:
        results = [r for r in results if brand_domain in r.get("link", "")]

    if not results:
        return None

    if not results:
        # no search results
        return None

    best = results[0]

    link = best.get("link")
    res_value = None
    if link:
        # ensure we only await the coroutine (avoid accidentally printing the coroutine object)
        res_value = await extract_product_info(link)
        print("extract_product_info returned:", res_value)

    return {
        "product_name": product_name,
        "official_product_page": link,
        "page_title": best.get("title"),
        "snippet": best.get("snippet"),
        "extracted": res_value
    }


def get_official_product_page_sync(product_name, brand_domain=None):    
    try:
        return __import__("asyncio").run(get_official_product_page(product_name, brand_domain))
    except RuntimeError as e:
        print(f"RuntimeError in get_official_product_page_sync: {e}")
        try:
            loop = __import__("asyncio").new_event_loop()
            return loop.run_until_complete(get_official_product_page(product_name, brand_domain))
        except Exception as e2:
            print(f"Error in new event loop: {e2}")
            return None


async def extract_product_info(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")

        html = await page.content()
        await browser.close()

    # ---------- STEP 1: Extract structured data ----------
    base_url = get_base_url(html, url)
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])

    product = {}
    variants = []

    # Try to get structured product data (if available)
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

    soup = BeautifulSoup(html, "html.parser")

    script_tags = soup.find_all("script", string=re.compile(r"Shopify\.product|var meta"))
    for s in script_tags:
        text = s.string or ""
        match = re.search(r"Shopify\.product\s*=\s*(\{.*?\});", text, re.S)
        if match:
            shopify_json = json.loads(match.group(1))
            product["Title"] = shopify_json.get("title")
            product["Vendor"] = shopify_json.get("vendor")
            product["Product Type"] = shopify_json.get("type")
            product["Tags"] = ",".join(shopify_json.get("tags", []))

            for v in shopify_json.get("variants", []):
                variants.append({
                    "Variant SKU": v.get("sku"),
                    "Variant Price": v.get("price") / 100 if isinstance(v.get("price"), (int, float)) else v.get("price"),
                    "Option1 Value": v.get("title"),
                    "Available": v.get("available"),
                    "ID": v.get("id")
                })

            if "images" in shopify_json and shopify_json["images"]:
                product["Image Src"] = shopify_json["images"][0].get("src")
            break

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