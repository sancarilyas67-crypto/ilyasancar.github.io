const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const path = require('path');
const cors = require('cors');
const bodyParser = require('body-parser');
const session = require('express-session');
const fs = require('fs');

const app = express();
app.use(cors());
app.use(bodyParser.json());
app.use(bodyParser.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname)));
app.use(session({
  secret: 'portfoy-secret',
  resave: false,
  saveUninitialized: true,
  cookie: { secure: false, httpOnly: true }
}));

const dataDir = path.join(__dirname, 'data');
if (!fs.existsSync(dataDir)) {
  fs.mkdirSync(dataDir, { recursive: true });
}

const dbPath = path.join(dataDir, 'portfoy.db');
const db = new sqlite3.Database(dbPath);

const LOGIN_PASSWORD = '8789';
let lastRequest = {};

// Tablo oluşturma
function initDatabase() {
  db.serialize(() => {
    db.run(`CREATE TABLE IF NOT EXISTS aylar (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ad TEXT NOT NULL,
      yil INTEGER,
      acilis_bakiye REAL DEFAULT 0,
      kapanis_bakiye REAL DEFAULT 0,
      aktif BOOLEAN DEFAULT 1
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS islemler (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ay_id INTEGER,
      aciklama TEXT,
      tutar REAL,
      tur TEXT,
      tarih DATE,
      kategori_id INTEGER,
      duzenli_mi BOOLEAN DEFAULT 0,
      sira INTEGER DEFAULT 0,
      FOREIGN KEY(ay_id) REFERENCES aylar(id)
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS gider_kategorileri (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ad TEXT NOT NULL,
      tur TEXT
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS borclar (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ad TEXT NOT NULL,
      toplam_tutar REAL,
      kalan_tutar REAL,
      kredi_mi BOOLEAN DEFAULT 0,
      taksit_tutari REAL DEFAULT 0,
      toplam_taksit INTEGER DEFAULT 0,
      odenmis_taksit INTEGER DEFAULT 0,
      vade_gunu INTEGER DEFAULT 1,
      para_birimi TEXT DEFAULT 'TRY',
      altin_cinsi TEXT,
      olusturma_tarihi DATE
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS duzenli_odemeler (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ad TEXT NOT NULL,
      tutar REAL,
      tur TEXT,
      ay_gunu INTEGER DEFAULT 1,
      aktif BOOLEAN DEFAULT 1,
      baslangic_ayi TEXT,
      bitis_ayi TEXT,
      kategori_id INTEGER,
      sira INTEGER DEFAULT 0
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS birikimler (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ay_id INTEGER,
      para_birimi TEXT,
      tl_tutar REAL,
      birim_miktar REAL,
      alis_kuru REAL,
      altin_cinsi TEXT,
      tarih DATE,
      FOREIGN KEY(ay_id) REFERENCES aylar(id)
    )`);

    db.run(`CREATE TABLE IF NOT EXISTS auth_state (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      hatali_denemeler INTEGER DEFAULT 0,
      kilitli_kadar TEXT
    )`);
  });
}

initDatabase();

// Middleware for authentication
app.use((req, res, next) => {
  if (req.path === '/auth/login' || req.path === '/auth/status') {
    return next();
  }
  
  if (!req.session.authenticated) {
    return res.status(401).json({ error: 'Kimlik doğrulaması gerekli' });
  }
  
  // Idle timeout check
  if (req.session.loginTime && (Date.now() - req.session.loginTime) > 15 * 60 * 1000) {
    req.session.authenticated = false;
    return res.status(401).json({ error: 'Oturum süresi doldu' });
  }
  
  req.session.loginTime = Date.now();
  next();
});

// Auth
app.post('/auth/login', (req, res) => {
  const password = req.body.password || '';
  
  db.get('SELECT * FROM auth_state WHERE id = 1', (err, state) => {
    if (err) return res.json({ success: false, error: err.message });
    
    if (!state) {
      db.run('INSERT INTO auth_state (id, hatali_denemeler) VALUES (1, 0)');
      state = { id: 1, hatali_denemeler: 0, kilitli_kadar: null };
    }
    
    // Check lock
    if (state.kilitli_kadar) {
      const lockUntil = new Date(state.kilitli_kadar);
      const now = new Date();
      if (now < lockUntil) {
        const remaining = Math.ceil((lockUntil - now) / 1000);
        return res.json({ success: false, locked: true, remaining });
      }
    }
    
    if (password === LOGIN_PASSWORD) {
      db.run('UPDATE auth_state SET hatali_denemeler = 0, kilitli_kadar = NULL WHERE id = 1');
      req.session.authenticated = true;
      req.session.loginTime = Date.now();
      res.json({ success: true });
    } else {
      let hatali = (state.hatali_denemeler || 0) + 1;
      
      if (hatali >= 5) {
        const lockUntil = new Date(Date.now() + 30 * 60 * 1000);
        db.run('UPDATE auth_state SET hatali_denemeler = ?, kilitli_kadar = ? WHERE id = 1', 
          [hatali, lockUntil.toISOString()]);
        res.json({ success: false, locked: true, remaining: 30 * 60 });
      } else {
        db.run('UPDATE auth_state SET hatali_denemeler = ? WHERE id = 1', [hatali]);
        res.json({ success: false, remaining_attempts: 5 - hatali });
      }
    }
  });
});

app.post('/auth/logout', (req, res) => {
  req.session.authenticated = false;
  res.json({ success: true });
});

app.get('/auth/status', (req, res) => {
  if (req.session.authenticated) {
    return res.json({ authenticated: true });
  }
  
  db.get('SELECT * FROM auth_state WHERE id = 1', (err, state) => {
    if (state && state.kilitli_kadar) {
      const lockUntil = new Date(state.kilitli_kadar);
      const now = new Date();
      if (now < lockUntil) {
        const remaining = Math.ceil((lockUntil - now) / 1000);
        return res.json({ authenticated: false, locked: true, remaining });
      }
    }
    res.json({ authenticated: false });
  });
});

// Months
app.get('/api/months', (req, res) => {
  db.all('SELECT * FROM aylar ORDER BY yil DESC, id ASC', (err, rows) => {
    if (err) return res.json({ error: err.message });
    res.json(rows || []);
  });
});

app.get('/api/month/:id', (req, res) => {
  const monthId = req.params.id;
  db.get('SELECT * FROM aylar WHERE id = ?', [monthId], (err, month) => {
    if (err) return res.json({ error: err.message });
    if (!month) return res.json({ error: 'Ay bulunamadı' });
    
    db.all('SELECT * FROM islemler WHERE ay_id = ? ORDER BY sira, tarih DESC', [monthId], (err, transactions) => {
      db.all('SELECT * FROM borclar ORDER BY ad', (err2, debts) => {
        db.all('SELECT * FROM duzenli_odemeler ORDER BY sira', (err3, recurring) => {
          db.all('SELECT * FROM birikimler WHERE ay_id = ? ORDER BY tarih DESC', [monthId], (err4, savings) => {
            month.islemler = transactions || [];
            month.borclar = debts || [];
            month.duzenli = recurring || [];
            month.birikimler = savings || [];
            res.json(month);
          });
        });
      });
    });
  });
});

app.post('/api/month', (req, res) => {
  const { ad, yil, acilis_bakiye } = req.body;
  db.run('INSERT INTO aylar (ad, yil, acilis_bakiye) VALUES (?, ?, ?)', 
    [ad, yil, acilis_bakiye || 0], function(err) {
    if (err) return res.json({ error: err.message });
    res.json({ success: true, id: this.lastID });
  });
});

// Transactions
app.post('/api/add_transaction', (req, res) => {
  const { ay_id, aciklama, tutar, tur, tarih, kategori_id } = req.body;
  db.run('INSERT INTO islemler (ay_id, aciklama, tutar, tur, tarih, kategori_id) VALUES (?, ?, ?, ?, ?, ?)',
    [ay_id, aciklama, tutar, tur, tarih, kategori_id], function(err) {
    if (err) return res.json({ error: err.message });
    res.json({ success: true, id: this.lastID });
  });
});

app.get('/api/transaction/:id', (req, res) => {
  db.get('SELECT * FROM islemler WHERE id = ?', [req.params.id], (err, row) => {
    if (err) return res.json({ error: err.message });
    res.json(row || {});
  });
});

app.post('/api/delete_transaction/:id', (req, res) => {
  db.run('DELETE FROM islemler WHERE id = ?', [req.params.id], (err) => {
    if (err) return res.json({ error: err.message });
    res.json({ success: true });
  });
});

app.post('/api/update_transaction/:id', (req, res) => {
  const { aciklama, tutar, tur, tarih, kategori_id } = req.body;
  db.run('UPDATE islemler SET aciklama = ?, tutar = ?, tur = ?, tarih = ?, kategori_id = ? WHERE id = ?',
    [aciklama, tutar, tur, tarih, kategori_id, req.params.id], (err) => {
    if (err) return res.json({ error: err.message });
    res.json({ success: true });
  });
});

app.post('/api/reorder_transactions', (req, res) => {
  const { transactions } = req.body;
  let completed = 0;
  
  transactions.forEach((trans, idx) => {
    db.run('UPDATE islemler SET sira = ? WHERE id = ?', [idx, trans.id], (err) => {
      completed++;
      if (completed === transactions.length) {
        res.json({ success: true });
      }
    });
  });
});

// Categories
app.get('/api/categories', (req, res) => {
  db.all('SELECT * FROM gider_kategorileri ORDER BY ad', (err, rows) => {
    if (err) return res.json([]);
    res.json(rows || []);
  });
});

app.post('/api/add_category', (req, res) => {
  const { ad, tur } = req.body;
  db.run('INSERT INTO gider_kategorileri (ad, tur) VALUES (?, ?)', [ad, tur], function(err) {
    if (err) return res.json({ success: false });
    res.json({ success: true, id: this.lastID });
  });
});

app.post('/api/delete_category/:id', (req, res) => {
  db.run('DELETE FROM gider_kategorileri WHERE id = ?', [req.params.id], (err) => {
    if (err) return res.json({ success: false });
    res.json({ success: true });
  });
});

app.post('/api/rename_category/:id', (req, res) => {
  const { ad } = req.body;
  db.run('UPDATE gider_kategorileri SET ad = ? WHERE id = ?', [ad, req.params.id], (err) => {
    if (err) return res.json({ success: false });
    res.json({ success: true });
  });
});

// Debts
app.post('/api/add_debt', (req, res) => {
  const { ad, toplam_tutar, para_birimi, kredi_mi, toplam_taksit, taksit_tutari } = req.body;
  db.run('INSERT INTO borclar (ad, toplam_tutar, kalan_tutar, para_birimi, kredi_mi, toplam_taksit, taksit_tutari) VALUES (?, ?, ?, ?, ?, ?, ?)',
    [ad, toplam_tutar, toplam_tutar, para_birimi, kredi_mi ? 1 : 0, toplam_taksit || 0, taksit_tutari || 0], 
    function(err) {
    if (err) return res.json({ error: err.message });
    res.json({ success: true, id: this.lastID });
  });
});

app.get('/api/debts', (req, res) => {
  db.all('SELECT * FROM borclar ORDER BY ad', (err, rows) => {
    if (err) return res.json([]);
    res.json(rows || []);
  });
});

app.get('/api/debt/:id', (req, res) => {
  db.get('SELECT * FROM borclar WHERE id = ?', [req.params.id], (err, row) => {
    if (err) return res.json({ error: err.message });
    res.json(row || {});
  });
});

app.post('/api/pay_debt', (req, res) => {
  const { borç_id, tutar } = req.body;
  db.get('SELECT * FROM borclar WHERE id = ?', [borç_id], (err, debt) => {
    if (err || !debt) return res.json({ error: 'Borç bulunamadı' });
    
    const newRemaining = Math.max(0, debt.kalan_tutar - tutar);
    const newPaid = Math.min(debt.toplam_taksit || 0, (debt.odenmis_taksit || 0) + 1);
    
    db.run('UPDATE borclar SET kalan_tutar = ?, odenmis_taksit = ? WHERE id = ?',
      [newRemaining, newPaid, borç_id], (err) => {
      if (err) return res.json({ error: err.message });
      res.json({ success: true });
    });
  });
});

app.post('/api/delete_debt/:id', (req, res) => {
  db.run('DELETE FROM borclar WHERE id = ?', [req.params.id], (err) => {
    if (err) return res.json({ error: err.message });
    res.json({ success: true });
  });
});

// Recurring
app.post('/api/add_recurring', (req, res) => {
  const { ad, tutar, tur, ay_gunu, aktif, kategori_id } = req.body;
  db.run('INSERT INTO duzenli_odemeler (ad, tutar, tur, ay_gunu, aktif, kategori_id) VALUES (?, ?, ?, ?, ?, ?)',
    [ad, tutar, tur, ay_gunu || 1, aktif ? 1 : 0, kategori_id], function(err) {
    if (err) return res.json({ error: err.message });
    res.json({ success: true, id: this.lastID });
  });
});

app.get('/api/recurring', (req, res) => {
  db.all('SELECT * FROM duzenli_odemeler ORDER BY sira', (err, rows) => {
    if (err) return res.json([]);
    res.json(rows || []);
  });
});

app.get('/api/recurring/:id', (req, res) => {
  db.get('SELECT * FROM duzenli_odemeler WHERE id = ?', [req.params.id], (err, row) => {
    if (err) return res.json({ error: err.message });
    res.json(row || {});
  });
});

app.post('/api/toggle_recurring/:id', (req, res) => {
  const { aktif } = req.body;
  db.run('UPDATE duzenli_odemeler SET aktif = ? WHERE id = ?', [aktif ? 1 : 0, req.params.id], (err) => {
    if (err) return res.json({ success: false });
    res.json({ success: true });
  });
});

app.post('/api/delete_recurring/:id', (req, res) => {
  db.run('DELETE FROM duzenli_odemeler WHERE id = ?', [req.params.id], (err) => {
    if (err) return res.json({ error: err.message });
    res.json({ success: true });
  });
});

app.post('/api/reorder_recurring', (req, res) => {
  const { recurring } = req.body;
  let completed = 0;
  
  recurring.forEach((item, idx) => {
    db.run('UPDATE duzenli_odemeler SET sira = ? WHERE id = ?', [idx, item.id], (err) => {
      completed++;
      if (completed === recurring.length) {
        res.json({ success: true });
      }
    });
  });
});

// Savings
app.post('/api/add_saving', (req, res) => {
  const { ay_id, para_birimi, tl_tutar, birim_miktar, alis_kuru } = req.body;
  db.run('INSERT INTO birikimler (ay_id, para_birimi, tl_tutar, birim_miktar, alis_kuru, tarih) VALUES (?, ?, ?, ?, ?, ?)',
    [ay_id, para_birimi, tl_tutar, birim_miktar, alis_kuru, new Date().toISOString().split('T')[0]], 
    function(err) {
    if (err) return res.json({ error: err.message });
    res.json({ success: true, id: this.lastID });
  });
});

app.get('/api/savings/:monthId', (req, res) => {
  db.all('SELECT * FROM birikimler WHERE ay_id = ? ORDER BY tarih DESC', [req.params.monthId], (err, rows) => {
    if (err) return res.json([]);
    res.json(rows || []);
  });
});

app.post('/api/delete_saving/:id', (req, res) => {
  db.run('DELETE FROM birikimler WHERE id = ?', [req.params.id], (err) => {
    if (err) return res.json({ error: err.message });
    res.json({ success: true });
  });
});

const PORT = 5001;
app.listen(PORT, () => {
  console.log(`Server http://localhost:${PORT} adresinde çalışıyor`);
});
