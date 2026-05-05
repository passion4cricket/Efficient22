from pathlib import Path
import os

HOSTNAME = "127.0.0.1"
USERNAME = "efficient22"
PASSWORD = "Efficient22@123"
DATABASE = "efficient22"

PROFIT_MARGIN = 30
CONNECTED_CLIENTS = {}

# Path to the .env file used by dotenv loaders.
_ENV_PATH = Path(".env")

def set_env_path(path):
    global _ENV_PATH
    _ENV_PATH = Path(path)


def get_env_path():
    return str(_ENV_PATH)

SHOPIFY_HEADERS = [
    "Handle","Title","Body (HTML)","Vendor","Product Category","Type","Tags","Published",
    "Option1 Name","Option1 Value","Option2 Name","Option2 Value","Option3 Name","Option3 Value",
    "Variant SKU","Variant Grams","Variant Inventory Tracker","Variant Inventory Qty",
    "Variant Inventory Policy","Variant Fulfillment Service","Variant Price",
    "Variant Compare At Price","Variant Requires Shipping","Variant Taxable","Variant Barcode",
    "Image Src","Image Position","Image Alt Text","Gift Card","SEO Title","SEO Description",
    "Google Shopping / Google Product Category","Google Shopping / Gender",
    "Google Shopping / Age Group","Google Shopping / MPN","Google Shopping / Condition",
    "Google Shopping / Custom Product","Variant Image","Variant Weight Unit","Variant Tax Code",
    "Cost per item","Included / United States","Price / United States",
    "Compare At Price / United States","Included / International","Price / International",
    "Compare At Price / International","Status","Official Site Title","Official Site Description",
]

AMAZON_HEADERS = [
    "sku",
    "product-id",
    "product-id-type",
    "item-name",
    "brand-name",
    "manufacturer",
    "item-type",
    "feed_product_type",
    "product-description",
    "bullet-point1",
    "bullet-point2",
    "bullet-point3",
    "bullet-point4",
    "bullet-point5",
    "search-terms",
    "department",
    "sport-type",
    "material-type",
    "color",
    "size",
    "style-name",
    "outer-material-type",
    "price",
    "quantity",
    "condition-type",
    "main-image-url",
    "other-image-url1",
    "other-image-url2",
    "other-image-url3",
    "item-weight",
    "item-weight-unit-of-measure",
    "item-package-dimensions",
    "item-package-weight",
    "country-of-origin",
    "manufacturer-contact-information",
    "update_delete"
]