import mysql.connector

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
    
