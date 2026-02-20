import sqlite3
from datetime import datetime, date
import os

# Veritabanı yolunu belirle
db_path = os.path.join('instance', 'portfoy_tr.db')

# Mevcut veritabanını sil ve yenisini oluştur (temiz başlangıç)
if os.path.exists(db_path):
    os.remove(db_path)

conn = sqlite3.connect(db_path)
c = conn.cursor()

# Tablo oluştur (bu tabloları yeniportfoy.py kodu ile senkronize et)
c.execute('''CREATE TABLE IF NOT EXISTS month
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              carryover REAL DEFAULT 0,
              closing REAL DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS transaction
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              month_id INTEGER NOT NULL,
              description TEXT,
              amount REAL,
              category TEXT,
              ttype TEXT,
              date TEXT,
              sort_index INTEGER DEFAULT 0,
              FOREIGN KEY(month_id) REFERENCES month(id))''')

c.execute('''CREATE TABLE IF NOT EXISTS expense_category
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL)''')

c.execute('''CREATE TABLE IF NOT EXISTS income_category
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL)''')

c.execute('''CREATE TABLE IF NOT EXISTS debt
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              amount REAL,
              remaining REAL,
              is_credit INTEGER,
              installment_fee REAL DEFAULT 0,
              num_installments INTEGER DEFAULT 0,
              num_paid_installments INTEGER DEFAULT 0,
              due_day INTEGER DEFAULT 1)''')

c.execute('''CREATE TABLE IF NOT EXISTS recurring_payment
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              amount REAL,
              type TEXT,
              day_of_month INTEGER,
              active INTEGER DEFAULT 1,
              category TEXT,
              sort_index INTEGER DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS saving
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              month_id INTEGER NOT NULL,
              currency TEXT,
              tl_amount REAL,
              unit_amount REAL,
              purchase_rate REAL,
              unit_name TEXT,
              date TEXT,
              FOREIGN KEY(month_id) REFERENCES month(id))''')

c.execute('''CREATE TABLE IF NOT EXISTS auth_state
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              failed_attempts INTEGER DEFAULT 0,
              locked_until TEXT)''')

# Sample veriler ekle
c.execute('INSERT INTO month (title, carryover, closing) VALUES (?, ?, ?)',
          ('Ocak 2024', 5000, 5000))
month_id_1 = c.lastrowid

c.execute('INSERT INTO month (title, carryover, closing) VALUES (?, ?, ?)',
          ('Şubat 2024', 5000, 5000))
month_id_2 = c.lastrowid

# Gider kategorileri
expense_categories = ['Yiyecek', 'Ulaşım', 'Utilities', 'Eğlence', 'Sağlık']
for cat in expense_categories:
    c.execute('INSERT INTO expense_category (title) VALUES (?)', (cat,))

# Gelir kategorileri
income_categories = ['Maaş', 'Bonus', 'Freelance', 'Yatırım']
for cat in income_categories:
    c.execute('INSERT INTO income_category (title) VALUES (?)', (cat,))

# Örnek işlemler
c.execute('INSERT INTO transaction (month_id, description, amount, category, ttype, date, sort_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
          (month_id_1, 'Aylık Maaş', 5000, 'Maaş', 'gelir', '2024-01-01', 0))

c.execute('INSERT INTO transaction (month_id, description, amount, category, ttype, date, sort_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
          (month_id_1, 'Market Alışveriş', 500, 'Yiyecek', 'gider', '2024-01-05', 1))

c.execute('INSERT INTO transaction (month_id, description, amount, category, ttype, date, sort_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
          (month_id_1, 'Elektrik Faturası', 200, 'Utilities', 'gider', '2024-01-10', 2))

# Örnek borçlar
c.execute('INSERT INTO debt (title, amount, remaining, is_credit, due_day) VALUES (?, ?, ?, ?, ?)',
          ('Araba Kredisi', 50000, 30000, 1, 15))

c.execute('INSERT INTO debt (title, amount, remaining, is_credit, due_day) VALUES (?, ?, ?, ?, ?)',
          ('Arkadaş Borcu', 2000, 2000, 0, 0))

# Düzenli ödemeler
c.execute('INSERT INTO recurring_payment (title, amount, type, day_of_month, active, category, sort_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
          ('Telefon Faturası', 150, 'gider', 10, 1, 'Utilities', 0))

c.execute('INSERT INTO recurring_payment (title, amount, type, day_of_month, active, category, sort_index) VALUES (?, ?, ?, ?, ?, ?, ?)',
          ('İnternet Faturası', 100, 'gider', 15, 1, 'Utilities', 1))

# Birikimler
c.execute('INSERT INTO saving (month_id, currency, tl_amount, unit_amount, purchase_rate, unit_name, date) VALUES (?, ?, ?, ?, ?, ?, ?)',
          (month_id_1, 'USD', 3500, 100, 35, 'Dolar', '2024-01-20'))

conn.commit()
conn.close()

print(f"✅ Veritabanı başarıyla oluşturuldu: {db_path}")
