# 📊 Portföy Yönetimi

Açık kaynak portföy yönetim uygulaması - HTML5, CSS3 ve Vanilla JavaScript ile yapılmıştır.

## 🎯 Özellikler

- 🔐 Şifre korumalı giriş (Şifre: 8789)
- 💰 Gelir/Gider yönetimi
- 🏦 Borç takibi ve ödeme planı
- 💸 Düzenli ödemeler yönetimi
- 🪙 Birikim takibi (USD, EUR, Gram Altın)
- 📊 Aylık özet ve istatistikler
- 🌓 Koyu tema arayüzü

## 🛠️ Teknolojiler

- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Backend**: Node.js + Express.js + SQLite3
- **Stil**: Dark theme, Responsive Design

## 🚀 Kurulum

### Gereklilikler
- Node.js 14+
- npm

### Adımlar

```bash
# Bağımlılıkları yükle
npm install

# Sunucuyu başlat
npm start
```

Backend şu adresinde çalışacak: `http://localhost:5001`

## 📁 Proje Yapısı

```
portfoy-yonetimi/
├── index.html          # Ana HTML dosyası
├── css/
│   └── styles.css      # Stil dosyası
├── js/
│   └── app.js          # JavaScript mantığı
├── server.js           # Express backend
├── package.json        # Node bağımlılıkları
└── data/
    └── portfoy.db      # SQLite veritabanı
```

## 🔑 Giriş Bilgileri

- **Şifre**: `8789`
- **Kilit**: 5 hatalı denemeden sonra 30 dakika
- **Oturum Zaman Aşımı**: 15 dakika inaktivite

## 📊 Özellikler Detaylı

### Aylık Yönetim
- Aylık açılış bakiyesi belirleme
- Otomatik kapanış bakiyesi hesaplama
- Aylık özet istatistikleri

### İşlemler
- Gelir ve gider işlemleri
- Kategori yönetimi
- Tarih bazlı sıralama

### Borçlar
- Kredi oluşturma (taksitli)
- Ödeme yapma
- Kalan tutar takibi
- Para birimi desteği (TRY, USD, EUR, Altın)

### Düzenli Ödemeler
- Aylık tekrarlayan ödemeler
- Aktif/pasif durum yönetimi
- Kategori atama

### Birikimler
- Para birimi cinsinde birikim
- Alış kuru takibi
- TL karşılığı hesaplama

## 📝 Lisans

MIT License

## 👨‍💻 Geliştirici

Ilyas Ancar
