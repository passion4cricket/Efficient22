import mysql.connector
import json

def connect_db(hostname, username, password, db):
    try:
        conn = mysql.connector.connect(
            host=hostname,
            user=username,
            password=password,
            database=db
        )
        return conn
    except mysql.connector.Error as err:
        print("MySQL Error:", err)
        return None

def get_table_details(conn, table_name):
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """, (conn.database, table_name))

        rows = cursor.fetchall()
        cursor.close()
        # for column_name, data_type in rows:
        #     print(f"{column_name} - {data_type}")
        return rows
    except mysql.connector.Error as err:
        print("MySQL Error:", err)
        return []
    
def get_tg_table_value_count(conn, table_name):
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"""
            SELECT COUNT(*) AS value_count FROM {table_name}                       
        """)

        rows = cursor.fetchone()
        print(rows)
        cursor.close()
        return rows['value_count']
    except mysql.connector.Error as err:
        print("MySQL Error:", err)
        return []


def upsert_product_details(conn, product: dict):
    try:
        cursor = conn.cursor(dictionary=True)

        sku = product.get("sku")
        marketplace = product.get("marketplace")
        marketplace_product_id = product.get("marketplace_product_id")
        product_id_val = product.get("product_id")

        conditions = []
        params = []
        if sku:
            conditions.append("sku = %s")
            params.append(sku)
        if marketplace and marketplace_product_id:
            conditions.append("(marketplace = %s AND marketplace_product_id = %s)")
            params.extend([marketplace, marketplace_product_id])
        if not conditions and product_id_val:
            conditions.append("product_id = %s")
            params.append(product_id_val)

        existing_id = None
        if conditions:
            sql = "SELECT id FROM product_details WHERE " + " OR ".join(conditions) + " LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row:
                existing_id = row.get("id")

        # Columns available in the product_details table (excluding audit/id columns)
        cols = [
            'marketplace','marketplace_product_id','sku','parent_sku','group_id',
            'title','product_name','description','brand','manufacturer','vendor','product_type','product_category','category','tags',
            'mrp','selling_price','sale_price','compare_at_price','cost_price','currency',
            'stock','inventory_qty','inventory_policy','inventory_tracker',
            'weight','weight_unit','length','breadth','height','dimension_unit',
            'color','size','material','pattern_name','style_name','model_name','model_number','part_number',
            'sport_type','department_name','target_gender','age_group','ideal_for',
            'main_image_url','other_image_url_1','other_image_url_2','other_image_url_3','other_image_url_4','other_image_url_5','swatch_image_url','video_url',
            'seo_title','seo_description','search_keywords',
            'domestic_warranty','international_warranty','warranty_summary','warranty_service_type',
            'fulfillment_type','shipping_provider','handling_time','shipping_template',
            'hsn','tax_code','product_tax_code','country_of_origin',
            'barcode','ean_upc','product_id','product_id_type',
            'listing_status','published','item_condition',
            'option1_name','option1_value','option2_name','option2_value','option3_name','option3_value',
            'qc_status','qc_failed_reason',
            'browse_nodes','bullet_points','extra_attributes'
        ]

        if existing_id:
            # Build update statement for keys present in product
            set_parts = []
            set_params = []
            for c in cols:
                if c in product:
                    val = product[c]
                    if c == 'extra_attributes' and isinstance(val, dict):
                        val = json.dumps(val)
                    set_parts.append(f"{c} = %s")
                    set_params.append(val)
            if set_parts:
                set_sql = ", ".join(set_parts)
                sql = f"UPDATE product_details SET {set_sql} WHERE id = %s"
                set_params.append(existing_id)
                cursor.execute(sql, set_params)
                conn.commit()
            cursor.close()
            return existing_id

        # Insert path
        insert_cols = []
        insert_vals = []
        placeholders = []
        for c in cols:
            if c in product:
                val = product[c]
                if c == 'extra_attributes' and isinstance(val, dict):
                    val = json.dumps(val)
                insert_cols.append(c)
                insert_vals.append(val)
                placeholders.append('%s')

        if not insert_cols:
            cursor.close()
            return None

        sql = f"INSERT INTO product_details ({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})"
        cursor.execute(sql, insert_vals)
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        return new_id

    except mysql.connector.Error as err:
        print("MySQL Error:", err)
        return None
    except Exception as e:
        print("Error in upsert_product_details:", e)
        return None
    
