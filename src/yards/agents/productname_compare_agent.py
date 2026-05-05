import pandas as pd
import asyncio, os, json
import requests
from datetime import datetime
from yards.utils.utils import llm_init, call_llm
from dotenv import load_dotenv
from yards.utils.config import get_env_path, HOSTNAME, USERNAME, PASSWORD, DATABASE
from yards.database.get_table_details import connect_db
from yards.utils.get_zoho_token import ZohoInventoryClient
import re
from rapidfuzz import process, fuzz


llm, prompt = llm_init()
load_dotenv(get_env_path())

ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
ZOHO_REDIRECT_URI = os.getenv("ZOHO_REDIRECT_URI")
ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID")

async def product_name_compare(state):
    print("Starting product name comparison...")

    # Shopify
    shopify_products = await get_shopify_products()
    insert_shopify_products(shopify_products)

    # Zoho
    client = ZohoInventoryClient(
        client_id=ZOHO_CLIENT_ID,
        client_secret=ZOHO_CLIENT_SECRET,
        redirect_uri=ZOHO_REDIRECT_URI,
        organization_id=ZOHO_ORG_ID
    )

    zoho_products = client.get_all_items()
    insert_zoho_products(zoho_products)

    print(f"Zoho products fetched: {len(zoho_products)}")

    # 🔥 ADD THIS LINE
    compare_and_store_products()

    return {
        "status": "success",
        "message": "Comparison completed"
    }

    # name, City(Geography), store, quntity need to compare.

async def get_shopify_products():
    url = "https://22-yards-in.myshopify.com/admin/api/2025-10/graphql.json"

    E22_ACCESS_TOKEN = os.getenv("E22_ACCESS_TOKEN")

    headers = {
        "X-Shopify-Access-Token": E22_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    all_products = []
    cursor = None

    try:
        print("Starting GraphQL product fetch...")

        while True:
            query = f"""
{{
  products(first: 100{', after: "' + cursor + '"' if cursor else ''}) {{
    edges {{
      node {{
        id
        title
        vendor   # ✅ BRAND

        variants(first: 50) {{
          edges {{
            node {{
              id
              title
              sku
              selectedOptions {{
                name
                value
              }}
              inventoryQuantity
              inventoryItem {{
                inventoryLevels(first: 5) {{
                  edges {{
                    node {{
                      quantities(names: ["available"]) {{
                        name
                        quantity
                      }}
                      location {{
                        name
                        address {{
                          city
                          country
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    pageInfo {{
      hasNextPage
      endCursor
    }}
  }}
}}
"""

            response = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json={"query": query},
                timeout=15,
            )

            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                print("GraphQL Error:", data["errors"])
                return {"status": "error", "message": data["errors"]}

            products = data["data"]["products"]["edges"]

            for p in products:
                product = p["node"]

                product_name = product["title"]
                brand = product.get("vendor") or "Unknown"  # ✅ BRAND

                for variant in product["variants"]["edges"]:
                    v = variant["node"]

                    variant_name = v["title"]
                    sku = v["sku"]
                    options = v["selectedOptions"]
                    total_qty = v["inventoryQuantity"]

                    for level in v["inventoryItem"]["inventoryLevels"]["edges"]:
                        loc = level["node"]

                        qty = 0
                        for q in loc.get("quantities", []):
                            if q["name"] == "available":
                                qty = q["quantity"]

                        record = {
                            "product": product_name,
                            "brand": brand,   # ✅ ADDED
                            "variant": variant_name,
                            "sku": sku,
                            "options": options,
                            "store": loc["location"]["name"],
                            "city": loc["location"]["address"]["city"],
                            "quantity": qty,
                            "total_variant_quantity": total_qty
                        }

                        all_products.append(record)

            page_info = data["data"]["products"]["pageInfo"]

            if page_info["hasNextPage"]:
                cursor = page_info["endCursor"]
            else:
                break

        print(f"✅ Total products fetched: {len(all_products)}")

        return {"status": "success", "products": all_products}

    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error", "message": str(e)}
    

def insert_shopify_products(products):
    conn = None
    try:
        if isinstance(products, dict):
            products = products.get("products", [])

        if isinstance(products, str):
            products = json.loads(products)

        if not isinstance(products, list):
            print("Invalid products format")
            return

        conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)
        cursor = conn.cursor()

        # ✅ UPDATED TABLE (added brand)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_name VARCHAR(255),
                brand VARCHAR(255),
                variant VARCHAR(255),
                sku VARCHAR(255),
                store VARCHAR(255),
                city VARCHAR(255),
                quantity INT,
                total_variant_quantity INT,
                UNIQUE KEY unique_product (product_name, variant, store)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_product_options (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT,
                option_name VARCHAR(100),
                option_value VARCHAR(100),
                UNIQUE KEY unique_option (product_id, option_name),
                FOREIGN KEY (product_id) REFERENCES shopify_products(id) ON DELETE CASCADE
            )
        """)

        count = 0

        for p in products:
            if not isinstance(p, dict):
                continue

            product_name = p.get("product")
            brand = p.get("brand") or "Unknown"   # ✅
            variant = p.get("variant")
            sku = p.get("sku") or ""
            store = p.get("store")
            city = p.get("city")
            quantity = p.get("quantity", 0)
            total_variant_quantity = p.get("total_variant_quantity", 0)

            # ✅ UPSERT with brand
            cursor.execute("""
                INSERT INTO shopify_products 
                (product_name, brand, variant, sku, store, city, quantity, total_variant_quantity)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    brand = VALUES(brand),
                    sku = VALUES(sku),
                    city = VALUES(city),
                    quantity = VALUES(quantity),
                    total_variant_quantity = VALUES(total_variant_quantity)
            """, (
                product_name,
                brand,
                variant,
                sku,
                store,
                city,
                quantity,
                total_variant_quantity
            ))

            # get product_id
            cursor.execute("""
                SELECT id FROM shopify_products
                WHERE product_name=%s AND variant=%s AND store=%s
            """, (product_name, variant, store))

            result = cursor.fetchone()
            if not result:
                continue

            product_id = result[0]

            # options
            options = p.get("options", [])
            if isinstance(options, str):
                options = json.loads(options)

            for opt in options:
                cursor.execute("""
                    INSERT INTO shopify_product_options 
                    (product_id, option_name, option_value)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        option_value = VALUES(option_value)
                """, (
                    product_id,
                    opt.get("name"),
                    opt.get("value")
                ))

            count += 1

        conn.commit()
        print(f"✅ Upserted {count} records with brand.")

    except Exception as e:
        print(f"❌ Database Error: {e}")

    finally:
        if conn:
            conn.close() 


def format_zoho_datetime(dt_str):
    if not dt_str:
        return None
    try:
        # Remove timezone (+0530)
        dt_str = dt_str.split('+')[0]
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def insert_zoho_products(items):
    conn = None
    try:
        # ✅ Handle dict input
        if isinstance(items, dict):
            items = items.get("items", [])

        if not isinstance(items, list):
            print("Invalid items format")
            return

        conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)
        if not conn:
            print("Failed to connect to database.")
            return

        cursor = conn.cursor()

        inserted_count = 0

        for item in items:
            if not isinstance(item, dict):
                continue

            item_id = item.get("item_id")

            # 🔹 Convert datetime safely
            created_time = format_zoho_datetime(item.get("created_time"))
            last_modified_time = format_zoho_datetime(item.get("last_modified_time"))

            # 🔹 Main table UPSERT
            cursor.execute("""
                INSERT INTO zoho_items (
                    item_id, name, brand, status, rate,
                    purchase_rate, stock_on_hand, available_stock,
                    sku, vendor_name, hsn_or_sac,
                    created_time, last_modified_time
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name),
                    brand=VALUES(brand),
                    status=VALUES(status),
                    rate=VALUES(rate),
                    purchase_rate=VALUES(purchase_rate),
                    stock_on_hand=VALUES(stock_on_hand),
                    available_stock=VALUES(available_stock),
                    sku=VALUES(sku),
                    vendor_name=VALUES(vendor_name),
                    hsn_or_sac=VALUES(hsn_or_sac),
                    last_modified_time=VALUES(last_modified_time)
            """, (
                item_id,
                item.get("name"),
                item.get("brand"),
                item.get("status"),
                item.get("rate"),
                item.get("purchase_rate"),
                item.get("stock_on_hand"),
                item.get("available_stock"),
                item.get("sku"),
                item.get("vendor_name"),
                item.get("hsn_or_sac"),
                created_time,
                last_modified_time
            ))

            # 🔹 Insert Taxes
            taxes = item.get("item_tax_preferences", [])

            if isinstance(taxes, list):
                for tax in taxes:
                    if not isinstance(tax, dict):
                        continue

                    cursor.execute("""
                        INSERT INTO zoho_item_taxes (
                            item_id, tax_name, tax_percentage, tax_specification
                        )
                        VALUES (%s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            tax_percentage=VALUES(tax_percentage)
                    """, (
                        item_id,
                        tax.get("tax_name"),
                        tax.get("tax_percentage"),
                        tax.get("tax_specification")
                    ))

            # 🔹 Insert Dimensions
            cursor.execute("""
                INSERT INTO zoho_item_dimensions (
                    item_id, length, width, height, weight
                )
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    length=VALUES(length),
                    width=VALUES(width),
                    height=VALUES(height),
                    weight=VALUES(weight)
            """, (
                item_id,
                safe_decimal(item.get("length")),
                safe_decimal(item.get("width")),
                safe_decimal(item.get("height")),
                safe_decimal(item.get("weight"))
            ))

            inserted_count += 1

        conn.commit()
        print(f"✅ Inserted/Updated {inserted_count} Zoho items successfully.")

    except Exception as e:
        print(f"❌ Database Error: {e}")

    finally:
        if conn:
            conn.close()


def safe_decimal(value):
    if value in ("", None):
        return None
    try:
        return float(value)
    except:
        return None

# ---------------------------------------------------------------------------
# Normalization (MATCHING ONLY)
# ---------------------------------------------------------------------------

def normalize_for_match(text: str) -> str:
    if not text:
        return ""

    text = text.lower()

    # convert hyphen → space
    text = text.replace("-", " ")

    # remove special characters
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # normalize units
    text = text.replace("gb", " gb ")
    text = text.replace("kg", " kg ")

    # collapse spaces
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ---------------------------------------------------------------------------
# Display Name (KEEP hyphen)
# ---------------------------------------------------------------------------

def build_combined_name(product_name: str, variant: str) -> str:
    variant = (variant or "").strip()

    if variant.lower() == "default title":
        return product_name

    return f"{product_name} - {variant}".strip()


# ---------------------------------------------------------------------------
# Fuzzy Match Engine (NAME ONLY)
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 80


def find_zoho_match(norm_name: str, zoho_map: dict, zoho_names: list) -> dict | None:

    # ── Tier 1: Exact ─────────────────────
    if norm_name in zoho_map:
        return zoho_map[norm_name]

    # ── Tier 2: Fuzzy ─────────────────────
    result = process.extractOne(
        norm_name,
        zoho_names,
        scorer=fuzz.token_set_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )

    if result is None:
        return None

    matched_name, score, _ = result
    return zoho_map.get(matched_name)


# ---------------------------------------------------------------------------
# MAIN COMPARISON
# ---------------------------------------------------------------------------
def compare_and_store_products():
    conn = connect_db(HOSTNAME, USERNAME, PASSWORD, DATABASE)
    cursor = conn.cursor(dictionary=True)

    # ── Schema ──────────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_comparison (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            product_name        VARCHAR(255),
            brand               VARCHAR(255),
            store               VARCHAR(255),
            city                VARCHAR(255),
            shopify_quantity    INT,
            zoho_quantity       DECIMAL(12,2),
            quantity_difference DECIMAL(12,2),
            status              VARCHAR(50),
            last_updated        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                          ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY unique_compare (product_name, brand, store, city)
        )
    """)

    # ── Fetch data ──────────────────────────────────────────────────────────
    cursor.execute("""
        SELECT product_name, variant, brand, store, city, quantity
        FROM shopify_products
    """)
    shopify_products = cursor.fetchall()

    cursor.execute("SELECT name, brand, stock_on_hand FROM zoho_items")
    zoho_products = cursor.fetchall()

    # -----------------------------------------------------------------------
    # Shopify lookup (NAME ONLY)
    # -----------------------------------------------------------------------
    shopify_map: dict[str, list[dict]] = {}
    shopify_names: set[str] = set()

    for s in shopify_products:
        combined_name = build_combined_name(
            s["product_name"], s.get("variant") or ""
        )

        norm_name = normalize_for_match(combined_name)

        shopify_names.add(norm_name)
        shopify_map.setdefault(norm_name, []).append(s)

    # -----------------------------------------------------------------------
    # ZOHO FIRST PASS (PRIMARY)
    # -----------------------------------------------------------------------
    matched_shopify_names = set()

    for z in zoho_products:
        zoho_name = z["name"]
        zoho_brand = z.get("brand") or "unknown"
        zoho_qty = float(z.get("stock_on_hand") or 0)

        zoho_norm_name = normalize_for_match(zoho_name)

        # find match in Shopify
        result = process.extractOne(
            zoho_norm_name,
            list(shopify_names),
            scorer=fuzz.token_set_ratio,
            score_cutoff=FUZZY_THRESHOLD,
        )

        if result:
            matched_name, score, _ = result
            matched_shopify_names.add(matched_name)

            for s in shopify_map.get(matched_name, []):
                shopify_qty = s.get("quantity") or 0

                # 🔥 FIXED DIFF
                diff = zoho_qty - shopify_qty

                status = "MATCH" if diff == 0 else "MISMATCH"

                cursor.execute("""
                    INSERT INTO product_comparison (
                        product_name, brand, store, city,
                        shopify_quantity, zoho_quantity,
                        quantity_difference, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        shopify_quantity    = VALUES(shopify_quantity),
                        zoho_quantity       = VALUES(zoho_quantity),
                        quantity_difference = VALUES(quantity_difference),
                        status              = VALUES(status)
                """, (
                    zoho_name,
                    zoho_brand,
                    s.get("store"),
                    s.get("city"),
                    shopify_qty,
                    zoho_qty,
                    diff,
                    status
                ))

        else:
            # 🔴 NOT FOUND IN SHOPIFY
            cursor.execute("""
                INSERT INTO product_comparison (
                    product_name, brand, store, city,
                    shopify_quantity, zoho_quantity,
                    quantity_difference, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    zoho_quantity = VALUES(zoho_quantity),
                    status        = VALUES(status)
            """, (
                zoho_name,
                zoho_brand,
                "ZOHO",
                "N/A",
                None,
                zoho_qty,
                None,
                "NOT_FOUND_IN_SHOPIFY",
            ))

    # -----------------------------------------------------------------------
    # SHOPIFY LEFTOVER PASS
    # -----------------------------------------------------------------------
    for norm_name, records in shopify_map.items():

        if norm_name in matched_shopify_names:
            continue

        for s in records:
            combined_name = build_combined_name(
                s["product_name"], s.get("variant") or ""
            )

            cursor.execute("""
                INSERT INTO product_comparison (
                    product_name, brand, store, city,
                    shopify_quantity, zoho_quantity,
                    quantity_difference, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    shopify_quantity = VALUES(shopify_quantity),
                    status           = VALUES(status)
            """, (
                combined_name,
                s.get("brand") or "unknown",
                s.get("store"),
                s.get("city"),
                s.get("quantity"),
                None,
                None,
                "NOT_FOUND_IN_ZOHO",
            ))

    conn.commit()
    conn.close()
    print("✅ Product comparison completed (Zoho-first)")

def _zoho_key_matched_by_shopify(
    zoho_norm_name: str,
    shopify_norm_names: set[str],
) -> bool:

    # Exact
    if zoho_norm_name in shopify_norm_names:
        return True

    # Fuzzy
    result = process.extractOne(
        zoho_norm_name,
        list(shopify_norm_names),
        scorer=fuzz.token_set_ratio,
        score_cutoff=FUZZY_THRESHOLD,
    )

    return result is not None