import asyncio
import threading
import requests
import os
import json
import re
import pandas as pd
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

from yards.utils.config import AMAZON_HEADERS, SHOPIFY_HEADERS, FLIPKART_HEADERS

load_dotenv()
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
try:
    llm, prompt = llm_init()
except ValueError:
    llm, prompt = None, None

MAX_TOKENS_PER_REQUEST = 2500


def _get_llm_prompt(groq_api_key=None):
    if groq_api_key:
        return llm_init(groq_api_key=groq_api_key)
    if llm is None or prompt is None:
        raise ValueError(
            "Groq API key missing. Provide groq_api_key on the request or set GROQ_API_KEY in .env."
        )
    return llm, prompt


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

# Brands whose official websites return EUR/GBP prices — skip price from official site
EUR_PRICE_BRANDS = {"Kookaburra", "Gray-Nicolls", "Gunn & Moore", "New Balance", "Masuri"}


# =============================================================================
#  FLIP-1: CRICKET FIELD CONFIG
# =============================================================================

BRAND_TO_MANUFACTURER = {
    "SG":     "Sanspareils Greenlands Pvt Ltd",
    "SS":     "Sareen Sports Industries",
    "SS TON": "Sareen Sports Industries",
    "TON":    "Sareen Sports Industries",
    "TYKA":   "TYKA Sportswear Pvt Ltd",
    "Cosco":  "Cosco India Ltd",
    "DSC":    "DSC Sports",
    "MRF":    "MRF Limited",
}

MANDATORY_FIELDS = {
    "bat": [
        "Seller SKU ID", "MRP (INR)", "Your selling price (INR)", "Fullfilment by",
        "HSN", "Country Of Origin", "Manufacturer Details", "Packer Details",
        "Tax Code", "Brand", "Model Name", "Weight Range", "Weight Range - Measuring Unit",
        "Part Number", "Blade Material", "Sport Type", "Playing Level", "Ideal For",
        "Cover Included", "Bat Grade", "Size", "Age Group", "Color",
        "Width", "Width - Measuring Unit", "Height", "Height - Measuring Unit",
        "Depth", "Depth - Measuring Unit", "Main Image URL", "Other Image URL 1", "Stock",
    ],
    "ball": [
        "Seller SKU ID", "MRP (INR)", "Your selling price (INR)", "Fullfilment by",
        "HSN", "Country Of Origin", "Manufacturer Details", "Packer Details",
        "Tax Code", "Brand", "Model Name", "Part Number ID", "Weight Range (g)",
        "Type", "Ideal For", "Stitching Type", "Diameter (cm)", "Color",
        "Pack of", "Pack of - Measuring Unit", "Size", "Main Image URL",
    ],
}

BAT_ALLOWED = {
    "Blade Material": {
        "Cricket":  ["English Willow", "Kashmir Willow", "PVC/Plastic", "Poplar Willow"],
        "Baseball": ["Alloy", "Aluminium", "Maple", "Willow"],
    },
    "Size": {
        "Cricket": ["0", "1", "2", "3", "4", "5", "6", "Harrow", "Long Handle", "Short Handle"],
    },
    "Playing Level": ["Advanced", "Beginner", "Intermediate", "Recreational", "Training"],
    "Age Group": {
        "Cricket": [
            "10 - 12 Yrs", "11 - 13 Yrs", "12 - 14 Yrs", "15+ Yrs",
            "4 - 5 Yrs", "6 - 7 Yrs", "8 Yrs", "9 - 11 Yrs", "Below 4 Yrs",
        ],
    },
    "Ideal For": ["Junior", "Senior"],
    "Color": [
        "Beige", "Black", "Blue", "Brown", "Gold", "Green", "Grey", "Maroon",
        "Multicolor", "Orange", "Pink", "Purple", "Red", "Silver", "Tan", "White", "Yellow",
    ],
    "Bat Grade": ["Grade 1", "Grade 1+", "Grade 2", "Grade 3", "Grade 4", "Grade 5"],
}

BALL_ALLOWED = {
    "Type": [
        "Cricket Leather Ball", "Cricket Rubber Ball", "Cricket Synthetic Ball",
        "Cricket Tennis Ball", "Cricket Training Ball", "Foam Ball",
    ],
    "Ideal For":      ["Kids", "Standard"],
    "Stitching Type": ["Fused", "Hand Stitched", "Machine Stitched", "NA", "Rubber Moulded"],
    "Color": [
        "Beige", "Black", "Blue", "Brown", "Gold", "Green", "Grey", "Maroon",
        "Multicolor", "Orange", "Pink", "Purple", "Red", "Silver", "White", "Yellow",
    ],
}

GLOBAL_DEFAULTS = {
    "Fullfilment by":                  "Seller",
    "Packer Details":                  "Twenty2Yards The Milange Jakat Naka Virar West",
    "Tax Code":                        "GST_5",
    "Part Number":                     "NA",
    "Country Of Origin":               "India",
    "Listing Status":                  "Inactive",
    "Procurement type":                "domestic procurement",
    "Shipping provider":               "Flipkart",
    "Procurement SLA (DAY)":           "5",
    "Minimum Order Quantity (MinOQ)":  "1",
    "HSN":                             "95069920",
    "Stock":                           "10",
}

UNIT_DEFAULTS = {
    "Width - Measuring Unit":          "Inch",
    "Height - Measuring Unit":         "Inch",
    "Depth - Measuring Unit":          "Inch",
    "Weight Range - Measuring Unit":   "g",
    "Pack of - Measuring Unit":        "Unit",
}

PRODUCT_TYPE_DEFAULTS = {
    "bat": {
        "Bat Grade":      "Grade 4",
        "Cover Included": "Yes",
        "Playing Level":  "Advanced",
        "Ideal For":      "Senior",
        "Size":           "Short Handle",
        "Age Group":      "15+ Yrs",
        "Color":          "White",
        "Sport Type":     "Cricket",
    },
    "ball": {
        "Sport Type":  "Cricket",
        "Color":       "Red",
        "Ideal For":   "Standard",
    },
}

CATEGORY_TYPE_DEFAULTS = {
    "bat": {
        "Length (CM)":  "22",
        "Breadth (CM)": "10",
        "Height (CM)":  "4",
        "Weight (KG)":  "1.2",
    },
    "ball": {},
}


# =============================================================================
#  PRICE EXTRACTION — CSS SELECTORS (platform-specific, tried in order)
# =============================================================================

PLATFORM_PRICE_SELECTORS = [
    # Shopify
    "[data-product-price]",
    ".product__price .price",
    ".price__regular .price-item--regular",
    ".price-item--regular",
    ".product-single__price",
    ".price__sale .price-item--sale",
    # WooCommerce
    ".woocommerce-Price-amount.amount bdi",
    ".woocommerce-Price-amount.amount",
    "p.price .woocommerce-Price-amount",
    "ins .woocommerce-Price-amount",
    # Generic / Indian cricket stores
    "[itemprop='price']",
    "[data-price]",
    "[data-selling-price]",
    "[data-mrp]",
    ".selling-price",
    ".sale-price",
    ".product-price",
    ".price-box .price",
    "#product-price",
    ".current-price",
    ".offer-price",
    ".special-price .price",
    ".price .amount",
    ".price",
    # Indian-specific
    ".priceTxt",
    ".priceVal",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    ".a-price-whole",
    "span.a-offscreen",
    "#price_inside_buybox",
    ".price__current",
    # Flipkart
    "._30jeq3._16Jk6d",
    "._30jeq3",
    "._25b18 ._30jeq3",
    # DSC / SS TON / SG custom stores
    ".product-info-price .price",
    ".regular-price .price",
    ".pdp-price",
    ".pdp__price",
    ".pdp-details__price",
    "#product-price-value",
]

# Attributes that directly carry the price value
_PRICE_ATTRS = ["data-price", "data-selling-price", "data-mrp", "content", "value"]

# Extended price regex patterns
_INR_PRICE_PATTERNS = [
    r"[₹\u20b9]\s*([\d,]+(?:\.\d{1,2})?)",
    r"(?:Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)",
    r'(?:price|mrp|selling[_\-]?price)["\']?\s*[=:]\s*["\']?([\d,]+(?:\.\d{1,2})?)',
    r"(?:MRP|Price|Selling\s+Price|SP)\s*:?\s*([\d,]{3,6})",
    r"\b([\d]{1,2},[\d]{3}(?:\.\d{1,2})?)\b",
]
_USD_PRICE_PATTERNS = [
    r"\$\s*([\d,]+(?:\.\d{1,2})?)",
    r"USD\s*([\d,]+(?:\.\d{1,2})?)",
]
_GBP_PRICE_PATTERNS = [
    r"£\s*([\d,]+(?:\.\d{1,2})?)",
    r"GBP\s*([\d,]+(?:\.\d{1,2})?)",
]
_MRP_SELECTORS = [
    "s .woocommerce-Price-amount",
    "del .woocommerce-Price-amount",
    ".compare-at-price",
    ".price__compare",
    ".was-price .price",
    "._3I9_wc._2p6lqe",
    "._3I9_wc",
    ".price-mrp",
    ".price-old",
    "strike",
    "s",
    "del",
    "[data-compare-price]",
    "[data-mrp]",
]


# =============================================================================
#  FLIP-2: CRICKET FIELD EXTRACTION HELPERS
# =============================================================================

def _is_empty(val) -> bool:
    return str(val).strip().lower() in ("", "nan", "none", "na", "n/a", "not found")


def _extract_blade_material(text) -> str | None:
    if not text:
        return None
    tl = str(text).lower()
    cutoff = re.search(
        r'you may also like|related products|compare with another|customers also'
        r'|you might also|similar products|more from this brand',
        tl,
    )
    if cutoff:
        tl = tl[: cutoff.start()]

    strict = [
        (r'blade material\s*[:\-]?\s*english willow', "English Willow"),
        (r'blade material\s*[:\-]?\s*kashmir willow', "Kashmir Willow"),
        (r'wood type\s*[:\-]?\s*english willow',      "English Willow"),
        (r'wood type\s*[:\-]?\s*kashmir willow',      "Kashmir Willow"),
        (r'willow type\s*[:\-]?\s*english willow',    "English Willow"),
        (r'willow type\s*[:\-]?\s*kashmir willow',    "Kashmir Willow"),
        (r'made from english willow',                  "English Willow"),
        (r'made from kashmir willow',                  "Kashmir Willow"),
        (r'crafted from english willow',               "English Willow"),
        (r'crafted from kashmir willow',               "Kashmir Willow"),
    ]
    for pat, val in strict:
        if re.search(pat, tl, re.I):
            return val

    if re.search(r'\benglish willow cricket bat\b', tl): return "English Willow"
    if re.search(r'\bkashmir willow cricket bat\b', tl): return "Kashmir Willow"
    if re.search(r'\bew\b', tl): return "English Willow"
    if re.search(r'\bkw\b', tl): return "Kashmir Willow"
    if re.search(r'\bpw\b', tl): return "Poplar Willow"
    if re.search(r'\benglish willow\b', tl): return "English Willow"
    if re.search(r'\bkashmir willow\b', tl): return "Kashmir Willow"
    if re.search(r'\bpoplar willow\b', tl): return "Poplar Willow"
    if re.search(r'\bpvc\b|\bplastic\b', tl): return "PVC/Plastic"
    return None


def _extract_bat_grade(text) -> str | None:
    m = re.search(r'\bgrade\s*([1-5]\+?)\b', str(text), re.I)
    return f"Grade {m.group(1)}" if m else None


def _extract_size(text) -> str | None:
    tl = str(text).lower()
    if re.search(r'\bshort handle\b|\bsh\b', tl): return "Short Handle"
    if re.search(r'\blong handle\b|\blh\b', tl):  return "Long Handle"
    if re.search(r'\bharrow\b', tl):               return "Harrow"
    m = re.search(r'\bsize\s*[:\-]?\s*([0-6])\b', tl)
    return m.group(1) if m else None


def _extract_weight_range(text) -> str | None:
    if not text:
        return None
    m = re.search(r'(\d{3,4})\s*(?:to|-)\s*(\d{3,4})\s*(?:grams|g)', str(text), re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def convert_weight_to_range(raw: str) -> str:
    if not raw or _is_empty(str(raw)):
        return ""
    text = str(raw).strip().lower()
    grams_list = []
    for m in re.finditer(r'(\d[\d.,]*)\s*(kg|g|gm|gms|gram|grams)?', text):
        val_str = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        try:
            val = float(val_str)
            if unit == "kg" or (not unit and val < 10):
                val *= 1000
            grams_list.append(round(val))
        except Exception:
            pass
    if not grams_list:
        return ""
    grams_list = sorted(set(grams_list))
    if len(grams_list) == 1:
        g = grams_list[0]
        if g <= 1100:   return "1000-1100"
        elif g <= 1200: return "1100-1200"
        elif g <= 1300: return "1200-1300"
        elif g <= 1400: return "1300-1400"
        else:           return "1400-1500"
    return f"{grams_list[0]}-{grams_list[-1]}"


def derive_size_from_cm(height_cm) -> str:
    try:
        h = float(height_cm)
    except Exception:
        return ""
    if h >= 83.75:   return "Short Handle"
    elif h >= 81.5:  return "Harrow"
    elif h >= 79:    return "6"
    elif h >= 76.25: return "5"
    elif h >= 73.75: return "4"
    elif h >= 71.25: return "3"
    elif h >= 68.25: return "2"
    elif h >= 65.5:  return "1"
    elif h >= 63:    return "0"
    return ""


def derive_age_group(size: str) -> str:
    s = str(size).strip().lower().replace("-", " ").strip()
    mapping = {
        "short handle": "15+ Yrs", "sh": "15+ Yrs", "harrow": "15+ Yrs", "6": "15+ Yrs",
        "5": "12 - 14 Yrs", "4": "11 - 13 Yrs", "3": "10 - 12 Yrs",
        "2": "9 - 11 Yrs", "1": "8 Yrs", "0": "6 - 7 Yrs",
    }
    return mapping.get(s, "")


def derive_ideal_for(age: str) -> str:
    return "Senior" if "15+" in str(age) else "Junior"


def derive_playing_level(age: str) -> str:
    age = str(age).lower()
    if "15+" in age: return "Advanced"
    if any(x in age for x in ["12 - 14", "11 - 13", "10 - 12"]): return "Intermediate"
    return "Beginner"


def _extract_sport_type(text) -> str | None:
    tl = str(text).lower()
    m = re.search(r'sport\s*(?:type|s)?\s*[:\-]\s*(cricket|baseball|softball)', tl, re.I)
    if m:
        return m.group(1).capitalize()
    cricket_kws  = ["cricket", "batting", "wicket", "english willow", "kashmir willow", "ipl", "bcci"]
    baseball_kws = ["baseball", "mlb", "pitcher", "home run"]
    scores = {
        "Cricket":  sum(1 for kw in cricket_kws  if kw in tl),
        "Baseball": sum(1 for kw in baseball_kws if kw in tl),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _clean_manufacturer(raw: str) -> str:
    if not raw:
        return ""
    raw = str(raw).strip()
    for delim in [",", ".", "\n", "Pvt", "Ltd", "Private", "Limited",
                  "Address", "Plot", "No.", "Near", "Street", "Road",
                  "India", "Village", "District", "State", "Pin"]:
        idx = raw.lower().find(delim.lower())
        if idx > 2:
            raw = raw[:idx]
    raw = re.sub(r'\(.*?\)', '', raw)
    raw = re.sub(r'[\d@#&]', '', raw)
    raw = re.sub(
        r'^(manufactured\s+and\s+marketed\s+by|manufactured\s+by|marketed\s+by|mfg\.?\s*by)[:\s]+',
        '', raw, flags=re.I,
    )
    raw = raw.strip(" ,.-")
    return " ".join(raw.split()[:3]).title()


def _extract_manufacturer(text) -> str | None:
    m = re.search(
        r'(?:manufactured and marketed by|manufactured by|marketed by|mfg\.?\s*by)[:\s]+([^\.]{5,150})',
        str(text), re.I,
    )
    if m:
        result = _clean_manufacturer(m.group(1))
        return result or None
    return None


# =============================================================================
#  PRICE EXTRACTION — ALL STRATEGIES
# =============================================================================

def _extract_inr_price(text: str) -> float | None:
    """
    Extended INR price extractor — tries multiple patterns.
    Returns the first plausible value in the 100–99,999 INR range.
    """
    if not text:
        return None
    text = str(text)
    for pat in _INR_PRICE_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(",", ""))
                if 100 <= val <= 99_999:
                    return val
            except (ValueError, IndexError):
                pass
    return None


def _extract_price_any_currency(text: str) -> dict | None:
    """Returns {"value": float, "currency": "INR"/"USD"/"GBP"} or None."""
    inr = _extract_inr_price(text)
    if inr:
        return {"value": inr, "currency": "INR"}
    for pat in _USD_PRICE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 5 <= val <= 5_000:
                    return {"value": val, "currency": "USD"}
            except (ValueError, TypeError):
                pass
    for pat in _GBP_PRICE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 5 <= val <= 5_000:
                    return {"value": val, "currency": "GBP"}
            except (ValueError, TypeError):
                pass
    return None


def _extract_price_from_json_ld(soup: BeautifulSoup) -> dict | None:
    """
    Strategy 1: Parse JSON-LD structured data — most reliable source.
    Handles @graph arrays (common on Shopify) and nested offer lists.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            if not raw.strip():
                continue
            data = json.loads(raw)
            entries = data if isinstance(data, list) else [data]

            # Expand @graph
            expanded = []
            for entry in entries:
                if "@graph" in entry:
                    expanded.extend(entry["@graph"])
                else:
                    expanded.append(entry)

            for entry in expanded:
                if entry.get("@type") not in ("Product", "Offer"):
                    continue
                offers = entry.get("offers") or (
                    entry if entry.get("@type") == "Offer" else None
                )
                if not offers:
                    continue
                offer_list = offers if isinstance(offers, list) else [offers]
                for offer in offer_list:
                    raw_price = offer.get("price") or offer.get("lowPrice")
                    currency  = (offer.get("priceCurrency") or "").upper()
                    if raw_price is not None:
                        try:
                            val = float(str(raw_price).replace(",", ""))
                            if val > 0:
                                return {"value": val, "currency": currency or "INR"}
                        except (ValueError, TypeError):
                            pass
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _extract_price_from_meta(soup: BeautifulSoup) -> dict | None:
    """
    Strategy 2: Open Graph and product meta tags.
    Used by Shopify, WooCommerce, and many Indian cricket stores.
    """
    # og:price:amount + og:price:currency
    amt_tag = soup.find("meta", property="og:price:amount")
    cur_tag = soup.find("meta", property="og:price:currency")
    if amt_tag:
        try:
            val      = float(str(amt_tag.get("content", "0")).replace(",", ""))
            currency = (cur_tag.get("content", "INR") if cur_tag else "INR").upper()
            if val > 0:
                return {"value": val, "currency": currency}
        except (ValueError, TypeError):
            pass

    # product:price:amount (Facebook catalog)
    amt = soup.find("meta", property="product:price:amount")
    cur = soup.find("meta", property="product:price:currency")
    if amt:
        try:
            val      = float(str(amt.get("content", "0")).replace(",", ""))
            currency = (cur.get("content", "INR") if cur else "INR").upper()
            if val > 0:
                return {"value": val, "currency": currency}
        except (ValueError, TypeError):
            pass
    return None


def _extract_price_from_css(soup: BeautifulSoup) -> dict | None:
    """
    Strategy 3: CSS selectors — 30+ platform-specific selectors.
    Tries both data-attribute values and inner text.
    """
    for selector in PLATFORM_PRICE_SELECTORS:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        for el in elements:
            # Try data attributes first (already numeric)
            for attr in _PRICE_ATTRS:
                raw = el.get(attr, "")
                if raw:
                    price = _extract_price_any_currency(str(raw))
                    if price:
                        return price
            # Then inner text
            text = el.get_text(separator=" ", strip=True)
            if text:
                price = _extract_price_any_currency(text)
                if price:
                    return price
    return None


def _extract_price_from_scripts(soup: BeautifulSoup) -> dict | None:
    """
    Strategy 4: Inline JS globals.
    Shopify stores price as integer paise (₹1299 → 129900); we divide by 100.
    """
    js_patterns = [
        r'"price"\s*:\s*(\d+)',
        r'"price_min"\s*:\s*(\d+)',
        r'"compare_at_price"\s*:\s*(\d+)',
        r'"original_price"\s*:\s*(\d+)',
        r'(?:selling_price|mrp|sale_price|regular_price)\s*[=:]\s*["\']?([\d,]+(?:\.\d{1,2})?)',
        r'"finalPrice"\s*:\s*([\d.]+)',
        r'"offerPrice"\s*:\s*([\d.]+)',
        r'"discountedPrice"\s*:\s*([\d.]+)',
        r'"specialPrice"\s*:\s*([\d.]+)',
        r'"price_in_cents"\s*:\s*(\d+)',
    ]
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text or len(text) < 10:
            continue
        for pat in js_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    raw_val = float(m.group(1).replace(",", ""))
                    # Shopify paise detection: ₹1299 stored as 129900
                    if raw_val > 99_999:
                        raw_val = raw_val / 100
                    if 100 <= raw_val <= 99_999:
                        return {"value": raw_val, "currency": "INR"}
                except (ValueError, TypeError):
                    pass
    return None


def _extract_price_from_page(soup: BeautifulSoup, page_text: str = "") -> dict | None:
    """
    Master price extractor. Tries all strategies in reliability order:
      1. JSON-LD structured data
      2. Open Graph / product meta tags
      3. CSS selectors
      4. Inline JS globals
      5. Raw text regex (last resort)
    Returns {"value": float, "currency": str} or None.
    """
    price = _extract_price_from_json_ld(soup)
    if price:
        print(f"  💰 Price from JSON-LD       : {price}")
        return price

    price = _extract_price_from_meta(soup)
    if price:
        print(f"  💰 Price from meta tags     : {price}")
        return price

    price = _extract_price_from_css(soup)
    if price:
        print(f"  💰 Price from CSS selector  : {price}")
        return price

    price = _extract_price_from_scripts(soup)
    if price:
        print(f"  💰 Price from JS globals    : {price}")
        return price

    if page_text:
        price = _extract_price_any_currency(page_text[:10_000])
        if price:
            print(f"  💰 Price from raw text      : {price}")
            return price

    print("  ⚠️  No price found on this page")
    return None


def _extract_mrp_and_selling_price(soup: BeautifulSoup, page_text: str = "") -> dict:
    """
    Distinguishes MRP (crossed-out) from selling price.
    Returns {"selling_price": dict|None, "mrp": dict|None}.
    """
    result = {"selling_price": None, "mrp": None}

    for sel in _MRP_SELECTORS:
        try:
            el = soup.select_one(sel)
            if el:
                raw = (
                    el.get_text(strip=True)
                    or el.get("data-compare-price", "")
                    or el.get("data-mrp", "")
                )
                price = _extract_price_any_currency(raw)
                if price:
                    result["mrp"] = price
                    break
        except Exception:
            pass

    result["selling_price"] = _extract_price_from_page(soup, page_text)

    # Sanity: if MRP < selling price they may be the same element
    sp  = result["selling_price"]
    mrp = result["mrp"]
    if sp and mrp and mrp["value"] < sp["value"]:
        result["mrp"] = sp

    return result


def ensure_price_in_detail(product_detail: dict) -> dict:
    """
    Guarantees MRP (INR) and Your selling price (INR) are populated.
    Called at the top of build_flipkart_row() and build_amazon_row().
    Checks every possible location the scraped price might have been stored.
    """
    existing_mrp = str(product_detail.get("MRP (INR)", "")).strip()
    if existing_mrp and existing_mrp not in ("0", "0.0", "nan", ""):
        return product_detail  # already set

    # 1. Structured price dicts from scrape
    for key in ("selling_price", "price", "mrp_price"):
        src = product_detail.get(key)
        if isinstance(src, dict):
            val      = src.get("value", 0)
            currency = str(src.get("currency", "")).upper()
            if val > 0 and currency in ("INR", ""):
                product_detail["MRP (INR)"]                = val
                product_detail["Your selling price (INR)"] = val
                print(f"  💰 Price injected from '{key}': ₹{val}")
                return product_detail

    # 2. Flat price keys (various naming conventions)
    for key in ("Price", "price", "mrp", "MRP", "sellingPrice", "salePrice",
                "Variant Price", "variant_price"):
        raw = product_detail.get(key)
        if raw:
            inr = _extract_inr_price(str(raw))
            if inr:
                product_detail["MRP (INR)"]                = inr
                product_detail["Your selling price (INR)"] = inr
                print(f"  💰 Price injected from field '{key}': ₹{inr}")
                return product_detail

    # 3. Check variants list for any price
    for v in (product_detail.get("variants") or []):
        if not isinstance(v, dict):
            continue
        for vkey in ("Variant Price", "price", "Price", "variant_price"):
            raw = v.get(vkey)
            if raw:
                inr = _extract_inr_price(str(raw))
                if inr:
                    product_detail["MRP (INR)"]                = inr
                    product_detail["Your selling price (INR)"] = inr
                    print(f"  💰 Price injected from variant '{vkey}': ₹{inr}")
                    return product_detail

    return product_detail


def map_to_allowed_value(field: str, value, product_type: str, sport_type: str = "Cricket") -> str:
    value_str = str(value).strip()
    if _is_empty(value_str):
        return ""
    allowed_map = BAT_ALLOWED if product_type == "bat" else BALL_ALLOWED
    allowed = allowed_map.get(field)
    if allowed is None:
        return value_str
    options = (
        allowed.get(sport_type) or allowed.get("Cricket") or []
        if isinstance(allowed, dict) else allowed
    )
    for opt in options:
        if opt.lower() == value_str.lower():
            return opt
    for opt in options:
        if opt.lower() in value_str.lower() or value_str.lower() in opt.lower():
            return opt
    return value_str


def _detect_product_type(text: str) -> str:
    tl = text.lower()
    bat_hits  = sum(1 for kw in ["bat", "blade", "willow", "cricket bat"] if kw in tl)
    ball_hits = sum(1 for kw in ["ball", "cricket ball", "leather ball", "tennis ball"] if kw in tl)
    if bat_hits >= ball_hits and bat_hits > 0:
        return "bat"
    if ball_hits > 0:
        return "ball"
    return "accessory"


# =============================================================================
#  NEW EXTRACTION HELPERS — previously-empty Flipkart columns
# =============================================================================

def _extract_series(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'\bseries\s*[:\-]\s*([A-Za-z0-9][^\n\r\.]{2,40})', text, re.I)
    if m:
        return m.group(1).strip().title()
    series_patterns = [
        r'\b(reserve edition|players edition|icon|xtreme|ultra|premier|'
        r'signature|legend|platinum|gold edition|sterling|achiever|'
        r'sierra|sunny|sunny tonny|cover drive|proflex|dynamic|'
        r'cannon|hi storm|rp|evader|kookaburra beast|kahuna|'
        r'retro|classic|cobra|super|master|champion)\b',
    ]
    for pat in series_patterns:
        m = re.search(pat, tl, re.I)
        if m:
            return m.group(1).title()
    return None


def _extract_curve(text: str) -> str | None:
    tl = str(text).lower()
    if re.search(r'\b(curved|mid bow|high bow|low bow|pronounced bow|deep bow|slight bow|profile)\b', tl):
        return "Yes"
    if re.search(r'\bstraight\s*(bat|blade|edge)\b', tl):
        return "No"
    return None


def _extract_designed_for(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'designed\s+for\s*[:\-]?\s*([^\n\r\.]{5,60})', tl, re.I)
    if m:
        return m.group(1).strip().capitalize()
    if re.search(r'\bleather\s+ball\b', tl): return "Leather ball cricket"
    if re.search(r'\btennis\s+ball\b', tl):  return "Tennis ball cricket"
    if re.search(r'\brubber\s+ball\b', tl):  return "Rubber ball cricket"
    return None


def _extract_suitable_for(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'suitable\s+for\s*[:\-]?\s*([^\n\r\.]{3,40})', tl, re.I)
    if m:
        return m.group(1).strip().capitalize()
    if re.search(r'\bleather\s+ball\b', tl): return "Leather Ball"
    if re.search(r'\btennis\s+ball\b', tl):  return "Tennis Ball"
    if re.search(r'\brubber\s+ball\b', tl):  return "Rubber Ball"
    return None


def _extract_yes_no(text: str, patterns_yes: list, patterns_no: list = None) -> str | None:
    tl = str(text).lower()
    for pat in patterns_yes:
        if re.search(pat, tl, re.I):
            return "Yes"
    if patterns_no:
        for pat in patterns_no:
            if re.search(pat, tl, re.I):
                return "No"
    return None


def _extract_anti_scuff(text: str) -> str | None:
    return _extract_yes_no(
        text,
        patterns_yes=[r'\banti.?scuff\b', r'\bscuff\s+sheet\b', r'\bprotective\s+sheet\b'],
        patterns_no=[r'\bno\s+anti.?scuff\b', r'\bwithout\s+anti.?scuff\b'],
    )


def _extract_toe_guard(text: str) -> str | None:
    return _extract_yes_no(
        text,
        patterns_yes=[r'\btoe\s+guard\b', r'\btoe\s+protection\b'],
        patterns_no=[r'\bno\s+toe\s+guard\b', r'\bwithout\s+toe\s+guard\b'],
    )


def _extract_handle_type(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'handle\s+type\s*[:\-]\s*(round|oval|semi.?oval)', tl, re.I)
    if m:
        raw = m.group(1).lower()
        return {"round": "Round", "oval": "Oval"}.get(raw, "Semi-Oval")
    if re.search(r'\boval\s+handle\b', tl):  return "Oval"
    if re.search(r'\bround\s+handle\b', tl): return "Round"
    return None


def _extract_handle_grip_type(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'(grip\s+type|handle\s+grip)\s*[:\-]\s*([A-Za-z\s]{3,25})', tl, re.I)
    if m:
        return m.group(2).strip().title()
    for grip in ["chevron", "octopus", "scale", "diamond", "spiral", "inlay"]:
        if grip in tl:
            return grip.title()
    return None


def _extract_handle_material(text: str) -> str | None:
    tl = str(text).lower()
    if re.search(r'\bsarawak\b', tl):   return "Sarawak Cane"
    if re.search(r'\bsingapore\b', tl): return "Singapore Cane"
    m = re.search(r'handle\s+material\s*[:\-]\s*([A-Za-z][^\n\r\.]{2,30})', tl, re.I)
    if m:
        return m.group(1).strip().title()
    if re.search(r'\bcane\b', tl): return "Cane"
    return None


def _extract_other_body_features(text: str) -> str | None:
    tl = str(text).lower()
    features = []
    m = re.search(r'(\d+)\s*mm\s+edges?', tl, re.I)
    if m:
        features.append(f"{m.group(1)}mm edges")
    for bow in ["mid bow", "high bow", "low bow", "pronounced bow", "deep bow", "slight bow"]:
        if bow in tl:
            features.append(bow.title())
            break
    m = re.search(r'(\d+)\s*mm\s+spine', tl, re.I)
    if m:
        features.append(f"{m.group(1)}mm spine")
    if re.search(r'\bconcave\b', tl):       features.append("Concave face")
    if re.search(r'\bdomed\s+spine\b', tl): features.append("Domed spine")
    return "; ".join(features) if features else None


def _extract_dimensions_inch(text: str) -> dict:
    dims = {}
    patterns = {
        "Blade Width (inch)":     r'blade\s+width\s*[:\-]?\s*([\d.]+)\s*(?:inch|in\b|")',
        "Blade Height (inch)":    r'blade\s+(?:height|length)\s*[:\-]?\s*([\d.]+)\s*(?:inch|in\b|")',
        "Handle Length (inch)":   r'handle\s+length\s*[:\-]?\s*([\d.]+)\s*(?:inch|in\b|")',
        "Handle Diameter (inch)": r'handle\s+(?:diameter|width)\s*[:\-]?\s*([\d.]+)\s*(?:inch|in\b|")',
    }
    for field, pat in patterns.items():
        m = re.search(pat, str(text), re.I)
        if m:
            try:
                dims[field] = round(float(m.group(1)), 2)
            except ValueError:
                pass
    return dims


def _cm_to_inch_val(val_str: str) -> float | None:
    try:
        return round(float(val_str) / 2.54, 2)
    except (TypeError, ValueError):
        return None


def _extract_dimensions_inch_from_cm(text: str) -> dict:
    dims = {}
    patterns = {
        "Blade Width (inch)":     r'blade\s+width\s*[:\-]?\s*([\d.]+)\s*cm',
        "Blade Height (inch)":    r'blade\s+(?:height|length)\s*[:\-]?\s*([\d.]+)\s*cm',
        "Handle Length (inch)":   r'handle\s+length\s*[:\-]?\s*([\d.]+)\s*cm',
        "Handle Diameter (inch)": r'handle\s+(?:diameter|width)\s*[:\-]?\s*([\d.]+)\s*cm',
    }
    for field, pat in patterns.items():
        m = re.search(pat, str(text), re.I)
        if m:
            v = _cm_to_inch_val(m.group(1))
            if v:
                dims[field] = v
    return dims


def _extract_warranty(text: str) -> dict:
    result = {}
    tl = str(text).lower()
    m = re.search(r'(?:domestic\s+)?warranty\s*[:\-]\s*(\d+)\s*(year|month|day)s?', tl, re.I)
    if m:
        num  = m.group(1)
        unit = m.group(2).capitalize()
        result["Domestic Warranty"]                  = f"{num} {unit}{'s' if int(num) > 1 else ''}"
        result["Domestic Warranty - Measuring Unit"] = unit
    elif re.search(r'\b(no\s+warranty|not\s+covered|no\s+manufacturer\s+warranty)\b', tl):
        result["Domestic Warranty"]                  = "No Warranty"
        result["Domestic Warranty - Measuring Unit"] = ""
    m2 = re.search(
        r'warranty\s+(?:covers?|applies?\s+to|summary)[:\-\s]+([^\n\r\.]{10,160})', tl, re.I,
    )
    if m2:
        result["Warranty Summary"] = m2.group(1).strip().capitalize()
    elif "Domestic Warranty" in result and result["Domestic Warranty"] != "No Warranty":
        result.setdefault("Warranty Summary", "Warranty against manufacturing defects")
    return result


def _extract_ean(text: str) -> str | None:
    m = re.search(r'\b(\d{13})\b', str(text))
    return m.group(1) if m else None


def _extract_certification(text: str) -> str | None:
    tl = str(text).lower()
    certs = []
    if re.search(r'\bbis\s+certified\b|\bbis\s+approved\b', tl):  certs.append("BIS Certified")
    if re.search(r'\bisi\s+marked\b|\bisi\s+mark\b', tl):          certs.append("ISI Mark")
    if re.search(r'\bbcci\s+approved\b', tl):                       certs.append("BCCI Approved")
    if re.search(r'\bicc\s+approved\b', tl):                        certs.append("ICC Approved")
    if re.search(r'\bce\s+marked\b|\bce\s+certified\b', tl):        certs.append("CE Marked")
    return "; ".join(certs) if certs else None


def _extract_barrel_material(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(r'barr?el\s+material\s*[:\-]\s*([^\n\r\.]{3,40})', tl, re.I)
    if m:
        return m.group(1).strip().title()
    return None


def _extract_end_cap(text: str) -> dict:
    result = {}
    tl = str(text).lower()
    if re.search(r'\bend\s*cap\b', tl):
        result["End Cap"] = "Yes"
        m = re.search(r'end\s*cap\s+material\s*[:\-]\s*([^\n\r\.]{3,30})', tl, re.I)
        if m:
            result["End Cap Material"] = m.group(1).strip().title()
    return result


def _extract_other_features(text: str) -> str | None:
    tl = str(text).lower()
    m = re.search(
        r'(?:other|additional|extra)\s+features?\s*[:\-]\s*([^\n\r\.]{10,200})', tl, re.I,
    )
    return m.group(1).strip().capitalize() if m else None


def _enrich_cricket_fields(detail: dict) -> dict:
    """
    Runs all cricket extraction helpers and fills missing fields in-place.
    Safe to call on any product dict.
    """
    combined_text = " ".join([
        str(detail.get("title", "")),
        str(detail.get("description", "")),
        str(detail.get("Body HTML", "")),
        str(detail.get("SEO Description", "")),
    ])

    product_type = _detect_product_type(combined_text)

    if _is_empty(detail.get("Blade Material", "")):
        blade = _extract_blade_material(detail.get("title", "")) or _extract_blade_material(combined_text)
        if blade:
            detail["Blade Material"] = blade

    if _is_empty(detail.get("Bat Grade", "")):
        grade = _extract_bat_grade(combined_text)
        if grade:
            detail["Bat Grade"] = grade

    if _is_empty(detail.get("Size", "")):
        size = _extract_size(combined_text)
        if size:
            detail["Size"] = size

    if _is_empty(detail.get("Weight Range", "")):
        wt = _extract_weight_range(combined_text) or convert_weight_to_range(
            str(detail.get("weight", "") or detail.get("Weight", ""))
        )
        if wt:
            detail["Weight Range"] = wt

    if _is_empty(detail.get("Sport Type", "")):
        sport = _extract_sport_type(combined_text)
        if not sport and product_type in ("bat", "ball"):
            sport = "Cricket"
        if sport:
            detail["Sport Type"] = sport

    if _is_empty(detail.get("Manufacturer Details", "")):
        mfr = _extract_manufacturer(combined_text)
        if not mfr:
            brand_key = str(detail.get("brand", "") or detail.get("Brand", "")).upper()
            mfr = BRAND_TO_MANUFACTURER.get(brand_key) or BRAND_TO_MANUFACTURER.get(
                brand_key.replace(" TON", "")
            )
        if mfr:
            detail["Manufacturer Details"] = _clean_manufacturer(mfr) if "pvt" in mfr.lower() else mfr

    # Price (INR) — try structured price dict first, then regex over text
    if _is_empty(str(detail.get("MRP (INR)", ""))):
        # Structured dict (from new scraper)
        for pkey in ("selling_price", "price", "mrp_price"):
            src = detail.get(pkey)
            if isinstance(src, dict):
                val      = src.get("value", 0)
                currency = str(src.get("currency", "")).upper()
                if val > 0 and currency in ("INR", ""):
                    detail["MRP (INR)"]                = val
                    detail.setdefault("Your selling price (INR)", val)
                    break
        # Regex fallback
        if _is_empty(str(detail.get("MRP (INR)", ""))):
            price_raw = str(detail.get("price", "") or detail.get("Price", ""))
            inr = _extract_inr_price(price_raw) or _extract_inr_price(combined_text)
            if inr:
                detail["MRP (INR)"] = inr
                detail.setdefault("Your selling price (INR)", inr)

    size = str(detail.get("Size", "")).strip()
    if not _is_empty(size):
        age = derive_age_group(size)
        if age:
            detail["Age Group"]     = age
            detail["Ideal For"]     = derive_ideal_for(age)
            detail["Playing Level"] = derive_playing_level(age)

    images = detail.get("Official Images") or detail.get("images") or []
    if isinstance(images, list) and images:
        detail.setdefault("Main Image URL",      images[0])
        if len(images) > 1: detail.setdefault("Other Image URL 1", images[1])
        if len(images) > 2: detail.setdefault("Other Image URL 2", images[2])
        if len(images) > 3: detail.setdefault("Other Image URL 3", images[3])

    if _is_empty(detail.get("Series", "")):
        v = _extract_series(combined_text)
        if v: detail["Series"] = v

    if _is_empty(detail.get("Curve", "")):
        v = _extract_curve(combined_text)
        if v: detail["Curve"] = v

    if _is_empty(detail.get("Designed For", "")):
        v = _extract_designed_for(combined_text)
        if v: detail["Designed For"] = v

    if _is_empty(detail.get("Suitable For", "")):
        v = _extract_suitable_for(combined_text)
        if v: detail["Suitable For"] = v

    if _is_empty(detail.get("Anti-Scuff Sheet", "")):
        v = _extract_anti_scuff(combined_text)
        if v: detail["Anti-Scuff Sheet"] = v

    if _is_empty(detail.get("Toe Guard", "")):
        v = _extract_toe_guard(combined_text)
        if v: detail["Toe Guard"] = v

    if _is_empty(detail.get("Handle Type", "")):
        v = _extract_handle_type(combined_text)
        if v: detail["Handle Type"] = v

    if _is_empty(detail.get("Handle Grip Type", "")):
        v = _extract_handle_grip_type(combined_text)
        if v: detail["Handle Grip Type"] = v

    if _is_empty(detail.get("Handle Material", "")):
        v = _extract_handle_material(combined_text)
        if v: detail["Handle Material"] = v

    if _is_empty(detail.get("Other Body Features", "")):
        v = _extract_other_body_features(combined_text)
        if v: detail["Other Body Features"] = v

    if _is_empty(detail.get("Certification", "")):
        v = _extract_certification(combined_text)
        if v: detail["Certification"] = v

    if _is_empty(detail.get("EAN/UPC", "")):
        v = _extract_ean(combined_text)
        if v: detail["EAN/UPC"] = v

    if _is_empty(detail.get("Other Features", "")):
        v = _extract_other_features(combined_text)
        if v: detail["Other Features"] = v

    inch_dims = _extract_dimensions_inch(combined_text)
    cm_dims   = _extract_dimensions_inch_from_cm(combined_text)
    for field in ("Blade Width (inch)", "Blade Height (inch)",
                  "Handle Length (inch)", "Handle Diameter (inch)"):
        if _is_empty(detail.get(field, "")):
            val = inch_dims.get(field) or cm_dims.get(field)
            if val:
                detail[field] = val

    if _is_empty(detail.get("Domestic Warranty", "")):
        w = _extract_warranty(combined_text)
        for k, v in w.items():
            if _is_empty(detail.get(k, "")):
                detail[k] = v

    _fill_package_dims(detail)
    return detail


_PKG_DIMS: dict[str, tuple] = {
    "Short Handle": (90, 12, 6, 1.2),
    "Long Handle":  (93, 12, 6, 1.2),
    "Harrow":       (87, 11, 5, 1.0),
    "6":            (84, 11, 5, 1.0),
    "5":            (81, 10, 5, 0.9),
    "4":            (78, 10, 5, 0.85),
    "3":            (75, 10, 5, 0.8),
    "2":            (72, 10, 5, 0.75),
    "1":            (69, 10, 5, 0.7),
    "0":            (66, 10, 5, 0.65),
}
_PKG_DEFAULT = (90, 12, 6, 1.2)


def _fill_package_dims(detail: dict) -> None:
    if all(not _is_empty(detail.get(k, "")) for k in
           ("Length (CM)", "Breadth (CM)", "Height (CM)", "Weight (KG)")):
        return
    size = str(detail.get("Size", "")).strip()
    l, b, h, w = _PKG_DIMS.get(size, _PKG_DEFAULT)
    if _is_empty(detail.get("Weight (KG)", "")):
        wr = str(detail.get("Weight Range", ""))
        m = re.match(r'(\d+)-(\d+)', wr)
        if m:
            mid_g = (int(m.group(1)) + int(m.group(2))) / 2
            w = round(mid_g / 1000, 2)
    if _is_empty(detail.get("Length (CM)",  "")): detail["Length (CM)"]  = str(l)
    if _is_empty(detail.get("Breadth (CM)", "")): detail["Breadth (CM)"] = str(b)
    if _is_empty(detail.get("Height (CM)",  "")): detail["Height (CM)"]  = str(h)
    if _is_empty(detail.get("Weight (KG)",  "")): detail["Weight (KG)"]  = str(w)


# =============================================================================
#  FLIP-4: POST-LLM FLIPKART VALIDATOR
# =============================================================================

def validate_and_fill_flipkart(row: dict, product_type: str = "bat") -> dict:
    for k, v in GLOBAL_DEFAULTS.items():
        if _is_empty(row.get(k, "")):
            row[k] = v
    for k, v in UNIT_DEFAULTS.items():
        if _is_empty(row.get(k, "")):
            row[k] = v

    sport_type = str(row.get("Sport Type", "Cricket")) or "Cricket"
    check_fields = list(BAT_ALLOWED.keys()) if product_type == "bat" else list(BALL_ALLOWED.keys())
    for field in check_fields:
        if not _is_empty(row.get(field, "")):
            row[field] = map_to_allowed_value(field, row[field], product_type, sport_type)

    size = str(row.get("Size", "")).strip()
    if not _is_empty(size):
        age = derive_age_group(size)
        if age:
            row["Age Group"]     = age
            row["Ideal For"]     = derive_ideal_for(age)
            row["Playing Level"] = derive_playing_level(age)

    if _is_empty(row.get("Blade Material", "")):
        blade = (
            _extract_blade_material(row.get("Model Name", ""))
            or _extract_blade_material(row.get("Description", ""))
        )
        if blade:
            row["Blade Material"] = blade

    if _is_empty(row.get("Manufacturer Details", "")):
        brand_key = str(row.get("Brand", "")).upper()
        mfr = BRAND_TO_MANUFACTURER.get(brand_key)
        if mfr:
            row["Manufacturer Details"] = mfr

    raw_wt = str(row.get("Weight Range", "")).strip()
    if not _is_empty(raw_wt):
        converted = convert_weight_to_range(raw_wt)
        if converted:
            row["Weight Range"] = converted

    skip_in_defaults = {"Size", "Age Group", "Ideal For"}
    for k, v in PRODUCT_TYPE_DEFAULTS.get(product_type, {}).items():
        if k in skip_in_defaults:
            continue
        if _is_empty(row.get(k, "")):
            row[k] = v

    if _is_empty(str(row.get("Your selling price (INR)", ""))) and not _is_empty(str(row.get("MRP (INR)", ""))):
        row["Your selling price (INR)"] = row["MRP (INR)"]

    return row


# =============================================================================
#  AREA 1 — NAME CORRECTION
# =============================================================================
MANUAL_NAME_CORRECTIONS: dict[str, str] = {
    "ss toe guard kit":  "SS Toe Guard Kit",
    "ss autograph kit":  "SS Autograph Kit",
    "ss cricket":        "SS Cricket",
    "sg cricket":        "SG Cricket",
    "dsc":               "DSC",
    "ca sports":         "CA Sports",
    "ss ton master":     "SS TON Master",
    "ss matrix":         "SS Matrix",
}

_spell = SpellChecker(language="en")
_spell.word_frequency.load_words([b.lower() for b in brands])

_CRICKET_VOCAB = [
    "kookaburra", "kahuna", "xtreme", "retro", "premier", "willow",
    "adipower", "strikeline", "cricket", "bat", "gloves", "pads",
    "helmet", "spikes", "jersey", "whites", "abdominal", "inners",
    "moonwalkr", "payntr", "masuri", "aero", "shrey", "protos",
    "tyka", "cosco", "spartan", "dsc", "sg", "ton", "sstoncricket",
    "dhoni", "thala", "virat", "kohli", "rohit", "sharma", "sachin",
    "tendulkar", "gayle", "pollard", "bumrah", "jadeja", "ashwin",
    "dhawan", "ganguly", "dravid", "kumble", "harbhajan", "yuvi",
    "yuvraj", "raina", "pandya", "hardik", "surya", "suryakumar",
    "babar", "azam", "stokes", "root", "anderson", "broad", "flintoff",
    "warne", "mcgrath", "ponting", "lara", "kallis", "finch", "warner",
    "smith", "maxwell", "stoinis",
    "thala", "captain", "finisher", "gladiator", "slasher", "stunner",
    "destroyer", "smacker", "striker", "blaster", "titan", "magnum",
    "natwest", "ipl", "odi", "t20", "ranjji", "duleep",
]
_spell.word_frequency.load_words(_CRICKET_VOCAB)

ALL_CAPS_BRANDS = {"SS", "SG", "DSC", "CA", "MRF", "TON", "GM", "ASICS", "TYKA"}


def _apply_pascal_case(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    return " ".join(
        w.upper() if w.upper() in ALL_CAPS_BRANDS else w.capitalize()
        for w in name.split()
    )


def _fix_extra_spaces(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


def _spellcheck_words(name: str) -> str:
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
    if len(official_clean.split()) < 3 and len(input_name.split()) > len(official_clean.split()):
        print(f"  ⚠️  Official title too short — keeping input: '{input_name}'")
        return input_name
    if score >= 85:
        return input_name
    corrected = _apply_pascal_case(official_clean)
    print(f"  ✏️  Name replaced with official: '{input_name}' → '{corrected}'")
    return corrected


def expand_willow_abbreviations(name: str) -> str:
    if not name:
        return ""
    expanded = str(name)
    expanded = re.sub(r"\b(e\.w|e\.w\.|ew)\b",    "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(k\.w|k\.w\.|kw)\b",    "Kashmir Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(cr\.?)(?!\w)",          "Cricket",        expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(english\s+willow)\b",   "English Willow", expanded, flags=re.IGNORECASE)
    expanded = re.sub(r"\b(kashmir\s+willow)\b",   "Kashmir Willow", expanded, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", expanded).strip()


def correct_product_name(raw_name: str) -> str:
    if not raw_name or not raw_name.strip():
        return raw_name
    lookup_key = re.sub(r"\s+", " ", raw_name.strip().lower())
    if lookup_key in MANUAL_NAME_CORRECTIONS:
        corrected = MANUAL_NAME_CORRECTIONS[lookup_key]
        print(f"  📖 Manual override: '{raw_name}' → '{corrected}'")
        return corrected
    name = _fix_extra_spaces(raw_name)
    name = expand_willow_abbreviations(name)
    name_spell_fixed = _spellcheck_words(name.lower())
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


async def fetch_page_in_thread(url: str, timeout_ms: int = 40_000) -> str:
    """
    Improved Playwright fetch:
    - Blocks images/fonts to speed up loading
    - Waits up to 8s for a known price selector to appear
    - Scrolls page to trigger lazy-loaded content
    """
    loop   = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _worker():
        async def _run():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    java_script_enabled=True,
                )
                # Block heavy assets — prices are in HTML/JSON, not images
                await context.route(
                    "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,otf}",
                    lambda route, _: route.abort(),
                )
                page = await context.new_page()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception:
                    try:
                        await page.goto(url, timeout=timeout_ms)
                    except Exception:
                        pass

                # Wait up to 8s for any price selector to appear (JS-rendered prices)
                price_wait_selector = (
                    "[itemprop='price'], [data-price], .a-price-whole, ._30jeq3, "
                    ".price__regular, .woocommerce-Price-amount, .product__price, "
                    ".selling-price, .pdp-price, #priceblock_ourprice"
                )
                try:
                    await page.wait_for_selector(
                        price_wait_selector, timeout=8_000, state="visible"
                    )
                except Exception:
                    await page.wait_for_timeout(3_000)

                # Scroll to trigger lazy-loaded content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1_500)

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
    BODY_HTML_SELECTORS = [
        {"class": "product-description"},
        {"class": "product__description"},
        {"class": "product-single__description"},
        {"id":    "product-description"},
        {"id":    "tab-description"},
        {"class": "woocommerce-product-details__short-description"},
        {"class": "woocommerce-Tabs-panel--description"},
        {"class": "description"},
        {"class": "product-details"},
        {"class": "product-info__description"},
        {"class": "pdp-description"},
        {"class": "product__content"},
        {"itemprop": "description"},
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
    images = []

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

    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content", "").startswith("http"):
        img_url = og_img["content"]
        if img_url not in images:
            images.insert(0, img_url)

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

    for img_tag in soup.find_all("img"):
        src = img_tag.get("src") or img_tag.get("data-src", "")
        if not src:
            continue
        src = urljoin(base_url, src)
        if not src.startswith("http"):
            continue
        if re.search(r"product|item|goods|cricket", src, re.I) and src not in images:
            images.append(src)

    images = [
        img for img in images
        if not re.search(r"logo|icon|banner|pixel|tracker|1x1|placeholder|spacer", img, re.I)
        and not img.endswith(".svg")
    ]

    if product_name:
        _stop = {"the", "and", "for", "with", "set", "kit", "ss", "sg", "by", "of", "in"}
        name_keywords = [
            w.lower() for w in re.split(r"[\s\-_]+", product_name)
            if len(w) > 2 and w.lower() not in _stop
        ]
        if name_keywords:
            relevant = [img for img in images if any(kw in img.lower() for kw in name_keywords)]
            fallback = [img for img in images if img not in relevant]
            images = relevant + fallback
            print(f"  🔎 Image relevance filter: {len(relevant)} relevant, {len(fallback)} fallback")

    print(f"  🖼️  Official images found: {len(images)}")
    return images


# =============================================================================
#  AREA C — MAIN PAGE EXTRACTION  (price extraction integrated)
# =============================================================================

async def extract_product_info(url: str, is_official: bool = False, product_name: str = ""):
    """
    Main page scraper — now always extracts price using all 5 strategies.
    Stores price as data["price"], data["mrp_price"], data["selling_price"].
    """
    try:
        html = await fetch_page_in_thread(url)
        if not html:
            return {}

        base_url = get_base_url(html, url)
        data     = extruct.extract(html, base_url=base_url, syntaxes=["json-ld", "microdata"])
        soup     = BeautifulSoup(html, "html.parser")

        meta_title    = soup.find("meta", property="og:title")
        data["SEO Title"] = meta_title["content"] if meta_title else None

        # ── PRICE EXTRACTION — always runs regardless of is_official ──────────
        page_text              = soup.get_text(separator=" ", strip=True)
        prices                 = _extract_mrp_and_selling_price(soup, page_text)
        data["selling_price"]  = prices["selling_price"] or {}
        data["mrp_price"]      = prices["mrp"] or prices["selling_price"] or {}
        data["price"]          = prices["selling_price"] or prices["mrp"] or {}

        if data["price"]:
            print(f"  💰 Page price extracted     : {data['price']}")
        else:
            print(f"  ⚠️  Price not found on       : {url}")

        if is_official:
            body_html     = extract_official_body_html(soup)
            seo_desc      = extract_seo_description(soup)
            official_imgs = extract_official_images(soup, base_url, product_name=product_name)

            data["Body HTML"]       = body_html
            data["SEO Description"] = seo_desc
            data["Official Images"] = official_imgs

            page_title_tag = soup.find("title")
            data["Official Site Title"] = (
                page_title_tag.get_text(strip=True) if page_title_tag else ""
            )
            official_meta_desc = soup.find("meta", attrs={"name": "description"})
            data["Official Site Description"] = (
                official_meta_desc["content"].strip()
                if official_meta_desc and official_meta_desc.get("content") else ""
            )

            print(f"  📄 Body HTML extracted      : {len(body_html)} chars")
            print(f"  🔍 SEO Description          : {seo_desc[:80]}{'...' if len(seo_desc) > 80 else ''}")
            print(f"  🏷️  Official Site Title      : {data['Official Site Title']}")
            print(f"  📝 Official Site Description : {data['Official Site Description'][:80]}")

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
    url_lower    = url.lower()
    snippet_text = snippet_text.lower()

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
        "/in/": "India", "/hi-in/": "India", "/en-in/": "India",
        "/en-us/": "United States", "/en-gb/": "United Kingdom",
        "/uk/": "United Kingdom", "/ca/": "Canada",
        "/de/": "Germany", "/fr/": "France",
    }
    for key, region in path_region_patterns.items():
        if key in url_lower:
            return region

    if "₹" in snippet_text:  return "India"
    if "$" in snippet_text:  return "United States"
    if "£" in snippet_text:  return "United Kingdom"
    if "€" in snippet_text:  return "Europe"

    phone_patterns = {
        r"\+91": "India",
        r"\+1(?!\d{1,2})": "United States",
        r"\+44": "United Kingdom",
        r"\+61": "Australia",
    }
    for pattern, region in phone_patterns.items():
        if re.search(pattern, snippet_text):
            return region

    extracted = tldextract.extract(url)
    tld = extracted.suffix
    tld_mapping = {
        "in": "India", "co.in": "India", "co.uk": "United Kingdom",
        "uk": "United Kingdom", "ca": "Canada", "com.au": "Australia",
        "sg": "Singapore", "ae": "United Arab Emirates",
        "de": "Germany", "fr": "France",
    }
    return tld_mapping.get(tld, "Unknown / Global")


# =============================================================================
#  OFFICIAL-SITE HELPERS
# =============================================================================

def _normalize_brand_search(text: str) -> str:
    if not text:
        return ""
    normalized = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def detect_brand_from_name(product_name: str) -> tuple[str | None, str | None]:
    normalized_name = _normalize_brand_search(product_name)
    for brand_name in brands:
        normalized_brand = _normalize_brand_search(brand_name)
        pattern = rf"\b{re.escape(normalized_brand)}\b"
        if re.search(pattern, normalized_name):
            return brand_name, official_sites[brand_name]
    match = process.extractOne(
        normalized_name, brands, scorer=fuzz.partial_ratio, score_cutoff=80
    )
    if match:
        brand_name = match[0]
        return brand_name, official_sites[brand_name]
    return None, None


def get_official_domain(official_url: str) -> str:
    parsed   = urlparse(official_url)
    hostname = parsed.netloc.lower()
    parts    = hostname.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_official_url(link: str, official_domain: str) -> bool:
    return official_domain in link.lower()


def is_data_complete(product: dict) -> bool:
    if not isinstance(product, dict):
        return False
    has_title       = bool(product.get("title", "").strip())
    has_description = bool(product.get("description", "").strip())
    has_images      = bool(product.get("images"))
    variants        = product.get("variants", [])
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
    return re.sub(r"\s+", " ", text).strip()


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
    """
    AMZN-1: Build a complete Amazon IN bulk-upload row.
    ensure_price_in_detail() runs first to guarantee price is populated.
    """
    # Guarantee price is populated before enrichment
    product_detail = ensure_price_in_detail(product_detail)
    # Pre-enrich with cricket helpers
    product_detail = _enrich_cricket_fields(product_detail)

    title = str(
        product_detail.get("Title") or product_detail.get("title")
        or product_detail.get("item-name") or product_detail.get("item_name") or ""
    ).strip()

    brand = str(
        product_detail.get("brand", "") or product_detail.get("Brand", "")
        or product_detail.get("brand-name", "") or ""
    ).strip()

    category = str(
        product_detail.get("category", "") or product_detail.get("Category", "")
        or product_detail.get("feed_product_type", "") or ""
    ).strip()

    item_type = str(
        product_detail.get("type", "") or product_detail.get("Type", "")
        or product_detail.get("item-type", "") or category
    ).strip()

    description = str(
        product_detail.get("description", "") or product_detail.get("Body HTML", "")
        or product_detail.get("Body (HTML)", "") or product_detail.get("body_html", "")
    )
    description_plain = clean_text(description)

    tags = product_detail.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()] if tags.strip() else []
    if not tags:
        tags = product_detail.get("Tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]

    # Price — MRP (INR) is now guaranteed by ensure_price_in_detail above
    price_value = str(product_detail.get("MRP (INR)", "")).strip()
    if not price_value or price_value in ("0", "0.0"):
        price_info = product_detail.get("price", {}) or product_detail.get("Price", {})
        if isinstance(price_info, dict):
            price_value = str(price_info.get("value", ""))
        else:
            price_value = str(price_info or "")

    images = product_detail.get("Official Images") or product_detail.get("images", []) or []
    if isinstance(images, str):  images = [images]
    if isinstance(images, dict): images = [images]
    images = [str(img).strip() for img in images if str(img).strip()]

    blade_material = str(product_detail.get("Blade Material", "")).strip()
    weight_range   = str(product_detail.get("Weight Range", "")).strip()
    sport_type     = str(product_detail.get("Sport Type", "Cricket")).strip() or "Cricket"
    size_value     = str(product_detail.get("Size", "")).strip() or extract_first_variant_size(
        product_detail.get("variants", [])
    )
    playing_level  = str(product_detail.get("Playing Level", "")).strip()
    handle_grip    = str(product_detail.get("Handle Grip Type", "")).strip()
    handle_mat     = str(product_detail.get("Handle Material", "")).strip()
    manufacturer   = str(product_detail.get("Manufacturer Details", "")).strip()
    if not manufacturer:
        manufacturer = BRAND_TO_MANUFACTURER.get(brand.upper(), brand)

    sku     = str(product_detail.get("official_sku", "") or product_detail.get("sku", "") or "").strip()
    country = str(product_detail.get("country_of_origin", "India") or "India").strip()

    color_value = str(product_detail.get("Color", "")).strip()
    if not color_value:
        allowed_colors = [c.lower() for c in BAT_ALLOWED.get("Color", [])]
        for tag in tags:
            if tag.lower() in allowed_colors:
                color_value = tag.capitalize()
                break

    material = blade_material or ""
    if not material:
        for tag in tags:
            if tag.lower() in ["leather", "wood", "carbon", "polyester", "nylon", "rubber", "foam"]:
                material = tag.capitalize()
                break

    warranty_text = ""
    w_data = product_detail.get("Warranty Summary", "") or product_detail.get("Domestic Warranty", "")
    if w_data and str(w_data).strip() not in ("", "No Warranty"):
        warranty_text = str(w_data).strip()

    pkg_length  = str(product_detail.get("Length (CM)", "")).strip()
    pkg_breadth = str(product_detail.get("Breadth (CM)", "")).strip()
    pkg_height  = str(product_detail.get("Height (CM)", "")).strip()
    pkg_weight  = str(product_detail.get("Weight (KG)", "")).strip()

    bullet_points = extract_bullet_points(description_plain)

    row = {header: "" for header in AMAZON_HEADERS}

    row["SKU"]                = sku
    row["Product Id"]         = sku
    row["Product Id Type"]    = "ASIN" if sku else ""
    row["Item Name"]          = title
    row["Brand Name"]         = brand
    row["Model Name"]         = str(product_detail.get("model_name", "") or title).strip()
    row["Model Number"]       = sku
    row["Part Number"]        = sku or "NA"
    row["Manufacturer"]       = manufacturer or brand

    row["Item Type Name"]     = item_type
    row["Style"]              = item_type or title
    row["Department Name"]    = "Sports"

    row["Product Description"]= description_plain
    row["Bullet Point"]       = bullet_points[0] if bullet_points else ""
    row["_bullet_points"]     = bullet_points
    row["Generic Keywords"]   = ", ".join([t.lower() for t in tags if t])
    row["Special Features"]   = "; ".join(bullet_points[1:4]) if len(bullet_points) > 1 else ""

    row["Sport Type"]               = sport_type
    row["Sport Bat Willow Type"]    = blade_material
    row["Bat Construction"]         = "Hand Crafted" if blade_material == "English Willow" else "Machine Made"
    row["Skill Level"]              = playing_level or (
        "Advanced" if size_value in ("Short Handle", "Long Handle", "Harrow") else "Intermediate"
    )
    row["Grip Type"]                = handle_grip
    row["Handle Material"]          = handle_mat
    row["Recommended Uses For Product"] = "Cricket"

    row["Material"]           = material
    row["Color"]              = color_value
    row["Size"]               = size_value
    row["Item Weight"]        = weight_range
    row["Item Weight Unit"]   = "grams" if weight_range else ""

    row["Item Package Length"]  = pkg_length
    row["Package Length Unit"]  = "centimeters" if pkg_length else ""
    row["Item Package Width"]   = pkg_breadth
    row["Package Width Unit"]   = "centimeters" if pkg_breadth else ""
    row["Item Package Height"]  = pkg_height
    row["Package Height Unit"]  = "centimeters" if pkg_height else ""
    row["Package Weight"]       = pkg_weight
    row["Package Weight Unit"]  = "kilograms" if pkg_weight else ""

    row["Main Image URL"]       = images[0] if images else ""
    row["Main Image Location"]  = images[0] if images else ""
    row["Other Image URL"]      = images[1] if len(images) > 1 else ""
    row["Other Image Location"] = images[1] if len(images) > 1 else ""
    row["_extra_images"]        = images

    row["Your Price INR (Sell on Amazon, IN)"]       = price_value
    row["Maximum Retail Price (Sell on Amazon, IN)"] = price_value

    row["Quantity (IN)"]                 = "1"
    row["Fulfillment Channel Code (IN)"] = "DEFAULT"
    row["Handling Time (IN)"]            = "3"
    row["Item Condition"]                = "New"

    row["Country of Origin"]               = country
    row["Manufacturer Contact Information"]= manufacturer or brand
    row["Importer Contact Information"]    = manufacturer or brand
    row["Packer Contact Information"]      = "Twenty2Yards The Milange Jakat Naka Virar West"
    row["Warranty Description"]            = warranty_text

    return row


# =============================================================================
#  FLIP-3: build_flipkart_row
# =============================================================================

def build_flipkart_row(product_detail: dict) -> dict:
    # Guarantee price is populated before enrichment
    product_detail = ensure_price_in_detail(product_detail)
    # Pre-enrich with cricket helpers
    product_detail = _enrich_cricket_fields(product_detail)

    title = str(
        product_detail.get("Title") or product_detail.get("title")
        or product_detail.get("Model Name") or ""
    ).strip()
    brand = str(
        product_detail.get("brand", "") or product_detail.get("Brand", "")
        or product_detail.get("Manufacturer", "") or ""
    ).strip()
    description = str(
        product_detail.get("description", "") or product_detail.get("Body HTML", "")
        or product_detail.get("Body (HTML)", "") or ""
    )
    description_plain = clean_text(description)[:2000]

    tags = product_detail.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r",|;|\n|\r", tags) if t.strip()]
    search_keywords = ", ".join(tags[:10]) if tags else ""

    # Price — ensure_price_in_detail already ran above
    mrp = str(product_detail.get("MRP (INR)", "")).strip()
    if not mrp or mrp in ("0", "0.0"):
        price_info = product_detail.get("price", {}) or product_detail.get("Price", {})
        if isinstance(price_info, dict):
            raw_val  = price_info.get("value", "")
            currency = str(price_info.get("currency", "")).upper()
            if raw_val and currency in ("", "INR"):
                mrp = str(raw_val)
        elif price_info:
            mrp = str(price_info)

    selling_price = str(product_detail.get("Your selling price (INR)", "")).strip() or mrp

    images = product_detail.get("Official Images") or product_detail.get("images", []) or []
    if isinstance(images, str):  images = [images]
    if isinstance(images, dict): images = [images]
    images = [str(img).strip() for img in images if str(img).strip()]

    weight_range = str(product_detail.get("Weight Range", "")).strip()

    sku = str(product_detail.get("official_sku", "") or product_detail.get("sku", "") or "").strip()
    if sku:
        sku = re.sub(r"[^a-zA-Z0-9\-_]", "", sku)

    variants     = product_detail.get("variants", []) or []
    size         = str(product_detail.get("Size", "")).strip() or extract_first_variant_size(variants)
    blade_material = str(product_detail.get("Blade Material", "")).strip()
    bat_grade      = str(product_detail.get("Bat Grade", "")).strip()
    sport_type     = str(product_detail.get("Sport Type", "Cricket")).strip() or "Cricket"
    color          = str(product_detail.get("Color", "")).strip()
    age_group      = str(product_detail.get("Age Group", "")).strip()
    ideal_for      = str(product_detail.get("Ideal For", "")).strip()
    playing_level  = str(product_detail.get("Playing Level", "")).strip()
    manufacturer   = str(product_detail.get("Manufacturer Details", "")).strip()

    if size and not age_group:
        age_group     = derive_age_group(size)
        ideal_for     = derive_ideal_for(age_group)
        playing_level = derive_playing_level(age_group)

    bullet_points = extract_bullet_points(description_plain, max_points=5)
    key_features  = "; ".join(bullet_points[:3]) if bullet_points else ""

    product_type = _detect_product_type(f"{title} {description_plain} {blade_material}")

    if blade_material:
        blade_material = map_to_allowed_value("Blade Material", blade_material, product_type, sport_type)
    if color:
        color = map_to_allowed_value("Color", color, product_type, sport_type)
    if size:
        size = map_to_allowed_value("Size", size, product_type, sport_type)
    if ideal_for:
        ideal_for = map_to_allowed_value("Ideal For", ideal_for, product_type, sport_type)
    if bat_grade and product_type == "bat":
        bat_grade = map_to_allowed_value("Bat Grade", bat_grade, product_type, sport_type)
    if playing_level:
        playing_level = map_to_allowed_value("Playing Level", playing_level, product_type, sport_type)

    row = {header: "" for header in FLIPKART_HEADERS}

    def _pd(key, fallback=""):
        v = product_detail.get(key, fallback) or fallback
        return "" if _is_empty(str(v)) else str(v)

    row.update({
        "Seller SKU ID":                      sku,
        "MRP (INR)":                          mrp,
        "Your selling price (INR)":           selling_price or mrp,
        "Brand":                              brand,
        "Model Name":                         str(product_detail.get("model_name", "") or title),
        "Description":                        description_plain,
        "Search Keywords":                    search_keywords,
        "Key Features":                       key_features,
        "Size":                               size,
        "Age Group":                          age_group,
        "Ideal For":                          ideal_for,
        "Playing Level":                      playing_level,
        "Color":                              color,
        "Blade Material":                     blade_material,
        "Bat Grade":                          bat_grade,
        "Sport Type":                         sport_type,
        "Weight Range":                       weight_range,
        "Manufacturer Details":               manufacturer,
        "Country Of Origin":                  str(product_detail.get("country_of_origin", "India") or "India"),
        "Cover Included":                     str(product_detail.get("Cover Included", "Yes") or "Yes"),
        "Main Image URL":                     images[0] if images else "",
        "Other Image URL 1":                  images[1] if len(images) > 1 else "",
        "Other Image URL 2":                  images[2] if len(images) > 2 else "",
        "Other Image URL 3":                  images[3] if len(images) > 3 else "",
        "Other Image URL 4":                  images[4] if len(images) > 4 else "",
        "Listing Status":                     "Active",
        "Pack of":                            str(product_detail.get("pack_size", "1") or "1"),
        "Items Included":                     _pd("items_included"),
        "Series":                             _pd("Series"),
        "Curve":                              _pd("Curve"),
        "Designed For":                       _pd("Designed For"),
        "Suitable For":                       _pd("Suitable For"),
        "Anti-Scuff Sheet":                   _pd("Anti-Scuff Sheet"),
        "Toe Guard":                          _pd("Toe Guard"),
        "Certification":                      _pd("Certification"),
        "Handle Type":                        _pd("Handle Type"),
        "Handle Grip Type":                   _pd("Handle Grip Type"),
        "Handle Material":                    _pd("Handle Material"),
        "Other Body Features":                _pd("Other Body Features"),
        "Blade Width (inch)":                 _pd("Blade Width (inch)"),
        "Blade Height (inch)":                _pd("Blade Height (inch)"),
        "Handle Length (inch)":               _pd("Handle Length (inch)"),
        "Handle Diameter (inch)":             _pd("Handle Diameter (inch)"),
        "Other Features":                     _pd("Other Features"),
        "EAN/UPC":                            _pd("EAN/UPC"),
        "Domestic Warranty":                  _pd("Domestic Warranty") or "No Warranty",
        "Domestic Warranty - Measuring Unit": _pd("Domestic Warranty - Measuring Unit"),
        "Warranty Summary":                   _pd("Warranty Summary"),
        "Length (CM)":                        _pd("Length (CM)"),
        "Breadth (CM)":                       _pd("Breadth (CM)"),
        "Height (CM)":                        _pd("Height (CM)"),
        "Weight (KG)":                        _pd("Weight (KG)"),
    })

    for k, v in GLOBAL_DEFAULTS.items():
        if k in row and _is_empty(row.get(k, "")):
            row[k] = v
    for k, v in UNIT_DEFAULTS.items():
        if k in row and _is_empty(row.get(k, "")):
            row[k] = v

    skip_in_defaults = {"Size", "Age Group", "Ideal For"}
    for k, v in PRODUCT_TYPE_DEFAULTS.get(product_type, {}).items():
        if k in skip_in_defaults:
            continue
        if k in row and _is_empty(row.get(k, "")):
            row[k] = v

    return row


# =============================================================================
#  FORMAT PRODUCTS
# =============================================================================

async def format_products(products, format_type, groq_api_key=None, serper_api_key=None):
    local_llm, local_prompt = _get_llm_prompt(groq_api_key)

    # -------------------------------------------------------------------------
    if format_type == "shopify":
    # -------------------------------------------------------------------------
        formatted = []
        manufacturer_details = {}
        for product_detail in products:
            product_detail = _enrich_cricket_fields(product_detail)

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
                CRICKET FIELD HINTS (pre-extracted — use these values directly)
                --------------------------------------------------
                - Blade Material (if present): {product_detail.get("Blade Material", "")}
                - Sport Type: {product_detail.get("Sport Type", "Cricket")}
                - Size: {product_detail.get("Size", "")}
                - Bat Grade: {product_detail.get("Bat Grade", "")}
                - Weight Range: {product_detail.get("Weight Range", "")}
                - Color: {product_detail.get("Color", "")}

                --------------------------------------------------
                VARIANT CREATION RULES
                --------------------------------------------------
                - Variants may ONLY be created using: Size, Color, Material.
                - DO NOT create variants from: price, SKU, inventory, images.
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
                - Clean option values only: "Harrow Size" → "Harrow", "Red Color" → "Red"

                --------------------------------------------------
                TITLE & HANDLE RULES
                --------------------------------------------------
                - Title MUST be Proper case, Human-readable, Brand + Product Name, NO hyphens.
                - Handle MUST be generated FROM Title: lowercase, spaces → hyphens.

                --------------------------------------------------
                BODY HTML / PRODUCT DESCRIPTION RULES (MANDATORY)
                --------------------------------------------------
                - Use "Body HTML" in the input as the starting point.
                - Output must use only: <p>, <ul>, <li>, <strong>, <h2>, <h3>.
                - No newline characters (\\n) inside the HTML string.
                SPELLING AND GRAMMAR: Fix "Color may very" → "Color may vary" etc.
                REMOVE: Manufacturer address, shipping/return policy, website navigation.
                KEEP: Product features, kit contents, materials, size/weight, performance.

                --------------------------------------------------
                IMAGE RULES
                --------------------------------------------------
                - Use ONLY unique image URLs.
                - If only one unique URL → output exactly ONE row with Image Position = 1.
                - Image Src MUST be a valid absolute URL starting with https://.
                - Image Alt Text: "[Product Title] - [Brand Name]"

                --------------------------------------------------
                SEO RULES
                --------------------------------------------------
                - SEO Title: Copy product Title exactly.
                - SEO Description: ONE benefit-driven sentence, max 160 chars.
                - Tags: comma-separated, lowercase, relevant cricket keywords only.

                --------------------------------------------------
                SHOPIFY HEADERS: {", ".join(SHOPIFY_HEADERS)}

                --------------------------------------------------
                ADDITIONAL REQUIRED FIELDS:
                1. "Official Site Title": {product_detail.get("Official Site Title", "")}
                2. "Official Site Description": {product_detail.get("Official Site Description", "")}

                --------------------------------------------------
                Input product data: {product_detail}
            """

            sys_prompt = """
                You are an expert Shopify product data builder and eCommerce SEO specialist.
                Return PURE JSON only — begin with "[" end with "]".
                Do NOT include explanations, text, labels, or markdown.
                "Body (HTML)" must not contain \\n but must preserve valid HTML tags.
                The final output MUST parse successfully using json.loads().
            """

            try:
                extractor_response = await call_llm(local_llm, local_prompt, sys_prompt, user_prompt)
                raw_json = extractor_response.content.strip()
                raw_json = sanitize_json_online_llm(raw_json)
                try:
                    extracted = json.loads(raw_json)
                except Exception:
                    try:
                        cleaned = sanitize_json_online_llm(raw_json)
                        extracted = json.loads(cleaned)
                    except Exception:
                        try:
                            cleaned = extract_first_json(raw_json)
                            extracted = json.loads(cleaned)
                        except Exception as json_err:
                            print(f"⚠️ JSON parse failed, skipping product: {json_err}")
                            extracted = []

                if isinstance(extracted, dict):  extracted = [extracted]
                elif isinstance(extracted, str):
                    try: extracted = json.loads(extracted)
                    except: extracted = []

                for item in extracted:
                    if not isinstance(item, dict):
                        continue
                    for key, value in list(item.items()):
                        if isinstance(value, list):
                            item[key] = ", ".join(map(str, value))

                    clean_item = {}
                    item_name  = item.get("Title", "").strip()
                    brand_name = ""

                    for k in SHOPIFY_HEADERS:
                        value = item.get(k, "")
                        if k == "Vendor":
                            if not value or str(value).strip() == "":
                                value = product_detail.get("brand", "") or item.get("brand", "")
                            vendor = str(value).lower()
                            MANUFACTURER_DIR = None
                            if "mrf"        in vendor: MANUFACTURER_DIR, brand_name = os.path.join("manufacturer", "mrf.xlsx"), "mrf"
                            elif "moonwalkr" in vendor: MANUFACTURER_DIR, brand_name = os.path.join("manufacturer", "moonwalkr.xlsx"), "moonwalkr"
                            elif "sg"       in vendor: MANUFACTURER_DIR, brand_name = os.path.join("manufacturer", "sg.xlsx"), "sg"

                            if brand_name and brand_name not in manufacturer_details:
                                if MANUFACTURER_DIR and os.path.exists(MANUFACTURER_DIR):
                                    df        = pd.read_excel(MANUFACTURER_DIR)
                                    price_map = {}
                                    for row in df.to_dict(orient="records"):
                                        subcat = row.get("Sub Catergory")
                                        usd    = row.get("Retailer Price in USD")
                                        if pd.notna(subcat) and pd.notna(usd):
                                            price_map[str(subcat).lower()] = usd
                                    manufacturer_details[brand_name] = price_map

                        clean_item[k] = str(value)

                    if not clean_item.get("Vendor") or clean_item["Vendor"] in ("", "None"):
                        clean_item["Vendor"] = product_detail.get("brand", "") or item.get("brand", "")
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
                    if not clean_item.get("Image Alt Text") or clean_item["Image Alt Text"] in ("", "None"):
                        clean_item["Image Alt Text"] = f"{clean_item.get('Title', '')} - {clean_item.get('Vendor', '')}".strip(" -")

                    for blank_col in [
                        "Option1 Name", "Option1 Value",
                        "Option2 Name", "Option2 Value",
                        "Option3 Name", "Option3 Value",
                        "Variant SKU", "Variant Grams",
                    ]:
                        clean_item[blank_col] = ""

                    clean_item["Official Site Title"]       = str(product_detail.get("Official Site Title", "") or item.get("Official Site Title", ""))
                    clean_item["Official Site Description"] = str(product_detail.get("Official Site Description", "") or item.get("Official Site Description", ""))
                    formatted.append(clean_item)

            except Exception as e:
                print(f"⚠️ Error extracting: {e}")
        return formatted

    # -------------------------------------------------------------------------
    elif format_type == "amazon":
    # -------------------------------------------------------------------------
        formatted = []
        for product_detail in products:
            try:
                product_name = product_detail.get("title") or product_detail.get("Title") or ""
                enriched_detail = await fetch_missing_data_from_official(
                    product_detail, product_name, region="India",
                    groq_api_key=groq_api_key, serper_api_key=serper_api_key,
                )
                amazon_row = build_amazon_row(enriched_detail)

                if _is_empty(amazon_row.get("sport-type", "")):
                    amazon_row["sport-type"] = "Cricket"
                if _is_empty(amazon_row.get("country-of-origin", "")):
                    amazon_row["country-of-origin"] = "India"
                if _is_empty(amazon_row.get("condition-type", "")):
                    amazon_row["condition-type"] = "New"

                formatted.append(amazon_row)
            except Exception as e:
                print(f"⚠️ Error building Amazon row: {e}")
        return formatted

    # -------------------------------------------------------------------------
    elif format_type == "flipkart":
    # -------------------------------------------------------------------------
        formatted = []
        for product_detail in products:
            try:
                product_name = product_detail.get("title") or product_detail.get("Title") or ""

                if product_detail.pop("_skip_online_enrichment", False):
                    enriched_detail = product_detail
                else:
                    enriched_detail = await fetch_missing_data_from_official(
                        product_detail, product_name, region="India",
                        groq_api_key=groq_api_key, serper_api_key=serper_api_key,
                    )

                flipkart_row = build_flipkart_row(enriched_detail)

                if local_llm:
                    product_type = _detect_product_type(
                        f"{product_name} {enriched_detail.get('description', '')} {flipkart_row.get('Blade Material', '')}"
                    )
                    mandatory_hint = "\n".join(
                        f"- {f}" for f in MANDATORY_FIELDS.get(product_type, [])
                        if _is_empty(flipkart_row.get(f, ""))
                    )

                    user_prompt = f"""
                       You are an expert product data builder for Flipkart bulk uploads,
                       specializing in Cricket Bats and Sports equipment.

                       Your task is to extract and format details from the product data into
                       a JSON object matching the Flipkart Cricket {product_type.capitalize()} category.

                       Strict output rule:
                       - Return pure JSON only.
                       - The output must begin with {{ and end with }}.
                       - Do not include explanations, labels, markdown, or commentary.
                       - Use empty strings ("") for missing text values.

                       --------------------------------------------------
                       PRE-EXTRACTED VALUES (use these directly — do NOT change them):
                       --------------------------------------------------
                       - Model Name:        {flipkart_row.get("Model Name", "")}
                       - Brand:             {flipkart_row.get("Brand", "")}
                       - Blade Material:    {flipkart_row.get("Blade Material", "")}
                       - Size:              {flipkart_row.get("Size", "")}
                       - Age Group:         {flipkart_row.get("Age Group", "")}
                       - Ideal For:         {flipkart_row.get("Ideal For", "")}
                       - Playing Level:     {flipkart_row.get("Playing Level", "")}
                       - Bat Grade:         {flipkart_row.get("Bat Grade", "")}
                       - Weight Range:      {flipkart_row.get("Weight Range", "")}
                       - Color:             {flipkart_row.get("Color", "")}
                       - Sport Type:        {flipkart_row.get("Sport Type", "Cricket")}
                       - MRP (INR):         {flipkart_row.get("MRP (INR)", "")}
                       - Cover Included:    {flipkart_row.get("Cover Included", "")}
                       - Main Image URL:    {flipkart_row.get("Main Image URL", "")}
                       - Manufacturer:      {flipkart_row.get("Manufacturer Details", "")}
                       - Series:            {flipkart_row.get("Series", "")}
                       - Curve:             {flipkart_row.get("Curve", "")}
                       - Designed For:      {flipkart_row.get("Designed For", "")}
                       - Suitable For:      {flipkart_row.get("Suitable For", "")}
                       - Anti-Scuff Sheet:  {flipkart_row.get("Anti-Scuff Sheet", "")}
                       - Toe Guard:         {flipkart_row.get("Toe Guard", "")}
                       - Handle Type:       {flipkart_row.get("Handle Type", "")}
                       - Handle Grip Type:  {flipkart_row.get("Handle Grip Type", "")}
                       - Handle Material:   {flipkart_row.get("Handle Material", "")}
                       - Other Body Features: {flipkart_row.get("Other Body Features", "")}
                       - Domestic Warranty: {flipkart_row.get("Domestic Warranty", "")}
                       - Warranty Summary:  {flipkart_row.get("Warranty Summary", "")}
                       - EAN/UPC:           {flipkart_row.get("EAN/UPC", "")}

                       --------------------------------------------------
                       STILL MISSING MANDATORY FIELDS (fill these):
                       --------------------------------------------------
                       {mandatory_hint if mandatory_hint else "All mandatory fields are pre-filled."}

                       --------------------------------------------------
                       FLIPKART CRICKET {product_type.upper()} FIELD RULES:
                       --------------------------------------------------
                       - "Description": Plain text, no HTML, max 2000 characters.
                       - "Key Features": Semicolon-separated feature bullets (max 5).
                       - "Search Keywords": Comma-separated relevant cricket keywords.
                       - "Cover Included": "Yes" if a cover/carrying case is mentioned.
                       - "Weight Range": Format as "1150-1200" (grams range). Keep existing if set.
                       - "Weight Range - Measuring Unit": "g" always.
                       - "Part Number": MPN or SKU if available, otherwise "NA".
                       - "Bat Grade": Must be one of: "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5".
                       - "Blade Material": Must be one of: "English Willow", "Kashmir Willow", "Poplar Willow", "PVC/Plastic".
                       - "Size": Must be one of: "Short Handle", "Long Handle", "Harrow", "6", "5", "4", "3", "2", "1", "0".
                       - "Handle Type": "Round", "Oval", or "Semi-Oval".
                       - "Handle Grip Type": e.g. "Chevron", "Scale", "Octopus", "Diamond".
                       - "Handle Material": "Sarawak Cane", "Singapore Cane", or "Cane".
                       - "Toe Guard": "Yes" or "No".
                       - "Anti-Scuff Sheet": "Yes" or "No".
                       - "Curve": "Yes" or "No" — whether the bat has a pronounced bow.
                       - "Designed For": e.g. "Leather ball cricket", "Tennis ball cricket".
                       - "Suitable For": e.g. "Leather Ball", "Tennis Ball".
                       - "Series": Short series/collection name, e.g. "Reserve Edition", "RP Icon".
                       - "Other Body Features": Edge thickness, bow depth, spine profile e.g. "40mm edges; Mid Bow".
                       - "Certification": e.g. "BIS Certified", "ICC Approved" or leave empty.
                       - "Blade Width (inch)": Numeric inches, e.g. "4.25".
                       - "Blade Height (inch)": Numeric inches, e.g. "33.5".
                       - "Handle Length (inch)": Numeric inches, e.g. "10.5".
                       - "Handle Diameter (inch)": Numeric inches, e.g. "1.4".
                       - "Other Features": Any additional product features not covered above.
                       - "EAN/UPC": 13-digit barcode number if found, else "".
                       - "Domestic Warranty": e.g. "6 Months", "1 Year" or "No Warranty".
                       - "Domestic Warranty - Measuring Unit": "Month" or "Year".
                       - "Warranty Summary": One sentence e.g. "Warranty against manufacturing defects".
                       - "Length (CM)": Shipping box length in cm (NOT blade length).
                       - "Breadth (CM)": Shipping box breadth in cm.
                       - "Height (CM)": Shipping box height in cm.
                       - "Weight (KG)": Shipping weight in kg (product + packaging).

                       --------------------------------------------------
                       Input product data: {enriched_detail}
                    """

                    sys_prompt = (
                        "You are an expert Flipkart product data builder. "
                        "Return ONLY a valid JSON object matching the requested fields."
                    )

                    try:
                        extractor_response = await call_llm(local_llm, local_prompt, sys_prompt, user_prompt)
                        raw_json = extractor_response.content.strip()

                        try:
                            raw_json_sanitized = sanitize_json(raw_json)
                            llm_data = json.loads(raw_json_sanitized)
                        except json.JSONDecodeError as json_err:
                            try:
                                import ast
                                llm_data = ast.literal_eval(raw_json_sanitized)
                            except Exception:
                                try:
                                    llm_data = json.loads(raw_json)
                                except Exception:
                                    print(f"⚠️ Flipkart LLM JSON parse failed on line {json_err.lineno} col {json_err.colno}: {json_err.msg}")
                                    print(f"   Raw output (first 300 chars): {raw_json[:300]}")
                                    raise

                        if isinstance(llm_data, dict):
                            protected = {
                                "Blade Material", "Size", "Age Group", "Ideal For",
                                "Playing Level", "Bat Grade", "Weight Range",
                                "Sport Type", "Color", "Brand", "Manufacturer Details",
                                "MRP (INR)", "Your selling price (INR)", "Main Image URL",
                            }
                            for key, val in llm_data.items():
                                if key in FLIPKART_HEADERS and val not in (None, "", [], {}):
                                    if key in protected and not _is_empty(flipkart_row.get(key, "")):
                                        continue
                                    flipkart_row[key] = str(val).strip()
                    except Exception as e:
                        print(f"⚠️ Flipkart LLM formatting failed (non-critical): {e}")
                        print(f"   Proceeding with pre-extracted fields for product: {product_name}")

                product_type = _detect_product_type(
                    f"{product_name} {flipkart_row.get('Blade Material', '')}"
                )
                flipkart_row = validate_and_fill_flipkart(flipkart_row, product_type)
                formatted.append(flipkart_row)
            except Exception as e:
                print(f"⚠️ Error building Flipkart row: {e}")
        return formatted

    else:
        return products


# =============================================================================
#  BULK UPLOAD GENERATION HELPERS
# =============================================================================

async def generate_bulk_upload_file(
    product_names_or_csv_path: list | str,
    output_format: str = "flipkart",
    output_filepath: str = None,
    region: str = "India",
    enrich_from_official: bool = True,
    serper_api_key: str = None,
    groq_api_key: str = None,
):
    print(f"\n{'='*80}")
    print(f"🚀 Generating {output_format.upper()} bulk upload file")
    print(f"{'='*80}\n")

    if isinstance(product_names_or_csv_path, str):
        if not os.path.exists(product_names_or_csv_path):
            raise FileNotFoundError(f"CSV file not found: {product_names_or_csv_path}")
        print(f"📂 Loading predata from: {product_names_or_csv_path}")
        predata_df    = pd.read_csv(product_names_or_csv_path)
        products      = [row.to_dict() for _, row in predata_df.iterrows()]
        product_names = [str(r.get("product_name") or r.get("title") or "") for r in products]
    else:
        product_names = product_names_or_csv_path
        products      = [{"title": name} for name in product_names]

    print(f"📦 Processing {len(product_names)} products...")

    enriched_products = await get_multi_source_product_pages(
        product_names, region=region, format_type="raw",
        serper_api_key=serper_api_key, groq_api_key=groq_api_key,
    )

    if isinstance(product_names_or_csv_path, str):
        for i, enriched in enumerate(enriched_products):
            if i < len(products):
                enriched_products[i] = {**products[i], **enriched}

    print(f"\n📋 Formatting for {output_format.upper()}...")
    formatted_products = await format_products(
        enriched_products, format_type=output_format, groq_api_key=groq_api_key,
    )

    df = pd.DataFrame(formatted_products)

    if output_filepath:
        df.to_csv(output_filepath, index=False, encoding="utf-8")
        print(f"\n✅ Bulk upload file saved: {output_filepath}")
        print(f"📊 Total rows: {len(df)} | Columns: {len(df.columns)}")
    else:
        print(f"\n✅ Bulk upload data prepared | Rows: {len(df)} | Columns: {len(df.columns)}")

    return df


# =============================================================================
#  MAIN PIPELINE — get_multi_source_product_pages
# =============================================================================

async def get_multi_source_product_pages(
    product_names,
    region="India",
    format_type="raw",
    serper_api_key=None,
    groq_api_key=None,
):
    final_results = []
    batch_size    = 3

    for i in range(0, len(product_names), batch_size):
        batch = product_names[i: i + batch_size]
        print(f"Processing batch {i // batch_size + 1} of {len(batch)} products")

        batch_results = []
        for name in batch:
            result = await process_single_product(
                name, region, format_type,
                serper_api_key=serper_api_key, groq_api_key=groq_api_key,
            )
            if result:
                batch_results.append(result)
        final_results.extend(batch_results)

        if i + batch_size < len(product_names):
            await asyncio.sleep(5)

    if format_type != "raw":
        final_results = await format_products(
            final_results, format_type, groq_api_key=groq_api_key,
        )

    return final_results


async def process_single_product(name, region, format_type, serper_api_key=None, groq_api_key=None):
    name = correct_product_name(name)
    print(f"🔍 Searching for product: {name}")

    active_serper_key = serper_api_key or SERPER_API_KEY
    if not active_serper_key:
        raise ValueError("Serper API key is required.")

    local_llm, local_prompt = _get_llm_prompt(groq_api_key)

    detected_brand, official_url = detect_brand_from_name(name)
    official_data   = None
    official_domain = None

    if detected_brand and official_url:
        official_domain = get_official_domain(official_url)
        print(f"🏷️  Detected brand: '{detected_brand}' → Official site: {official_url}")

        site_query_url = "https://google.serper.dev/search"
        headers        = {"X-API-KEY": active_serper_key, "Content-Type": "application/json"}
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
            official_data = await normalize_info(raw_official, groq_api_key=groq_api_key)

            official_site_title_raw = raw_official.get("Official Site Title", "")
            name = compare_and_correct_name(name, official_site_title_raw, detected_brand)

            if is_data_complete(official_data):
                print(f"✅ Official site data complete for '{name}'. Skipping other sources.")
                official_data["_source"] = "official"
                merged = await summarize_product_info(
                    name, [official_data], official_first=True, format_type=format_type
                )
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

    serper_url = "https://google.serper.dev/search"
    headers    = {"X-API-KEY": active_serper_key, "Content-Type": "application/json"}
    payload    = {"q": name, "num": 5}

    try:
        res     = requests.post(serper_url, headers=headers, json=payload)
        data    = res.json()
        results = data.get("organic", [])

        if not results:
            print(f"⚠️ No Serper results found for '{name}'")
            if official_data:
                merged = await summarize_product_info(
                    name, [official_data], official_first=True, format_type=format_type
                )
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
                normalize = await normalize_info(prod, groq_api_key=groq_api_key)
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
                name, product_data,
                official_first=bool(official_data),
                format_type=format_type,
            )
            print(f"✅ Final product for '{name}' ready.")
            return merged

    except Exception as e:
        print(f"[❌ Error fetching '{name}'] {e}")

    return None


# =============================================================================
#  BATCH / CHUNK HELPERS
# =============================================================================

def _estimate_batch_prompt_size(product_values, official_first=False):
    if not product_values:
        return 0
    batch_data = {
        "Descriptions": [], "Variants": [], "Images": [],
        "Official Images": [], "Official Body HTML": "", "Official SEO Description": "",
    }
    if official_first and product_values:
        official = product_values[0]
        batch_data["Official Body HTML"]       = official.get("Body HTML", "")[:5000]
        batch_data["Official SEO Description"] = official.get("SEO Description", "")
        batch_data["Official Images"]          = official.get("Official Images", [])[:10]
    for source in product_values:
        if source.get("description"): batch_data["Descriptions"].append(source["description"])
        if source.get("variants"):    batch_data["Variants"].extend(source["variants"])
        if source.get("images"):      batch_data["Images"].extend(source["images"])
    batch_data["Descriptions"] = batch_data["Descriptions"][:3]
    batch_data["Variants"]     = batch_data["Variants"][:10]
    batch_data["Images"]       = batch_data["Images"][:10]
    return len(json.dumps(batch_data, default=str))


def _chunk_product_values(product_values, max_chars=10000):
    batches, current, current_size = [], [], 0
    for item in product_values:
        item_payload = {
            "description":     item.get("description", ""),
            "variants":        item.get("variants", [])[:10],
            "images":          item.get("images", [])[:10],
            "brand":           item.get("brand", ""),
            "Body HTML":       item.get("Body HTML", "")[:5000],
            "SEO Description": item.get("SEO Description", ""),
        }
        item_size = len(json.dumps(item_payload, default=str))
        if current and current_size + item_size > max_chars:
            batches.append(current)
            current, current_size = [item], item_size
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
        for field in ["brand", "official_sku", "body_html", "seo_description",
                       "official_site_description", "category", "type"]:
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


# =============================================================================
#  summarize_product_info
# =============================================================================

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
                product_name, batch, region_info, official_first=(official_first and idx == 0)
            )
            batch_summaries.append(batch_summary)
        return _merge_summary_batches(batch_summaries)

    prod_description, prod_variant, prod_images, prod_brand = [], [], [], []
    official_body_html        = ""
    official_seo_description  = ""
    official_images           = []
    official_site_title       = ""
    official_site_description = ""

    # Collect raw prices from all sources for fallback
    raw_prices = []

    if official_first and product_values:
        official_entry            = product_values[0]
        official_body_html        = official_entry.get("Body HTML", "")
        official_seo_description  = official_entry.get("SEO Description", "")
        official_images           = official_entry.get("Official Images", [])
        official_site_title       = official_entry.get("Official Site Title", "")
        official_site_description = official_entry.get("Official Site Description", "")
        if len(official_body_html) > 5000:
            official_body_html = official_body_html[:5000] + "..."
        official_images = official_images[:10]

    print(f"After normalize: {product_values}")

    for p in product_values:
        if p.get("description"): prod_description.append(p["description"])
        if p.get("variants"):    prod_variant.extend(p["variants"])
        if p.get("images"):      prod_images.extend(p["images"])
        if p.get("brand"):       prod_brand.append(p["brand"])
        # Collect scraped prices for fallback
        for pkey in ("selling_price", "price", "mrp_price"):
            src = p.get(pkey)
            if isinstance(src, dict) and src.get("value", 0) > 0:
                raw_prices.append(src)
                break

    prod_description = prod_description[:3]
    prod_variant     = prod_variant[:10]
    prod_images      = prod_images[:10]
    official_images  = official_images[:10]

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

        Your task: Given the input data, generate ONLY a single clean JSON object.
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
        5. Images: Return ONLY ONE image — the first valid URL after deduplication.
        6. Variant structure: {{"name":"","sku":"","price":0,"currency":"","size":""}}
        7. Non-official SKU → set sku = "".
        8. Only include variants where currency matches the region.
        {official_priority_rule}
        9. Body HTML — clean HTML only, no address, fix spelling.
        10. SEO Description — max 160 chars, plain text.
        11. Category — from: Cricket Bats|Cricket Balls|Batting Pads|Batting Gloves|
            Wicket Keeping Gloves|Wicket Keeping Pads|Helmets|Cricket Shoes|
            Cricket Clothing|Cricket Bags|Protective Equipment|Cricket Accessories|Training Equipment
        12. Price priority: INR → USD → GBP. "value" = LOWEST price among matching variants.
            IMPORTANT: if Raw Prices are provided below, use the first INR one as the price value.
        13. Official image always wins as primary.
    """

    # Include raw scraped prices in the user prompt so the LLM can use them
    raw_prices_hint = ""
    if raw_prices:
        inr_prices = [p for p in raw_prices if p.get("currency", "").upper() in ("INR", "")]
        if inr_prices:
            raw_prices_hint = f"\nRaw Prices (scraped directly — use first INR value): {json.dumps(inr_prices[:3])}"

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
        {raw_prices_hint}

        REGION-BASED VARIANT FILTERING (MANDATORY):
        1. Region is "{region_info}".
        2. India → INR; us/Unknown/Global → USD; uk → GBP.
        3. REMOVE every variant whose currency does NOT match region currency.

        REQUIRED OUTPUT FORMAT:
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

        FORMAT-SPECIFIC GUIDANCE:
        {"" if format_type != "amazon" else "Amazon: prioritise bullet_points, search_terms, exact brand name."}
        {"" if format_type != "shopify" else "Shopify: prioritise body_html (clean HTML), seo_description (160 chars), tags (5-15 hyphenated keywords)."}
        {"" if format_type != "flipkart" else "Flipkart: prioritise Blade Material (English/Kashmir Willow), Size (Short Handle/Harrow/6/5/4/3/2/1/0), INR price."}
    """

    response = await call_llm(llm, prompt, summary_prompt, user_prompt)

    try:
        result = json.loads(response.content.strip())
    except Exception:
        result = json.loads(sanitize_json(response.content.strip()))

    result["Body HTML"]       = result.pop("body_html", "")
    result["SEO Description"] = result.pop("seo_description", "")

    llm_official_desc = result.pop("official_site_description", "")
    if llm_official_desc and llm_official_desc.strip():
        result["Official Site Description"] = llm_official_desc.strip()
    elif official_site_description and official_site_description.strip():
        result["Official Site Description"] = official_site_description.strip()
    else:
        result["Official Site Description"] = result.get("SEO Description", "")

    result.setdefault("category", "")
    result.setdefault("type",     "")
    result.setdefault("tags",     [])
    result.setdefault("price",    {"value": 0.0, "currency": "INR"})

    # Price fallback: if LLM returned 0, restore the best scraped price
    if not result.get("price") or result["price"].get("value", 0) == 0:
        inr_prices = [p for p in raw_prices if p.get("currency", "").upper() in ("INR", "")]
        if inr_prices:
            result["price"] = inr_prices[0]
            print(f"  💰 Price restored from raw scrape: {inr_prices[0]}")
        elif raw_prices:
            result["price"] = raw_prices[0]

    if official_images:
        result["images"] = [official_images[0]]
    else:
        result.setdefault("images", [])

    result["Official Site Title"] = official_site_title

    # Carry raw price dicts forward so ensure_price_in_detail can use them later
    if raw_prices:
        result.setdefault("selling_price", raw_prices[0])

    return result


# =============================================================================
#  NORMALIZE INFO — preserves price fields through LLM call
# =============================================================================

def extract_first_json(text: str) -> str:
    start = -1
    for idx, char in enumerate(text):
        if char in '{[':
            start = idx
            break
    if start == -1:
        return text

    stack = []
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        char = text[idx]
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            stack.append('{')
        elif char == '[':
            stack.append('[')
        elif char == '}' and stack and stack[-1] == '{':
            stack.pop()
        elif char == ']' and stack and stack[-1] == '[':
            stack.pop()
        if not stack:
            return text[start:idx+1]

    return text[start:]


def sanitize_json(text: str) -> str:
    text = re.sub(r"```(?:json|python|text)?\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")

    if "{" in text:
        text = text[text.index("{"):]
    elif "[" in text:
        text = text[text.index("["):]

    lines = text.split('\n')
    text  = ''.join(lines)

    output = []
    i = 0
    while i < len(text):
        if text[i] == '"' and (i == 0 or text[i-1] != '\\'):
            output.append('"')
            i += 1
            while i < len(text):
                if text[i:i+2] == '\\"':
                    output.append('\\')
                    output.append('"')
                    i += 2
                elif text[i] == '\\' and i + 1 < len(text):
                    output.append(text[i])
                    output.append(text[i+1])
                    i += 2
                elif text[i] == '"':
                    output.append('"')
                    i += 1
                    break
                elif text[i] in '\n\r\t':
                    if text[i] == '\n':   output.append('\\n')
                    elif text[i] == '\r': output.append('\\r')
                    else:                 output.append('\\t')
                    i += 1
                else:
                    output.append(text[i])
                    i += 1
            else:
                output.append('"')
        else:
            output.append(text[i])
            i += 1

    text = ''.join(output)
    text = re.sub(r',\s*([}\]])', r'\1', text)

    open_braces = open_brackets = 0
    in_string = False
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text):
            i += 2
            continue
        if text[i] == '"':
            in_string = not in_string
        elif not in_string:
            if text[i] == '{':   open_braces += 1
            elif text[i] == '}': open_braces   = max(0, open_braces - 1)
            elif text[i] == '[': open_brackets += 1
            elif text[i] == ']': open_brackets  = max(0, open_brackets - 1)
        i += 1

    text = text.rstrip().rstrip(",")
    if open_braces   > 0: text += "}" * open_braces
    if open_brackets > 0: text += "]" * open_brackets
    text = extract_first_json(text)

    return text.strip()


def sanitize_json_online_llm(text: str) -> str:
    return sanitize_json(text)


async def normalize_info(prod_detail, groq_api_key=None):
    local_llm, local_prompt = _get_llm_prompt(groq_api_key)

    # Stash ALL passthrough fields before LLM call
    official_body_html        = prod_detail.get("Body HTML", "")
    official_seo_description  = prod_detail.get("SEO Description", "")
    official_images           = prod_detail.get("Official Images", [])
    official_site_title       = prod_detail.get("Official Site Title", "")
    official_site_description = prod_detail.get("Official Site Description", "")

    # Stash raw price data — these must survive the LLM round-trip
    raw_price        = prod_detail.get("price") or {}
    raw_mrp          = prod_detail.get("mrp_price") or {}
    raw_selling      = prod_detail.get("selling_price") or {}

    # Build lean dict for LLM (avoid token overflow)
    # KEY FIX: price fields are ALWAYS included regardless of length/size
    cleaned_detail = {}
    for key, value in prod_detail.items():
        lower_key = key.lower()

        # Skip heavy passthrough fields
        if lower_key in {
            "body html", "seo description", "official images", "official site title",
            "official site description", "html_content", "raw_html", "page_content",
        }:
            continue

        # Always keep price fields
        if lower_key in {"price", "mrp_price", "selling_price", "mrp", "mrp (inr)",
                          "your selling price (inr)"}:
            cleaned_detail[key] = value
            continue

        # Truncate long strings but keep them (don't skip entirely)
        if isinstance(value, str) and len(value) > 1_000:
            cleaned_detail[key] = value[:1_000] + "…"
            continue

        if isinstance(value, (list, tuple)) and len(value) > 10:
            cleaned_detail[key] = list(value[:10])
            continue

        cleaned_detail[key] = value

    normalize_prompt = """
        You are a JSON-only generator.
        RULES:
        - Output ONLY a valid JSON object. Start with { end with }.
        - No markdown, headings, code fences, explanations, or comments.
        - Use empty strings for missing text, empty arrays for missing lists.
        - Every variant object MUST have a "currency" field (string).
        - For "price": copy the value EXACTLY from input — do NOT set it to 0 if input has a real value.

        Structure:
        {
            "title": "",
            "brand": "",
            "description": "",
            "price": {"value": 0.0, "currency": ""},
            "sku": "",
            "category": "",
            "images": [],
            "variants": [{"Variant Name":"","Variant SKU":"","Variant Price":0.0,"Size":"","currency":""}],
            "attributes": {"Color": "", "Size": ""}
        }

        Instructions:
        - Set "price" from the input price/mrp_price/selling_price fields (prefer selling_price).
        - Set "price.currency" to detected currency code (e.g. "INR", "USD").
        - Copy same currency into each variant "currency" field.
    """

    user_prompt = f"""
        Convert the following product details into the JSON structure.
        Return ONLY the JSON.
        PRODUCT DATA:
        {json.dumps(cleaned_detail, ensure_ascii=False, indent=2)}
    """

    try:
        extractor_response = await call_llm(local_llm, local_prompt, normalize_prompt, user_prompt)
        details = extractor_response.content.strip()
        try:
            parsed = json.loads(sanitize_json(details))
        except Exception:
            parsed = json.loads(details)
    except Exception as e:
        print(f"⚠️ Normalization error: {e}")
        parsed = {}

    # Price fallback: if LLM lost the price, put it back
    if not parsed.get("price") or parsed.get("price", {}).get("value", 0) == 0:
        for src in (raw_selling, raw_mrp, raw_price):
            if src and src.get("value", 0) > 0:
                parsed["price"] = src
                print(f"  💰 Price restored after normalize: {src}")
                break

    # Re-attach ALL passthrough fields
    if official_body_html:        parsed["Body HTML"]                = official_body_html
    if official_seo_description:  parsed["SEO Description"]          = official_seo_description
    if official_images:           parsed["Official Images"]           = official_images
    if official_site_title:       parsed["Official Site Title"]       = official_site_title
    if official_site_description: parsed["Official Site Description"] = official_site_description

    # Re-attach raw price dicts so downstream can access them
    if raw_selling: parsed["selling_price"] = raw_selling
    if raw_mrp:     parsed["mrp_price"]     = raw_mrp
    if raw_price:   parsed.setdefault("price", raw_price)

    return parsed


# =============================================================================
#  FETCH MISSING DATA FROM OFFICIAL SITES
# =============================================================================

async def fetch_missing_data_from_official(
    product_detail: dict,
    product_name: str,
    region: str = "India",
    groq_api_key: str = None,
    serper_api_key: str = None,
) -> dict:
    raw_critical_fields = {
        "title":       product_detail.get("title") or product_detail.get("Title"),
        "brand":       product_detail.get("brand") or product_detail.get("Brand"),
        "description": product_detail.get("description") or product_detail.get("Body HTML"),
        "images":      product_detail.get("images") or product_detail.get("Images"),
        "price":       product_detail.get("price") or product_detail.get("Price"),
        "variants":    product_detail.get("variants") or product_detail.get("Variants"),
    }
    missing_fields = [
        field for field, value in raw_critical_fields.items()
        if not value or (isinstance(value, (list, dict)) and len(value) == 0)
    ]
    has_missing_raw = len(missing_fields) > 0
    has_missing_seo = not product_detail.get("Body HTML") or not product_detail.get("SEO Description")

    if not has_missing_raw and not has_missing_seo:
        print(f"✓ Product '{product_name}' has complete data.")
        return product_detail

    print(f"⚠️  Product '{product_name}' missing: {missing_fields if has_missing_raw else 'Body HTML/SEO'}. Fetching...")

    try:
        detected_brand, official_url = detect_brand_from_name(product_name)
        active_serper_key = serper_api_key or os.getenv("SERPER_API_KEY")
        if not active_serper_key:
            print(f"⚠️  SERPER_API_KEY not set, skipping online fetch")
            return product_detail

        site_query_url = "https://google.serper.dev/search"
        headers = {"X-API-KEY": active_serper_key, "Content-Type": "application/json"}

        if official_url:
            official_domain = get_official_domain(official_url)
            print(f"🌐 Fetching from official site: {official_url}")
            payload = {"q": f"site:{official_domain} {product_name}", "num": 5}
        else:
            print(f"⚠️  No official site detected. General search...")
            payload = {"q": product_name, "num": 5}

        response = requests.post(site_query_url, headers=headers, json=payload, timeout=10)
        results  = response.json().get("organic", [])

        if not results:
            print(f"⚠️  No search results for '{product_name}'")
            return product_detail

        best_result = results[0]
        product_url = best_result.get("link")
        if not product_url:
            print(f"⚠️  No product URL found")
            return product_detail

        print(f"📄 Extracting data from: {product_url}")
        official_data = await extract_product_info(product_url, is_official=True, product_name=product_name)
        if not official_data:
            print(f"⚠️  Failed to extract data from product page")
            return product_detail

        official_data = await normalize_info(official_data, groq_api_key=groq_api_key)
        enriched_detail = product_detail.copy()

        if not enriched_detail.get("title") and not enriched_detail.get("Title"):
            title_val = (official_data.get("title") or official_data.get("Title")
                         or official_data.get("SEO Title") or product_name)
            enriched_detail["title"] = title_val
            enriched_detail["Title"] = title_val

        if not enriched_detail.get("brand") and not enriched_detail.get("Brand"):
            brand_val = official_data.get("brand") or official_data.get("Brand") or detected_brand or ""
            enriched_detail["brand"] = brand_val
            enriched_detail["Brand"] = brand_val

        if official_data.get("Body HTML"):
            enriched_detail["Body HTML"] = official_data["Body HTML"]
        if official_data.get("SEO Description"):
            enriched_detail["SEO Description"] = official_data["SEO Description"]

        official_desc_plain = clean_text(
            official_data.get("Body HTML", "") or official_data.get("description", "")
            or official_data.get("SEO Description", "")
        )
        existing_desc = enriched_detail.get("description") or enriched_detail.get("Description") or ""
        if official_desc_plain and (not existing_desc or len(official_desc_plain) > len(existing_desc)):
            enriched_detail["description"] = official_desc_plain
            enriched_detail["Description"] = official_desc_plain

        official_imgs = (official_data.get("Official Images") or official_data.get("images")
                         or official_data.get("Images"))
        if official_imgs:
            enriched_detail["Official Images"] = official_imgs
            if not enriched_detail.get("images") and not enriched_detail.get("Images"):
                enriched_detail["images"] = official_imgs
                enriched_detail["Images"] = official_imgs

        official_variants = official_data.get("Variants") or official_data.get("variants")
        if official_variants:
            existing_variants = enriched_detail.get("variants") or enriched_detail.get("Variants") or []
            variant_map = {}
            for v in existing_variants:
                if isinstance(v, dict):
                    key = (
                        str(v.get("size") or v.get("Size") or ""),
                        str(v.get("color") or v.get("Color") or ""),
                        str(v.get("sku") or v.get("Sku") or v.get("Variant SKU") or ""),
                    )
                    variant_map[key] = v
            for v in official_variants:
                if isinstance(v, dict):
                    key = (
                        str(v.get("size") or v.get("Size") or v.get("public_title") or ""),
                        str(v.get("color") or v.get("Color") or ""),
                        str(v.get("sku") or v.get("Sku") or v.get("Variant SKU") or ""),
                    )
                    if key not in variant_map:
                        variant_map[key] = v
            enriched_detail["variants"] = list(variant_map.values())
            enriched_detail["Variants"] = enriched_detail["variants"]

        # Price merge — prefer scraped price over LLM-processed price
        existing_price = enriched_detail.get("price") or enriched_detail.get("Price")
        official_price = (
            official_data.get("selling_price") or official_data.get("price")
            or official_data.get("mrp_price") or official_data.get("Price")
        )
        if official_price and isinstance(official_price, dict):
            val = official_price.get("value", 0)
            cur = official_price.get("currency", "").upper()
            if val > 0 and cur in ("INR", ""):
                if not existing_price or str(existing_price) in ("0", "0.0", "", "{}"):
                    enriched_detail["price"]         = official_price
                    enriched_detail["Price"]         = official_price
                    enriched_detail["selling_price"] = official_price
                    print(f"  💰 Price merged from official: ₹{val}")
        elif official_price and not existing_price:
            enriched_detail["price"] = official_price
            enriched_detail["Price"] = official_price

        if official_data.get("Official Site Title"):
            enriched_detail["Official Site Title"] = official_data["Official Site Title"]
        if official_data.get("Official Site Description"):
            enriched_detail["Official Site Description"] = official_data["Official Site Description"]

        print(f"✅ Successfully enriched product data from online sources")
        return enriched_detail

    except Exception as e:
        print(f"❌ Error fetching from online sources: {e}")
        return product_detail