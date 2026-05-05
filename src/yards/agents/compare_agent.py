import os
import pandas as pd
import re
import torch
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer, util


# ---------------- UTILS ---------------- #

def normalize(text):
    return re.sub(r'[^a-z0-9\s]', '', str(text).lower()).strip()


def clean_option(val):
    val = str(val).strip()
    if val.lower() in ["default title", "title", "default", "", "nan", "none"]:
        return ""
    return val


def ffill_column(series):
    return (
        series
        .replace("", pd.NA)
        .replace(r'^\s*$', pd.NA, regex=True)
        .ffill()
    )


# ---------------- NUMBER / WEIGHT EXTRACTION ---------------- #

def extract_numbers(text):
    decimals = set(re.findall(r'\d+\.\d+', text))
    wholes   = set(re.findall(r'\d+', text))
    return decimals | wholes


def numbers_compatible(orig_text1, orig_text2):
    nums1 = extract_numbers(orig_text1)
    nums2 = extract_numbers(orig_text2)
    if not nums1 or not nums2:
        return True
    return bool(nums1 & nums2)


# ---------------- BRAND DETECTION ---------------- #

KNOWN_BRANDS = [
    "moonwalkr", "cosco", "vector x", "hundred", "koxtons",
    "nivia", "speedo", "viva", "spalding", "everlast",
    "jonex", "siscaa", "synco", "konex", "kxana", "dylan",
    "trinity", "smart pro", "tyka", "sixit",
    "sg", "ss", "mrf", "ton", "gg", "dunlop", "gki", "vixen",
]


def extract_brand(text):
    text_lower = text.lower().strip()
    for brand in sorted(KNOWN_BRANDS, key=len, reverse=True):
        if text_lower.startswith(brand):
            return brand
    return None


# ---------------- PRODUCT TYPE GUARD ---------------- #

PRODUCT_TYPE_KEYWORDS = {
    "grip":             ["bat grip", "cricket grip", "grip"],
    "bat_cone":         ["bat cone"],
    "bat_mallet":       ["bat mallet", "ball mallet"],
    "bat_oil":          ["bat oil", "bat wax"],
    "anti_scuff":       ["anti scuff"],
    "mini_bat":         ["mini bat", "autograph bat", "mini autograph",
                         "catch cricket bat", "practice bat", "practise bat"],
    "bat":              ["cricket bat", "ew bat", "kw bat", "english willow",
                         "kashmir willow", "scoop bat", "tennis bat",
                         "narrow blade", "ibat", "i-bat"],
    "ball_mallet":      ["ball mallet"],
    "ball":             ["cricket ball", "leather ball", "tennis ball", "wind ball",
                         "bowling machine ball", "reaction ball", "swing ball",
                         "smiley ball", "plastic ball", "poly tuff", "cordy seamer",
                         "hanging ball"],
    "pad":              ["batting pad", "batting pads", "leg guard", "legguard",
                         "batting legguard", "wicket keeping pad", "keeping pad",
                         "thigh pad", "thigh guard", "inner thigh",
                         "abdominal pad", "abdo pad"],
    "glove":            ["batting glove", "batting gloves", "keeping glove",
                         "keeping gloves", "wicket keeping glove",
                         "wicket keeping gloves", "wk glove", "wk gloves",
                         "inner glove", "inner gloves", "catching mitt"],
    "helmet":           ["helmet"],
    "shoe":             ["shoes", "spike shoes", "sports shoes",
                         "turf shoes", "cricket shoe", "cricket shoes"],
    "kit_bag":          ["kit bag", "duffle", "wheelie", "trolley bag",
                         "trolly bag", "kitbag"],
    "elbow_guard":      ["elbow guard", "elbow sleeve"],
    "chest_guard":      ["chest guard"],
    "abdo_guard":       ["abdominal guard", "abdo guard"],
    "shin_guard":       ["shin guard", "shin guards", "fielding shin"],
    "stump":            ["stump", "stumps", "stump set"],
    "cap":              ["panama hat", "super cap", "cricket cap", "cap"],
    "hat":              ["hat"],
    "sock":             ["sock", "socks"],
    "wristband":        ["wrist band", "wristband", "wrist guard"],
    "headband":         ["headband", "head band"],
    "kit":              ["cricket kit", "combo kit", "economy kit"],
    "clothing":         ["shirt", "trouser", "pant", "jersey", "sweater",
                         "track pant", "clothing", "sleeves"],
    "sunglasses":       ["shades", "sunglasses", "sports glasses"],
    "umpire":           ["umpire counter", "toss coin"],
    "net":              ["practice net", "cricket net"],
    "fielding":         ["fielding cone", "fielding marker", "speed ladder",
                         "fielding gloves", "catching board"],
    "tape":             ["side tape", "tape roll"],
    "score_book":       ["score book", "scorebook"],
    "towel":            ["cooling towel"],
    "ice_bag":          ["ice bag"],
    "dumbbell":         ["dumbbell", "dumbbells", "hexagonal rubber",
                         "rubber dumbbell", "hex dumbbell", "hexa dumbbell"],
    "hand_grip":        ["hand grip", "hand gripper", "grip strengthener"],
    "resistance_band":  ["resistance band", "toning tube", "loop band",
                         "tpe loop", "exercise band"],
    "gym_ball":         ["gym ball", "exercise ball", "yoga ball"],
    "yoga":             ["yoga mat", "yoga block", "cork yoga"],
    "skipping_rope":    ["skipping rope", "jump rope"],
    "skate":            ["roller skate", "inline skate", "roller shoe",
                         "skate shoe", "skating"],
    "racket":           ["tennis racket", "badminton racket", "tt racket",
                         "table tennis racket", "squash racket",
                         "pickleball paddle"],
    "shuttlecock":      ["shuttlecock", "shuttle", "feather shuttle",
                         "nylon shuttle"],
    "tt_ball":          ["tt ball", "table tennis ball", "ping pong",
                         "table tennis"],
    "football":         ["football", "foot ball", "soccer ball", "soccer"],
    "volleyball":       ["volleyball", "volley ball", "volleyball net",
                         "volley ball net"],
    "basketball":       ["basketball", "basket ball"],
    "hockey":           ["hockey ball", "hockey stick", "hockey"],
    "swim":             ["swimming goggle", "swim goggle", "swimming cap",
                         "swim cap", "swimming costume", "swim suit",
                         "kickboard", "swim bag", "ear plug"],
    "chess":            ["chess board", "chessmen", "chess piece", "chess rollon"],
    "carrom":           ["carrom board", "carrom coin", "carrom powder",
                         "carrom stand"],
    "dart":             ["dart board"],
    "knee_guard":       ["knee guard", "knee pad", "knee support"],
    "supporter":        ["supporter", "athletic supporter"],
    "sports_net":       ["volleyball net", "volley ball net", "badminton net",
                         "net set", "cotton net", "nylon net"],
}


def detect_product_type(text):
    text_norm = normalize(text)
    for ptype, keywords in PRODUCT_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_norm:
                return ptype
    return "unknown"


def is_compatible_type(text1, text2):
    t1 = detect_product_type(text1)
    t2 = detect_product_type(text2)
    if t1 == "unknown" or t2 == "unknown":
        return True
    return t1 == t2


# ---------------- TEXT BUILDERS ---------------- #

def build_f1_text(row):
    tab_name = str(row.get("_tab_name", "")).strip()
    name     = str(row.get("Name On Brand WebSite", "")).strip()
    size     = str(row.get("Size", "")).strip()
    parts    = [p for p in [tab_name, name, size]
                if p and p.lower() not in ["nan", "none", ""]]
    return " ".join(parts)


def build_f2_text(row):
    title = str(row.get("Title", "")).strip()
    opt1  = clean_option(row.get("Option1 Value", ""))
    opt2  = clean_option(row.get("Option2 Value", ""))
    parts = [p for p in [title, opt1, opt2] if p]
    return " ".join(parts)


# ---------------- FILE1 MULTI-TAB LOADER ---------------- #

def load_file1_all_tabs(path):
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(path, dtype=str)
        tab_name = os.path.splitext(os.path.basename(path))[0]
        df["_tab_name"] = tab_name
        print(f"  📄 CSV loaded as tab: '{tab_name}'")
        return df

    all_sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    frames = []

    for sheet_name, df in all_sheets.items():
        if df.empty:
            print(f"  ⚠️  Skipping empty tab: '{sheet_name}'")
            continue
        df.columns      = df.columns.str.strip()
        df["_tab_name"] = sheet_name.strip()
        print(f"  📋 Tab '{sheet_name}' → {len(df)} rows")
        frames.append(df)

    if not frames:
        raise ValueError("No valid tabs found in file1")

    merged = pd.concat(frames, ignore_index=True)
    print(f"✅ File1 total rows after merging all tabs: {len(merged)}")
    return merged


# ---------------- MAIN ---------------- #

# Load model once at module level — not inside the function
print("🔄 Loading semantic model...")
SEMANTIC_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
print("✅ Model loaded")


async def compare_agent(state):
    print("🚀 Starting comparison...")

    try:
        file1_name = state.get("file1_name", "")
        file2_name = state.get("file2_name", "")

        base_dir = "uploads/compare_files"
        f1_path  = os.path.join(base_dir, file1_name)
        f2_path  = os.path.join(base_dir, file2_name)

        if not os.path.exists(f1_path):
            return {"status": 404, "message": "File 1 not found"}
        if not os.path.exists(f2_path):
            return {"status": 404, "message": "File 2 not found"}

        print("\n📂 Loading File1 (all tabs)...")
        file1 = load_file1_all_tabs(f1_path)

        ext2 = os.path.splitext(file2_name)[1].lower()
        if ext2 == ".csv":
            file2 = pd.read_csv(f2_path, dtype=str)
        elif ext2 in [".xlsx", ".xls"]:
            file2 = pd.read_excel(f2_path, dtype=str)
        else:
            raise ValueError(f"Unsupported file2 format: {ext2}")

        file1.columns = file1.columns.str.strip()
        file2.columns = file2.columns.str.strip()

        if "Name On Brand WebSite" not in file1.columns:
            return {"status": 400, "message": "File1 missing 'Name On Brand WebSite'"}
        if "Title" not in file2.columns:
            return {"status": 400, "message": "File2 missing 'Title'"}

        file2["Title"] = ffill_column(file2["Title"])
        if "Handle" in file2.columns:
            file2["Handle"] = ffill_column(file2["Handle"])

        # Build original text (used for number check + semantic encoding)
        file1["_text"] = file1.apply(build_f1_text, axis=1)
        file2["_text"] = file2.apply(build_f2_text, axis=1)

        # Normalized text (used for brand/type guard)
        file1["_norm"] = file1["_text"].apply(normalize)
        file2["_norm"] = file2["_text"].apply(normalize)

        print(f"\nSample File1 text:\n" +
              "\n".join(f"  → {t}" for t in file1["_text"].head(5).tolist()))
        print(f"\nSample File2 text:\n" +
              "\n".join(f"  → {t}" for t in file2["_text"].head(5).tolist()))

        col_map = {
            "L":                       "Length (product.metafields.custom.length)",
            "W":                       "Breadth (product.metafields.custom.breadth)",
            "H":                       "Height (product.metafields.custom.height)",
            "Product weight (Per PC)": "Weight (product.metafields.custom.weight)",
            "MRP":                     "MRP",
            "Tax %":                   "Variant Tax Code"
        }

        for f2_col in col_map.values():
            if f2_col not in file2.columns:
                file2[f2_col] = None
                print(f"✅ Created column: '{f2_col}'")

        for f1_col in col_map.keys():
            if f1_col not in file1.columns:
                print(f"⚠️  WARNING: '{f1_col}' not in file1!")

        # ✅ Pre-compute semantic embeddings for ALL file1 rows once
        # This is the key — encode meaning not character patterns
        print("\n🔄 Encoding file1 product names semantically...")
        f1_texts_list = file1["_text"].tolist()
        f1_embeddings = SEMANTIC_MODEL.encode(
            f1_texts_list,
            convert_to_tensor=True,
            show_progress_bar=True,
            batch_size=64
        )
        print(f"✅ Encoded {len(f1_texts_list)} file1 entries")

        # Build brand-scoped index
        # Maps brand_name → list of file1 row indices belonging to that tab
        tab_indices = {}
        for idx, row in file1.iterrows():
            tab_norm = normalize(row["_tab_name"])
            if tab_norm not in tab_indices:
                tab_indices[tab_norm] = []
            tab_indices[tab_norm].append(idx)

        print(f"Brand tabs: {list(tab_indices.keys())}")

        def find_best_match_semantic(orig_text2, norm_text2, brand_name,
                                     sem_threshold=0.45, fuzz_threshold=55):
            """
            Semantic + fuzzy hybrid matching:

            1. Scope to brand tab if available (prevents cross-brand matches)
            2. Encode file2 product semantically
            3. Compute cosine similarity against scoped file1 embeddings
            4. For top candidates: apply type guard + number guard
            5. Re-score survivors with WRatio for final ranking
            6. Return the best combined-score candidate

            Why hybrid?
            - Semantic handles: "Hexagonal Rubber Dumbbell" ≈ "Hexa Dumbbell Rubber"
            - WRatio handles: exact model numbers, size codes ("SH", "2.5 Kg")
            - Type guard handles: cross-category blocking
            - Number guard handles: correct weight/size variant selection
            """
            if not orig_text2:
                return None, 0

            # Determine which file1 rows to search
            if brand_name and brand_name in tab_indices:
                candidate_indices = tab_indices[brand_name]
            else:
                candidate_indices = list(range(len(file1)))

            if not candidate_indices:
                return None, 0

            # Get embeddings for scoped candidates only
            scoped_embeddings = f1_embeddings[candidate_indices]

            # Encode file2 product text
            emb2 = SEMANTIC_MODEL.encode(orig_text2, convert_to_tensor=True)

            # Semantic cosine similarity
            cos_scores = util.cos_sim(emb2, scoped_embeddings)[0]

            # Get top 20 by semantic score
            top_k = min(20, len(candidate_indices))
            top_results = torch.topk(cos_scores, k=top_k)

            best_row   = None
            best_score = 0

            for sem_score, local_idx in zip(
                top_results.values.tolist(),
                top_results.indices.tolist()
            ):
                # Map local index back to file1 global index
                f1_global_idx = candidate_indices[local_idx]
                f1_row        = file1.iloc[f1_global_idx]
                orig_text1    = f1_row["_text"]

                # Skip if semantic score too low
                if sem_score < sem_threshold:
                    continue

                # Guard 1: product type compatibility
                if not is_compatible_type(orig_text2, orig_text1):
                    print(f"  🚫 Type blocked: '{orig_text2}' ↔ '{orig_text1}'"
                          f" (sem={sem_score:.2f})")
                    continue

                # Guard 2: number/weight compatibility
                if not numbers_compatible(orig_text2, orig_text1):
                    print(f"  🔢 Number mismatch: '{orig_text2}' ↔ '{orig_text1}'"
                          f" (sem={sem_score:.2f})")
                    continue

                # ✅ Hybrid score: semantic (meaning) + fuzzy (exact tokens)
                # Semantic catches meaning similarity across different wordings
                # WRatio catches exact model codes, numbers, size abbreviations
                fuzz_score = fuzz.WRatio(normalize(orig_text2), normalize(orig_text1))

                if fuzz_score < fuzz_threshold:
                    continue

                # Combined score: 60% semantic + 40% fuzzy
                combined = (0.6 * sem_score * 100) + (0.4 * fuzz_score)

                if combined > best_score:
                    best_score = combined
                    best_row   = f1_row

            return best_row, best_score

        match_count    = 0
        no_match_count = 0
        MIN_COMBINED_SCORE = 60  # combined score threshold

        for i, row in file2.iterrows():
            orig_text2 = row["_text"]
            norm_text2 = row["_norm"]

            if not orig_text2:
                no_match_count += 1
                continue

            brand = extract_brand(orig_text2)

            best_row, score = find_best_match_semantic(
                orig_text2, norm_text2, brand
            )

            if best_row is None or score < MIN_COMBINED_SCORE:
                print(f"❌ No match: '{orig_text2}'")
                no_match_count += 1
                continue

            # Apply column mapping
            for f1_col, f2_col in col_map.items():
                if f1_col not in best_row:
                    continue
                val = best_row[f1_col]
                if val and str(val).strip().lower() not in ["nan", "none", ""]:
                    file2.at[i, f2_col] = val

            tab = best_row.get("_tab_name", "")
            print(f"✅ ({score:.1f}) [{tab}] '{orig_text2}' → '{best_row['_text']}'")
            match_count += 1

        print(f"\n📊 {match_count} matched | {no_match_count} unmatched"
              f" | {len(file2)} total rows")

        file2.drop(columns=["_text", "_norm"], inplace=True, errors="ignore")

        out_dir = "uploads/compare_results"
        os.makedirs(out_dir, exist_ok=True)

        base    = os.path.splitext(file2_name)[0]
        counter = 1
        while True:
            out_name = f"updated_{base}_{counter}.xlsx"
            out_path = os.path.join(out_dir, out_name)
            if not os.path.exists(out_path):
                break
            counter += 1

        file2.to_excel(out_path, index=False)
        print(f"✅ Saved: {out_path}")

        state["output_file_name"] = out_name
        state["output_file_path"] = out_path

        return {
            "status": 200,
            "message": "Comparison completed successfully",
            "output_file_name": out_name,
            "output_file_path": out_path,
            "matched_rows": match_count,
            "unmatched_rows": no_match_count
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "status": 500,
            "message": str(e),
            "output_file_path": None,
            "output_file_name": None
        }