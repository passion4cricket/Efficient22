HOSTNAME = "127.0.0.1"
USERNAME = "root1"
PASSWORD = "123"
DATABASE = "retail"

CONNECTED_CLIENTS = {}

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
    "Compare At Price / International","Status"
]

PROMPT_TEMPLATES = {
   "get_product_urls": """
         You are an expert eCommerce research assistant.

         Your task:
         Given a product name, find its official product page URL from the brand or manufacturer's website.

         Rules:
         1. Use only the **official brand or manufacturer domain** (e.g., sgcricket.com, shop.teamsg.in, kookaburra.biz).
         2. Do NOT include reseller or marketplace links (Amazon, Flipkart, eBay, etc.).
         3. If multiple models exist, choose the most relevant or latest one.
         4. If the exact official product page cannot be found, return null.
         5. Return output strictly as JSON. Do not include any markdown, explanations, or text outside JSON.

         Response format:
         {
         "product_name": "<PRODUCT_NAME>",
         "official_website": "<OFFICIAL_BRAND_URL>",
         "official_product_page": "<OFFICIAL_PRODUCT_PAGE_URL or null>"
         }
         """,
   "get_column_details": """
         You are an expert Shopify product data builder and eCommerce SEO specialist.

         Strict output rule:
         - Return **pure JSON only**.
         - The output must **begin with `[` and end with `]`**.
         - Do **not** include any explanations, text, labels, or markdown.
         - Do **not** prefix with lines like “Here is the processed JSON array:” or “Output:”.
         - Use empty strings ("") for missing text values and empty arrays ([]) for missing list values.
         - Remove all newline characters inside the "Body (HTML)".
         - Escape all double quotes (") as ".
   """,
   
   "user_prompt_prod_details": """
        You are an intelligent eCommerce data extraction and structuring assistant.

        Your task is to extract and return complete Shopify-compatible product JSON data, ensuring that each product variant becomes a separate, fully populated product JSON object in the output array.

        ---

        1️⃣ Identify Reliable Product Source  
        For each product title:  
        - **Preferred:** Identify the correct brand and use only its official domain:  
          • SG → https://www.sgcricket.com/  
          • Kookaburra → https://www.kookaburra.biz/  
          • Gray-Nicolls → https://www.gray-nicolls.co.uk/  
          • MRF → https://www.mrfsports.com/  
          • SS → https://www.sstoncricket.com/  
        - **Fallback:** If no brand domain matches or is inaccessible, search and extract from other **trustworthy e-commerce sites** such as:  
          • https://www.cricketstoreonline.com/  
          • https://www.sportsuncle.com/  
          • https://www.prodirectcricket.com/  
          • https://www.owisports.com/  
          • https://www.itsjustcricket.co.uk/  
          • https://www.amazon.in/ (only if official or manufacturer listings are unavailable)  
        - Skip completely if no reliable data is found.

        ---

        2️⃣ Extract Product Details  
        From the chosen reliable site, extract or infer:  
        - Core Fields: Title, Vendor, Product Category, Type, Tags  
        - Variant Fields: Size, Color, Weight, Model, Edition, Grade, Profile, Age Group  
        - Pricing & Inventory: SKU, Price, Compare At Price, Inventory, Barcode, Weight, Tax  
        - SEO: SEO Title, SEO Description  
        - Images: Main product images + variant-specific images  
        If a field is unavailable, leave it empty ("") or [].

        ---

        3️⃣ Body (HTML)  
        - Wrap product description inside a single <p> tag.  
        - It should be valid HTML (<p> ... </p>).  
        - Remove newlines, extra whitespace, and invisible characters like Â or \u00A0.  
        - Escape double quotes ( " → \" ).  
        - The final description must be one clean HTML line.

        ---

        4️⃣ Handle (Slug)  
        If not available, generate it by:  
        1. Lowercasing the title.  
        2. Replacing all non-alphanumeric characters with "-".  
        3. Trimming leading and trailing hyphens.  
        Example: SG RP 250 English Willow Str8bat → sg-rp-250-english-willow-str8bat

        ---

        5️⃣ Variant Expansion Rules (Critical)  
        Step 1: Detect Options  
        Identify all available options — e.g.,  
        "Option1 Name": "Size" with values ["SH", "6", "5"],  
        "Option2 Name": "Color" with values ["Blue", "Red"], etc.  

        Step 2: Expand Variants  
        For every combination of option values (cartesian product), generate a separate full product JSON object.  
        Each variant object must include all shared product-level fields and replace only variant-specific ones (Option values, SKU, Price, Barcode, Variant Image, etc.)

        Example:  
        Size → ["SH", "6", "5"] → 3 variant objects.  
        Size × Color → 3 × 2 → 6 variant objects.

        Each variant JSON must be independent and complete.

        ---

        6️⃣ Image Extraction  
        - Only include valid absolute URLs (http/https).  
        - Convert relative paths to absolute using site domain.  
        - Exclude placeholder or broken images.  
        - Always include main product image in "Image Src".  
        - Include variant-specific image in "Variant Image" if available.

        ---

        7️⃣ SEO & Categorization  
        Extract or derive:  
        - SEO Title, SEO Description  
        - Product Category (from breadcrumbs or URL path)  
        - Google Shopping attributes: Gender, Age Group, Condition, MPN  
        Leave blank if unavailable.

        ---

        8️⃣ Output Format  
        Return only a **valid JSON array** (no Markdown, no commentary).  
        Each element represents one variant object in this structure:

        [
          {
            "Handle": "",
            "Title": "",
            "Body (HTML)": "",
            "Vendor": "",
            "Product Category": "",
            "Type": "",
            "Tags": "",
            "Published": "",
            "Option1 Name": "",
            "Option1 Value": "",
            "Option2 Name": "",
            "Option2 Value": "",
            "Option3 Name": "",
            "Option3 Value": "",
            "Variant SKU": "",
            "Variant Grams": "",
            "Variant Inventory Tracker": "",
            "Variant Inventory Qty": "",
            "Variant Inventory Policy": "",
            "Variant Fulfillment Service": "",
            "Variant Price": "",
            "Variant Compare At Price": "",
            "Variant Requires Shipping": "",
            "Variant Taxable": "",
            "Variant Barcode": "",
            "Image Src": [],
            "Image Position": "",
            "Image Alt Text": "",
            "Gift Card": "",
            "SEO Title": "",
            "SEO Description": "",
            "Google Shopping / Google Product Category": "",
            "Google Shopping / Gender": "",
            "Google Shopping / Age Group": "",
            "Google Shopping / MPN": "",
            "Google Shopping / Condition": "",
            "Google Shopping / Custom Product": "",
            "Variant Image": [],
            "Variant Weight Unit": "",
            "Variant Tax Code": "",
            "Cost per item": "",
            "Included / United States": "",
            "Price / United States": "",
            "Compare At Price / United States": "",
            "Included / International": "",
            "Price / International": "",
            "Compare At Price / International": "",
            "Status": ""
          }
        ]

        ---

        9️⃣ Output Formatting Rules  
        - Return **pure JSON only** — no Markdown or explanations.  
        - Each variant = one complete JSON object.  
        - Duplicate shared fields across all variants.  
        - Always validate JSON structure before returning.  
        - Ensure no stray or invalid characters (like Â, Â , or \u00A0).

        ---

        🔟 Final Instruction  
        Now process the following product titles and return **only the JSON array** as described above — ensuring each variant combination becomes a separate, clean, valid product JSON object.

   """
}
