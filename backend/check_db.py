import sqlite3

def check_database():
    conn = sqlite3.connect("purity_beans.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    
    for table in tables:
        tname = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {tname}")
        count = cursor.fetchone()[0]
        print(f"Table '{tname}' has {count} rows")
        
        # Print a few sample rows if any
        if count > 0:
            cursor.execute(f"SELECT * FROM {tname} LIMIT 3")
            rows = cursor.fetchall()
            print("  Sample rows:")
            for r in rows:
                print("   ", r)

if __name__ == "__main__":
    check_database()
