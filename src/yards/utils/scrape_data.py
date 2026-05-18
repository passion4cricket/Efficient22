import asyncio
import threading
import requests
import os
import json
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import extruct
from urllib.parse import urljoin, urlparse
from rapidfuzz import process, fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from yards.utils.utils import llm_init, call_llm, run_local_llm
from dotenv import load_dotenv
import tldextract
from spellchecker import SpellChecker

from yards.utils.config import AMAZON_HEADERS, SHOPIFY_HEADERS


# =============================================================================
#  ENV + INIT
# =============================================================================
load_dotenv()
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
llm, prompt = llm_init()

MAX_TOKENS_PER_REQUEST = 2500

# =============================================================================
#  OFFICIAL SITES MAP
# =============================================================================
official_sites = {
    "SG":           "https://shop.teamsg.in/",
    "Kookaburra":   "https://www.kookaburrasport.com.au/",
    "Gray-Nicolls": "https://www.gray-nicolls.co.uk/",
    "SS":           "https://www.sstoncricket.com/",
    "Adidas":       "https://www.adidas.co.in/cricket",
    "New Balance":  "https://www.newbalance.co.uk/cricket/",
    "Gunn & Moore": "https://www.gm-cricket.com/",
    "DSC":          "https://dsc-cricket.com/",
    "CA":           "https://www.ca-sports.com.pk/",
    "Spartan":      "https://www.spartansports.com/",
    "Puma":         "https://in.puma.com/in/en/mens/mens-sports/cricket",
    "TON":          "https://www.toncricket.com/",
    "SS TON":       "https://www.sstoncricket.com/",
    "ASICS":        "https://www.asics.com/in/en-in/cricket/c/cricket/",
    "Masuri":       "https://www.masuri.com/",
    "Aero":         "https://aerocricket.com/",
    "Shrey":        "https://shreysports.com/",
    "Protos":       "https://protoscricket.com/",
    "Payntr":       "https://www.payntr.com/",
    "Moonwalkr":    "https://moonwalkr.com/",
    "Hundred":      "https://in.hndrd.co/",
    "TYKA":         "https://www.tyka.com/",
    "Cosco":        "https://store.cosco.in/",
}

brands = list(official_sites.keys())


# =============================================================================
#  AREA 1 — NAME CORRECTION CONFIGURATION
# =============================================================================
# Manual override dict — highest priority in the correction pipeline.
# Keys   = wrong / variant spellings from your input file (case-insensitive).
# Values = exact correct product name for the output.
#
# Use this for:
#   - Brand names with hyphens  : "gray nicolls"  → "Gray-Nicolls"
#   - Brand names with ampersand: "gunn and moore" → "Gunn & Moore"
#   - All-caps brand acronyms   : "sg rnx 10"      → "SG RNX 10"
#   - Any case where Google/spell-check gets it wrong
# =============================================================================
MANUAL_NAME_CORRECTIONS: dict[str, str] = {
    # Brand acronyms that Pascal Case breaks — add all-caps brands here
    "ss toe guard kit":        "SS Toe Guard Kit",
    "ss autograph kit":        "SS Autograph Kit",
    "ss cricket":              "SS Cricket",
    "sg cricket":              "SG Cricket",
    "dsc":                     "DSC",
    "ca sports":               "CA Sports",
    # Add any other SS/SG/DSC/CA products you have in your input file:
    "ss ton master":         "SS TON Master",
    "ss matrix":             "SS Matrix",
}

# SpellChecker — brand names and cricket vocab protected from correction
_spell = SpellChecker(language="en")
_spell.word_frequency.load_words([b.lower() for b in brands])

_CRICKET_VOCAB = [
    # Equipment & brands
    "kookaburra", "kahuna", "xtreme", "retro", "premier", "willow",
    "adipower", "strikeline", "cricket", "bat", "gloves", "pads",
    "helmet", "spikes", "jersey", "whites", "abdominal", "inners",
    "moonwalkr", "payntr", "masuri", "aero", "shrey", "protos",
    "tyka", "cosco", "spartan", "dsc", "sg", "ton", "sstoncricket",
    # Player names — must never be spell-corrected
    "dhoni", "thala", "virat", "kohli", "rohit", "sharma", "sachin",
    "tendulkar", "gayle", "pollard", "bumrah", "jadeja", "ashwin",
    "dhawan", "ganguly", "dravid", "kumble", "harbhajan", "yuvi",
    "yuvraj", "raina", "pandya", "hardik", "surya", "suryakumar",
    "babar", "azam", "stokes", "root", "anderson", "broad", "flintoff",
    "warne", "mcgrath", "ponting", "lara", "kallis", "deklerk",
    "finch", "warner", "smith", "maxwell", "stoinis", "hazlewood",
    # Cricket series / edition names
    "thala", "captain", "finisher", "gladiator", "slasher", "retro",
    "stunner", "destroyer", "smacker", "striker", "blaster", "titan",
    "magnum", "natwest", "ipl", "odi", "t20", "ranjji", "duleep",
]
_spell.word_frequency.load_words(_CRICKET_VOCAB)


# =============================================================================
#  AREA 2 — NAME CORRECTION FUNCTIONS
# =============================================================================

# ── AREA 2 — Add this dict at the top of the file near MANUAL_NAME_CORRECTIONS ──
# All-caps brand acronyms that must never be lowercased by Pascal Case
ALL_CAPS_BRANDS = {"SS", "SG", "DSC", "CA", "MRF", "TON", "GM", "ASICS", "TYKA"}

def _apply_pascal_case(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    words = name.split()
    result = []
    for word in words:
        if word.upper() in ALL_CAPS_BRANDS:
            result.append(word.upper())   # keep SS as SS, SG as SG etc.
        else:
            result.append(word.capitalize())
    return " ".join(result)


def _fix_extra_spaces(name: str) -> str:
    """Collapse 2+ spaces into one, strip edges."""
    return re.sub(r"\s+", " ", name).strip()


def _spellcheck_words(name: str) -> str:
    """Word-by-word spell check — skips numbers, known brands, cricket vocab."""
    words = name.split()
    corrected = []
    for word in words:
        if word.isdigit() or len(word) <= 1:
            corrected.append(word)
            continue
        if word.lower() in _spell:
            corrected.append(word)
        else:
            suggestion = _spell.correction(word.lower())
            if suggestion and suggestion != word.lower():
                corrected.append(suggestion)
                print(f"  🔤 Spell-corrected: '{word}' → '{suggestion}'")
            else:
                corrected.append(word)
    return " ".join(corrected)


def extract_clean_name_from_official_title(official_title: str, brand: str) -> str:
    """
    Extracts the clean product name from the official page <title> tag.

    Official titles look like:
      "Kookaburra Kahuna Pro 900 Cricket Bat | Kookaburra Sport"
      "SG RNX 10 Cricket Bat - SG Cricket"
      "Gray-Nicolls Oblivion Destroyer | Gray-Nicolls"

    Splits on  |  -  –  —  ::  and returns the first part (the product name).
    """
    if not official_title or not official_title.strip():
        return ""

    parts = re.split(r"\s*[\|–—]{1,2}\s*|\s+-\s+|\s*::\s*", official_title)
    clean = parts[0].strip() if parts else official_title.strip()

    if len(clean) < 4:
        return official_title.strip()

    print(f"  🏷️  Clean name from official title: '{clean}'")
    return clean


def compare_and_correct_name(input_name: str, official_title: str, brand: str) -> str:
    if not official_title:
        return input_name

    official_clean = extract_clean_name_from_official_title(official_title, brand)
    if not official_clean:
        return input_name

    score = fuzz.partial_ratio(input_name.lower(), official_clean.lower())
    print(f"  🔍 Name match score: {score} | Input: '{input_name}' | Official: '{official_clean}'")

    # Guard: if official title is much shorter than input, it's likely a
    # homepage/brand title (e.g. "SS Cricket") not a specific product page.
    # Never replace a detailed product name with a generic brand title.
    input_words    = len(input_name.split())
    official_words = len(official_clean.split())
    if official_words < 3 and input_words > official_words:
        print(f"  ⚠️  Official title too short ({official_words} words) — keeping input: '{input_name}'")
        return input_name

    if score >= 85:
        print(f"  ✅ Name matches official site — keeping: '{input_name}'")
        return input_name
    else:
        corrected = _apply_pascal_case(official_clean)
        print(f"  ✏️  Name replaced with official: '{input_name}' → '{corrected}'")
        return corrected


def expand_willow_abbreviations(name: str) -> str:
    if not name:
        return ""

    expanded = str(name)
    expanded = re.sub(r"\b(e\.w|e\.w\.|ew)\b", "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(k\.w|k\.w\.|kw)\b", "Kashmir Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(cr\.?)(?!\w)", "Cricket", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(english\s+willow)\b", "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(kashmir\s+willow)\b", "Kashmir Willow", expanded, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", expanded).strip()


def correct_product_name(raw_name: str) -> str:
    """
    MASTER NAME CORRECTION PIPELINE — called on every input name.

    Step 1 | Manual override dict     → exact, case-insensitive lookup
    Step 2 | Extra space removal      → "SG  RNX  10" → "SG RNX 10"
    Step 3 | Willow abbreviation expansion
    Step 4 | Spell check              → "kokaburra" → "kookaburra"
    Step 5 | Pascal Case              → "kookaburra kahuna" → "Kookaburra Kahuna"

    NOTE: Official-site name comparison (compare_and_correct_name) runs AFTER
    scraping in get_multi_source_product_pages() — it has higher accuracy because
    it uses the actual brand page title.
    """
    if not raw_name or not raw_name.strip():
        return raw_name

    # Step 1: Manual override
    lookup_key = re.sub(r"\s+", " ", raw_name.strip().lower())
    if lookup_key in MANUAL_NAME_CORRECTIONS:
        corrected = MANUAL_NAME_CORRECTIONS[lookup_key]
        print(f"  📖 Manual override: '{raw_name}' → '{corrected}'")
        return corrected

    # Step 2: Extra space removal
    name = _fix_extra_spaces(raw_name)

    # Step 3: Willow abbreviation expansion
    name = expand_willow_abbreviations(name)

    # Step 4: Spell check
    name_lower = name.lower()
    name_spell_fixed = _spellcheck_words(name_lower)

    # Step 5: Pascal Case
    final_name = _apply_pascal_case(name_spell_fixed)

    if final_name != raw_name:
        print(f"  ✅ Name corrected: '{raw_name}' → '{final_name}'")

    return final_name


# =============================================================================
#  UTILITIES
# =============================================================================

def get_base_url(html_content, page_url):
    base_href = re.search(r'<base\s+href=["\'](.*?)["\']', html_content, re.I)
    return urljoin(page_url, base_href.group(1)) if base_href else page_url


async def fetch_page_in_thread(url: str, timeout_ms: int = 40000) -> str:
    """Runs Playwright in an isolated thread to avoid asyncio event loop conflicts."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _worker():
        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                )
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
    """Extracts variants from JSON-LD structured data or <select> dropdowns."""
    variants = []
    for entry in data.get("json-ld", []):
        if entry.get("@type") == "Product":
            offers = entry.get("offers")
            if isinstance(offers, list):
                for offer in offers:
                    variants.append({
                        "Variant Name":  entry.get("name"),
                        "Variant SKU":   offer.get("sku"),
                        "Variant Price": offer.get("price"),
                        "Currency":      offer.get("priceCurrency"),
                        "Size":          offer.get("name") or offer.get("description"),
                    })
            elif isinstance(offers, dict):
                variants.append({
                    "Variant Name":  entry.get("name"),
                    "Variant SKU":   offers.get("sku"),
                    "Variant Price": offers.get("price"),
                    "Currency":      offers.get("priceCurrency"),
                    "Size":          offers.get("name") or offers.get("description"),
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
    """Extracts variants from Shopify's var meta = {...} script block."""
    variants = []
    script_tags = soup.find_all("script", string=re.compile(r"Shopify\.product|var meta"))
    for s in script_tags:
        s = s.string
        match = re.search(r"var\s+meta\s*=\s*(\{.*?\});", s, re.S)
        if match:
            try:
                shopify_json = json.loads(match.group(1))
                product_data = shopify_json.get("product", {})
                for variant in product_data.get("variants", []):
                    variants.append({
                        "Variant Name":  variant.get("name") or product_data.get("title"),
                        "Variant SKU":   variant.get("sku"),
                        "Variant Price": variant.get("price") / 100
                            if isinstance(variant.get("price"), (int, float))
                            else variant.get("price"),
                        "Size":          variant.get("public_title"),
                    })
            except Exception as e:
                print(f"[⚠️ Shopify JSON parse error] {e}")
    return variants


# =============================================================================
#  AREA A — BODY HTML EXTRACTION
# =============================================================================

def extract_official_body_html(soup: BeautifulSoup) -> str:
    """
    Extracts the product description as clean HTML from the official page.

    Strategy (in order):
    1. Known CSS selectors (Shopify, WooCommerce, common brand patterns)
    2. itemprop="description" attribute
    3. Largest <div>/<section>/<article> by text length (fallback)

    To add a brand-specific selector → add to BODY_HTML_SELECTORS below.
    """
    BODY_HTML_SELECTORS = [
        # Shopify
        {"class": "product-description"},
        {"class": "product__description"},
        {"class": "product-single__description"},
        {"id":    "product-description"},
        {"id":    "tab-description"},
        # WooCommerce
        {"class": "woocommerce-product-details__short-description"},
        {"class": "woocommerce-Tabs-panel--description"},
        # Common brand patterns
        {"class": "description"},
        {"class": "product-details"},
        {"class": "product-info__description"},
        {"class": "pdp-description"},
        {"class": "product__content"},
        {"itemprop": "description"},
        # Generic content blocks
        {"class": "tab-content"},
        {"class": "product-tab-content"},
    ]

    for attrs in BODY_HTML_SELECTORS:
        el = soup.find(attrs=attrs)
        if el and len(el.get_text(strip=True)) >= 30:
            return str(el)

    el = soup.find(attrs={"itemprop": "description"})
    if el and len(el.get_text(strip=True)) >= 30:
        return str(el)

    best_el, best_len = None, 0
    for tag in soup.find_all(["div", "section", "article"]):
        text = tag.get_text(strip=True)
        if len(text) > best_len and len(text) >= 80:
            classes = " ".join(tag.get("class", []))
            if re.search(r"nav|header|footer|menu|cart|sidebar|cookie|popup|modal", classes, re.I):
                continue
            best_el, best_len = tag, len(text)

    return str(best_el) if best_el else ""


# =============================================================================
#  AREA B — SEO DESCRIPTION EXTRACTION
# =============================================================================

def extract_seo_description(soup: BeautifulSoup) -> str:
    """
    Extracts plain-text SEO description in priority order:
    1. <meta name="description">
    2. <meta property="og:description">
    3. <meta name="twitter:description">
    4. First meaningful <p> tag (fallback)

    To add a non-standard meta tag → add to META_SELECTORS below.
    """
    META_SELECTORS = [
        {"name":     "description"},
        {"property": "og:description"},
        {"name":     "twitter:description"},
        {"itemprop": "description"},
    ]

    for attrs in META_SELECTORS:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content", "").strip():
            return tag["content"].strip()

    for p in soup.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) >= 40:
            return text

    return ""


# =============================================================================
#  AREA IMG — OFFICIAL IMAGE EXTRACTION
# =============================================================================

def extract_official_images(soup: BeautifulSoup, base_url: str, product_name: str = "") -> list[str]:
    """
    Extracts product images from the official page.
    Priority: JSON-LD → og:image → product container <img> → URL pattern match.
    Filters out logos, icons, SVGs, tracking pixels.

    product_name is used to re-rank images by filename relevance — images whose
    filename contains keywords from the product name are promoted to the front,
    preventing unrelated product images (from sidebars, related products etc.)
    from being selected as the primary image.
    """
    images = []

    # Priority 1: JSON-LD structured data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            entries = ld if isinstance(ld, list) else [ld]
            for entry in entries:
                if entry.get("@type") == "Product":
                    img = entry.get("image")
                    if isinstance(img, str) and img.startswith("http"):
                        images.append(img)
                    elif isinstance(img, list):
                        images += [i for i in img if isinstance(i, str) and i.startswith("http")]
        except Exception:
            pass

    # Priority 2: og:image
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content", "").startswith("http"):
        img_url = og_img["content"]
        if img_url not in images:
            images.insert(0, img_url)

    # Priority 3: <img> inside product containers
    IMAGE_CONTAINER_SELECTORS = [
        {"class": "product__media"},
        {"class": "product-image"},
        {"class": "product-single__photo"},
        {"class": "product-gallery"},
        {"class": "woocommerce-product-gallery__image"},
        {"class": "pdp-image"},
        {"id":    "product-image"},
    ]
    for attrs in IMAGE_CONTAINER_SELECTORS:
        container = soup.find(attrs=attrs)
        if container:
            for img_tag in container.find_all("img"):
                src = (img_tag.get("src")
                       or img_tag.get("data-src")
                       or img_tag.get("data-lazy-src", ""))
                src = urljoin(base_url, src)
                if src.startswith("http") and src not in images:
                    images.append(src)

    # Priority 4: any <img> with product-related URL pattern
    for img_tag in soup.find_all("img"):
        src = img_tag.get("src") or img_tag.get("data-src", "")
        if not src:
            continue
        src = urljoin(base_url, src)
        if not src.startswith("http"):
            continue
        if re.search(r"product|item|goods|cricket", src, re.I) and src not in images:
            images.append(src)

    # Filter obvious non-product images
    images = [
        img for img in images
        if not re.search(r"logo|icon|banner|pixel|tracker|1x1|placeholder|spacer", img, re.I)
        and not img.endswith(".svg")
    ]

    # ── Re-rank by filename relevance to product name ─────────────────────────
    # Build keywords from product name — skip short/common words
    # e.g. "SS Toe Guard Kit" → ["toe", "guard", "kit"]
    # Images whose filename contains any keyword are promoted to the front.
    # This prevents sidebar/related-product images from being picked as primary.
    if product_name:
        _stop = {"the", "and", "for", "with", "set", "kit", "ss", "sg", "by", "of", "in"}
        name_keywords = [
            w.lower() for w in re.split(r"[\s\-_]+", product_name)
            if len(w) > 2 and w.lower() not in _stop
        ]
        if name_keywords:
            relevant = [
                img for img in images
                if any(kw in img.lower() for kw in name_keywords)
            ]
            fallback = [img for img in images if img not in relevant]
            images = relevant + fallback
            print(f"  🔎 Image relevance filter: {len(relevant)} relevant, {len(fallback)} fallback")
    # ─────────────────────────────────────────────────────────────────────────

    print(f"  🖼️  Official images found: {len(images)}")
    return images


# =============================================================================
#  AREA C — MAIN PAGE EXTRACTION
# =============================================================================

async def extract_product_info(url: str, is_official: bool = False, product_name: str = ""):
    """
    Scrapes a product page and returns structured data.

    is_official=True  → extracts Body HTML, SEO Description, Official Images,
                        Official Site Title, Official Site Description
    is_official=False → extracts only standard meta description (third-party pages)

    product_name is passed to extract_official_images() so the relevance filter
    can promote images whose filename matches the product being searched.
    """
    try:
        html = await fetch_page_in_thread(url)
        if not html:
            return {}

        base_url = get_base_url(html, url)
        data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])
        soup = BeautifulSoup(html, "html.parser")

        meta_title = soup.find("meta", property="og:title")
        data["SEO Title"] = meta_title["content"] if meta_title else None

        if is_official:
            body_html     = extract_official_body_html(soup)
            seo_desc      = extract_seo_description(soup)
            # Pass product_name so relevance filter promotes correct images
            official_imgs = extract_official_images(soup, base_url, product_name=product_name)

            data["Body HTML"]       = body_html
            data["SEO Description"] = seo_desc
            data["Official Images"] = official_imgs

            # Raw official page title and meta description — passthrough fields,
            # never rewritten by LLM, go straight to output columns.
            page_title_tag = soup.find("title")
            data["Official Site Title"] = (
                page_title_tag.get_text(strip=True) if page_title_tag else ""
            )
            official_meta_desc = soup.find("meta", attrs={"name": "description"})
            data["Official Site Description"] = (
                official_meta_desc["content"].strip()
                if official_meta_desc and official_meta_desc.get("content")
                else ""
            )

            print(f"  📄 Body HTML extracted        : {len(body_html)} chars")
            print(f"  🔍 SEO Description            : {seo_desc[:80]}{'...' if len(seo_desc) > 80 else ''}")
            print(f"  🏷️  Official Site Title        : {data['Official Site Title']}")
            print(f"  📝 Official Site Description  : {data['Official Site Description'][:80]}")

        else:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            data["SEO Description"] = meta_desc["content"] if meta_desc else None
            data["Body HTML"]       = ""

        variants = await extract_variants_from_shopify(soup)
        if not variants:
            variants = await extract_variants_generic(soup, data)
        data["Variants"] = variants or []

        return data

    except Exception as e:
        print(f"Extract product info error: {e}")
        return {}


# =============================================================================
#  REGION DETECTION
# =============================================================================

def detect_region(url, snippet_text=""):
    url_lower     = url.lower()
    snippet_text  = snippet_text.lower()

    retailer_rules = {
    "amazon.in":        "India",
    "amazon.com":       "United States",
    "flipkart.com":     "India",
    "croma.com":        "India",
    "reliance":         "India",
    "walmart.com":      "United States",
    "bestbuy.com":      "United States",
    "target.com":       "United States",
    "argos.co.uk":      "United Kingdom",
    "currys.co.uk":     "United Kingdom",
    "amazon.co.uk":     "United Kingdom",
    "amazon.ca":        "Canada",
    "amazon.de":        "Germany",
    "amazon.fr":        "France",
    "sstoncricket.com": "India",
    "teamsg.in":        "India",
    "toncricket.com":   "India",
    "dsc-cricket.com":  "India",
    "moonwalkr.com":    "India",
    "shreysports.com":  "India",
    "store.cosco.in":   "India",
    "protoscricket.com":"India",
    }
    for key, region in retailer_rules.items():
        if key in url_lower:
            return region

    path_region_patterns = {
        "/in/":    "India",
        "/hi-in/": "India",
        "/en-in/": "India",
        "/en-us/": "United States",
        "/en-gb/": "United Kingdom",
        "/uk/":    "United Kingdom",
        "/ca/":    "Canada",
        "/de/":    "Germany",
        "/fr/":    "France",
    }
    for key, region in path_region_patterns.items():
        if key in url_lower:
            return region

    if "₹" in snippet_text:
        return "India"
    if "$" in snippet_text or "usd" in snippet_text:
        return "United States"
    if "£" in snippet_text:
        return "United Kingdom"
    if "€" in snippet_text:
        return "Europe"

    phone_patterns = {
        r"\+91":          "India",
        r"\+1(?!\d{1,2})": "United States",
        r"\+44":          "United Kingdom",
        r"\+61":          "Australia",
    }
    for pattern, region in phone_patterns.items():
        if re.search(pattern, snippet_text):
            return region

    extracted = tldextract.extract(url)
    tld = extracted.suffix
    tld_mapping = {
        "in":     "India",
        "co.in":  "India",
        "co.uk":  "United Kingdom",
        "uk":     "United Kingdom",
        "ca":     "Canada",
        "com.au": "Australia",
        "sg":     "Singapore",
        "ae":     "United Arab Emirates",
        "de":     "Germany",
        "fr":     "France",
    }
    if tld in tld_mapping:
        return tld_mapping[tld]

    return "Unknown / Global"


# =============================================================================
#  OFFICIAL-SITE HELPERS
# =============================================================================

def _normalize_brand_search(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r"[^\w\s]", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def detect_brand_from_name(product_name: str) -> tuple[str | None, str | None]:
    """Detects a known brand in the product name, using exact token matches then fuzzy matching."""
    normalized_name = _normalize_brand_search(product_name)

    for brand_name in brands:
        normalized_brand = _normalize_brand_search(brand_name)
        pattern = rf"\b{re.escape(normalized_brand)}\b"
        if re.search(pattern, normalized_name):
            return brand_name, official_sites[brand_name]

    match = process.extractOne(
        normalized_name, brands,
        scorer=fuzz.partial_ratio,
        score_cutoff=80
    )
    if match:
        brand_name = match[0]
        return brand_name, official_sites[brand_name]
    return None, None


def get_official_domain(official_url: str) -> str:
    """Extracts bare domain from a URL. e.g. 'https://shop.teamsg.in/' → 'teamsg.in'"""
    parsed   = urlparse(official_url)
    hostname = parsed.netloc.lower()
    parts    = hostname.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_official_url(link: str, official_domain: str) -> bool:
    return official_domain in link.lower()


def is_data_complete(product: dict) -> bool:
    """Returns True if the scraped official data has all critical fields."""
    if not isinstance(product, dict):
        return False

    has_title       = bool(product.get("title", "").strip())
    has_description = bool(product.get("description", "").strip())
    has_images      = bool(product.get("images"))

    variants = product.get("variants", [])
    has_valid_variant = any(
        v.get("Variant Price") or v.get("price")
        for v in variants if isinstance(v, dict)
    )

    return has_title and has_description and has_images and has_valid_variant


# =============================================================================
#  AMAZON FORMATTING FUNCTIONS
# =============================================================================

def clean_text(text: str) -> str:
    if text is None:
        return ""
    parsed = BeautifulSoup(str(text), "html.parser")
    text = parsed.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    if not text.strip():
        return []
    parts = re.split(r",|;|\n|\r", text)
    return [p.strip() for p in parts if p.strip()]


def extract_bullet_points(text: str, max_points: int = 5) -> list[str]:
    text = clean_text(text)
    if not text:
        return []

    fragments = re.split(r"[\n\r]+|\.\s+", text)
    bullets = []
    for fragment in fragments:
        fragment = fragment.strip().rstrip(".")
        if len(fragment) < 20:
            continue
        bullets.append(fragment)
        if len(bullets) >= max_points:
            break

    if not bullets and text:
        bullets = [text[:200].rstrip(".")]

    return bullets[:max_points]


def extract_first_variant_size(variants) -> str:
    if not isinstance(variants, list):
        return ""
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        for key in ("size", "Size", "variant_size", "Variant Size"):
            size = variant.get(key, "")
            if size and str(size).strip():
                return str(size).strip()
    return ""


def build_amazon_row(product_detail: dict) -> dict:
    title = str(
        product_detail.get("Title")
        or product_detail.get("title")
        or product_detail.get("item-name")
        or product_detail.get("item_name")
        or ""
    ).strip()
    brand = str(
        product_detail.get("brand", "")
        or product_detail.get("Brand", "")
        or product_detail.get("brand-name", "")
        or product_detail.get("brand_name", "")
        or ""
    ).strip()
    category = str(
        product_detail.get("category", "")
        or product_detail.get("Category", "")
        or product_detail.get("feed_product_type", "")
        or ""
    ).strip()
    item_type = str(
        product_detail.get("type", "")
        or product_detail.get("Type", "")
        or product_detail.get("item-type", "")
        or category
    ).strip()
    description = str(
        product_detail.get("description", "")
        or product_detail.get("Body HTML", "")
        or product_detail.get("Body (HTML)", "")
        or product_detail.get("body_html", "")
    )
    description_plain = clean_text(description)
    tags = product_detail.get("tags", [])
    if isinstance(tags, str):
        if tags.strip():
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
        else:
            tags = []
    if not tags:
        tags = product_detail.get("Tags") or product_detail.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
    price_info = product_detail.get("price", {})
    if not price_info:
        price_info = product_detail.get("Price", product_detail.get("Variant Price", {}))
    if isinstance(price_info, dict):
        price_value = price_info.get("value", "")
        currency = price_info.get("currency", "")
    else:
        price_value = price_info
        currency = ""

    images = product_detail.get("images", []) or []
    if not images:
        images = product_detail.get("Image Src") or product_detail.get("images") or product_detail.get("image_src") or []
    if isinstance(images, str):
        images = [images]
    if isinstance(images, dict):
        images = [images]
    images = [str(img).strip() for img in images if str(img).strip()]

    bullet_points = extract_bullet_points(description_plain)
    material = ""
    for tag in tags:
        if tag.lower() in ["leather", "wood", "carbon", "inox", "polyester", "nylon", "rubber", "foam"]:
            material = tag
            break

    size_value = extract_first_variant_size(product_detail.get("variants", []))
    color_value = ""
    for tag in tags:
        if tag.lower() in ["red", "blue", "black", "white", "yellow", "green", "gray", "orange", "pink", "brown"]:
            color_value = tag
            break

    row = {header: "" for header in AMAZON_HEADERS}
    row.update({
        "sku": str(product_detail.get("official_sku", "") or product_detail.get("sku", "") or ""),
        "product-id": str(product_detail.get("official_sku", "") or ""),
        "product-id-type": "ASIN" if product_detail.get("official_sku", "") else "",
        "item-name": title,
        "brand-name": brand,
        "manufacturer": brand,
        "item-type": item_type,
        "feed_product_type": category or item_type,
        "product-description": description_plain,
        "search-terms": ", ".join([t.lower() for t in tags if t]),
        "department": "Sports",
        "sport-type": "Cricket",
        "material-type": material,
        "color": color_value,
        "size": size_value,
        "style-name": item_type or title,
        "outer-material-type": material,
        "price": str(price_value),
        "quantity": "1",
        "condition-type": "New",
        "main-image-url": images[0] if images else "",
        "other-image-url1": images[1] if len(images) > 1 else "",
        "other-image-url2": images[2] if len(images) > 2 else "",
        "other-image-url3": images[3] if len(images) > 3 else "",
        "item-weight": str(product_detail.get("weight", "") or ""),
        "item-weight-unit-of-measure": str(product_detail.get("weight_unit", "") or ""),
        "item-package-dimensions": "",
        "item-package-weight": "",
        "country-of-origin": str(product_detail.get("country_of_origin", "") or ""),
        "manufacturer-contact-information": "",
        "update_delete": "Update",
    })

    for idx, bullet in enumerate(bullet_points, start=1):
        row[f"bullet-point{idx}"] = bullet

    return row


# =============================================================================
#  FORMAT PRODUCTS
# =============================================================================

async def format_products(products, format_type):
    if format_type == "shopify":
        formatted = []
        manufacturer_details = {}
        for product_detail in products:
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

                    formatted.append(clean_item)

            except Exception as e:
                print(f"⚠️ Error extracting: {e}")
        return formatted
    elif format_type == "amazon":
        formatted = []
        for product_detail in products:
            try:
                amazon_row = build_amazon_row(product_detail)
                formatted.append(amazon_row)
            except Exception as e:
                print(f"⚠️ Error building Amazon row: {e}")
        return formatted
    else:
        return products


# =============================================================================
#  MAIN PIPELINE — get_multi_source_product_pages
# =============================================================================

async def get_multi_source_product_pages(product_names, region="India", format_type="raw"):
    final_results = []
    batch_size = 3  # Process 3 products at a time to avoid rate limits

    for i in range(0, len(product_names), batch_size):
        batch = product_names[i:i + batch_size]
        print(f"Processing batch {i//batch_size + 1} of {len(batch)} products")

        batch_results = []
        for name in batch:
            # Process each product in the batch
            result = await process_single_product(name, region, format_type)
            if result:
                batch_results.append(result)

        final_results.extend(batch_results)
        # Small delay between batches to prevent rate limits
        if i + batch_size < len(product_names):
            await asyncio.sleep(5)

    if format_type != "raw":
        final_results = await format_products(final_results, format_type)

    return final_results


async def process_single_product(name, region, format_type):
    # ── Step 1: Pre-scrape name correction (spell-check + manual dict) ──
    name = correct_product_name(name)
    print(f"🔍 Searching for product: {name}")

    # ── Step 2: Detect brand → try official site first ───────────────────
    detected_brand, official_url = detect_brand_from_name(name)
    official_data   = None
    official_domain = None

    if detected_brand and official_url:
        official_domain = get_official_domain(official_url)
        print(f"🏷️  Detected brand: '{detected_brand}' → Official site: {official_url}")

        site_query_url = "https://google.serper.dev/search"
        headers        = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        site_payload   = {"q": f"site:{official_domain} {name}", "num": 10}

        try:
            site_res     = requests.post(site_query_url, headers=headers, json=site_payload)
            site_data    = site_res.json()
            site_results = site_data.get("organic", [])

            official_product_url = None
            for r in site_results:
                link = r.get("link", "")
                if is_official_url(link, official_domain):
                    official_product_url = link
                    break

            if not official_product_url:
                official_product_url = official_url
                print(f"⚠️  No product page found on official site, using homepage.")

            print(f"🌐 Scraping official site: {official_product_url}")
            off_region = detect_region(official_product_url)
            if off_region in ("Unknown / Global", "United States"):
                sep = "&" if "?" in official_product_url else "?"
                official_product_url = f"{official_product_url}{sep}currency=usd"

            raw_official  = await extract_product_info(official_product_url, is_official=True, product_name=name)
            official_data = await normalize_info(raw_official)

            # ── Post-scrape name correction from official page title ────────
            # This is the most accurate correction — uses the brand's own title.
            # Replaces the input name if it differs significantly (score < 85).
            official_site_title_raw = raw_official.get("Official Site Title", "")
            name = compare_and_correct_name(name, official_site_title_raw, detected_brand)
            # ───────────────────────────────────────────────────────────────

            if is_data_complete(official_data):
                print(f"✅ Official site data complete for '{name}'. Skipping other sources.")
                official_data["_source"] = "official"
                merged = await summarize_product_info(name, [official_data], official_first=True, format_type=format_type)
                print(f"✅ Final product for '{name}' ready.")
                return merged

            else:
                print(f"⚠️  Official site data incomplete for '{name}'. Falling back to Serper.")
                if official_data:
                    official_data["_source"] = "official"

        except Exception as e:
            print(f"[❌ Failed to scrape official site for '{name}'] {e}")
            official_data = None

    else:
        print(f"⚠️  No brand match found for '{name}'. Proceeding with Serper only.")

    # ── Step 3: Serper fallback ───────────────────────────────────────────
    serper_url = "https://google.serper.dev/search"
    headers    = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload    = {"q": name, "num": 5}

    try:
        res     = requests.post(serper_url, headers=headers, json=payload)
        data    = res.json()
        results = data.get("organic", [])

        if not results:
            print(f"⚠️ No Serper results found for '{name}'")
            if official_data:
                merged = await summarize_product_info(name, [official_data], official_first=True, format_type=format_type)
                return merged

        product_data = []
        if official_data:
            product_data.append(official_data)

        for result in results:
            link = result.get("link")
            if not link:
                continue

            if official_domain and is_official_url(link, official_domain):
                print(f"⏭️  Skipping official domain duplicate: {link}")
                continue

            try:
                detected_region = detect_region(link, result.get("snippet", ""))
                if detected_region in ("Unknown / Global", "United States"):
                    link = f"{link}/?currency=usd"

                print(f"base URL {link} | region: {detected_region}")

                prod      = await extract_product_info(link)
                normalize = await normalize_info(prod)

                if isinstance(normalize, dict):
                    normalize["_source"] = "third_party"
                    product_data.append(normalize)
                elif prod:
                    prod["_source"] = "third_party"
                    product_data.append(prod)

            except Exception as e:
                print(f"[❌ Failed to extract from {link}] {e}")

        if product_data:
            merged = await summarize_product_info(
                name,
                product_data,
                official_first=bool(official_data),
                format_type=format_type
            )
            print(f"✅ Final product for '{name}' ready.")
            return merged

    except Exception as e:
        print(f"[❌ Error fetching '{name}'] {e}")

    return None

def _estimate_batch_prompt_size(product_values, official_first=False):
    if not product_values:
        return 0

    batch_data = {
        "Descriptions": [],
        "Variants": [],
        "Images": [],
        "Official Images": [],
        "Official Body HTML": "",
        "Official SEO Description": "",
    }

    if official_first and product_values:
        official = product_values[0]
        batch_data["Official Body HTML"] = official.get("Body HTML", "")[:5000]
        batch_data["Official SEO Description"] = official.get("SEO Description", "")
        batch_data["Official Images"] = official.get("Official Images", [])[:10]

    for source in product_values:
        if source.get("description"):
            batch_data["Descriptions"].append(source["description"])
        if source.get("variants"):
            batch_data["Variants"].extend(source["variants"])
        if source.get("images"):
            batch_data["Images"].extend(source["images"])

    batch_data["Descriptions"] = batch_data["Descriptions"][:3]
    batch_data["Variants"] = batch_data["Variants"][:10]
    batch_data["Images"] = batch_data["Images"][:10]

    return len(json.dumps(batch_data, default=str))


def _chunk_product_values(product_values, max_chars=10000):
    batches = []
    current = []
    current_size = 0

    for item in product_values:
        item_payload = {
            "description": item.get("description", ""),
            "variants": item.get("variants", [])[:10],
            "images": item.get("images", [])[:10],
            "brand": item.get("brand", ""),
            "Body HTML": item.get("Body HTML", "")[:5000],
            "SEO Description": item.get("SEO Description", ""),
        }
        item_size = len(json.dumps(item_payload, default=str))

        if current and current_size + item_size > max_chars:
            batches.append(current)
            current = [item]
            current_size = item_size
        else:
            current.append(item)
            current_size += item_size

    if current:
        batches.append(current)

    return batches


def _merge_summary_batches(batch_summaries):
    merged = batch_summaries[0].copy()

    for summary in batch_summaries[1:]:
        if summary.get("description"):
            merged["description"] = " ".join(
                filter(None, [merged.get("description", "").strip(), summary["description"].strip()])
            )

        if summary.get("variants"):
            merged.setdefault("variants", []).extend(summary["variants"])

        if summary.get("images") and not merged.get("images"):
            merged["images"] = summary["images"]

        for field in [
            "brand",
            "official_sku",
            "body_html",
            "seo_description",
            "official_site_description",
            "category",
            "type",
        ]:
            if not merged.get(field) and summary.get(field):
                merged[field] = summary[field]

        if summary.get("tags"):
            merged.setdefault("tags", []).extend(summary["tags"])

        if summary.get("price", {}).get("value", 0) > 0:
            current_price = merged.get("price", {}).get("value", 0)
            if current_price == 0 or summary["price"]["value"] < current_price:
                merged["price"] = summary["price"]

    if merged.get("tags"):
        merged["tags"] = list(dict.fromkeys(merged["tags"]))

    return merged


async def summarize_product_info(
    product_name,
    product_values,
    region_info="us",
    official_first: bool = False,
    format_type: str = "raw",
):
    if not product_values:
        return {}

    max_chars_per_batch = MAX_TOKENS_PER_REQUEST * 4
    batches = _chunk_product_values(product_values, max_chars=max_chars_per_batch)
    if len(batches) > 1:
        print(f"Large dataset ({len(product_values)} sources), splitting into {len(batches)} LLM batches")
        batch_summaries = []
        for idx, batch in enumerate(batches):
            batch_summary = await summarize_product_info(
                product_name,
                batch,
                region_info,
                official_first=(official_first and idx == 0)
            )
            batch_summaries.append(batch_summary)

        return _merge_summary_batches(batch_summaries)

    # Original logic for small datasets
    prod_description = []
    prod_variant     = []
    prod_images      = []
    prod_brand       = []

    # ── AREA F: Stash all official passthrough fields ─────────────────────────
    # official_body_html / official_seo_description  → sent to LLM for rewriting
    # official_images                                 → hardcoded into result after LLM
    # official_site_title / official_site_description → bypass LLM, go straight to output
    official_body_html        = ""
    official_seo_description  = ""
    official_images           = []
    official_site_title       = ""
    official_site_description = ""

    if official_first and product_values:
        official_entry            = product_values[0]
        official_body_html        = official_entry.get("Body HTML", "")
        official_seo_description  = official_entry.get("SEO Description", "")
        official_images           = official_entry.get("Official Images", [])
        official_site_title       = official_entry.get("Official Site Title", "")
        official_site_description = official_entry.get("Official Site Description", "")
        # Limit large fields to avoid token limits
        if len(official_body_html) > 5000:
            official_body_html = official_body_html[:5000] + "..."
        official_images = official_images[:10]
    # ─────────────────────────────────────────────────────────────────────────

    print(f"After normalize: {product_values}")

    for p in product_values:
        if p.get("description"):
            prod_description.append(p["description"])
        if p.get("variants"):
            prod_variant.extend(p["variants"])
        if p.get("images"):
            prod_images.extend(p["images"])
        if p.get("brand"):
            prod_brand.append(p["brand"])

    # Limit data size to avoid token limits
    prod_description = prod_description[:3]  # Limit to first 3 descriptions
    prod_variant = prod_variant[:10]  # Limit to first 10 variants
    prod_images = prod_images[:10]  # Limit to first 10 images
    official_images = official_images[:10]  # Limit to first 10 official images

    # Official-source priority instruction
    official_priority_rule = ""
    if official_first:
        official_priority_rule = """
        OFFICIAL SOURCE PRIORITY (MANDATORY):
        - The FIRST entry in Descriptions, Variants, and Images is from the OFFICIAL brand website.
        - Always prefer official source for: title, brand, description, SKU, images.
        - Only use third-party sources to fill genuinely missing fields.
        - Do NOT override official data with third-party data.
        """

    summary_prompt = f"""
        You are an e-commerce catalog normalization engine.

        Your task:
        Given the input data, generate ONLY a single clean JSON object.
        Do NOT output anything except pure JSON.
        No explanations, no headings, no markdown, no comments.

        JSON FORMAT (MANDATORY):
        {{
            "title": "",
            "brand": "",
            "official_sku": "",
            "description": "",
            "body_html": "",
            "seo_description": "",
            "official_site_description": "",
            "category": "",
            "type": "",
            "tags": [],
            "price": {{"value": 0.0, "currency": "INR"}},
            "variants": [],
            "images": []
        }}

        RULES:

        1. Use only the provided input. Do not invent any data.

        2. Title: Set "title" = product_name exactly as provided.

        3. Brand: Extract only if clearly present in the product name. Otherwise "".

        4. Description: Merge all descriptions. Remove duplicates. Facts only.

        5. Images:
           - Include only valid http/https URLs.
           - Return ONLY ONE image — the first valid URL after deduplication.

        6. Variant Structure — each variant:
        {{
            "name": "",
            "sku": "",
            "price": 0,
            "currency": "",
            "size": ""
        }}
        Field mapping: Name→("Variant Name","name","title") | SKU→("Variant SKU","sku","id")
        Price→("Variant Price","price","amount") | Currency→("currency") | Size→("Size","size")
        - Price must be numeric. Missing/invalid price → exclude variant.
        - Only include variants where currency matches the region.

        7. Variant Source Rule:
           Official sources: sgcricket.com, teamsg.in, sanspareils.co.in
           Non-official SKU → set sku = "".

        8. Official SKU Selection:
           - Must come from official source URL.
           - Prefer manufacturer patterns (e.g. SG****), 4-12 alphanumeric chars.
           - Ignore null/empty/short numeric-only SKUs.
           - If none qualify → official_sku = "".

        9. Non-Official SKU: set "sku" = "".

        10. Variants only from: size, color, material, quantity, pack-size.
            DO NOT create variants from price, SKU, name/title differences.

        11. Variant Deduplication:
            - Normalize: lowercase, remove punctuation, remove "for/the/-/_"
            - Keep ONE — lowest price. Non-official SKU → sku = "".

        12. If no real variation → output EXACTLY ONE variant (lowest price).

        {official_priority_rule}

        13. Body HTML — REWRITE FROM OFFICIAL SOURCE:
        Input field: "Official Body HTML"
        - Extract all product facts from the raw HTML.
        - Rewrite as clean HTML using only: <p>, <ul>, <li>, <strong>, <h2>, <h3>.

        SPELLING AND GRAMMAR — FIX ALL OF THESE:
        - "Color may very" → "Color may vary"
        - "colour may very" → "Colour may vary"
        - "Regmar" → "Remar" (if context is bat toe guard kit)
        - Fix any other obvious spelling mistakes in the content.
        - Fix all grammar errors. Write in clear, professional English.

        REMOVE — strip out ALL of the following completely, they are NOT product details:
        - Any manufacturer address, company address, street, city, state, PIN code, country
        - Any line starting with or containing "Manufactured and Marketed by:"
        - Any line starting with or containing "Manufactured by:"
        - Any line starting with or containing "Marketed by:"
        - Company registration or legal details
        - Shipping, return, or delivery policy text
        - Website navigation fragments or promotional taglines unrelated to the product
        - Any sentence containing a full postal address (road, district, pin code)

        KEEP — include ONLY genuine product information:
        - Product features and specifications
        - Materials and construction details
        - What is included in the box / kit contents / net quantity
        - Size, weight, color options
        - Performance or usage details

        - Do NOT add any facts not present in the original source.
        - If Official Body HTML is empty → body_html = "".

        14. SEO Description — DERIVE FROM BODY HTML CONTENT:
        - Write a clean plain-text SEO description, maximum 160 characters.
        - Base it on the product details in the Body HTML you just wrote in rule 13.
        - If "Official SEO Description" input is available and useful → use it as
          a starting point, but rewrite it to match the Body HTML content.
        - If "Official SEO Description" is empty → derive entirely from Body HTML.
        - Must include: product name, key feature or kit contents, brand name.
        - Fix all spelling and grammar errors (same rules as rule 13 above).
        - Remove filler phrases: "Buy now", "Free shipping", "Best price", "Order today".
        - No HTML tags. Plain text only.
        - Must NOT contain any manufacturer address or company registration details.
        - If both Official SEO Description and Body HTML are empty → seo_description = "".

        14b. Official Site Description — DERIVE FROM BODY HTML CONTENT:
        Output field: "official_site_description"
        - Write a plain-text product summary based on the same Body HTML content
          used in rule 13. This should read as a natural product description.
        - Length: 1 to 3 sentences. More detailed than the SEO Description.
        - Include: what the product is, what is included/in the box, key specs or features.
        - Fix all spelling and grammar errors (same rules as rule 13 above).
        - Must NOT contain any manufacturer address, company name, or legal text.
        - If "Official Site Description" input already has useful content →
          clean it up (fix spelling, remove address and legal lines) and use it.
        - If both the input and Body HTML are empty → official_site_description = "".
        - No HTML tags. Plain text only.

        15. Category — choose ONLY from this list:
            Cricket Bats | Cricket Balls | Batting Pads | Batting Gloves |
            Wicket Keeping Gloves | Wicket Keeping Pads | Helmets | Cricket Shoes |
            Cricket Clothing | Cricket Bags | Protective Equipment |
            Cricket Accessories | Training Equipment
        - If unclear → "Cricket Accessories".

        16. Type: Short product type label, 2-4 words.
            Examples: "English Willow Bat", "Leather Cricket Ball", "Junior Batting Pads"
            Derive strictly from product name and description. No invented details.

        17. Tags: 5-15 lowercase hyphen-separated tags.
            Include: brand, category keywords, type, key features, target user,
            material (if known), size/weight (if mentioned).
            Example: ["sg","cricket-bat","english-willow","senior","premium"]

        18. Price (INR PRIORITY — MANDATORY):
            Priority: INR → USD → GBP → any other currency.
            "value" = LOWEST price among variants matching the selected currency.
            If no valid price → {{"value": 0.0, "currency": "INR"}}.

        19. Images (OFFICIAL IMAGE PRIORITY):
            Use first URL from "Official Images" input as the primary image.
            If Official Images empty → first valid http/https URL from all images.
            Return ONLY ONE image URL.

        FINAL OUTPUT: Return ONLY the JSON object. Nothing else.
    """

    user_prompt = f"""
        ### INPUT DATA
        Product Name: "{product_name}"
        Descriptions: {json.dumps(prod_description)}
        Variants: {json.dumps(prod_variant)}
        Images: {json.dumps(prod_images)}
        Official Images: {json.dumps(official_images)}
        Brands: {json.dumps(prod_brand)}
        Region: "{region_info}"
        Official Body HTML: {json.dumps(official_body_html)}
        Official SEO Description: {json.dumps(official_seo_description)}

        -----------------------------------------------------
        REGION-BASED VARIANT FILTERING (MANDATORY)
        -----------------------------------------------------

        1. The region is "{region_info}".

        2. Region → Currency:
            us / Unknown / Global → USD
            india / in            → INR
            uk                    → GBP
            eu                    → EUR

        3. HARD FILTER: REMOVE every variant whose currency does NOT match region currency.
           No fallback. No inference. No currency conversion.

        4. After filtering: keep only the LOWEST price variant(s).
           If multiple share the same lowest price → keep one unless real attribute differs.

        5. No assumptions. No multiple currencies in output.

        6. Product name Pascal Case:
           Every word starts with Capital letter, rest lowercase.
           "apple iphone pro max" → "Apple Iphone Pro Max"

        -----------------------------------------------------
        REQUIRED OUTPUT FORMAT
        -----------------------------------------------------
        {{
        "title": "{product_name}",
        "brand": "",
        "official_sku": "",
        "description": "",
        "body_html": "",
        "seo_description": "",
        "official_site_description": "",
        "category": "",
        "type": "",
        "tags": [],
        "price": {{"value": 0.0, "currency": "INR"}},
        "variants": [],
        "images": []
        }}

        -----------------------------------------------------
        FORMAT-SPECIFIC FIELD GUIDANCE
        -----------------------------------------------------
        {"" if format_type != "amazon" else '''
        This data will be used for an Amazon bulk upload listing.
        Prioritise these fields:
        - bullet_points: extract 5 concise feature sentences from description/body_html
        - search_terms: 5 comma-separated keyword phrases a buyer would search
        - brand: must be exact brand name (e.g. "SS", "SG", "Kookaburra")
        - category / type: use Amazon Sports category conventions
        - price: INR value, numeric only
        - images: direct product image URLs, no banners or logos
        '''}
        {"" if format_type != "shopify" else '''
        This data will be used for a Shopify product listing.
        Prioritise these fields:
        - body_html: clean HTML with <p><ul><li><strong> tags only
        - seo_description: 160-char plain-text benefit sentence
        - tags: 5-15 lowercase hyphen-separated keywords
        - variants: size/color/material options with prices
        - vendor: exact brand name
        '''}
    """

    response = await call_llm(llm, prompt, summary_prompt, user_prompt)

    try:
        result = json.loads(response.content.strip())
    except Exception:
        result = json.loads(sanitize_json(response.content.strip()))

    # ── AREA F (continued): Finalise all output fields ────────────────────────

    # LLM rewrites body_html and seo_description → map to output column names
    result["Body HTML"]       = result.pop("body_html", "")
    result["SEO Description"] = result.pop("seo_description", "")

    # Official Site Description — LLM derives from Body HTML content (rule 14b).
    # If the LLM produced one, use it. Otherwise fall back in priority order:
    #   1. LLM-written official_site_description  (from rule 14b)
    #   2. Raw official_site_description from scrape (if non-empty)
    #   3. SEO Description as last resort (so column is never blank)
    llm_official_desc = result.pop("official_site_description", "")
    if llm_official_desc and llm_official_desc.strip():
        result["Official Site Description"] = llm_official_desc.strip()
    elif official_site_description and official_site_description.strip():
        result["Official Site Description"] = official_site_description.strip()
    else:
        # Fallback: derive from SEO Description so column is never empty
        result["Official Site Description"] = result.get("SEO Description", "")

    # LLM fills these from rules 15-17
    result.setdefault("category", "")
    result.setdefault("type",     "")
    result.setdefault("tags",     [])

    # LLM fills price with INR priority from rule 18
    result.setdefault("price", {"value": 0.0, "currency": "INR"})

    # Official image always wins — hardcoded after LLM call
    if official_images:
        result["images"] = [official_images[0]]
    else:
        result.setdefault("images", [])

    # Official Site Title — raw passthrough, no LLM rewrite
    result["Official Site Title"] = official_site_title
    # ─────────────────────────────────────────────────────────────────────────

    return result


# =============================================================================
#  NORMALIZE INFO  (LLM pass 1 — structure raw scraped data)
# =============================================================================

def sanitize_json(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = text.replace("```", "")
    if "{" in text:
        text = text[text.index("{"):]
    return text.strip()


def sanitize_json_online_llm(text: str) -> str:
    # Fix bad backslash escapes
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)

    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Find the start of the JSON array or object
    if not text.startswith("[") and not text.startswith("{"):
        start = text.find("[")
        if start == -1:
            start = text.find("{")
        if start != -1:
            text = text[start:]

    # ── FIX: Handle unterminated strings / truncated responses ────────────
    # If JSON is cut off mid-response (token limit), close open structures.
    # Count open brackets to detect truncation.
    open_braces   = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    # Close any open strings first — find last complete value
    # Remove any trailing incomplete key-value pair
    text = re.sub(r',\s*"[^"]*$', '', text)        # trailing incomplete key
    text = re.sub(r':\s*"[^"]*$', ': ""', text)    # truncated string value → empty string

    # Close open structures
    text = text.rstrip().rstrip(",")
    text += "}" * max(open_braces, 0)
    text += "]" * max(open_brackets, 0)
    # ─────────────────────────────────────────────────────────────────────

    return text


async def normalize_info(prod_detail):
    """
    LLM pass 1 — structure raw scraped data into clean JSON.

    Strips large content fields before LLM call to avoid token limits.
    """
    # ── AREA E: Stash all official passthrough fields before LLM call ─────────
    # LLM will not see or modify these — they are re-attached after.
    official_body_html        = prod_detail.get("Body HTML", "")
    official_seo_description  = prod_detail.get("SEO Description", "")
    official_images           = prod_detail.get("Official Images", [])
    official_site_title       = prod_detail.get("Official Site Title", "")
    official_site_description = prod_detail.get("Official Site Description", "")
    # ─────────────────────────────────────────────────────────────────────────

    # ── Create cleaned input for LLM (remove large content to avoid token limits) ──
    cleaned_detail = {}
    for key, value in prod_detail.items():
        # Skip large content fields that would exceed token limits
        if key.lower() in [
            "body html", "seo description", "official images", "official site title",
            "official site description", "html_content", "raw_html", "page_content"
        ]:
            continue
        # Skip very long text fields (>1000 chars)
        if isinstance(value, str) and len(value) > 1000:
            continue
        # Skip large arrays/lists
        if isinstance(value, (list, tuple)) and len(value) > 10:
            continue
        cleaned_detail[key] = value
    # ─────────────────────────────────────────────────────────────────────────

    normalize_prompt = """
        You are a JSON-only generator.

        RULES:
        - Output ONLY a valid JSON object. Start with { end with }.
        - No markdown, headings, code fences, explanations, or comments.
        - Use empty strings for missing text, empty arrays for missing lists.
        - Every variant object MUST have a "currency" field (string).

        Structure:
        {
            "title": "",
            "brand": "",
            "description": "",
            "price": {"value": 0.0, "currency": ""},
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
            "attributes": {"Color": "", "Size": ""}
        }

        Instructions:
        - Set "price.currency" to detected currency code (e.g. "INR", "USD").
        - Copy same currency into each variant's "currency" field.
        - If currency undetectable → use "".
    """

    clean_input = json.dumps(cleaned_detail, ensure_ascii=False, indent=2)

    user_prompt = f"""
        Convert the following product details into the JSON structure.

        Return ONLY the JSON.

        PRODUCT DATA:
        {clean_input}
    """

    try:
        extractor_response = await call_llm(llm, prompt, normalize_prompt, user_prompt)
        details = extractor_response.content.strip()

        try:
            parsed = json.loads(sanitize_json(details))
        except Exception:
            parsed = json.loads(details)
    except Exception as e:
        print(f"⚠️ Normalization error: {e}")
        parsed = {}

    # ── AREA E (continued): Re-attach passthrough fields after LLM call ───────
    if official_body_html:
        parsed["Body HTML"]       = official_body_html
    if official_seo_description:
        parsed["SEO Description"] = official_seo_description
    if official_images:
        parsed["Official Images"] = official_images
    if official_site_title:
        parsed["Official Site Title"]       = official_site_title
    if official_site_description:
        parsed["Official Site Description"] = official_site_description
    # ─────────────────────────────────────────────────────────────────────────

    return parsed