# type: ignore
# pyright: basic
from flask import Flask, render_template_string, request, jsonify, redirect, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import calendar
import sqlite3
import requests
import os

app = Flask(__name__)
os.makedirs(app.instance_path, exist_ok=True)
custom_db_path = os.environ.get('PORTFOY_DB_PATH')
if custom_db_path:
    custom_db_path = os.path.expanduser(custom_db_path)
    if not os.path.isabs(custom_db_path):
        custom_db_path = os.path.abspath(custom_db_path)
db_path = custom_db_path or os.path.join(app.instance_path, 'portfoy_tr.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('PORTFOY_SECRET_KEY', 'change-me')
app.config['LOGIN_PASSWORD'] = os.environ.get('PORTFOY_LOGIN_PASSWORD', '8789')
db = SQLAlchemy(app)

# Tutar formatı için Jinja2 filter
@app.template_filter('tr_currency')
def tr_currency_format(value):
    """Tutarı Türk formatında göster: ₺292,00"""
    if value is None:
        return '₺0,00'
    return f'₺{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

@app.template_filter('tr_number')
def tr_number_format(value):
    """Sayiyi para birimi sembolü olmadan Türk formatında göster: 292,00"""
    if value is None:
        return '0,00'
    return f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')

@app.template_filter('usd_currency')
def usd_currency_format(value):
    """USD formatı: $1,234.56"""
    if value is None:
        return '$0.00'
    return f'${value:,.2f}'

# Sabit başlıklar
CARRYOVER_TITLE = 'Devreden Bakiye'
MONTH_NAMES = [
    'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
    'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
]

# ---------- MODELLER ----------
class Month(db.Model):
    __tablename__ = 'aylar'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column('ad', db.String(20), nullable=False)
    year = db.Column('yil', db.Integer, default=lambda: date.today().year, nullable=False)
    opening_balance = db.Column('acilis_bakiye', db.Float, default=0.0)
    closing_balance = db.Column('kapanis_bakiye', db.Float, default=0.0)
    is_active = db.Column('aktif', db.Boolean, default=True)
    transactions = db.relationship('Transaction', backref='month', lazy=True, cascade='all, delete-orphan')

class Transaction(db.Model):
    __tablename__ = 'islemler'
    id = db.Column(db.Integer, primary_key=True)
    month_id = db.Column('ay_id', db.Integer, db.ForeignKey('aylar.id'), nullable=False)
    title = db.Column('aciklama', db.String(200), nullable=False)
    amount = db.Column('tutar', db.Float, nullable=False)
    type = db.Column('tur', db.String(10), nullable=False)  # 'gelir' veya 'gider'
    date = db.Column('tarih', db.Date, default=date.today)
    is_recurring = db.Column('duzenli_mi', db.Boolean, default=False)
    category_id = db.Column('kategori_id', db.Integer, db.ForeignKey('gider_kategorileri.id'), nullable=True)
    debt_id = db.Column('borc_id', db.Integer, db.ForeignKey('borclar.id'), nullable=True)  # Borç referansı
    order_index = db.Column('sira', db.Integer, default=0)
    # Borç ödeme detayları
    purchase_rate = db.Column('alis_kuru', db.Float, nullable=True)  # Satın alınan dolar kuru
    gold_type = db.Column('altin_cinsi', db.String(50), nullable=True)  # Altın cinsi (22K, 24K vb)
    gold_grams = db.Column('altin_gram', db.Float, nullable=True)  # Satın alınan gram
    gold_tl_value = db.Column('altin_tl_degeri', db.Float, nullable=True)  # Altın TL değeri

class Debt(db.Model):
    __tablename__ = 'borclar'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column('ad', db.String(200), nullable=False)
    total_amount = db.Column('toplam_tutar', db.Float, nullable=False)
    remaining_amount = db.Column('kalan_tutar', db.Float, nullable=False)
    is_credit = db.Column('kredi_mi', db.Boolean, default=False)
    installment_amount = db.Column('taksit_tutari', db.Float, default=0.0)
    total_installments = db.Column('toplam_taksit', db.Integer, default=0)
    installments_paid = db.Column('odenmis_taksit', db.Integer, default=0)
    due_day = db.Column('vade_gunu', db.Integer, default=1)
    currency = db.Column('para_birimi', db.String(10), default='TRY')
    gold_type = db.Column('altin_cinsi', db.String(50), nullable=True)
    created_at = db.Column('olusturma_tarihi', db.Date, default=date.today)

class RecurringPayment(db.Model):
    __tablename__ = 'duzenli_odemeler'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column('ad', db.String(200), nullable=False)
    amount = db.Column('tutar', db.Float, nullable=False)
    type = db.Column('tur', db.String(10), nullable=False)  # 'gelir' veya 'gider'
    day_of_month = db.Column('ay_gunu', db.Integer, nullable=False, default=1)
    is_active = db.Column('aktif', db.Boolean, default=True)
    last_applied_month = db.Column('son_uygulanan_ay', db.String(7), nullable=True)  # YYYY-MM
    category_id = db.Column('kategori_id', db.Integer, db.ForeignKey('gider_kategorileri.id'), nullable=True)
    debt_id = db.Column('borc_id', db.Integer, db.ForeignKey('borclar.id'), nullable=True)
    unit_currency = db.Column('birim_para', db.String(10), nullable=True)  # TRY / USD / GAU
    unit_grams = db.Column('birim_gram', db.Float, nullable=True)
    start_month = db.Column('baslangic_ayi', db.String(7), nullable=True)  # YYYY-MM
    end_month = db.Column('bitis_ayi', db.String(7), nullable=True)  # YYYY-MM
    order_index = db.Column('sira', db.Integer, default=0)

class ExpenseCategory(db.Model):
    __tablename__ = 'gider_kategorileri'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column('ad', db.String(100), nullable=False)
    type = db.Column('tur', db.String(10), default='gider')  # gider / gelir
    created_at = db.Column('olusturma_tarihi', db.Date, default=date.today)

class Saving(db.Model):
    __tablename__ = 'birikimler'
    id = db.Column(db.Integer, primary_key=True)
    month_id = db.Column('ay_id', db.Integer, db.ForeignKey('aylar.id'), nullable=False)
    currency = db.Column('para_birimi', db.String(10), nullable=False)  # USD / GAU
    tl_amount = db.Column('tl_tutar', db.Float, nullable=False, default=0.0)
    unit_amount = db.Column('birim_miktar', db.Float, nullable=False, default=0.0)  # USD miktarı veya gram
    purchase_rate = db.Column('alis_kuru', db.Float, nullable=True)  # USD için kur
    gold_type = db.Column('altin_cinsi', db.String(50), nullable=True)
    date = db.Column('tarih', db.Date, default=date.today)

class AuthState(db.Model):
    __tablename__ = 'auth_state'
    id = db.Column(db.Integer, primary_key=True)
    failed_attempts = db.Column(db.Integer, default=0)
    lock_until = db.Column(db.DateTime, nullable=True)

# ---------- AUTH SETTINGS ----------
AUTH_IDLE_MINUTES = 15
AUTH_MAX_ATTEMPTS = 5
AUTH_LOCKOUT_MINUTES = 30

def get_auth_state():
    state = AuthState.query.get(1)
    if not state:
        state = AuthState(id=1, failed_attempts=0, lock_until=None)
        db.session.add(state)
        db.session.commit()
    return state

def get_lock_remaining_seconds(state, now):
    if not state or not state.lock_until:
        return 0
    if now >= state.lock_until:
        return 0
    return int((state.lock_until - now).total_seconds())

# ---------- HTML TEMPLATE ----------
HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Portföy Yönetimi | Finans Takip</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Ccircle cx='32' cy='32' r='30' fill='%23007aff'/%3E%3Cpath d='M20 24c0-5 4.5-9 10-9s10 4 10 9c0 7-10 9-10 13' fill='none' stroke='%23fff' stroke-width='4' stroke-linecap='round'/%3E%3Ccircle cx='32' cy='44' r='3' fill='%23fff'/%3E%3C/svg%3E" />
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
    :root {
        --bg-primary: #f5f5f7;
        --bg-secondary: #ffffff;
        --bg-sidebar: #f8f9fa;
        --bg-rail: #f2f2f7;
        --text-primary: #1d1d1f;
        --text-secondary: #86868b;
        --text-tertiary: #a1a1a6;
        --border-light: #e5e5e7;
        --border-medium: #d2d2d7;
        --accent-blue: #007aff;
        --accent-green: #34c759;
        --accent-red: #ff3b30;
        --accent-orange: #ff9500;
        --accent-yellow: #ffcc00;
        --shadow-sm: 0 1px 3px rgba(0,0,0,0.05);
        --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
        --shadow-lg: 0 8px 24px rgba(0,0,0,0.1);
        --radius-sm: 8px;
        --radius-md: 12px;
        --radius-lg: 18px;
        --spacing-xs: 4px;
        --spacing-sm: 8px;
        --spacing-md: 16px;
        --spacing-lg: 24px;
        --spacing-xl: 32px;
    }

    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        -webkit-tap-highlight-color: transparent;
    }

    html, body {
        height: 100%;
        width: 100%;
        overflow-x: hidden;
        -webkit-overflow-scrolling: touch;
    }

    body {
        background-color: var(--bg-primary);
        color: var(--text-primary);
        font-size: 14px;
        line-height: 1.5;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        overflow-y: auto;
        overflow-x: hidden;
        position: relative;
    }

    /* Mobil için üst menü */
    .mobile-header {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border-light);
        padding: 12px 16px;
        z-index: 1000;
        box-shadow: var(--shadow-sm);
        align-items: center;
        height: 60px;
    }

    .mobile-menu-btn {
        background: none;
        border: none;
        font-size: 24px;
        color: var(--text-primary);
        cursor: pointer;
        padding: 8px;
        margin-right: 12px;
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: var(--radius-sm);
        transition: background-color 0.2s ease;
    }

    .mobile-menu-btn:active {
        background-color: var(--bg-rail);
    }

    .mobile-title {
        font-size: 18px;
        font-weight: 700;
        color: var(--text-primary);
        flex: 1;
        text-align: left;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    /* .mobile-month-selector kaldırıldı */

    .app-container {
        display: flex;
        min-height: 100vh;
        width: 100%;
        padding-top: 0;
    }

    /* Sol Sidebar - Masaüstü */
    .sidebar {
        width: 240px;
        background: var(--bg-sidebar);
        border-right: 1px solid var(--border-light);
        display: flex;
        flex-direction: column;
        flex-shrink: 0;
        padding: var(--spacing-lg);
        overflow-y: auto;
        position: relative;
        height: 100vh;
        z-index: 100;
    }

    .sidebar-header {
        margin-bottom: var(--spacing-xl);
    }

    .app-title {
        font-size: 20px;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: var(--spacing-xs);
    }

    .year-subtitle {
        font-size: 14px;
        color: var(--text-secondary);
        font-weight: 500;
    }

    .year-select {
        margin-top: var(--spacing-sm);
    }

    .month-list {
        list-style: none;
        margin-bottom: var(--spacing-xl);
    }

    .month-item {
        margin-bottom: var(--spacing-xs);
    }

    .month-link {
        display: block;
        padding: 10px 14px;
        color: var(--text-primary);
        text-decoration: none;
        border-radius: var(--radius-sm);
        font-weight: 500;
        transition: all 0.2s ease;
        border: 1px solid transparent;
    }

    .month-link:hover {
        background: var(--bg-secondary);
        border-color: var(--border-medium);
    }

    .month-link.active {
        background: var(--accent-blue);
        color: white;
        border-color: var(--accent-blue);
    }

    .sidebar-actions {
        margin-top: auto;
        display: flex;
        flex-direction: column;
        gap: var(--spacing-sm);
    }

    /* Mobil Sidebar Overlay */
    .sidebar-overlay {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0,0,0,0.5);
        z-index: 999;
        backdrop-filter: blur(2px);
        animation: fadeIn 0.3s ease;
    }

    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }

    .sidebar-mobile {
        position: fixed;
        top: 0;
        left: -280px;
        width: 280px;
        height: 100vh;
        background: var(--bg-sidebar);
        border-right: 1px solid var(--border-light);
        z-index: 1000;
        transition: left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        overflow-y: auto;
        padding: var(--spacing-lg);
        padding-top: 80px;
        box-shadow: var(--shadow-lg);
    }

    .sidebar-mobile.active {
        left: 0;
    }

    .close-sidebar {
        position: absolute;
        top: 16px;
        right: 16px;
        background: none;
        border: none;
        font-size: 28px;
        color: var(--text-secondary);
        cursor: pointer;
        padding: 8px;
        width: 40px;
        height: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: var(--radius-sm);
        transition: background-color 0.2s ease;
    }

    .close-sidebar:active {
        background-color: var(--bg-rail);
    }

    /* Ana İçerik */
    .main-content {
        flex: 1;
        padding: var(--spacing-lg);
        overflow-y: auto;
        overflow-x: hidden;
        display: flex;
        flex-direction: column;
        background: var(--bg-primary);
        min-height: 100vh;
        -webkit-overflow-scrolling: touch;
        width: 100%;
    }

    .content-header {
        margin-bottom: var(--spacing-xl);
        margin-top: 0;
    }

    .month-title {
        font-size: 28px;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: var(--spacing-sm);
    }

    .month-stats {
        display: flex;
        gap: var(--spacing-lg);
        font-size: 14px;
        color: var(--text-secondary);
    }

    /* Tab Navigasyon */
    .tab-navigation {
        display: flex;
        gap: var(--spacing-xs);
        margin-bottom: var(--spacing-xl);
        border-bottom: 1px solid var(--border-light);
        padding-bottom: var(--spacing-xs);
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
        position: sticky;
        top: 0;
        background: var(--bg-primary);
        z-index: 50;
        padding-top: 8px;
    }

    .tab-navigation::-webkit-scrollbar {
        display: none;
    }

    .tab-btn {
        padding: 10px 20px;
        background: none;
        border: none;
        color: var(--text-secondary);
        font-weight: 500;
        cursor: pointer;
        border-radius: var(--radius-md);
        transition: all 0.2s ease;
        position: relative;
        white-space: nowrap;
        flex-shrink: 0;
        touch-action: manipulation;
        font-size: 14px;
    }

    .tab-btn:active {
        transform: scale(0.95);
    }

    .tab-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 18px;
        padding: 0 6px;
        margin-left: 8px;
        font-size: 11px;
        font-weight: 700;
        color: #fff;
        background: var(--accent-red);
        border-radius: 999px;
    }

    .tab-btn:hover {
        background: var(--bg-rail);
        color: var(--text-primary);
    }

    .tab-btn.active {
        color: var(--accent-blue);
        background: rgba(0, 122, 255, 0.1);
    }

    .tab-btn.active::after {
        content: '';
        position: absolute;
        bottom: -9px;
        left: 0;
        right: 0;
        height: 2px;
        background: var(--accent-blue);
        border-radius: 1px;
    }

    .badge-overdue {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        background: rgba(255, 59, 48, 0.12);
        color: var(--accent-red);
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        margin-left: 8px;
    }

    th.sortable {
        cursor: pointer;
        user-select: none;
        touch-action: manipulation;
    }

    .drag-handle {
        cursor: grab;
        width: 28px;
        text-align: center;
        color: var(--text-tertiary);
        user-select: none;
        font-weight: 700;
    }

    .dragging {
        opacity: 0.6;
    }

    /* Tab Content */
    .tab-content {
        display: none;
        flex: 1;
        overflow-x: hidden;
        width: 100%;
    }

    .tab-content.active {
        display: block;
        animation: fadeIn 0.3s ease;
    }

    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }

    /* Özet Sayfası */
    .summary-cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: var(--spacing-lg);
        margin-bottom: var(--spacing-xl);
    }

    .card {
        background: var(--bg-secondary);
        border-radius: var(--radius-md);
        padding: var(--spacing-lg);
        border: 1px solid var(--border-light);
        transition: all 0.3s ease;
    }

    .overdue-row {
        background: rgba(255, 59, 48, 0.03);
    }

    .card:hover {
        box-shadow: var(--shadow-md);
        transform: translateY(-2px);
    }

    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: var(--spacing-md);
    }

    .card-title {
        font-size: 14px;
        font-weight: 600;
        color: var(--text-secondary);
    }

    .card-icon {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 20px;
    }

    .income-card .card-icon {
        background: rgba(52, 199, 89, 0.1);
        color: var(--accent-green);
    }

    .expense-card .card-icon {
        background: rgba(255, 59, 48, 0.1);
        color: var(--accent-red);
    }

    .balance-card .card-icon {
        background: rgba(0, 122, 255, 0.1);
        color: var(--accent-blue);
    }

    .card-amount {
        font-size: 32px;
        font-weight: 700;
        margin-bottom: var(--spacing-xs);
    }

    .income-card .card-amount {
        color: var(--accent-green);
    }

    .expense-card .card-amount {
        color: var(--accent-red);
    }

    .balance-card .card-amount {
        color: var(--accent-blue);
    }

    .card-subtitle {
        font-size: 13px;
        color: var(--text-tertiary);
    }

    /* Tablolar */
    .tables-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--spacing-xl);
        margin-bottom: var(--spacing-xl);
    }

    .table-container {
        background: var(--bg-secondary);
        border-radius: var(--radius-md);
        border: 1px solid var(--border-light);
        overflow: hidden;
        width: 100%;
        display: flex;
        flex-direction: column;
    }

    .table-header {
        padding: var(--spacing-lg);
        border-bottom: 1px solid var(--border-light);
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
    }

    .table-header-left {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .header-icon-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 34px;
        height: 34px;
        border-radius: 10px;
        border: 1px solid var(--border-light);
        background: var(--bg-rail);
        color: var(--text-primary);
        cursor: pointer;
        transition: transform 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }

    .header-icon-btn:hover {
        background: #e9e9ee;
        box-shadow: var(--shadow-sm);
        transform: translateY(-1px);
    }

    .header-icon-btn:active {
        transform: translateY(0);
        box-shadow: none;
    }

    .table-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--text-primary);
    }

    .table-scroll-wrapper {
        overflow-x: auto;
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
        width: 100%;
        max-width: 100%;
        max-height: calc(100vh - 260px);
    }

    .rates-table-wrapper {
        overflow-y: hidden;
    }

    .rates-table-wrapper #rates-table thead,
    .rates-table-wrapper #rates-table tbody tr {
        display: table;
        width: 100%;
        table-layout: fixed;
    }

    .rates-table-wrapper #rates-table tbody {
        display: block;
        max-height: calc(100vh - 720px);
        overflow-y: auto;
    }

    .rate-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        justify-content: flex-end;
    }

    .rate-actions .btn {
        padding: 6px 10px;
        font-size: 12px;
    }

    .rate-icon-btn {
        width: 30px;
        height: 30px;
        padding: 0;
        border-radius: 8px;
        justify-content: center;
    }

    .rate-icon-btn i {
        font-size: 13px;
    }


    .rate-actions .btn {
        padding: 6px 10px;
        font-size: 12px;
    }

    table {
        width: 100%;
        border-collapse: collapse;
        min-width: 600px;
    }

    th {
        text-align: left;
        padding: 10px var(--spacing-md);
        background: var(--bg-rail);
        font-weight: 600;
        color: var(--text-secondary);
        font-size: 12px;
        border-bottom: 1px solid var(--border-light);
        white-space: nowrap;
    }

    td {
        padding: 10px var(--spacing-md);
        border-bottom: 1px solid var(--border-light);
        color: var(--text-primary);
        font-size: 12px;
        white-space: nowrap;
    }

    tr:last-child td {
        border-bottom: none;
    }

    tr:hover {
        background: var(--bg-rail);
    }

    /* Butonlar */
    .btn {
        padding: 8px 16px;
        border-radius: var(--radius-sm);
        font-weight: 500;
        font-size: 13px;
        border: none;
        cursor: pointer;
        transition: all 0.2s ease;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        touch-action: manipulation;
        white-space: nowrap;
    }

    .btn:active {
        transform: scale(0.98);
    }

    .btn-sm {
        padding: 6px 12px;
        font-size: 12px;
    }

    .btn-primary {
        background: var(--accent-blue);
        color: white;
    }

    .btn-primary:hover {
        background: #0062cc;
        box-shadow: var(--shadow-sm);
    }

    .btn-success {
        background: var(--accent-green);
        color: white;
    }

    .btn-success:hover {
        background: #2db350;
        box-shadow: var(--shadow-sm);
    }

    .btn-danger {
        background: var(--accent-red);
        color: white;
    }

    .btn-danger:hover {
        background: #e03530;
        box-shadow: var(--shadow-sm);
    }

    .btn-warning {
        background: var(--accent-orange);
        color: white;
    }

    .btn-secondary {
        background: var(--bg-rail);
        color: var(--text-primary);
        border: 1px solid var(--border-medium);
    }

    .btn-secondary:hover {
        background: var(--border-light);
    }

    /* Modal */
    .modal {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0,0,0,0.5);
        backdrop-filter: blur(4px);
        z-index: 1100;
        align-items: center;
        justify-content: center;
        padding: 20px;
        overflow-y: auto;
        -webkit-overflow-scrolling: touch;
    }

    .modal-content {
        background: var(--bg-secondary);
        border-radius: var(--radius-lg);
        padding: var(--spacing-xl);
        max-width: 500px;
        width: 100%;
        max-height: 90vh;
        overflow-y: auto;
        box-shadow: var(--shadow-lg);
        animation: modalSlide 0.3s ease;
        -webkit-overflow-scrolling: touch;
    }

    @keyframes modalSlide {
        from { opacity: 0; transform: translateY(-20px); }
        to { opacity: 1; transform: translateY(0); }
    }

    .modal-header {
        margin-bottom: var(--spacing-lg);
    }

    .modal-title {
        font-size: 20px;
        font-weight: 700;
        color: var(--text-primary);
    }

    .form-group {
        margin-bottom: var(--spacing-lg);
    }

    .form-label {
        display: block;
        margin-bottom: var(--spacing-xs);
        font-weight: 500;
        color: var(--text-primary);
    }

    .form-control {
        width: 100%;
        padding: 10px 14px;
        border: 1px solid var(--border-medium);
        border-radius: var(--radius-sm);
        background: var(--bg-secondary);
        color: var(--text-primary);
        font-size: 14px;
        transition: all 0.2s ease;
        -webkit-appearance: none;
        -moz-appearance: none;
        appearance: none;
    }

    .form-control:focus {
        outline: none;
        border-color: var(--accent-blue);
        box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.1);
    }

    .form-actions {
        display: flex;
        gap: var(--spacing-sm);
        margin-top: var(--spacing-xl);
    }

    /* Birikimler */
    .savings-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: var(--spacing-xl);
    }

    .savings-card {
        background: var(--bg-secondary);
        border-radius: var(--radius-md);
        padding: var(--spacing-lg);
        border: 1px solid var(--border-light);
    }

    /* Döviz */
    .currency-cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: var(--spacing-lg);
        margin-bottom: var(--spacing-xl);
    }

    .currency-card {
        background: var(--bg-secondary);
        border-radius: var(--radius-md);
        padding: var(--spacing-lg);
        border: 1px solid var(--border-light);
        text-align: center;
    }

    .currency-rate {
        font-size: 32px;
        font-weight: 700;
        margin: var(--spacing-md) 0;
    }

    /* Responsive Design */

    /* iPad ve Tablet */
    @media (max-width: 1024px) {
        .app-container {
            flex-direction: column;
            padding-top: 60px;
        }

        .mobile-header {
            display: flex;
        }

        .sidebar {
            display: none;
        }

        .sidebar-mobile {
            display: block;
        }

        .main-content {
            padding: var(--spacing-md);
            padding-top: 16px;
            width: 100%;
        }

        .content-header {
            margin-top: 0;
            margin-bottom: var(--spacing-lg);
        }

        .month-title {
            font-size: 22px;
            margin-bottom: 8px;
        }

        .month-stats {
            flex-direction: column;
            gap: 4px;
            font-size: 13px;
        }

        /* .mobile-month-selector kaldırıldı */

        .tables-grid {
            grid-template-columns: 1fr;
            gap: var(--spacing-lg);
        }

        .summary-cards {
            grid-template-columns: 1fr;
            gap: var(--spacing-md);
        }

        .tab-navigation {
            position: sticky;
            top: 110px;
            z-index: 80;
            background: var(--bg-primary);
            margin-bottom: var(--spacing-lg);
            padding: 8px 0;
        }

        .tab-content {
            margin-top: 0;
        }
    }

    /* iPhone 16 Pro/15 Pro ve küçük cihazlar */
    @media (max-width: 428px) {
        /* Ensure mobile header and button are on top */
        .mobile-header {
            position: relative;
            z-index: 1100;
        }

        .mobile-menu-btn {
            position: relative;
            z-index: 1110;
        }
        body {
            font-size: 13px;
        }

        .mobile-header {
            padding: 10px 12px;
            height: 56px;
        }

        .mobile-menu-btn {
            font-size: 22px;
            width: 36px;
            height: 36px;
        }

        .mobile-title {
            font-size: 16px;
        }

        .main-content {
            padding: 12px;
            padding-top: 12px;
        }

        .month-title {
            font-size: 20px;
            display: block;
        }

        /* .mobile-month-selector kaldırıldı */

        .tab-navigation {
            top: 98px;
        }

        .tab-btn {
            padding: 8px 16px;
            font-size: 13px;
        }

        .summary-cards {
            margin-bottom: var(--spacing-md);
        }

        .card {
            padding: var(--spacing-md);
        }

        .card-amount {
            font-size: 24px;
        }

        .card-icon {
            width: 32px;
            height: 32px;
            font-size: 16px;
        }

        .tables-grid {
            gap: var(--spacing-md);
            margin-bottom: var(--spacing-md);
        }

        .table-header {
            padding: var(--spacing-md);
            flex-direction: column;
            align-items: flex-start;
            gap: var(--spacing-sm);
        }

        .table-title {
            font-size: 15px;
        }

        .btn {
            padding: 6px 12px;
            font-size: 12px;
        }

        .btn-sm {
            padding: 4px 8px;
            font-size: 11px;
        }

        .modal-content {
            padding: var(--spacing-lg);
            width: 95%;
            max-height: 85vh;
            margin: 0 auto;
        }

        .form-group {
            margin-bottom: var(--spacing-md);
        }

        .form-control {
            padding: 8px 12px;
            font-size: 13px;
        }

        .currency-cards {
            grid-template-columns: repeat(2, 1fr);
            gap: var(--spacing-sm);
            margin-bottom: var(--spacing-md);
        }

        .currency-card {
            padding: var(--spacing-md);
        }

        .currency-rate {
            font-size: 24px;
        }

        th, td {
            padding: 8px var(--spacing-sm);
            font-size: 11px;
        }

        .drag-handle {
            width: 24px;
        }

        .tab-badge {
            min-width: 16px;
            padding: 0 4px;
            font-size: 10px;
        }

        .month-stats {
            font-size: 12px;
        }
    }

    /* iPhone XR */
    @media (max-width: 414px) {
        .currency-cards {
            grid-template-columns: 1fr;
        }

        .modal-content {
            padding: var(--spacing-md);
        }

        .form-actions {
            flex-direction: column;
            gap: var(--spacing-xs);
        }

        .form-actions .btn {
            width: 100%;
            justify-content: center;
        }

        .table-scroll-wrapper {
            margin-left: -12px;
            margin-right: -12px;
            padding-left: 12px;
            padding-right: 12px;
        }

        .sidebar-mobile {
            width: 260px;
            padding: var(--spacing-md);
            padding-top: 70px;
        }
    }

    /* Küçük cihazlar (375px ve altı) */
    @media (max-width: 375px) {
        .month-title {
            font-size: 18px;
        }

        .card-amount {
            font-size: 20px;
        }

        .tab-btn {
            padding: 6px 12px;
            font-size: 12px;
        }

        .tab-badge {
            min-width: 14px;
            font-size: 9px;
        }

        .modal-content {
            padding: var(--spacing-sm);
        }

        .btn {
            padding: 5px 10px;
            font-size: 11px;
        }

        .btn-sm {
            padding: 3px 6px;
            font-size: 10px;
        }

        .table-header .btn {
            width: 100%;
            justify-content: center;
            margin-top: 4px;
        }

        .sidebar-mobile {
            width: 240px;
        }
    }

    /* Yatay mod */
    @media (max-height: 600px) and (orientation: landscape) {
        .sidebar-mobile {
            padding-top: 60px;
        }

        .mobile-header {
            height: 50px;
        }

        /* .mobile-month-selector kaldırıldı */

        .tab-navigation {
            top: 88px;
        }
    }

    /* Yüksek çözünürlüklü ekranlar */
    @media (min-width: 1440px) {
        .sidebar {
            width: 280px;
        }

        .summary-cards {
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        }

        .savings-grid {
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
        }
    }

    /* Mobil özel stiller */
    @media (hover: none) and (pointer: coarse) {
        .btn:hover,
        .tab-btn:hover,
        .card:hover {
            transform: none;
        }

        .btn:active,
        .tab-btn:active,
        .month-link:active {
            transform: scale(0.95);
            opacity: 0.8;
        }

        .month-link:hover {
            background: none;
        }

        .month-link.active {
            background: var(--accent-blue);
            color: white;
        }

        tr:hover {
            background: none;
        }

        tr:active {
            background: var(--bg-rail);
        }
    }

    /* Utility */
    .status-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 500;
    }

    .status-active {
        background: rgba(52, 199, 89, 0.1);
        color: var(--accent-green);
    }

    .status-inactive {
        background: rgba(255, 59, 48, 0.1);
        color: var(--accent-red);
    }

    .text-success {
        color: var(--accent-green);
    }

    .text-danger {
        color: var(--accent-red);
    }

    .text-warning {
        color: var(--accent-orange);
    }

    .text-info {
        color: var(--accent-blue);
    }

    .mb-2 { margin-bottom: var(--spacing-sm); }
    .mb-3 { margin-bottom: var(--spacing-md); }
    .mb-4 { margin-bottom: var(--spacing-lg); }
    .mb-5 { margin-bottom: var(--spacing-xl); }

    .mt-2 { margin-top: var(--spacing-sm); }
    .mt-3 { margin-top: var(--spacing-md); }
    .mt-4 { margin-top: var(--spacing-lg); }
    .mt-5 { margin-top: var(--spacing-xl); }

    /* Login overlay */
    .auth-overlay {
        position: fixed;
        inset: 0;
        background: rgba(245, 245, 247, 0.92);
        backdrop-filter: blur(6px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
    }

    .auth-overlay.hidden {
        display: none;
    }

    .auth-card {
        background: var(--bg-secondary);
        padding: 24px;
        border-radius: var(--radius-lg);
        box-shadow: var(--shadow-lg);
        width: 90%;
        max-width: 360px;
        text-align: center;
    }

    .auth-title {
        font-size: 20px;
        font-weight: 700;
        margin-bottom: 6px;
    }

    .auth-subtitle {
        font-size: 13px;
        color: var(--text-secondary);
        margin-bottom: 14px;
    }

    .auth-message {
        min-height: 18px;
        font-size: 12px;
        margin-bottom: 10px;
        color: var(--text-secondary);
    }

    .auth-message.error {
        color: var(--accent-red);
    }

    .auth-input {
        width: 100%;
        padding: 10px 12px;
        border-radius: var(--radius-sm);
        border: 1px solid var(--border-medium);
        margin-bottom: 12px;
        font-size: 13px;
    }
</style>
</head>
<body>
    <!-- Mobile Header (Sadece mobilde görünür) -->
    <div class="mobile-header">
        <button type="button" class="mobile-menu-btn">☰</button>
        <div class="mobile-title">Portföy</div>
    </div>

    <!-- Sidebar Overlay -->
    <div class="sidebar-overlay"></div>

    <!-- Mobile Sidebar -->
    <div class="sidebar-mobile">
        <button class="close-sidebar">×</button>
        <div class="sidebar-header">
            <div class="app-title">Portföy</div>

            <div class="year-select">
                <select class="form-control" onchange="changeYear(this.value)">
                    {% for y in years %}
                    <option value="{{ y }}" {% if y == current_year %}selected{% endif %}>{{ y }}</option>
                    {% endfor %}
                </select>
            </div>
        </div>

        <ul class="month-list">
            {% for month in months %}
            <li class="month-item">
                <a href="/month/{{ month.id }}" class="month-link {% if active_month and month.id == active_month.id %}active{% endif %}">
                    {{ month.name }}
                </a>
            </li>
            {% endfor %}
        </ul>

        <div class="sidebar-actions">
            <button class="btn btn-secondary" onclick="logoutUser()">
                <i class="fa fa-right-from-bracket"></i> Çıkış Yap
            </button>
        </div>
    </div>

    <div class="app-container">
        <!-- Sol Sidebar - Desktop Only -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="app-title">Portföy</div>

                <div class="year-select">
                    <select class="form-control" onchange="changeYear(this.value)">
                        {% for y in years %}
                        <option value="{{ y }}" {% if y == current_year %}selected{% endif %}>{{ y }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>

            <ul class="month-list">
                {% for month in months %}
                <li class="month-item">
                    <a href="/month/{{ month.id }}" class="month-link {% if active_month and month.id == active_month.id %}active{% endif %}">
                        {{ month.name }}
                    </a>
                </li>
                {% endfor %}
            </ul>

            <div class="sidebar-actions">
                <button class="btn btn-secondary" onclick="logoutUser()">
                    <i class="fa fa-right-from-bracket"></i> Çıkış Yap
                </button>
            </div>
        </div>

        <!-- Ana İçerik -->
        <div class="main-content">
            <div class="content-header">
                <h1 class="month-title">{{ active_month.name }} {{ active_month.year }}</h1>
                <div class="month-stats">
                    <div>Açılış Bakiyesi: {{ active_month.opening_balance|tr_currency }}</div>
                    <div>Kapanış Bakiyesi: {{ active_month.closing_balance|tr_currency }}</div>
                </div>
            </div>

            <!-- Tab Navigasyon -->
            <div class="tab-navigation">
                <button class="tab-btn active" data-tab="overview" onclick="switchTab('overview', event)">Özet</button>
                <button class="tab-btn" data-tab="debts" onclick="switchTab('debts', event)">
                    Borçlar
                    {% if overdue_debt_count > 0 %}
                    <span class="tab-badge" title="{{ overdue_debt_names }}">{{ overdue_debt_count }}</span>
                    {% endif %}
                </button>
                <button class="tab-btn" data-tab="savings" onclick="switchTab('savings', event)">Birikimler</button>
                <button class="tab-btn" data-tab="currencies" onclick="switchTab('currencies', event)">Döviz</button>
            </div>

            <!-- Özet Tab -->
            <div id="overview" class="tab-content active">
                <div class="summary-cards">
                    <div class="card income-card">
                        <div class="card-header">
                            <div class="card-title">Toplam Gelir</div>
                            <div class="card-icon">📈</div>
                        </div>
                        <div class="card-amount">{{ total_income|tr_currency }}</div>
                        <div class="card-subtitle">Bu ayki toplam gelir</div>
                    </div>

                    <div class="card expense-card">
                        <div class="card-header">
                            <div class="card-title">Toplam Gider</div>
                            <div class="card-icon">📉</div>
                        </div>
                        <div class="card-amount">{{ total_expense|tr_currency }}</div>
                        <div class="card-subtitle">Bu ayki toplam gider</div>
                    </div>

                    <div class="card balance-card">
                        <div class="card-header">
                            <div class="card-title">Kalan / Birikim</div>
                            <div class="card-icon">💰</div>
                        </div>
                        <div class="card-amount">{{ balance|tr_currency }}</div>
                        <div class="card-subtitle">Net kazanç / tasarruf</div>
                    </div>
                </div>

                <div class="tables-grid">
                    <div class="table-container">
                        <div class="table-header">
                            <div class="table-title">Gelirler</div>
                            <button class="btn btn-success btn-sm" onclick="showModal('new-transaction-modal', 'gelir')">
                                <span>+</span> Yeni Gelir
                            </button>
                        </div>
                        <div class="table-scroll-wrapper">
                            <table id="income-table">
                                <thead>
                                    <tr>
                                        <th style="width:32px;"></th>
                                        <th class="sortable" onclick="sortTable('income-table',1,'text')">Açıklama</th>
                                        <th class="sortable" onclick="sortTable('income-table',2,'currency')">Tutar</th>
                                        <th class="sortable" onclick="sortTable('income-table',3,'date')">Tarih</th>
                                        <th>İşlem</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for transaction in transactions if transaction.type == 'gelir' and transaction.title != 'Devreden Bakiye' %}
                                    <tr data-transaction-id="{{ transaction.id }}">
                                        <td class="drag-handle" draggable="true">⋮⋮</td>
                                        <td>{{ transaction.title }}</td>
                                        <td>{{ transaction.amount|tr_currency }}</td>
                                        <td>{{ transaction.date.strftime('%d.%m.%Y') }}</td>
                                        <td>
                                            <button class="btn btn-warning btn-sm" onclick="editTransaction({{ transaction.id }})" title="Düzenle"><i class="fa fa-edit"></i></button>
                                            <button class="btn btn-danger btn-sm" onclick="deleteTransaction({{ transaction.id }})" title="Sil"><i class="fa fa-trash"></i></button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="table-container">
                        <div class="table-header">
                            <div class="table-title">Giderler</div>
                            <button class="btn btn-danger btn-sm" onclick="showModal('new-transaction-modal', 'gider')">
                                <span>+</span> Yeni Gider
                            </button>
                        </div>
                        <div class="table-scroll-wrapper">
                            <table id="expense-table">
                                <thead>
                                    <tr>
                                        <th style="width:32px;"></th>
                                        <th class="sortable" onclick="sortTable('expense-table',1,'text')">Açıklama</th>
                                        <th class="sortable" onclick="sortTable('expense-table',2,'currency')">Tutar</th>
                                        <th class="sortable" onclick="sortTable('expense-table',3,'date')">Tarih</th>
                                        <th>Detaylar</th>
                                        <th>İşlem</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for transaction in transactions if transaction.type == 'gider' and transaction.title != 'Devreden Bakiye' %}
                                    <tr data-transaction-id="{{ transaction.id }}">
                                        <td class="drag-handle" draggable="true">⋮⋮</td>
                                        <td>{{ transaction.title }}</td>
                                        <td>{{ transaction.amount|tr_currency }}</td>
                                        <td>{{ transaction.date.strftime('%d.%m.%Y') }}</td>
                                        <td>
                                            {% if transaction.purchase_rate %}
                                                <small class="text-info">💵 Kur: {{ transaction.purchase_rate|tr_currency }}</small>
                                            {% elif transaction.gold_type %}
                                                <small class="text-warning">🏆 {{ transaction.gold_type }} - {{ transaction.gold_grams }}g</small>
                                            {% else %}
                                                -
                                            {% endif %}
                                        </td>
                                        <td>
                                            <button class="btn btn-warning btn-sm" onclick="editTransaction({{ transaction.id }})" title="Düzenle"><i class="fa fa-edit"></i></button>
                                            <button class="btn btn-danger btn-sm" onclick="deleteTransaction({{ transaction.id }})" title="Sil"><i class="fa fa-trash"></i></button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <div class="table-container mt-4">
                    <div class="table-header" style="cursor: pointer;" onclick="toggleRecurringTable()">
                        <div class="table-header-left">
                            <button class="header-icon-btn" onclick="openRecurringModal(event)" title="Düzenli işlem ekle" aria-label="Düzenli işlem ekle">
                                <i class="fa fa-calendar-plus"></i>
                            </button>
                            <div class="table-title">Düzenli Ödemeler</div>
                        </div>
                        <div style="display:flex; align-items:center; gap:8px; color: var(--text-secondary);">
                            <small>Otomatik olarak ilgili aylara ekleniyor</small>
                            <span id="recurring-toggle-icon">▼</span>
                        </div>
                    </div>
                    <div id="recurring-table-wrapper" style="display:none;">
                        <div class="table-scroll-wrapper">
                            <table id="recurring-table">
                                <thead>
                                    <tr>
                                        <th style="width:32px;"></th>
                                        <th>Açıklama</th>
                                        <th>Tutar</th>
                                        <th>Tür</th>
                                        <th>Gün</th>
                                        <th>Durum</th>
                                        <th>İşlem</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for payment in recurring_payments %}
                                    <tr data-recurring-id="{{ payment.id }}">
                                        <td class="drag-handle" draggable="true">⋮⋮</td>
                                        <td>{{ payment.name }}</td>
                                        <td>{{ payment.amount|tr_currency }}</td>
                                        <td>{{ payment.type }}</td>
                                        <td>Ayın {{ payment.day_of_month }}'i</td>
                                        <td>
                                            <label style="display:inline-flex; align-items:center; gap:6px;">
                                                <input type="checkbox" onchange="toggleRecurringActive({{ payment.id }}, this.checked, this)" {% if payment.is_active %}checked{% endif %}>
                                                <span class="status-badge {% if payment.is_active %}status-active{% else %}status-inactive{% endif %}">
                                                    {% if payment.is_active %}Aktif{% else %}Pasif{% endif %}
                                                </span>
                                            </label>
                                        </td>
                                        <td>
                                            <button class="btn btn-warning btn-sm" onclick="editRecurring({{ payment.id }})" title="Düzenle"><i class="fa fa-edit"></i></button>
                                            <button class="btn btn-danger btn-sm" onclick="deleteRecurring({{ payment.id }})" title="Sil"><i class="fa fa-trash"></i></button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Borçlar Tab -->
            <div id="debts" class="tab-content">
                <div class="table-header mb-4">
                    <div class="table-title">Borç Takibi</div>
                    <button class="btn btn-danger" onclick="openDebtModal()">
                        <span>+</span> Borç Ekle
                    </button>
                </div>

                <div class="table-container">
                    <div class="table-scroll-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>Borç Adı</th>
                                    <th>Tür</th>
                                    <th>Toplam</th>
                                    <th>Kalan</th>
                                    <th>Ödenen</th>
                                    <th>Taksit</th>
                                    <th>Aylık</th>
                                    <th>Gün</th>
                                    <th>İşlem</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for debt in debts %}
                                <tr class="{% if debt in overdue_debts %}overdue-row{% endif %}">
                                    <td>
                                        {{ debt.name }}
                                        {% if debt in overdue_debts %}
                                            <span class="badge-overdue">Gecikmiş</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.currency == 'USD' %}
                                            <span class="status-badge" style="background: rgba(52, 152, 219, 0.1); color: #3498db;">Dolar</span>
                                        {% elif debt.currency == 'GAU' %}
                                            <span class="status-badge" style="background: rgba(243, 156, 18, 0.1); color: #f39c12;">Altın{% if debt.gold_type %} - {{ debt.gold_type }}{% endif %}</span>
                                        {% else %}
                                            <span class="status-badge" style="background: rgba(39, 174, 96, 0.1); color: #27ae60;">TL</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.currency == 'GAU' %}
                                            {{ debt.total_amount }} gr
                                            <small style="display: block; color: var(--text-tertiary); font-size: 11px;">
                                                ≈{{ (debt.total_amount * rates.GAU)|tr_currency }}
                                            </small>
                                        {% elif debt.currency == 'USD' %}
                                            {{ debt.total_amount|usd_currency }}
                                            <small style="display: block; color: var(--text-tertiary); font-size: 11px;">
                                                ≈{{ (debt.total_amount * rates.USD)|tr_currency }}
                                            </small>
                                        {% else %}
                                            {{ debt.total_amount|tr_currency }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.currency == 'GAU' %}
                                            {{ debt.remaining_amount }} gr
                                            <small style="display: block; color: var(--accent-red); font-size: 11px; font-weight: 600;">
                                                ≈{{ (debt.remaining_amount * rates.GAU)|tr_currency }}
                                            </small>
                                        {% elif debt.currency == 'USD' %}
                                            {{ debt.remaining_amount|usd_currency }}
                                            <small style="display: block; color: var(--accent-red); font-size: 11px; font-weight: 600;">
                                                ≈{{ (debt.remaining_amount * rates.USD)|tr_currency }}
                                            </small>
                                        {% else %}
                                            {{ debt.remaining_amount|tr_currency }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% set paid_amount = debt.paid_amount_calculated if debt.paid_amount_calculated is defined else (debt.total_amount - debt.remaining_amount) %}
                                        {% if paid_amount < 0 %}{% set paid_amount = 0 %}{% endif %}
                                        {% if paid_amount > 0 %}
                                            {% if debt.currency == 'GAU' %}
                                                {{ paid_amount }} gr
                                                <small style="display: block; color: var(--accent-green); font-size: 11px;">
                                                    ≈{{ (paid_amount * rates.GAU)|tr_currency }}
                                                </small>
                                            {% elif debt.currency == 'USD' %}
                                                {{ paid_amount|usd_currency }}
                                                <small style="display: block; color: var(--accent-green); font-size: 11px;">
                                                    ≈{{ (paid_amount * rates.USD)|tr_currency }}
                                                </small>
                                            {% else %}
                                                {{ paid_amount|tr_currency }}
                                            {% endif %}
                                        {% else %}
                                            ₺0,00
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.is_credit and debt.total_installments %}
                                            {{ debt.installments_paid }}/{{ debt.total_installments }}
                                        {% else %}
                                            -
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.is_credit and debt.installment_amount %}
                                            {% if debt.currency == 'GAU' %}
                                                {{ debt.installment_amount }} gr
                                                <small style="display: block; color: var(--text-tertiary); font-size: 11px;">
                                                    ≈{{ (debt.installment_amount * rates.GAU)|tr_currency }}
                                                </small>
                                            {% elif debt.currency == 'USD' %}
                                                {{ debt.installment_amount|usd_currency }}
                                                <small style="display: block; color: var(--text-tertiary); font-size: 11px;">
                                                    ≈{{ (debt.installment_amount * rates.USD)|tr_currency }}
                                                </small>
                                            {% else %}
                                                {{ debt.installment_amount|tr_currency }}
                                            {% endif %}
                                        {% else %}
                                            -
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if debt.is_credit %}
                                            {{ debt.due_day }}
                                        {% else %}
                                            -
                                        {% endif %}
                                    </td>
                                    <td>
                                        <button class="btn btn-primary btn-sm" onclick="payDebt({{ debt.id }}, {{ debt.installment_amount or 0 }})" title="Ödeme"><i class="fa fa-money-bill-wave"></i></button>
                                        <button class="btn btn-warning btn-sm" onclick="editDebt({{ debt.id }})" title="Düzenle"><i class="fa fa-edit"></i></button>
                                        <button class="btn btn-danger btn-sm" onclick="deleteDebt({{ debt.id }})" title="Sil"><i class="fa fa-trash"></i></button>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Birikimler Tab -->
            <div id="savings" class="tab-content">
                <div class="table-container">
                    <div class="table-header">
                        <div class="table-title">Birikimler</div>
                        <button class="btn btn-primary" onclick="toggleSavingForm()">Yeni Birikim</button>
                    </div>
                    <div id="saving-form-container" class="card" style="display:none; padding:16px; margin-bottom:16px;">
                        <form method="POST" action="/add_saving">
                            <input type="hidden" name="saving_id" id="saving-id" value="">
                            <input type="hidden" name="month_id" value="{{ active_month.id }}">
                            <div class="form-group">
                                <label class="form-label">Tür</label>
                                <select name="currency" id="saving-currency" class="form-control" onchange="updateSavingFields()">
                                    <option value="USD">Dolar</option>
                                    <option value="GAU">Altın</option>
                                </select>
                            </div>
                            <div id="saving-usd-fields">
                                <div class="form-group">
                                    <label class="form-label">Alış Kuru</label>
                                    <input type="number" step="0.01" name="purchase_rate" id="saving-usd-rate" class="form-control" placeholder="Örn: 32.50" value="{{ usd_rate }}">
                                </div>
                                <div class="form-group">
                                    <label class="form-label">TL Tutarı</label>
                                    <input type="number" step="0.01" name="tl_amount_usd" id="saving-usd-tl" class="form-control" placeholder="Örn: 10000">
                                </div>
                            </div>
                            <div id="saving-gold-fields" style="display:none;">
                                <div class="form-group">
                                    <label class="form-label">Altın Gram</label>
                                    <input type="number" step="0.01" name="gold_grams" id="saving-gold-grams" class="form-control" placeholder="Örn: 5">
                                </div>
                                <div class="form-group">
                                    <label class="form-label">TL Tutarı</label>
                                    <input type="number" step="0.01" name="tl_amount_gau" id="saving-gold-tl" class="form-control" placeholder="Örn: 5000">
                                </div>
                                <div class="form-group">
                                    <label class="form-label">Altın Cinsi</label>
                                    <input type="text" name="gold_type" id="saving-gold-type" class="form-control" placeholder="Örn: Gram">
                                </div>
                            </div>
                            <div class="form-actions">
                                <button type="submit" class="btn btn-primary">Kaydet</button>
                                <button type="button" class="btn btn-secondary" onclick="toggleSavingForm(false)">Vazgeç</button>
                            </div>
                        </form>
                    </div>
                    <div class="table-scroll-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>Tarih</th>
                                    <th>Ay</th>
                                    <th>Tür</th>
                                    <th>Miktar</th>
                                    <th>TL Karşılığı</th>
                                    <th>Not</th>
                                    <th>İşlem</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for saving in savings %}
                                <tr>
                                    <td>{{ saving.date.strftime('%d.%m.%Y') if saving.date else '' }}</td>
                                    <td>
                                        {% set ay = months_dict[saving.month_id]['name'] if months_dict and saving.month_id in months_dict else '' %}
                                        {% set yil = months_dict[saving.month_id]['year'] if months_dict and saving.month_id in months_dict else '' %}
                                        {{ ay }} {{ yil }}
                                    </td>
                                    <td>
                                        {% if saving.currency == 'USD' %}
                                            <span class="status-badge" style="background: rgba(52, 152, 219, 0.1); color: #3498db;">Dolar</span>
                                        {% elif saving.currency == 'GAU' %}
                                            <span class="status-badge" style="background: rgba(243, 156, 18, 0.1); color: #f39c12;">Altın{% if saving.gold_type %} - {{ saving.gold_type }}{% endif %}</span>
                                        {% else %}
                                            <span class="status-badge" style="background: rgba(88, 86, 214, 0.12); color: #5856d6;">{{ saving.currency }}</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if saving.currency == 'USD' %}
                                            {{ saving.unit_amount|tr_number }} USD
                                            <small style="display:block; color: var(--text-tertiary); font-size: 11px;">
                                                ≈{{ (saving.unit_amount * rates.USD)|tr_currency }}
                                            </small>
                                        {% elif saving.currency == 'GAU' %}
                                            {{ saving.unit_amount|tr_number }} g
                                            <small style="display:block; color: var(--text-tertiary); font-size: 11px;">
                                                ≈{{ (saving.unit_amount * rates.GAU)|tr_currency }}
                                            </small>
                                        {% else %}
                                            {{ saving.unit_amount|tr_number }} {{ saving.currency }}
                                            <small style="display:block; color: var(--text-tertiary); font-size: 11px;">
                                                {% set r = rates_map.get(saving.currency) %}
                                                ≈{{ (saving.unit_amount * r)|tr_currency if r else saving.tl_amount|tr_currency }}
                                            </small>
                                        {% endif %}
                                    </td>
                                    <td>{{ saving.tl_amount|tr_currency }}</td>
                                    <td>
                                        {% if saving.purchase_rate %}
                                            Kur: {{ saving.purchase_rate|tr_currency }}
                                        {% endif %}
                                        {% if saving.currency == 'GAU' and saving.gold_type %}
                                            {{ saving.gold_type }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        <button class="btn btn-warning btn-sm" onclick="editSaving({{ saving.id }})" title="Düzenle"><i class="fa fa-edit"></i></button>
                                        <button class="btn btn-danger btn-sm" onclick="deleteSaving({{ saving.id }})" title="Sil"><i class="fa fa-trash"></i></button>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                            <tfoot>
                                <tr style="background: var(--bg-rail); font-weight: 600;">
                                    <td colspan="4">Tum aylar toplam birikim</td>
                                    <td colspan="3">
                                        {{ total_saving_tl|tr_currency }}
                                    </td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>

                <div class="table-container mt-4">
                    <div class="table-header">
                        <div class="table-title">Toplam Birikim</div>
                    </div>
                    <div class="table-scroll-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>Tür</th>
                                    <th>Birim Toplam</th>
                                    <th>TL Karşılığı</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td>Dolar</td>
                                    <td>{{ total_saving_usd|tr_currency }} USD</td>
                                    <td>{{ (total_saving_usd * rates.USD)|tr_currency if rates.USD else '0' }}</td>
                                </tr>
                                <tr>
                                    <td>Altın</td>
                                    <td>{{ total_saving_gau|tr_currency }} g</td>
                                    <td>{{ (total_saving_gau * rates.GAU)|tr_currency if rates.GAU else '0' }}</td>
                                </tr>
                                {% for code, total in saving_totals.items() if code not in ['USD','GAU'] %}
                                <tr>
                                    <td>{{ code }}</td>
                                    <td>{{ total|tr_number }} {{ code }}</td>
                                    <td>
                                        {% set r = rates_map.get(code) %}
                                        {{ (total * r)|tr_currency if r else '0' }}
                                    </td>
                                </tr>
                                {% endfor %}

                                <tr style="font-weight: 600; background: var(--bg-rail);">
                                    <td>Toplam TL Karşılığı</td>
                                    <td colspan="2">{{ total_saving_tl|tr_currency }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Döviz Tab -->
            <div id="currencies" class="tab-content">
                <div class="currency-cards">
                    <div class="currency-card">
                        <div>💵 Dolar (USD)</div>
                        <div class="currency-rate">{{ usd_rate|tr_currency }}</div>
                        <div style="color: var(--text-secondary); font-size: 13px;">Alış / Satış</div>
                    </div>
                    <div class="currency-card">
                        <div>💶 Euro (EUR)</div>
                        <div class="currency-rate">{{ eur_rate|tr_currency }}</div>
                        <div style="color: var(--text-secondary); font-size: 13px;">Alış / Satış</div>
                    </div>
                    <div class="currency-card">
                        <div>🪩 Gram Altın</div>
                        <div class="currency-rate">{{ gold_rate|tr_currency }}</div>
                        <div style="color: var(--text-secondary); font-size: 13px;">Alış / Satış</div>
                    </div>
                    <div class="currency-card">
                        <div>₿ Bitcoin</div>
                        <div class="currency-rate">{{ btc_rate|tr_currency }}</div>
                        <div style="color: var(--text-secondary); font-size: 13px;">TRY Fiyat?</div>
                    </div>
                </div>

                <div class="table-container">
                    <div class="table-header">
                        <div class="table-title">Tüm Kurlar</div>
                        <div>
                            <input id="rate-search" class="form-control" style="width: 200px;" placeholder="Koda göre ara..." oninput="filterRates()" />
                        </div>
                    </div>
                    <div class="table-scroll-wrapper rates-table-wrapper">
                        <table id="rates-table">
                                                        <thead>
                                <tr>
                                    <th>Kod</th>
                                    <th>Değer (TL)</th>
                                    <th>İşlem</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for item in rates_all %}
                                                                                                                                                                <tr data-saving="{{ saving_totals.get(item.code, 0) or 0 }}" data-debt="{{ debt_totals.get(item.code, 0) or 0 }}">
                                    <td>{{ item.code }}</td>
                                    <td>{{ item.value|tr_currency }}</td>
                                    <td>
                                        <div class="rate-actions">
                                            {% if (saving_totals.get(item.code, 0) or 0) > 0 or (debt_totals.get(item.code, 0) or 0) > 0 %}
                                            <button class="btn btn-danger btn-sm rate-icon-btn" onclick="quickSellUnified('{{ item.code }}', {{ item.value }}, event)" title="Sat"><i class="fa fa-minus"></i></button>
                                            {% endif %}
                                            <button class="btn btn-success btn-sm rate-icon-btn" onclick="quickBuyUnified('{{ item.code }}', {{ item.value }})" title="Al"><i class="fa fa-plus"></i></button>
                                        </div>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div style="text-align: center; margin-top: var(--spacing-lg); color: var(--text-tertiary); font-size: 12px;">
                    Son Güncelleme: {{ last_update }}<br>
                    *Kurlar altin.in sayfasından alınmaktadır
                </div>
            </div>
        </div>
    </div>

    <!-- Modaller -->
    <div id="quick-action-modal" class="modal">
        <div class="modal-content" style="max-width: 360px;">
            <div class="modal-header">
                <div id="quick-action-title" class="modal-title">Hızlı İşlem</div>
            </div>
            <div id="quick-action-body"></div>
            <div class="form-actions" id="quick-action-actions"></div>
        </div>
    </div>

    <div id="new-transaction-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div class="modal-title">Yeni İşlem</div>
            </div>
            <form id="transaction-form" method="POST" action="/add_transaction">
                <input type="hidden" name="month_id" value="{{ active_month.id }}">
                <input type="hidden" name="title" id="transaction-title-hidden" value="">
                <input type="hidden" name="transaction_id" id="transaction-id-hidden" value="">
                <div class="form-group">
                    <label class="form-label">Tür</label>
                    <select id="transaction-type" name="type" class="form-control" required onchange="updateCategoryField(this.value)">
                        <option value="gelir">Gelir</option>
                        <option value="gider">Gider</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Tutar (TL)</label>
                    <input type="number" step="0.01" min="0" name="amount" class="form-control" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Tarih</label>
                    <input type="date" name="date" class="form-control" value="{{ today }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Kategoriler</label>
                    <select id="category-select" name="category_id" class="form-control" onchange="syncTitleWithCategory()" required></select>
                    <button type="button" class="btn btn-secondary mt-2" onclick="toggleCategoryActions()">Kategori işlemleri</button>
                    <div id="category-actions" style="display:none; flex-wrap:wrap; gap:8px; margin-top:8px;">
                        <input type="text" id="new-category-name" class="form-control" placeholder="Yeni kategori adı" style="flex:1;">
                        <button type="button" class="btn btn-secondary" onclick="addCategory()">Ekle</button>
                        <button type="button" class="btn btn-danger" onclick="deleteSelectedCategory()">Sil</button>
                        <button type="button" class="btn btn-warning" onclick="renameSelectedCategory()">Ad Değiştir</button>
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label">
                        <input type="checkbox" name="is_recurring" id="transaction-is-recurring" onchange="toggleTransactionRecurring()"> Düzenli ödeme olarak işaretle
                    </label>
                </div>
                <div id="transaction-recurring-fields" style="display:none;">
                    <div class="form-group">
                        <label class="form-label">Ödeme Günü</label>
                        <input type="number" name="recurring_day" id="transaction-recurring-day" class="form-control" min="1" max="28" value="1">
                    </div>
                    <div class="form-group">
                        <label class="form-label">Başlangıç / Bitiş</label>
                        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                            <input type="month" name="recurring_start" id="transaction-recurring-start" class="form-control">
                            <input type="month" name="recurring_end" id="transaction-recurring-end" class="form-control">
                        </div>
                    </div>
                </div>
                <div class="form-actions">
                    <button type="submit" id="transaction-submit-btn" class="btn btn-primary">Kaydet</button>
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">İptal</button>
                </div>
            </form>
        </div>
    </div>

    <div id="recurring-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div id="recurring-modal-title" class="modal-title">Düzenli İşlem</div>
            </div>
            <form id="recurring-form" method="POST" action="/add_recurring">
                <input type="hidden" name="recurring_id" value="">
                <div class="form-group" style="display: flex; align-items: center; gap: 16px;">
                    <label class="form-label" style="margin-bottom:0;">
                        <input type="checkbox" name="is_active" id="recurring-is-active" value="1"> Aktif
                    </label>
                    <button type="button" class="btn btn-secondary" onclick="toggleRecurringCategoryActions()" style="margin-bottom:0;">Kategori işlemleri</button>
                </div>
                <div id="recurring-category-actions" style="display:none; flex-wrap:wrap; gap:8px; margin-top:8px;">
                    <input type="text" id="recurring-new-category-name" class="form-control" placeholder="Yeni kategori adı" style="flex:1;">
                    <button type="button" class="btn btn-secondary" onclick="addRecurringCategory()">Ekle</button>
                    <button type="button" class="btn btn-danger" onclick="deleteSelectedRecurringCategory()">Sil</button>
                    <button type="button" class="btn btn-warning" onclick="renameSelectedRecurringCategory()">Ad Değiştir</button>
                </div>
                <div class="form-group">
                    <label class="form-label">Tür</label>
                    <select name="type" id="recurring-type" class="form-control" onchange="renderRecurringCategories(this.value)">
                        <option value="gider">Gider</option>
                        <option value="gelir">Gelir</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Kategoriler</label>
                    <select name="category_id" id="recurring-category-select" class="form-control" required></select>
                </div>
                <div class="form-group">
                    <label class="form-label">Tutar (TL)</label>
                    <input type="number" step="0.01" name="amount" class="form-control" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Ay Günü</label>
                    <input type="number" name="day_of_month" min="1" max="28" class="form-control" value="1" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Başlangıç / Bitiş</label>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                        <input type="month" id="recurring-start-month" name="start_month" class="form-control" value="{{ current_year }}-01">
                        <input type="month" id="recurring-end-month" name="end_month" class="form-control" value="{{ current_year }}-12">
                    </div>
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary">Kaydet</button>
                    <button type="button" class="btn btn-secondary" onclick="closeModal('recurring-modal')">İptal</button>
                </div>
            </form>
        </div>
    </div>

    <div id="debt-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div id="debt-modal-title" class="modal-title">Borç Ekle</div>
            </div>
            <form id="debt-form" method="POST" action="/add_debt">
                <input type="hidden" name="debt_id" value="">
                <div class="form-group">
                    <label class="form-label">Borç Adı</label>
                    <input type="text" name="name" class="form-control" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Para Birimi</label>
                    <select name="currency" id="debt-currency" class="form-control" onchange="updateDebtCurrencyFields()">
                        <option value="TRY">TL</option>
                        <option value="USD">USD</option>
                        <option value="GAU">Altın (gram)</option>
                    </select>
                </div>
                <div class="form-group" id="debt-gold-type-row" style="display: none;">
                    <label class="form-label">Altın Cinsi</label>
                    <input type="text" name="gold_type" id="debt-gold-type" class="form-control" placeholder="Örn: 22 Ayar Bilezik">
                </div>
                <div class="form-group">
                    <label class="form-label">Toplam Tutar</label>
                    <input type="number" step="0.01" name="total_amount" class="form-control" required>
                </div>
                <div class="form-group">
                    <label class="form-label">
                        <input type="checkbox" name="is_credit"> Taksitli
                    </label>
                </div>
                <div class="form-group">
                    <label class="form-label">Toplam Taksit</label>
                    <input type="number" name="total_installments" class="form-control" min="0" value="0">
                </div>
                <div class="form-group">
                    <label class="form-label">Taksit Tutarı</label>
                    <input type="number" step="0.01" name="installment_amount" class="form-control" min="0" value="0">
                </div>
                <div class="form-group">
                    <label class="form-label">Vade Günü</label>
                    <input type="number" name="due_day" class="form-control" min="1" max="28" value="1">
                </div>
                <div class="form-actions">
                    <button type="submit" class="btn btn-primary">Kaydet</button>
                    <button type="button" class="btn btn-secondary" onclick="closeModal('debt-modal')">İptal</button>
                </div>
            </form>
        </div>
    </div>

    <div id="auth-overlay" class="auth-overlay hidden" aria-hidden="true">
        <div class="auth-card">
            <div class="auth-title">Portföy Yönetimi Giriş Ekranı</div>
            <div id="auth-message" class="auth-message"></div>
            <input id="auth-password" class="auth-input" type="password" placeholder="Şifreyi giriniz" autocomplete="current-password">
            <button id="auth-submit" class="btn btn-primary">Giriş</button>
        </div>
    </div>

    <script>
                        // Düzenli ödemeler için kategori işlemleri butonu aç/kapa
                        function toggleRecurringCategoryActions() {
                            const actions = document.getElementById('recurring-category-actions');
                            if (actions) {
                                actions.style.display = actions.style.display === 'none' || actions.style.display === '' ? 'flex' : 'none';
                            }
                        }

                        // Düzenli ödemeler için kategori ekle
                        function addRecurringCategory() {
                            const name = document.getElementById('recurring-new-category-name').value.trim();
                            const type = document.getElementById('recurring-type').value;
                            if (!name) return alert('Kategori adı girin.');
                            fetch('/add_category', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ name, type })
                            })
                            .then(r => r.json())
                            .then(data => {
                                if (data.success) {
                                    refreshCategoryList(type, data.category_id, true);
                                    document.getElementById('recurring-new-category-name').value = '';
                                } else {
                                    alert(data.message || 'Kategori eklenemedi.');
                                }
                            });
                        }

                        // Düzenli ödemeler için kategori sil
                        function deleteSelectedRecurringCategory() {
                            const select = document.getElementById('recurring-category-select');
                            const id = select.value;
                            if (!id) return alert('Silinecek kategoriyi seçin.');
                            if (!confirm('Kategoriyi silmek istediğinize emin misiniz?')) return;
                            fetch(`/delete_category/${id}`, { method: 'POST' })
                                .then(r => r.json())
                                .then(data => {
                                    if (data.success) {
                                        refreshCategoryList(document.getElementById('recurring-type').value, null, true);
                                    } else {
                                        alert(data.message || 'Kategori silinemedi.');
                                    }
                                });
                        }

                        // Düzenli ödemeler için kategori ad değiştir
                        function renameSelectedRecurringCategory() {
                            const select = document.getElementById('recurring-category-select');
                            const id = select.value;
                            const newName = prompt('Yeni kategori adı:');
                            if (!id || !newName) return;
                            fetch(`/rename_category/${id}`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ name: newName })
                            })
                            .then(r => r.json())
                            .then(data => {
                                if (data.success) {
                                    refreshCategoryList(document.getElementById('recurring-type').value, id, true);
                                } else {
                                    alert(data.message || 'Kategori adı değiştirilemedi.');
                                }
                            });
                        }
                // Düzenli ödeme düzenle
                function editRecurring(id) {
                    fetch(`/get_recurring/${id}`)
                        .then(response => response.json())
                        .then(data => {
                            if (data && !data.error) {
                                const modal = document.getElementById('recurring-modal');
                                if (!modal) return;
                                modal.style.display = 'flex';
                                document.body.style.overflow = 'hidden';
                                // Formu doldur
                                const form = document.getElementById('recurring-form');
                                if (!form) return;
                                form.recurring_id.value = data.id;
                                form.name.value = data.name || '';
                                form.amount.value = data.amount || '';
                                form.type.value = data.type || 'gider';
                                form.day_of_month.value = data.day_of_month || 1;
                                form.category_id.value = data.category_id || '';
                                form.debt_id && (form.debt_id.value = data.debt_id || '');
                                form.unit_currency && (form.unit_currency.value = data.unit_currency || '');
                                form.unit_grams && (form.unit_grams.value = data.unit_grams || '');
                                form.start_month.value = data.start_month || '';
                                form.end_month.value = data.end_month || '';
                                renderRecurringCategories(data.type, data.category_id);
                                // Aktiflik checkbox'ı
                                const isActiveCheckbox = document.getElementById('recurring-is-active');
                                if (isActiveCheckbox) {
                                    isActiveCheckbox.checked = !!data.is_active;
                                }
                                // Modal başlığı
                                document.getElementById('recurring-modal-title').textContent = 'Düzenli İşlem Düzenle';
                            }
                        });
                }

                // Düzenli ödeme sil
                function deleteRecurring(id) {
                    if (!confirm('Bu düzenli ödemeyi silmek istediğinize emin misiniz?')) return;
                    fetch(`/delete_recurring/${id}`, { method: 'POST' })
                        .then(() => window.location.reload());
                }
        // Düzenli ödemeler aç/kapa
        function toggleRecurringTable() {
            const wrapper = document.getElementById('recurring-table-wrapper');
            const icon = document.getElementById('recurring-toggle-icon');
            if (!wrapper || !icon) return;
            if (wrapper.style.display === 'none' || wrapper.style.display === '') {
                wrapper.style.display = 'block';
                icon.textContent = '▲';
            } else {
                wrapper.style.display = 'none';
                icon.textContent = '▼';
            }
        }

        function openRecurringModal(evt) {
            if (evt) evt.stopPropagation();
            showModal('recurring-modal');
            const form = document.getElementById('recurring-form');
            if (form) {
                form.reset();
                form.recurring_id.value = '';
            }
            const typeSelect = document.getElementById('recurring-type');
            if (typeSelect) {
                typeSelect.value = 'gider';
            }
            renderRecurringCategories('gider');
            const isActiveCheckbox = document.getElementById('recurring-is-active');
            if (isActiveCheckbox) {
                isActiveCheckbox.checked = true;
            }
            const actions = document.getElementById('recurring-category-actions');
            if (actions) {
                actions.style.display = 'none';
            }
            const title = document.getElementById('recurring-modal-title');
            if (title) {
                title.textContent = 'Düzenli İşlem Ekle';
            }
        }

        function changeYear(year) {
        if (!year) return;
        window.location.href = `/year/${year}`;
    }

    function openRecurringCategoryActions(evt) {
            if (evt) evt.stopPropagation();
            openRecurringModal();
            const actions = document.getElementById('recurring-category-actions');
            if (actions) {
                actions.style.display = 'flex';
            }
        }

        function refreshCategoryList(type, selectedId = null, updateRecurring = false) {
            const url = type === 'gelir' ? '/get_income_categories' : '/get_expense_categories';
            fetch(url)
                .then(r => r.json())
                .then(list => {
                    if (type === 'gelir') {
                        INCOME_CATEGORIES = list;
                    } else {
                        EXPENSE_CATEGORIES = list;
                    }
                    renderCategories(type);
                    if (updateRecurring) {
                        renderRecurringCategories(type, selectedId);
                    }
                    if (selectedId) {
                        const select = document.getElementById('category-select');
                        if (select) select.value = String(selectedId);
                    }
                });
        }

        function toggleCategoryActions() {
            const actions = document.getElementById('category-actions');
            if (actions) {
                actions.style.display = actions.style.display === 'none' || actions.style.display === '' ? 'flex' : 'none';
            }
        }

        function addCategory() {
            const name = document.getElementById('new-category-name').value.trim();
            const type = document.getElementById('transaction-type').value;
            if (!name) return alert('Kategori adı girin.');
            fetch('/add_category', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, type })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    refreshCategoryList(type, data.category_id);
                    document.getElementById('new-category-name').value = '';
                } else {
                    alert(data.message || 'Kategori eklenemedi.');
                }
            });
        }

        function deleteSelectedCategory() {
            const select = document.getElementById('category-select');
            const id = select.value;
            if (!id) return alert('Silinecek kategoriyi seçin.');
            if (!confirm('Kategoriyi silmek istediğinize emin misiniz?')) return;
            fetch(`/delete_category/${id}`, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        refreshCategoryList(document.getElementById('transaction-type').value);
                    } else {
                        alert(data.message || 'Kategori silinemedi.');
                    }
                });
        }

        function renameSelectedCategory() {
            const select = document.getElementById('category-select');
            const id = select.value;
            const newName = prompt('Yeni kategori adı:');
            if (!id || !newName) return;
            fetch(`/rename_category/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    refreshCategoryList(document.getElementById('transaction-type').value, id);
                } else {
                    alert(data.message || 'Kategori adı değiştirilemedi.');
                }
            });
        }

        function toggleRecurringActive(id, isActive, el) {
            fetch(`/toggle_recurring_active/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_active: isActive })
            })
            .then(r => r.json())
            .then(data => {
                if (!data.success) {
                    if (el) el.checked = !isActive;
                    return alert(data.message || 'Durum güncellenemedi.');
                }
                const row = document.querySelector(`tr[data-recurring-id="${id}"]`);
                const badge = row ? row.querySelector('.status-badge') : null;
                if (badge) {
                    badge.textContent = isActive ? 'Aktif' : 'Pasif';
                    badge.classList.toggle('status-active', isActive);
                    badge.classList.toggle('status-inactive', !isActive);
                }
            })
            .catch(() => {
                if (el) el.checked = !isActive;
                alert('Durum güncellenemedi.');
            });
        }
    // Mobile detection
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;

    // Değişkenler
    const ACTIVE_MONTH_ID = {{ active_month.id }};
    let INCOME_CATEGORIES = {{ income_categories|tojson }};
    let EXPENSE_CATEGORIES = {{ expense_categories|tojson }};
    const SORT_STATE = {};
    const AUTH_IDLE_MS = 15 * 60 * 1000;
    let authState = { authenticated: false };
    let authOverlay = null;
    let authPasswordInput = null;
    let authSubmitBtn = null;
    let authMessage = null;
    let idleTimerId = null;
    let lockCountdownId = null;

    function cacheAuthElements() {
        authOverlay = document.getElementById('auth-overlay');
        authPasswordInput = document.getElementById('auth-password');
        authSubmitBtn = document.getElementById('auth-submit');
        authMessage = document.getElementById('auth-message');
    }

    function setAuthOverlay(visible) {
        if (!authOverlay) return;
        authOverlay.classList.toggle('hidden', !visible);
        authOverlay.setAttribute('aria-hidden', visible ? 'false' : 'true');
        if (visible) {
            document.body.style.overflow = 'hidden';
            if (authPasswordInput) authPasswordInput.focus();
        } else {
            document.body.style.overflow = '';
        }
    }

    function setAuthMessage(text, isError) {
        if (!authMessage) return;
        authMessage.textContent = text || '';
        authMessage.classList.toggle('error', !!isError);
    }

    function formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function startLockCountdown(seconds) {
        if (lockCountdownId) clearInterval(lockCountdownId);
        if (authSubmitBtn) authSubmitBtn.disabled = true;
        let remaining = Math.max(0, seconds);
        const update = () => {
            if (remaining <= 0) {
                if (authSubmitBtn) authSubmitBtn.disabled = false;
                setAuthMessage('Kilitleme kalkti. Giris yapabilirsiniz.', false);
                if (lockCountdownId) clearInterval(lockCountdownId);
                return;
            }
            setAuthMessage(`Cok fazla hatali giris. ${formatDuration(remaining)} bekleyin.`, true);
            remaining -= 1;
        };
        update();
        lockCountdownId = setInterval(update, 1000);
    }

    function resetIdleTimer() {
        if (!authState.authenticated) return;
        if (idleTimerId) clearTimeout(idleTimerId);
        idleTimerId = setTimeout(() => {
            triggerIdleLock();
        }, AUTH_IDLE_MS);
    }

    function triggerIdleLock() {
        if (!authState.authenticated) return;
        logoutUser('15 dakika islem yapilmadigi icin giris gerekli.');
    }

    function applyAuthStatus(data) {
        authState.authenticated = !!data.authenticated;
        if (lockCountdownId) {
            clearInterval(lockCountdownId);
            lockCountdownId = null;
        }
        if (authSubmitBtn) authSubmitBtn.disabled = false;
        if (data.lock_remaining && data.lock_remaining > 0) {
            setAuthOverlay(true);
            startLockCountdown(data.lock_remaining);
            return;
        }
        if (authState.authenticated) {
            setAuthOverlay(false);
            setAuthMessage('');
            resetIdleTimer();
        } else {
            setAuthOverlay(true);
            setAuthMessage('Giriş yapmak için şifre girin.', false);
        }
    }

    function fetchAuthStatus() {
        return fetch('/auth/status')
            .then(r => r.json())
            .then(data => {
                applyAuthStatus(data);
            })
            .catch(() => {
                setAuthOverlay(true);
                setAuthMessage('Bağlantı hatası. Tekrar deneyin.', true);
            });
    }

    function loginUser() {
        if (!authPasswordInput) return;
        const password = authPasswordInput.value || '';
        fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                authPasswordInput.value = '';
                setAuthMessage('');
                applyAuthStatus({ authenticated: true });
                return;
            }
            if (data.lock_remaining && data.lock_remaining > 0) {
                startLockCountdown(data.lock_remaining);
                return;
            }
            const remaining = data.remaining_attempts !== undefined ? data.remaining_attempts : '';
            const remainingText = remaining !== '' ? ` Kalan deneme: ${remaining}` : '';
            setAuthMessage(`Hatali sifre.${remainingText}`, true);
        })
        .catch(() => {
            setAuthMessage('Giris yapilamadi. Tekrar deneyin.', true);
        });
    }

    function logoutUser(message) {
        fetch('/auth/logout', { method: 'POST' })
            .catch(() => {})
            .finally(() => {
                authState.authenticated = false;
                setAuthOverlay(true);
                setAuthMessage(message || 'Giris gerekli.', false);
                if (authPasswordInput) authPasswordInput.value = '';
            });
    }

    // Scroll restoration'ı devre dışı bırak
    if ('scrollRestoration' in history) {
        history.scrollRestoration = 'manual';
    }

    // Kategori render ve senkronizasyon fonksiyonları (yeniportfoy.py'den kopyalandı)
    function renderCategories(type) {
        const select = document.getElementById('category-select');
        if (!select) return;
        const list = type === 'gelir' ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
        select.innerHTML = '<option value="">Kategori seçin</option>';
        list.forEach(cat => {
            const opt = document.createElement('option');
            opt.value = cat.id;
            opt.textContent = cat.name;
            select.appendChild(opt);
        });
        syncTitleWithCategory();
    }

    function renderRecurringCategories(type, selectedId) {
        const select = document.getElementById('recurring-category-select');
        if (!select) return;
        const list = type === 'gelir' ? INCOME_CATEGORIES : EXPENSE_CATEGORIES;
        select.innerHTML = '<option value="">Kategori seçin</option>';
        list.forEach(cat => {
            const opt = document.createElement('option');
            opt.value = cat.id;
            opt.textContent = cat.name;
            if (selectedId && String(selectedId) === String(cat.id)) {
                opt.selected = true;
            }
            select.appendChild(opt);
        });
    }

    function updateCategoryField(typeVal) {
        const type = typeVal || document.getElementById('transaction-type')?.value || 'gelir';
        renderCategories(type);
    }

    function syncTitleWithCategory() {
        const select = document.getElementById('category-select');
        const hiddenTitle = document.getElementById('transaction-title-hidden');
        if (!select || !hiddenTitle) return;
        const text = select.options[select.selectedIndex]?.textContent || '';
        if (text && text.toLowerCase() !== 'kategori seçin') {
            hiddenTitle.value = text;
        }
    }

    // Mobile menü yönetimi
    function initMobileMenu() {
        // Sadece masaüstü görünümde (1024px'den büyük) ise mobil menüyü başlatma
        if (window.innerWidth > 1024) {
            // Masaüstünde mobil sidebar'ı gizle
            const sidebarMobile = document.querySelector('.sidebar-mobile');
            const overlay = document.querySelector('.sidebar-overlay');
            if (sidebarMobile) sidebarMobile.style.display = 'none';
            if (overlay) overlay.style.display = 'none';
            return;
        }

        const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
        const sidebarMobile = document.querySelector('.sidebar-mobile');
        const overlay = document.querySelector('.sidebar-overlay');
        const closeBtn = document.querySelector('.close-sidebar');
        // .mobile-month-selector kaldırıldı
        // Mobilde sidebar'ı göster
        if (sidebarMobile) sidebarMobile.style.display = 'block';
        // Mobile menu button event
        if (mobileMenuBtn && sidebarMobile && overlay) {
            mobileMenuBtn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                sidebarMobile.classList.add('active');
                overlay.style.display = 'block';
                document.body.style.overflow = 'hidden';
            });
        }
        // Close button event
        if (closeBtn && overlay) {
            const closeMenu = function(e) {
                e.preventDefault();
                e.stopPropagation();
                sidebarMobile.classList.remove('active');
                overlay.style.display = 'none';
                document.body.style.overflow = '';
            };
            closeBtn.addEventListener('click', closeMenu);
            overlay.addEventListener('click', closeMenu);
        }
        // Ay seçici kaldırıldı
    }

    // Mobil için optimize edilmiş format fonksiyonu
    function formatTRCurrency(value) {
        if (value === null || value === undefined) return '₺0,00';
        const num = parseFloat(value);
        if (isNaN(num)) return '₺0,00';

        // Mobil için daha hızlı formatlama
        if (isMobile) {
            const formatted = Math.abs(num).toFixed(2).replace('.', ',');
            const parts = [];
            let remaining = formatted.split(',')[0];

            while (remaining.length > 3) {
                parts.unshift(remaining.slice(-3));
                remaining = remaining.slice(0, -3);
            }
            parts.unshift(remaining);

            const result = parts.join('.') + ',' + formatted.split(',')[1];
            return (num < 0 ? '-' : '') + '₺' + result;
        }

        // Masaüstü için
        const formatted = num.toLocaleString('tr-TR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
        return '₺' + formatted;
    }

    function updateDebtCurrencyFields() {
        const currency = document.getElementById('debt-currency')?.value;
        const goldRow = document.getElementById('debt-gold-type-row');
        if (goldRow) {
            goldRow.style.display = currency === 'GAU' ? 'block' : 'none';
        }
    }

    function openDebtModal() {
        const modal = document.getElementById('debt-modal');
        if (!modal) return;
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        const form = document.getElementById('debt-form');
        if (form) {
            form.reset();
            form.action = '/add_debt';
            if (form.debt_id) form.debt_id.value = '';
        }
        const title = document.getElementById('debt-modal-title');
        if (title) {
            title.textContent = 'Borç Ekle';
        }
        updateDebtCurrencyFields();
    }

    function editDebt(id) {
        fetch(`/get_debt/${id}`)
            .then(r => r.json())
            .then(data => {
                if (data.error) return alert(data.error);
                const modal = document.getElementById('debt-modal');
                if (!modal) return;
                modal.style.display = 'flex';
                document.body.style.overflow = 'hidden';
                const form = document.getElementById('debt-form');
                if (!form) return;
                form.debt_id.value = data.id;
                form.name.value = data.name || '';
                form.currency.value = data.currency || 'TRY';
                form.gold_type.value = data.gold_type || '';
                form.total_amount.value = data.total_amount || 0;
                form.is_credit.checked = !!data.is_credit;
                form.total_installments.value = data.total_installments || 0;
                form.installment_amount.value = data.installment_amount || 0;
                form.due_day.value = data.due_day || 1;
                const title = document.getElementById('debt-modal-title');
                if (title) {
                    title.textContent = 'Borç Düzenle';
                }
                updateDebtCurrencyFields();
            });
    }

    function deleteDebt(id) {
        if (!confirm('Bu borcu silmek istediğinize emin misiniz?')) return;
        fetch(`/delete_debt/${id}`, { method: 'POST' })
            .then(() => window.location.reload());
    }

    async function payDebt(id, defaultAmount) {
        const hint = defaultAmount && defaultAmount > 0 ? String(defaultAmount) : '';
        const amount = await showQuickAmountOptional('Borç Ödeme', 'Tutar', hint);
        if (amount === undefined) return;
        fetch('/pay_debt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                debt_id: id,
                amount: amount,
                month_id: ACTIVE_MONTH_ID
            })
        })
        .then(r => r.json())
        .then(data => {
            if (!data.success) {
                return alert(data.error || 'Ödeme kaydedilemedi.');
            }
            window.location.reload();
        })
        .catch(() => alert('Ödeme kaydedilemedi.'));
    }

    // Touch event için optimize edilmiş tab değiştirme
    function switchTab(tabName, evt) {
        // Prevent multiple rapid taps
        if (this._lastTap && (Date.now() - this._lastTap) < 300) return;
        this._lastTap = Date.now();

        document.querySelectorAll('.tab-content').forEach(tab => {
            tab.classList.remove('active');
        });

        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });

        const tabElement = document.getElementById(tabName);
        if (tabElement) {
            tabElement.classList.add('active');
        }

        const targetBtn = evt ? evt.target.closest('.tab-btn') :
                               document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
        if (targetBtn) {
            targetBtn.classList.add('active');
        }

        localStorage.setItem('activeTab', tabName);

        // Mobilde kaydırmayı başa al
        if (isMobile || window.innerWidth <= 768) {
            setTimeout(() => {
                const tabContent = document.querySelector('.tab-content.active');
                if (tabContent) {
                    tabContent.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }, 50);
        }
    }

    // Mobil için optimize edilmiş modal yönetimi
    function showModal(modalId, type = '', resetForm = true) {
        const modal = document.getElementById(modalId);
        if (!modal) return;

        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';

        // iOS için input focus sorununu çöz
        if (isMobile) {
            modal.style.position = 'fixed';
            modal.style.top = '0';
            modal.style.left = '0';
            modal.style.width = '100%';
            modal.style.height = '100%';
            modal.style.alignItems = 'flex-start';
            modal.style.paddingTop = '20px';
            modal.style.paddingBottom = '20px';
            modal.style.overflowY = 'auto';
            modal.style.webkitOverflowScrolling = 'touch';
        }

        // Diğer modal kodları...
        if (type && modalId === 'new-transaction-modal') {
            const typeSelect = modal.querySelector('select[name="type"]');
            if (typeSelect) {
                typeSelect.value = type;
                updateCategoryField(type);
            }
            syncTitleWithCategory();
            const form = document.getElementById('transaction-form');
            const submit = document.getElementById('transaction-submit-btn');
            if (form && submit) {
                form.action = '/add_transaction';
                form.transaction_id.value = '';
                submit.textContent = 'Kaydet';
            }
        }

        // Mobilde klavye açılınca modal'ı yukarı kaydır
        if (isMobile) {
            const inputs = modal.querySelectorAll('input, select, textarea');
            inputs.forEach(input => {
                input.addEventListener('focus', () => {
                    setTimeout(() => {
                        modal.scrollTop = 0;
                    }, 100);
                });
            });
        }
    }

    function closeModal(modalId) {
        if (modalId) {
            const modal = document.getElementById(modalId);
            if (modal) {
                modal.style.display = 'none';
            }
        } else {
            document.querySelectorAll('.modal').forEach(modal => {
                modal.style.display = 'none';
            });
        }
        document.body.style.overflow = '';
    }

    // Mobil için optimize edilmiş dokunma olayları
    function setupTouchEvents() {
        if (!isTouchDevice) return;

        // Butonlar için touch feedback
        document.querySelectorAll('.btn, .tab-btn, .month-link').forEach(btn => {
            btn.addEventListener('touchstart', function() {
                this.classList.add('touch-active');
            });

            btn.addEventListener('touchend', function() {
                this.classList.remove('touch-active');
            });
        });
    }

    // Mobil için responsive tablo yönetimi
    let quickModalResolve = null;

    function openQuickModal({ title, bodyNode, actions }) {
        const modal = document.getElementById('quick-action-modal');
        const titleEl = document.getElementById('quick-action-title');
        const bodyEl = document.getElementById('quick-action-body');
        const actionsEl = document.getElementById('quick-action-actions');
        titleEl.textContent = title;
        bodyEl.innerHTML = '';
        bodyEl.appendChild(bodyNode);
        actionsEl.innerHTML = '';
        actions.forEach((action) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = action.className || 'btn btn-secondary';
            btn.textContent = action.label;
            btn.onclick = () => {
                action.onClick();
            };
            actionsEl.appendChild(btn);
        });
        showModal('quick-action-modal', '', false);
    }

    function closeQuickModal() {
        closeModal('quick-action-modal');
    }

    function showQuickChoice(title, options) {
        return new Promise((resolve) => {
            quickModalResolve = resolve;
            const body = document.createElement('div');
            body.style.display = 'flex';
            body.style.flexDirection = 'column';
            body.style.gap = '8px';
            body.innerHTML = '<div style="color: var(--text-secondary); font-size: 13px;">Seçiminizi yapın</div>';
            const actions = options.map((opt) => ({
                label: opt.label,
                className: opt.className,
                onClick: () => {
                    closeQuickModal();
                    resolve(opt.value);
                },
            }));
            actions.push({
                label: 'Vazgeç',
                className: 'btn btn-secondary',
                onClick: () => {
                    closeQuickModal();
                    resolve(null);
                },
            });
            openQuickModal({ title, bodyNode: body, actions });
        });
    }

    function showQuickAmount(title, placeholder) {
        return new Promise((resolve) => {
            quickModalResolve = resolve;
            const body = document.createElement('div');
            body.style.display = 'flex';
            body.style.flexDirection = 'column';
            body.style.gap = '8px';
            const input = document.createElement('input');
            input.type = 'number';
            input.step = '0.01';
            input.min = '0';
            input.placeholder = placeholder || '';
            input.className = 'form-control';
            const error = document.createElement('div');
            error.style.color = 'var(--danger)';
            error.style.fontSize = '12px';
            body.appendChild(input);
            body.appendChild(error);
            const submit = () => {
                const value = parseFloat(String(input.value).replace(',', '.'));
                if (!isFinite(value) || value <= 0) {
                    error.textContent = 'Lütfen geçerli bir miktar girin.';
                    return;
                }
                closeQuickModal();
                resolve(value);
            };
            openQuickModal({
                title,
                bodyNode: body,
                actions: [
                    {
                        label: 'Onayla',
                        className: 'btn btn-primary',
                        onClick: submit,
                    },
                    {
                        label: 'Vazgeç',
                        className: 'btn btn-secondary',
                        onClick: () => {
                            closeQuickModal();
                            resolve(null);
                        },
                    },
                ],
            });
            setTimeout(() => input.focus(), 0);
        });
    }

    function showQuickAmountOptional(title, placeholder, hint) {
        return new Promise((resolve) => {
            quickModalResolve = resolve;
            const body = document.createElement('div');
            body.style.display = 'flex';
            body.style.flexDirection = 'column';
            body.style.gap = '8px';
            const input = document.createElement('input');
            input.type = 'number';
            input.step = '0.01';
            input.min = '0';
            input.placeholder = placeholder || '';
            if (hint) input.value = hint;
            input.className = 'form-control';
            const help = document.createElement('div');
            help.style.color = 'var(--text-secondary)';
            help.style.fontSize = '12px';
            help.textContent = 'Boş bırakırsanız otomatik tutar kullanılır.';
            const error = document.createElement('div');
            error.style.color = 'var(--danger)';
            error.style.fontSize = '12px';
            body.appendChild(input);
            body.appendChild(help);
            body.appendChild(error);
            const submit = () => {
                if (String(input.value).trim() === '') {
                    closeQuickModal();
                    resolve(null);
                    return;
                }
                const value = parseFloat(String(input.value).replace(',', '.'));
                if (!isFinite(value) || value <= 0) {
                    error.textContent = 'Lütfen geçerli bir tutar girin.';
                    return;
                }
                closeQuickModal();
                resolve(value);
            };
            openQuickModal({
                title,
                bodyNode: body,
                actions: [
                    {
                        label: 'Onayla',
                        className: 'btn btn-primary',
                        onClick: submit,
                    },
                    {
                        label: 'Otomatik',
                        className: 'btn btn-secondary',
                        onClick: () => {
                            closeQuickModal();
                            resolve(null);
                        },
                    },
                    {
                        label: 'Vazgeç',
                        className: 'btn btn-secondary',
                        onClick: () => {
                            closeQuickModal();
                            resolve(undefined);
                        },
                    },
                ],
            });
            setTimeout(() => input.focus(), 0);
        });
    }

    function normalizeCurrencyCode(code) {
        return code === 'GRA' ? 'GAU' : code;
    }

    async function quickBuyUnified(code, rate) {
        const choice = await showQuickChoice('Alma türü', [
            { label: 'Birikim Yap', value: 'saving', className: 'btn btn-success' },
            { label: 'Borç Al', value: 'debt', className: 'btn btn-warning' },
        ]);
        if (!choice) return;
        if (choice === 'debt') {
            await quickBorrowCurrency(code);
        } else {
            await quickBuySaving(code, rate);
        }
    }

    async function quickBuySaving(code, rate) {
        const normalized = normalizeCurrencyCode(code);
        const isGold = normalized === 'GAU';
        const label = isGold ? 'Kaç gram altın alacaksınız?' : `Kaç ${code} alacaksınız?`;
        const amount = await showQuickAmount(label, isGold ? 'Gram' : code);
        if (!amount) return;
        fetch('/quick_saving_buy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: normalized, unit_amount: amount, rate, month_id: ACTIVE_MONTH_ID })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert(data.error || 'Birikim eklenemedi.');
            }
        })
        .catch(() => alert('Birikim eklenemedi.'));
    }


    async function quickSellUnified(code, rate, evt) {
        const row = evt ? evt.target.closest('tr') : null;
        const saving = row ? parseFloat(row.dataset.saving || '0') : 0;
        const debt = row ? parseFloat(row.dataset.debt || '0') : 0;
        if (saving > 0 && debt > 0) {
            const choice = await showQuickChoice('Satma türü', [
                { label: 'Birikim Sat', value: 'saving', className: 'btn btn-primary' },
                { label: 'Borç Öde', value: 'debt', className: 'btn btn-warning' },
            ]);
            if (!choice) return;
            if (choice === 'debt') {
                await quickPayDebt(code, rate);
            } else {
                await quickSellSaving(code, rate);
            }
            return;
        }
        if (saving > 0) {
            await quickSellSaving(code, rate);
            return;
        }
        if (debt > 0) {
            await quickPayDebt(code, rate);
            return;
        }
        alert('Bu kur için satılacak birikim veya borç yok.');
    }

    async function quickSellSaving(code, rate) {
        const normalized = normalizeCurrencyCode(code);
        const isGold = normalized === 'GAU';
        const label = isGold ? 'Kaç gram altın satacaksınız?' : `Kaç ${code} satacaksınız?`;
        const amount = await showQuickAmount(label, isGold ? 'Gram' : code);
        if (!amount) return;
        fetch('/quick_saving_sell', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: normalized, unit_amount: amount, rate, month_id: ACTIVE_MONTH_ID })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert(data.error || 'Satış işlemi yapılamadı.');
            }
        })
        .catch(() => alert('Satış işlemi yapılamadı.'));
    }


    async function quickBorrowCurrency(code) {
        const normalized = normalizeCurrencyCode(code);
        const isGold = normalized === 'GAU';
        const label = isGold ? 'Kaç gram borç alacaksınız?' : `Kaç ${code} borç alacaksınız?`;
        const amount = await showQuickAmount(label, isGold ? 'Gram' : code);
        if (!amount) return;
        const defaultName = isGold ? 'Altın Borcu' : `Döviz Borcu - ${code}`;
        const name = prompt('Borç adı (boş bırakabilirsiniz):', defaultName) || defaultName;
        fetch('/quick_debt_add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: normalized, amount, name })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert(data.error || 'Borç eklenemedi.');
            }
        })
        .catch(() => alert('Borç eklenemedi.'));
    }


    async function quickPayDebt(code, rate) {
        const normalized = normalizeCurrencyCode(code);
        const isGold = normalized === 'GAU';
        const label = isGold ? 'Kaç gram borç ödeyeceksiniz?' : `Kaç ${code} borç ödeyeceksiniz?`;
        const amount = await showQuickAmount(label, isGold ? 'Gram' : code);
        if (!amount) return;
        fetch('/quick_debt_pay', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: normalized, amount, rate, month_id: ACTIVE_MONTH_ID })
        })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert(data.error || 'Borç ödeme işlemi yapılamadı.');
            }
        })
        .catch(() => alert('Borç ödeme işlemi yapılamadı.'));
    }


    function initResponsiveTables() {
        if (window.innerWidth <= 768) {
            document.querySelectorAll('table').forEach(table => {
                if (!table.closest('.table-scroll-wrapper')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'table-scroll-wrapper';
                    table.parentNode.insertBefore(wrapper, table);
                    wrapper.appendChild(table);
                }
            });
        }
    }

    // Ekran boyutu değiştiğinde
    function handleResize() {
        initMobileMenu();
        initResponsiveTables();

        // Masaüstünde mobil sidebar'ı gizle
        if (window.innerWidth > 1024) {
            const sidebarMobile = document.querySelector('.sidebar-mobile');
            const overlay = document.querySelector('.sidebar-overlay');
            if (sidebarMobile) {
                sidebarMobile.classList.remove('active');
                sidebarMobile.style.display = 'none';
            }
            if (overlay) {
                overlay.style.display = 'none';
            }
            document.body.style.overflow = '';
        }
    }

    // Sayfa yüklendiğinde
    document.addEventListener('DOMContentLoaded', function() {
        console.log('DOM loaded, initializing...');

        cacheAuthElements();
        if (authSubmitBtn) {
            authSubmitBtn.addEventListener('click', loginUser);
        }
        if (authPasswordInput) {
            authPasswordInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    loginUser();
                }
            });
        }
        ['mousemove', 'keydown', 'click', 'touchstart', 'scroll'].forEach(evt => {
            document.addEventListener(evt, () => {
                if (authState.authenticated) resetIdleTimer();
            }, { passive: true });
        });
        setAuthOverlay(true);
        setAuthMessage('Giris yapmak icin sifre girin.', false);
        fetchAuthStatus();

        // Mobile menu'yu başlat
        initMobileMenu();

        // Touch event'lerini kur
        setupTouchEvents();

        // Responsive tablolar
        initResponsiveTables();

        // Aktif tab'ı yükle
        const hashTab = location.hash ? location.hash.replace('#','') : '';
        const savedTab = localStorage.getItem('activeTab') || 'overview';
        const tab = hashTab || savedTab;
        switchTab(tab);

        // Diğer başlangıç fonksiyonları
        updateCategoryField(document.getElementById('transaction-type')?.value || 'gelir');
        syncTitleWithCategory();

        // Mobilde scroll'u başa al
        if (isMobile || window.innerWidth <= 768) {
            window.scrollTo(0, 0);
            setTimeout(() => {
                window.scrollTo(0, 0);
            }, 100);
        }

        // Resize event listener
        window.addEventListener('resize', handleResize);

        // Modal dışına tıklanınca kapat
        window.addEventListener('click', function(event) {
            if (event.target.classList.contains('modal') ||
                event.target.classList.contains('sidebar-overlay')) {
                closeModal();
                const overlay = document.querySelector('.sidebar-overlay');
                const sidebarMobile = document.querySelector('.sidebar-mobile');
                if (overlay) overlay.style.display = 'none';
                if (sidebarMobile) sidebarMobile.classList.remove('active');
                document.body.style.overflow = '';
            }
        });

        // Klavye kapatıldığında
        window.addEventListener('orientationchange', function() {
            setTimeout(() => {
                window.scrollTo(0, 0);
                handleResize();
            }, 300);
        });

        // Escape tuşu ile menü kapatma
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const sidebarMobile = document.querySelector('.sidebar-mobile');
                const overlay = document.querySelector('.sidebar-overlay');
                if (sidebarMobile && sidebarMobile.classList.contains('active')) {
                    sidebarMobile.classList.remove('active');
                    if (overlay) overlay.style.display = 'none';
                    document.body.style.overflow = '';
                }
            }
        });
    });

    // Sayfa yükleme tamamlandığında
    window.addEventListener('load', function() {
        // Mobil cihazlarda viewport ayarı
        if (isMobile) {
            const viewport = document.querySelector('meta[name="viewport"]');
            if (viewport) {
                viewport.setAttribute('content', 'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no');
            }
        }

        // İlk yüklemede scroll'u düzelt
        setTimeout(() => {
            window.scrollTo(0, 0);
            handleResize();
        }, 500);
    });

    // Diğer mevcut JavaScript fonksiyonlarınızı buraya ekleyin...

            // İşlem düzenle
            function editTransaction(id) {
                fetch(`/get_transaction/${id}`)
                    .then(r => r.json())
                    .then(data => {
                        if (data.error) return alert(data.error);
                        showModal('new-transaction-modal', data.type, false);
                        document.getElementById('transaction-id-hidden').value = data.id;
                        document.getElementById('transaction-type').value = data.type;
                        document.querySelector('input[name="amount"]').value = data.amount;
                        document.querySelector('input[name="date"]').value = data.date;
                        renderCategories(data.type);
                        document.getElementById('category-select').value = data.category_id || '';
                        syncTitleWithCategory();
                        // Diğer alanlar (gerekirse)
                        // ...
                        // Submit butonunu güncelle
                        document.getElementById('transaction-form').action = '/update_transaction';
                        document.getElementById('transaction-submit-btn').textContent = 'Güncelle';
                    });
            }

            // İşlem sil
            function deleteTransaction(id) {
                if (!confirm('Bu işlemi silmek istediğinize emin misiniz?')) return;
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = `/delete_transaction/${id}`;
                document.body.appendChild(form);
                form.submit();
            }
        // Birikim formunu aç/kapat
        function toggleSavingForm(show = null) {
            const form = document.getElementById('saving-form-container');
            if (!form) return;
            if (show === true) {
                form.style.display = 'block';
            } else if (show === false) {
                form.style.display = 'none';
                clearSavingForm();
            } else {
                form.style.display = (form.style.display === 'none' || form.style.display === '') ? 'block' : 'none';
                if (form.style.display === 'none') clearSavingForm();
            }
        }

        // Formu temizle
        function clearSavingForm() {
            document.getElementById('saving-id').value = '';
            document.getElementById('saving-currency').value = 'USD';
            document.getElementById('saving-usd-rate').value = '';
            document.getElementById('saving-usd-tl').value = '';
            document.getElementById('saving-gold-grams').value = '';
            document.getElementById('saving-gold-tl').value = '';
            document.getElementById('saving-gold-type').value = '';
            updateSavingFields();
        }

        // USD/Altın alanlarını göster/gizle
    function updateSavingFields() {
        const currency = document.getElementById('saving-currency').value;
        document.getElementById('saving-usd-fields').style.display = (currency === 'USD') ? 'block' : 'none';
        document.getElementById('saving-gold-fields').style.display = (currency === 'GAU') ? 'block' : 'none';
    }

    function filterRates() {
        const input = document.getElementById('rate-search');
        const query = (input ? input.value : '').trim().toUpperCase();
        const rows = document.querySelectorAll('#rates-table tbody tr');
        rows.forEach(row => {
            const code = row.querySelector('td')?.textContent?.trim().toUpperCase() || '';
            row.style.display = code.includes(query) ? 'table' : 'none';
        });
    }

        // Birikim düzenle
        function editSaving(id) {
            fetch(`/get_saving/${id}`)
                .then(r => r.json())
                .then(data => {
                    if (data.error) return alert(data.error);
                    document.getElementById('saving-id').value = data.id;
                    document.getElementById('saving-currency').value = data.currency;
                    if (data.currency === 'USD') {
                        document.getElementById('saving-usd-rate').value = data.purchase_rate || '';
                        document.getElementById('saving-usd-tl').value = data.tl_amount || '';
                    } else {
                        document.getElementById('saving-gold-grams').value = data.unit_amount || '';
                        document.getElementById('saving-gold-tl').value = data.tl_amount || '';
                        document.getElementById('saving-gold-type').value = data.gold_type || '';
                    }
                    updateSavingFields();
                    toggleSavingForm(true);
                });
        }

        // Birikim sil
        function deleteSaving(id) {
            if (!confirm('Bu birikimi silmek istediğinize emin misiniz?')) return;
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = `/delete_saving/${id}`;
            document.body.appendChild(form);
            form.submit();
        }
    // (updateSavingFields, toggleSavingForm, editSaving, openDebtModal, vb.)

    // Hızlı test için konsola mesaj
    console.log('Application loaded successfully');
</script>
</body>
</html>'''


@app.before_request
def enforce_idle_timeout():
    if request.endpoint in ('auth_status', 'auth_login', 'auth_logout', 'static', 'favicon'):
        return
    if not session.get('authenticated'):
        return
    now = datetime.utcnow()
    last_activity = session.get('last_activity')
    if last_activity:
        try:
            last_dt = datetime.fromisoformat(last_activity)
        except ValueError:
            last_dt = now
        if now - last_dt > timedelta(minutes=AUTH_IDLE_MINUTES):
            session['authenticated'] = False
            session.pop('last_activity', None)
            return
    session['last_activity'] = now.isoformat()

# ---------- ROTALAR ----------
@app.route('/')
def index():
    # İlk ay varsayılan
    first_month = Month.query.order_by(Month.id).first()
    if first_month:
        return redirect(f'/month/{first_month.id}')

    return redirect('/init_db')

@app.route('/init_db')
def init_database():
    # Veritabanı migration'larını çalıştır
    ensure_debt_columns()
    ensure_transaction_columns()
    ensure_category_columns()
    ensure_recurring_columns()
    ensure_saving_table()

    # Ayları oluştur
    current_year = date.today().year
    for i, month_name in enumerate(MONTH_NAMES, 1):
        if not Month.query.filter_by(name=month_name, year=current_year).first():
            month = Month(name=month_name, year=current_year)
            db.session.add(month)

    db.session.commit()

    # Varsayılan kategorileri oluştur
    init_categories()

    fix_month_names(current_year)

    # Düzenli ödemeleri tüm aylara ekle
    check_recurring_payments(current_year)

    return redirect('/')

@app.route('/auth/status')
def auth_status():
    state = get_auth_state()
    now = datetime.utcnow()
    lock_remaining = get_lock_remaining_seconds(state, now)
    if lock_remaining == 0 and state.lock_until:
        state.lock_until = None
        db.session.commit()

    authenticated = bool(session.get('authenticated'))
    last_activity = session.get('last_activity')
    if authenticated and last_activity:
        try:
            last_dt = datetime.fromisoformat(last_activity)
        except ValueError:
            last_dt = now
        if now - last_dt > timedelta(minutes=AUTH_IDLE_MINUTES):
            authenticated = False
            session['authenticated'] = False
            session.pop('last_activity', None)
        else:
            session['last_activity'] = now.isoformat()
    elif authenticated:
        session['last_activity'] = now.isoformat()

    remaining_attempts = 0 if lock_remaining > 0 else max(0, AUTH_MAX_ATTEMPTS - state.failed_attempts)
    return jsonify({
        'authenticated': authenticated,
        'lock_remaining': lock_remaining,
        'remaining_attempts': remaining_attempts
    })

@app.route('/auth/login', methods=['POST'])
def auth_login():
    state = get_auth_state()
    now = datetime.utcnow()
    lock_remaining = get_lock_remaining_seconds(state, now)
    if lock_remaining > 0:
        return jsonify(success=False, lock_remaining=lock_remaining)

    payload = request.get_json(silent=True) or {}
    password = payload.get('password', '')
    if password == app.config['LOGIN_PASSWORD']:
        state.failed_attempts = 0
        state.lock_until = None
        db.session.commit()
        session['authenticated'] = True
        session['last_activity'] = now.isoformat()
        return jsonify(success=True)

    state.failed_attempts += 1
    remaining_attempts = max(0, AUTH_MAX_ATTEMPTS - state.failed_attempts)
    if state.failed_attempts >= AUTH_MAX_ATTEMPTS:
        state.failed_attempts = 0
        state.lock_until = now + timedelta(minutes=AUTH_LOCKOUT_MINUTES)
        db.session.commit()
        return jsonify(success=False, lock_remaining=AUTH_LOCKOUT_MINUTES * 60, remaining_attempts=0)
    db.session.commit()
    return jsonify(success=False, remaining_attempts=remaining_attempts)

@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    session['authenticated'] = False
    session.pop('last_activity', None)
    return jsonify(success=True)

@app.route('/year/<int:year>')
def year_view(year):
    ensure_year_months(year)
    fix_month_names(year)
    first_month = Month.query.filter_by(year=year).order_by(Month.id).first()
    if first_month:
        return redirect(f'/month/{first_month.id}')
    return redirect('/')

@app.route('/month/<int:month_id>')
def month_detail(month_id):
    # Veritabanı migration'larını çalıştır
    ensure_debt_columns()
    ensure_transaction_columns()
    ensure_category_columns()
    ensure_recurring_columns()
    ensure_saving_table()

    # Varsayılan kategorileri oluştur (ilk çalışmada)
    init_categories()

    # Döviz kurlarını al
    rates, rates_all = get_exchange_rates(return_all=True)

    # Aktif ay
    active_month = Month.query.get_or_404(month_id)

    ensure_year_months(active_month.year)

    fix_month_names(active_month.year)

    # Tüm aylar (sidebar için)
    months = Month.query.filter_by(year=active_month.year).all()
    years = sorted({m.year for m in Month.query.all()})
    if active_month.year not in years:
        years.append(active_month.year)
        years.sort()

    # Düzenli ödemeleri tüm aylara otomatik ekle
    check_recurring_payments(active_month.year)

    # Bakiye ve devreden işlemleri yeniden hesapla
    recalculate_balances(active_month.year)

    # İşlemler (Devreden Bakiye hariç - sistem işlemidir)
    all_transactions = Transaction.query.filter_by(month_id=month_id).order_by(Transaction.order_index.asc(), Transaction.date.desc()).all()
    transactions = [t for t in all_transactions if t.title != CARRYOVER_TITLE]

    # Hesaplamalar (tüm işlemlerle yapılır, Devreden Bakiye dahil)
    total_income = sum(t.amount for t in all_transactions if t.type == 'gelir')
    total_expense = sum(t.amount for t in all_transactions if t.type == 'gider')
    balance = total_income - total_expense

    # Borçlar
    debts = Debt.query.all()
    for debt in debts:
        update_debt_progress(debt)
        db.session.add(debt)
    db.session.commit()
    today_day = date.today().day
    current_month_start = date(date.today().year, date.today().month, 1)
    if date.today().month == 12:
        current_month_end = date(date.today().year + 1, 1, 1)
    else:
        current_month_end = date(date.today().year, date.today().month + 1, 1)
    overdue_debts = []
    for d in debts:
        if not d.is_credit:
            continue
        if not d.installment_amount:
            continue
        if not d.due_day or d.due_day <= 0:
            continue  # gün belirtilmemişse uyarı yok
        if (d.remaining_amount or 0) <= 0:
            continue
        if d.due_day > today_day:
            continue  # gün gelmemişse uyarı yok
        # Bu ay ödeme yapıldı mı? (borca bağlı gider işlemi)
        payment_exists = Transaction.query.filter_by(debt_id=d.id, type='gider') \
            .filter(Transaction.date >= current_month_start, Transaction.date < current_month_end) \
            .first()
        if payment_exists:
            continue
        overdue_debts.append(d)
    overdue_debt_count = len(overdue_debts)
    overdue_debt_names = ', '.join(d.name for d in overdue_debts)

    total_debt_usd = sum((d.remaining_amount or 0) for d in debts if d.currency == 'USD')
    total_debt_gau = sum((d.remaining_amount or 0) for d in debts if d.currency == 'GAU')

    # Düzenli ödemeler
    recurring_payments = RecurringPayment.query.all()

    # Kategoriler
    expense_categories_raw = ExpenseCategory.query.filter_by(type='gider').order_by(ExpenseCategory.name).all()
    income_categories_raw = ExpenseCategory.query.filter_by(type='gelir').order_by(ExpenseCategory.name).all()
    expense_categories = [{'id': c.id, 'name': c.name, 'type': c.type} for c in expense_categories_raw]
    income_categories = [{'id': c.id, 'name': c.name, 'type': c.type} for c in income_categories_raw]

    # Birikimler (tum aylar)
    savings = Saving.query.order_by(Saving.date.desc(), Saving.id.desc()).all()
    total_saving_tl = sum(s.tl_amount for s in savings)
    total_saving_usd = sum(s.unit_amount for s in savings if s.currency == 'USD')
    total_saving_gau = sum(s.unit_amount for s in savings if s.currency == 'GAU')
    months_dict = {m.id: {'name': m.name, 'year': m.year} for m in months}

    saving_totals = {}
    for s in savings:
        code = s.currency or 'TRY'
        saving_totals[code] = saving_totals.get(code, 0) + (s.unit_amount or 0)

    debt_totals = {}
    for d in debts:
        code = d.currency or 'TRY'
        debt_totals[code] = debt_totals.get(code, 0) + (d.remaining_amount or 0)

    rates_map = {r.get('code'): r.get('value') for r in rates_all if r.get('code')}
    if 'GAU' in saving_totals:
        saving_totals['GRA'] = saving_totals.get('GRA', 0) + saving_totals.get('GAU', 0)
    if 'GAU' in debt_totals:
        debt_totals['GRA'] = debt_totals.get('GRA', 0) + debt_totals.get('GAU', 0)

    return render_template_string(
        HTML_TEMPLATE,
        active_month=active_month,
        months=months,
        transactions=transactions,
        total_income=total_income,
        total_expense=total_expense,
        balance=balance,
        debts=debts,
        recurring_payments=recurring_payments,
        savings=savings,
        total_saving_tl=total_saving_tl,
        total_saving_usd=total_saving_usd,
        total_saving_gau=total_saving_gau,
        total_debt_usd=total_debt_usd,
        total_debt_gau=total_debt_gau,
        saving_totals=saving_totals,
        debt_totals=debt_totals,
        rates_map=rates_map,
        expense_categories=expense_categories,
        income_categories=income_categories,
        current_year=active_month.year,
        years=years,
        today=date.today().isoformat(),
        overdue_debt_count=overdue_debt_count,
        overdue_debt_names=overdue_debt_names,
        rates=rates,
        usd_rate=rates.get('USD', 0),
        eur_rate=rates.get('EUR', 0),
        gold_rate=rates.get('GAU', 0),
        btc_rate=rates.get('BTC', 0),
        rates_all=rates_all,
        last_update=datetime.now().strftime('%d.%m.%Y %H:%M'),
        months_dict=months_dict
    )

@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    month_id = int(request.form.get('month_id') or 0)
    title = str(request.form.get('title') or '')
    amount = float(request.form.get('amount') or 0)
    type_ = str(request.form.get('type') or 'gelir')
    transaction_date = request.form.get('date')
    is_recurring = request.form.get('is_recurring') == 'on'
    rec_day = int(request.form.get('recurring_day') or date.today().day)
    rec_start = request.form.get('recurring_start') or None
    rec_end = request.form.get('recurring_end') or None
    category_id = request.form.get('category_id')
    category_id = int(category_id) if category_id else None
    if not category_id:
        return redirect(request.referrer or '/')
    if not title:
        cat = ExpenseCategory.query.get(category_id)
        if cat:
            title = cat.name

    # Order index
    max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month_id).scalar() or 0
    order_index = max_order + 1

    # İşlemi ekle
    transaction = Transaction(
        month_id=month_id,
        title=title,
        amount=amount,
        type=type_,
        date=datetime.strptime(transaction_date, '%Y-%m-%d').date() if transaction_date else date.today(),
        is_recurring=is_recurring,
        category_id=category_id,
        order_index=order_index
    )
    db.session.add(transaction)

    # Düzenli ödeme ise kaydet
    if is_recurring:
        recurring = RecurringPayment(
            name=title,
            amount=amount,
            type=type_,
            day_of_month=rec_day,
            is_active=True,
            start_month=rec_start,
            end_month=rec_end,
            category_id=category_id
        )
        db.session.add(recurring)

    # Borç ödemesi ise borçtan düş (düzenli ödemeler hariç)
    title_lower = title.lower()
    if type_ == 'gider' and 'borç' in title_lower and not is_recurring:
        debts = Debt.query.all()
        if debts:
            debt = debts[0]
            debt.remaining_amount = max(0, debt.remaining_amount - amount)

    db.session.commit()

    # Bir sonraki ayın açılış bakiyesini güncelle
    recalculate_balances(get_month_year(month_id))

    if is_recurring:
        check_recurring_payments(get_month_year(month_id))

    return redirect(f'/month/{month_id}')

@app.route('/add_saving', methods=['POST'])
def add_saving():
    saving_id = request.form.get('saving_id')
    month_id = int(request.form.get('month_id') or 0)
    currency = request.form.get('currency') or 'USD'
    tl_amount = 0.0
    unit_amount = 0.0
    purchase_rate = None
    gold_type = None
    gold_grams = None

    if currency == 'USD':
        tl_amount = float(request.form.get('tl_amount_usd') or 0)
        purchase_rate = float(request.form.get('purchase_rate') or 0)
        unit_amount = tl_amount / purchase_rate if purchase_rate else 0
    else:
        gold_grams = float(request.form.get('gold_grams') or 0)
        tl_amount = float(request.form.get('tl_amount_gau') or 0)
        unit_amount = gold_grams
        gold_type = request.form.get('gold_type') or 'Gram'

    # Eğer güncelleme ise mevcut kaydı güncelle
    if saving_id:
        saving = Saving.query.get(int(saving_id))
        if saving:
            old_title = 'Birikim - Dolar' if saving.currency == 'USD' else 'Birikim - Altın'
            old_amount = saving.tl_amount
            saving.currency = currency
            saving.tl_amount = tl_amount
            saving.unit_amount = unit_amount
            saving.purchase_rate = purchase_rate if currency == 'USD' else None
            saving.gold_type = gold_type if currency == 'GAU' else None
            saving.date = saving.date or date.today()
            # Eski gideri sil ve yenisini ekle
            tx_old = Transaction.query.filter_by(month_id=saving.month_id, title=old_title).filter(Transaction.amount == old_amount).order_by(Transaction.date.desc()).first()
            old_order = tx_old.order_index if tx_old else None
            if tx_old:
                db.session.delete(tx_old)
            title = 'Birikim - Dolar' if currency == 'USD' else 'Birikim - Altın'
            if old_order is None:
                old_order = (db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=saving.month_id).scalar() or 0) + 1
            new_tx = Transaction(
                month_id=saving.month_id,
                title=title,
                amount=tl_amount,
                type='gider',
                date=saving.date or date.today(),
                purchase_rate=purchase_rate if currency == 'USD' else None,
                gold_type=gold_type if currency == 'GAU' else None,
                gold_grams=gold_grams if currency == 'GAU' else None,
                gold_tl_value=tl_amount if currency == 'GAU' else None,
                order_index=old_order
            )
            db.session.add(new_tx)
            db.session.add(saving)
    else:
        saving = Saving(
            month_id=month_id,
            currency=currency,
            tl_amount=tl_amount,
            unit_amount=unit_amount,
            purchase_rate=purchase_rate if currency == 'USD' else None,
            gold_type=gold_type if currency == 'GAU' else None,
            date=date.today()
        )
        db.session.add(saving)

        # Satın alma giderini ekle
        title = 'Birikim - Dolar' if currency == 'USD' else 'Birikim - Altın'
        max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month_id).scalar() or 0
        transaction = Transaction(
            month_id=month_id,
            title=title,
            amount=tl_amount,
            type='gider',
            date=date.today(),
            purchase_rate=purchase_rate if currency == 'USD' else None,
            gold_type=gold_type if currency == 'GAU' else None,
            gold_grams=gold_grams if currency == 'GAU' else None,
            gold_tl_value=tl_amount if currency == 'GAU' else None,
            order_index=max_order + 1
        )
        db.session.add(transaction)

    db.session.commit()
    recalculate_balances(get_month_year(month_id))
    return redirect(f'/month/{month_id}')

@app.route('/delete_saving/<int:saving_id>', methods=['POST'])
def delete_saving(saving_id):
    saving = Saving.query.get(saving_id)
    month_id = saving.month_id if saving else None
    if saving:
        # İlişkili gider kaydını da sil
        title = 'Birikim - Dolar' if saving.currency == 'USD' else 'Birikim - Altın'
        Transaction.query.filter_by(
            month_id=saving.month_id,
            title=title,
            amount=saving.tl_amount
        ).delete()
        db.session.delete(saving)
        db.session.commit()
        recalculate_balances(get_month_year(month_id))
    return redirect(f'/month/{month_id}') if month_id else redirect('/')

@app.route('/get_saving/<int:saving_id>')
def get_saving(saving_id):
    saving = Saving.query.get(saving_id)
    if saving:
        return jsonify({
            'id': saving.id,
            'currency': saving.currency,
            'tl_amount': saving.tl_amount,
            'unit_amount': saving.unit_amount,
            'purchase_rate': saving.purchase_rate,
            'gold_type': saving.gold_type,
            'gold_grams': saving.unit_amount if saving.currency == 'GAU' else None,
            'date': saving.date.isoformat() if saving.date else None,
            'month_id': saving.month_id
        })
    return jsonify({'error': 'Birikim bulunamadı'}), 404

@app.route('/update_saving', methods=['POST'])
def update_saving():
    # Güncelleme mantığı add_saving içinde saving_id ile ele alındı
    return add_saving()

@app.route('/quick_saving_buy', methods=['POST'])
def quick_saving_buy():
    data = request.get_json(silent=True) or {}
    currency = str(data.get('currency') or 'USD')
    if currency == 'GRA':
        currency = 'GAU'
    unit_amount = float(data.get('unit_amount') or 0)
    rate = float(data.get('rate') or 0)
    month_id = int(data.get('month_id') or 0)

    if unit_amount <= 0 or rate <= 0:
        return jsonify(success=False, error='Geçersiz miktar.'), 400

    if not month_id:
        latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
        month_id = latest_month.id if latest_month else 0
    if not month_id:
        return jsonify(success=False, error='Aktif ay bulunamadı.'), 400

    tl_amount = round(unit_amount * rate, 2)
    if currency == 'GAU':
        saving = Saving(
            month_id=month_id,
            currency=currency,
            tl_amount=tl_amount,
            unit_amount=unit_amount,
            purchase_rate=None,
            gold_type='Gram',
            date=date.today()
        )
    else:
        saving = Saving(
            month_id=month_id,
            currency=currency,
            tl_amount=tl_amount,
            unit_amount=unit_amount,
            purchase_rate=rate,
            gold_type=None,
            date=date.today()
        )
    db.session.add(saving)

    if currency == 'USD':
        title = 'Birikim - Dolar'
    elif currency == 'GAU':
        title = 'Birikim - Altın'
    else:
        title = f'Birikim - {currency}'
    max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month_id).scalar() or 0
    transaction = Transaction(
        month_id=month_id,
        title=title,
        amount=tl_amount,
        type='gider',
        date=date.today(),
        purchase_rate=rate if currency != 'GAU' else None,
        gold_type='Gram' if currency == 'GAU' else None,
        gold_grams=unit_amount if currency == 'GAU' else None,
        gold_tl_value=rate if currency == 'GAU' else None,
        order_index=max_order + 1
    )
    db.session.add(transaction)

    db.session.commit()
    recalculate_balances(get_month_year(month_id))
    return jsonify(success=True)



@app.route('/quick_saving_sell', methods=['POST'])
def quick_saving_sell():
    data = request.get_json(silent=True) or {}
    currency = str(data.get('currency') or 'USD')
    if currency == 'GRA':
        currency = 'GAU'
    unit_amount = float(data.get('unit_amount') or 0)
    rate = float(data.get('rate') or 0)
    month_id = int(data.get('month_id') or 0)

    if unit_amount <= 0 or rate <= 0:
        return jsonify(success=False, error='Geçersiz miktar.'), 400

    total_available = sum((s.unit_amount or 0) for s in Saving.query.filter_by(currency=currency).all())
    if total_available + 1e-9 < unit_amount:
        return jsonify(success=False, error='Yetersiz birikim.'), 400

    remaining = unit_amount
    savings = Saving.query.filter_by(currency=currency).order_by(Saving.date.desc(), Saving.id.desc()).all()
    for saving in savings:
        if remaining <= 0:
            break
        current_units = saving.unit_amount or 0
        if current_units <= 0:
            continue
        if current_units <= remaining + 1e-9:
            remaining -= current_units
            db.session.delete(saving)
            continue
        unit_cost = (saving.tl_amount or 0) / current_units if current_units else 0
        saving.unit_amount = current_units - remaining
        saving.tl_amount = max(0.0, (saving.tl_amount or 0) - unit_cost * remaining)
        remaining = 0
        db.session.add(saving)

    if not month_id:
        latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
        month_id = latest_month.id if latest_month else 0
    if not month_id:
        return jsonify(success=False, error='Aktif ay bulunamadı.'), 400

    tl_amount = round(unit_amount * rate, 2)
    if currency == 'USD':
        title = 'Birikim Bozdurma - Dolar'
    elif currency == 'GAU':
        title = 'Birikim Bozdurma - Altın'
    else:
        title = f'Birikim Bozdurma - {currency}'
    max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month_id).scalar() or 0
    transaction = Transaction(
        month_id=month_id,
        title=title,
        amount=tl_amount,
        type='gelir',
        date=date.today(),
        purchase_rate=rate if currency != 'GAU' else None,
        gold_type='Gram' if currency == 'GAU' else None,
        gold_grams=unit_amount if currency == 'GAU' else None,
        gold_tl_value=rate if currency == 'GAU' else None,
        order_index=max_order + 1
    )
    db.session.add(transaction)

    db.session.commit()
    recalculate_balances(get_month_year(month_id))
    return jsonify(success=True)



@app.route('/quick_debt_add', methods=['POST'])
def quick_debt_add():
    data = request.get_json(silent=True) or {}
    currency = str(data.get('currency') or 'USD')
    if currency == 'GRA':
        currency = 'GAU'
    amount = float(data.get('amount') or 0)
    name = str(data.get('name') or '')

    if amount <= 0:
        return jsonify(success=False, error='Geçersiz miktar.'), 400

    if not name.strip():
        name = 'Altın Borcu' if currency == 'GAU' else f'Döviz Borcu - {currency}'

    debt = Debt.query.filter_by(name=name, currency=currency).first()
    if debt:
        debt.total_amount = (debt.total_amount or 0) + amount
        debt.remaining_amount = (debt.remaining_amount or 0) + amount
        debt.gold_type = 'Gram' if currency == 'GAU' else None
        db.session.add(debt)
    else:
        debt = Debt(
            name=name,
            total_amount=amount,
            remaining_amount=amount,
            is_credit=False,
            total_installments=0,
            installment_amount=0.0,
            installments_paid=0,
            due_day=date.today().day,
            currency=currency,
            gold_type='Gram' if currency == 'GAU' else None
        )
        db.session.add(debt)

    db.session.commit()
    refresh_all_debts()
    return jsonify(success=True)



@app.route('/quick_debt_pay', methods=['POST'])
def quick_debt_pay():
    data = request.get_json(silent=True) or {}
    currency = str(data.get('currency') or 'USD')
    if currency == 'GRA':
        currency = 'GAU'
    amount = float(data.get('amount') or 0)
    rate = float(data.get('rate') or 0)
    month_id = int(data.get('month_id') or 0)

    if amount <= 0 or rate <= 0:
        return jsonify(success=False, error='Geçersiz miktar.'), 400

    debt = Debt.query.filter_by(currency=currency).order_by(Debt.remaining_amount.desc()).first()
    if not debt:
        return jsonify(success=False, error='Borç bulunamadı.'), 404

    if (debt.remaining_amount or 0) <= 0:
        return jsonify(success=False, error='Borç zaten kapalı.'), 400

    if not month_id:
        latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
        month_id = latest_month.id if latest_month else 0
    if not month_id:
        return jsonify(success=False, error='Aktif ay bulunamadı.'), 400

    pay_units = min(amount, debt.remaining_amount or amount)
    if pay_units <= 0:
        return jsonify(success=False, error='Geçersiz ödeme.'), 400
    tl_amount = round(pay_units * rate, 2)
    max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month_id).scalar() or 0

    transaction = Transaction(
        month_id=month_id,
        title=f'Borç ödemesi - {debt.name}',
        amount=tl_amount,
        type='gider',
        date=date.today(),
        purchase_rate=rate if currency != 'GAU' else None,
        gold_type=debt.gold_type if currency == 'GAU' else None,
        gold_grams=pay_units if currency == 'GAU' else None,
        gold_tl_value=rate if currency == 'GAU' else None,
        is_recurring=False,
        debt_id=debt.id,
        order_index=max_order + 1
    )
    db.session.add(transaction)

    db.session.commit()
    refresh_all_debts()
    recalculate_balances(get_month_year(month_id))
    return jsonify(success=True)



@app.route('/reorder_transactions', methods=['POST'])
def reorder_transactions():
    data = request.json or {}
    order_list = data.get('order', [])
    if not isinstance(order_list, list):
        return jsonify({'success': False}), 400
    for idx, tx_id in enumerate(order_list):
        tx = Transaction.query.get(tx_id)
        if tx:
            tx.order_index = idx + 1
            db.session.add(tx)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/reorder_recurring', methods=['POST'])
def reorder_recurring():
    data = request.json or {}
    order_list = data.get('order', [])
    if not isinstance(order_list, list):
        return jsonify({'success': False}), 400
    for idx, rec_id in enumerate(order_list):
        rec = RecurringPayment.query.get(rec_id)
        if rec:
            rec.order_index = idx + 1
            db.session.add(rec)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/add_debt', methods=['POST'])
def add_debt():
    debt_id = request.form.get('debt_id')
    name = str(request.form.get('name') or '')
    currency = str(request.form.get('currency') or 'TRY')
    total_amount = float(request.form.get('total_amount') or 0)
    gold_type = request.form.get('gold_type') or None
    is_credit = request.form.get('is_credit') == 'on'
    total_installments = int(request.form.get('total_installments') or 0)
    installment_amount = float(request.form.get('installment_amount') or 0)
    due_day = int(request.form.get('due_day') or 1)

    if is_credit and total_installments > 0 and installment_amount <= 0:
        installment_amount = round(total_amount / total_installments, 2)

    if debt_id:
        # Mevcut borcu güncelle
        debt = Debt.query.get(int(debt_id))
        if debt:
            debt.name = name
            debt.currency = currency
            debt.gold_type = gold_type if currency == 'GAU' else None
            # Total amount değişirse, remaining amount'ı da güncelle
            if debt.total_amount != total_amount:
                debt.remaining_amount = total_amount
            debt.total_amount = total_amount
            debt.is_credit = is_credit
            debt.total_installments = total_installments if is_credit else 0
            debt.installment_amount = installment_amount if is_credit else 0.0
            debt.due_day = due_day if is_credit else date.today().day
            update_debt_progress(debt)
    else:
        # Yeni borç ekle
        debt = Debt(
            name=name,
            total_amount=total_amount,
            remaining_amount=total_amount,
            is_credit=is_credit,
            total_installments=total_installments if is_credit else 0,
            installment_amount=installment_amount if is_credit else 0.0,
            installments_paid=0,
            due_day=due_day if is_credit else date.today().day,
            currency=currency,
            gold_type=gold_type if currency == 'GAU' else None
        )
        db.session.add(debt)

    db.session.commit()
    return redirect(request.referrer)

@app.route('/get_debt/<int:debt_id>')
def get_debt(debt_id):
    debt = Debt.query.get(debt_id)
    if not debt:
        return jsonify({'error': 'Borç bulunamadı'}), 404
    return jsonify({
        'id': debt.id,
        'name': debt.name,
        'currency': debt.currency,
        'gold_type': debt.gold_type,
        'total_amount': debt.total_amount,
        'remaining_amount': debt.remaining_amount,
        'is_credit': debt.is_credit,
        'total_installments': debt.total_installments,
        'installment_amount': debt.installment_amount,
        'due_day': debt.due_day
    })

@app.route('/pay_debt', methods=['POST'])
def pay_debt():
    data = request.get_json(silent=True) or {}
    debt_id = data.get('debt_id')
    amount = data.get('amount')
    tl_amount = data.get('tl_amount')
    purchase_rate = data.get('purchase_rate')
    gold_tl_value = data.get('gold_tl_value')
    gold_grams = data.get('gold_grams')
    gold_type = data.get('gold_type')
    add_recurring = bool(data.get('add_recurring'))
    month_id = data.get('month_id')

    if not debt_id:
        return jsonify({'success': False, 'error': 'Borç bilgisi eksik'}), 400

    debt = Debt.query.get(int(debt_id))
    if not debt:
        return jsonify({'success': False, 'error': 'Borç bulunamadı'}), 404

    db.session.refresh(debt)

    if not amount or float(amount) <= 0:
        amount = debt.installment_amount if debt.is_credit and debt.installment_amount else debt.remaining_amount
    amount = float(amount)

    current_month_id = int(month_id) if month_id else None
    if not current_month_id:
        latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
        if latest_month:
            current_month_id = latest_month.id

    transaction_amount = float(tl_amount) if tl_amount else amount

    if debt.currency == 'USD':
        if not purchase_rate:
            rates_buy = get_exchange_rates_buying()
            purchase_rate = rates_buy.get('USD')
    if debt.currency == 'GAU':
        rates_buy = get_exchange_rates_buying()
        gold_tl_value = gold_tl_value or rates_buy.get('GAU')
        if tl_amount and gold_tl_value:
            gold_grams = float(tl_amount) / float(gold_tl_value)
            gold_type = gold_type or 'Gram'

    current_month = Month.query.get(current_month_id) if current_month_id else Month.query.order_by(Month.id.desc()).first()
    if current_month:
        max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=current_month.id).scalar() or 0
        transaction = Transaction(
            month_id=current_month.id,
            title=f'Borç ödemesi - {debt.name}',
            amount=transaction_amount,
            type='gider',
            date=date.today(),
            purchase_rate=purchase_rate,
            gold_type=gold_type,
            gold_grams=gold_grams,
            gold_tl_value=gold_tl_value,
            is_recurring=False,
            debt_id=debt.id,
            order_index=max_order + 1
        )
        db.session.add(transaction)

    if add_recurring:
        day = debt.due_day or date.today().day
        payment_name = f'Borç ödemesi - {debt.name}'
        recurring = RecurringPayment.query.filter_by(name=payment_name, type='gider').first()
        if recurring:
            recurring.amount = transaction_amount
            recurring.day_of_month = day
            recurring.is_active = True
            recurring.debt_id = debt.id
        else:
            db.session.add(RecurringPayment(
                name=payment_name,
                amount=transaction_amount,
                type='gider',
                day_of_month=day,
                is_active=True,
                debt_id=debt.id
            ))

    db.session.commit()
    refresh_all_debts()
    recalculate_balances(get_month_year(month_id))

    return jsonify({
        'success': True,
        'debt_id': debt.id,
        'remaining_amount': debt.remaining_amount,
        'total_installments': debt.total_installments,
        'installments_paid': debt.installments_paid
    })

@app.route('/add_recurring', methods=['POST'])
def add_recurring():
    recurring_id = request.form.get('recurring_id')
    name = str(request.form.get('name') or '')
    amount = float(request.form.get('amount') or 0)
    type_ = str(request.form.get('type') or 'gelir')
    day_of_month = int(request.form.get('day_of_month') or 1)
    start_month = request.form.get('start_month') or None
    end_month = request.form.get('end_month') or None
    category_id = request.form.get('category_id')
    debt_id = request.form.get('debt_id')
    unit_currency = request.form.get('unit_currency')
    unit_grams = request.form.get('unit_grams')
    category_id = int(category_id) if category_id else None
    debt_id = int(debt_id) if debt_id else None
    unit_grams = float(unit_grams) if unit_grams else None

    if not category_id:
        return redirect(request.referrer or '/')
    if not name:
        cat = ExpenseCategory.query.get(category_id)
        if cat:
            name = cat.name
        elif debt_id:
            debt = Debt.query.get(debt_id)
            if debt:
                name = f'Borç ödemesi - {debt.name}'
        elif unit_currency:
            name = f'Birim - {unit_currency}'

    # Varsayılan aralık: mevcut yılın Ocak-Aralık
    if not start_month:
        start_month = f"{date.today().year}-01"
    if not end_month:
        end_month = f"{date.today().year}-12"

    is_active = request.form.get('is_active') == '1' or request.form.get('is_active') == 'on'
    if recurring_id:
        # Mevcut düzenli ödemeyi güncelle
        recurring = RecurringPayment.query.get(int(recurring_id))
        if recurring:
            old_name = recurring.name
            old_debt_id = recurring.debt_id
            recurring.name = name
            recurring.amount = amount
            recurring.type = type_
            recurring.day_of_month = day_of_month
            recurring.category_id = category_id
            recurring.debt_id = debt_id
            recurring.unit_currency = unit_currency
            recurring.unit_grams = unit_grams
            recurring.start_month = start_month
            recurring.end_month = end_month
            recurring.is_active = is_active
            db.session.commit()
            # Eski kayıtları sil (isim veya borç referansı eşleşen)
            Transaction.query.filter(
                Transaction.is_recurring.is_(True),
                (Transaction.title == old_name) | (Transaction.debt_id == old_debt_id)
            ).delete()
    else:
        # Yeni düzenli ödeme ekle
        recurring = RecurringPayment(
            name=name,
            amount=amount,
            type=type_,
            day_of_month=day_of_month or 15,
            is_active=True,
            category_id=category_id,
            debt_id=debt_id,
            unit_currency=unit_currency,
            unit_grams=unit_grams,
            start_month=start_month,
            end_month=end_month,
            order_index=(db.session.query(db.func.max(RecurringPayment.order_index)).scalar() or 0) + 1
        )
        db.session.add(recurring)

    db.session.commit()

    # Düzenli ödemeyi tüm aylara otomatik ekle
    check_recurring_payments(date.today().year)

    return redirect(request.referrer)

@app.route('/get_recurring/<int:recurring_id>')
def get_recurring(recurring_id):
    recurring = RecurringPayment.query.get(recurring_id)
    if recurring:
        return jsonify({
            'id': recurring.id,
            'name': recurring.name,
            'amount': recurring.amount,
            'type': recurring.type,
            'day_of_month': recurring.day_of_month,
            'is_active': recurring.is_active,
            'category_id': recurring.category_id,
            'debt_id': recurring.debt_id,
            'unit_currency': recurring.unit_currency,
            'unit_grams': recurring.unit_grams,
            'start_month': recurring.start_month,
            'end_month': recurring.end_month,
            'order_index': recurring.order_index
        })
    return jsonify({'error': 'Düzenli ödeme bulunamadı'}), 404

@app.route('/toggle_recurring_active/<int:recurring_id>', methods=['POST'])
def toggle_recurring_active(recurring_id):
    data = request.json or {}
    is_active = bool(data.get('is_active'))
    month_id = data.get('month_id')
    if month_id:
        try:
            month_id = int(month_id)
        except Exception:
            month_id = None
    recurring = RecurringPayment.query.get(recurring_id)
    if not recurring:
        return jsonify({'success': False, 'message': 'Düzenli ödeme bulunamadı'}), 404
    recurring.is_active = is_active
    db.session.commit()
    if is_active:
        if not month_id:
            latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
            month_id = latest_month.id if latest_month else None
        check_recurring_payments(get_month_year(month_id, fallback=date.today().year))
    return jsonify({'success': True})

@app.route('/delete_recurring/<int:recurring_id>', methods=['POST'])
def delete_recurring(recurring_id):
    recurring = RecurringPayment.query.get(recurring_id)
    if recurring:
        # Tüm aylardan bu düzenli işlemin transaction'larını sil
        Transaction.query.filter_by(
            title=recurring.name,
            is_recurring=True
        ).delete()
        if recurring.debt_id:
            Transaction.query.filter_by(
                debt_id=recurring.debt_id,
                is_recurring=True
            ).delete()

        # Düzenli işlemi sil
        db.session.delete(recurring)
        db.session.commit()

        # Sıralamayı yeniden sıklaştır
        ordered = RecurringPayment.query.order_by(RecurringPayment.order_index.asc()).all()
        for idx, item in enumerate(ordered):
            item.order_index = idx + 1
            db.session.add(item)
        db.session.commit()

        # Bakiye yeniden hesapla
        recalculate_balances(date.today().year)
    return redirect(request.referrer)

@app.route('/apply_recurring/<int:recurring_id>', methods=['POST'])
def apply_recurring(recurring_id):
    """Planlı ödemeyi anında gelir/gider olarak ekle."""
    data = request.json or {}
    month_id = data.get('month_id')
    recurring = RecurringPayment.query.get(recurring_id)
    if not recurring:
        return jsonify({'success': False, 'message': 'Kayıt bulunamadı'}), 404

    target_month = Month.query.get(month_id) if month_id else Month.query.order_by(Month.id.desc()).first()
    if not target_month:
        return jsonify({'success': False, 'message': 'Ay bulunamadı'}), 400

    transaction = Transaction(
        month_id=target_month.id,
        title=recurring.name,
        amount=recurring.amount,
        type=recurring.type,
        date=date.today(),
        is_recurring=False,
        order_index=(db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=target_month.id).scalar() or 0) + 1
    )
    db.session.add(transaction)
    month_index = next((i for i, name in enumerate(MONTH_NAMES) if name == target_month.name), None)
    month_number = (month_index + 1) if month_index is not None else ((target_month.id % 12) or 12)
    recurring.last_applied_month = f"{target_month.year}-{month_number:02d}"
    db.session.commit()
    recalculate_balances(target_month.year)

    return jsonify({'success': True})

@app.route('/get_selling_rates')
def get_selling_rates():
    """Satış fiyatlarını döndür"""
    rates = get_exchange_rates()
    return jsonify(rates)

@app.route('/month_view/<int:month_id>')
def month_view(month_id):
    return month_detail(month_id)

# legacy below
    return render_template_string(HTML_TEMPLATE,
        active_month=month,
        months=months,
        transactions=transactions,
        debts=debts,
        recurring_payments=recurring_payments,
        savings=savings,
        rates=rates,
        total_saving_usd=total_saving_usd,
        total_saving_gau=total_saving_gau,
        total_saving_tl=total_saving_tl,
        usd_rate=usd_rate,
        eur_rate=eur_rate,
        gau_rate=gau_rate,
        saving_totals=saving_totals,
        debt_totals=debt_totals,
        rates_map=rates_map,
        today=today,
        current_year=current_year,
        months_dict=months_dict
    )
    debt = Debt.query.get(debt_id)
    if debt:
        # Database'den taze veri oku
        db.session.refresh(debt)

        if not amount or amount <= 0:
            amount = debt.installment_amount if debt.is_credit and debt.installment_amount else debt.remaining_amount
        amount = float(amount)

        # Bu ay için bu borç ödemesi kaç kez eklenmişse kontrol et
        current_month_id = month_id
        if not current_month_id:
            latest_month = Month.query.order_by(Month.year.desc(), Month.id.desc()).first()
            if latest_month:
                current_month_id = latest_month.id

        # Transaction için TL tutarını kullan (dolar/altın için tl_amount, TL için direkt amount)
        transaction_amount = float(tl_amount) if tl_amount else amount

        # Döviz/altın borçları için kur hesapla
        if debt.currency == 'USD':
            if not purchase_rate:
                rates_buy = get_exchange_rates_buying()
                purchase_rate = rates_buy.get('USD')
        if debt.currency == 'GAU':
            rates_buy = get_exchange_rates_buying()
            gold_tl_value = gold_tl_value or rates_buy.get('GAU')
            if tl_amount and gold_tl_value:
                gold_grams = float(tl_amount) / float(gold_tl_value)
                gold_type = gold_type or 'Gram'

        # Gider olarak kaydet (TL cinsinden)
        current_month = Month.query.get(month_id) if month_id else Month.query.order_by(Month.id.desc()).first()
        if current_month:
            max_order = db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=current_month.id).scalar() or 0
            transaction = Transaction(
                month_id=current_month.id,
                title=f'Borç ödemesi - {debt.name}',
                amount=transaction_amount,  # TL tutarı
                type='gider',
                date=date.today(),
                purchase_rate=purchase_rate,
                gold_type=gold_type,
                gold_grams=gold_grams,
                gold_tl_value=gold_tl_value,
                is_recurring=False,  # Manuel ödeme işlemi olarak kaydet (yineleme kontrol etmez)
                debt_id=debt.id,  # Borç referansını set et
                order_index=max_order + 1
            )
            db.session.add(transaction)

        if add_recurring:
            day = debt.due_day or date.today().day
            payment_name = f'Borç ödemesi - {debt.name}'
            recurring = RecurringPayment.query.filter_by(name=payment_name, type='gider').first()
            if recurring:
                recurring.amount = transaction_amount  # TL tutarı
                recurring.day_of_month = day
                recurring.is_active = True
                recurring.debt_id = debt.id  # Borç referansını set et
            else:
                db.session.add(RecurringPayment(
                    name=payment_name,
                    amount=transaction_amount,  # TL tutarı
                    type='gider',
                    day_of_month=day,
                    is_active=True,
                    debt_id=debt.id  # Borç referansını set et
                ))

        db.session.commit()

        # Ödemeler sonrası tüm borçları yeniden hesapla
        refresh_all_debts()

        recalculate_balances(get_month_year(month_id))

        # Güncellenmiş borç bilgisini dön
        return jsonify({
            'success': True,
            'debt_id': debt.id,
            'remaining_amount': debt.remaining_amount,
            'total_installments': debt.total_installments,
            'installments_paid': debt.installments_paid
        })

    return jsonify({'success': False, 'error': 'Borç bulunamadı'})

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    transaction = Transaction.query.get(transaction_id)
    month_id = transaction.month_id if transaction else None

    if transaction:
        if transaction.is_recurring:
            recurring = RecurringPayment.query.filter_by(name=transaction.title, type=transaction.type).first()
            if recurring:
                recurring.is_active = False
        db.session.delete(transaction)
        db.session.commit()

        if transaction.debt_id:
            refresh_all_debts()
        recalculate_balances(get_month_year(month_id))

    return redirect(f'/month/{month_id}') if month_id else redirect('/')

@app.route('/get_transaction/<int:transaction_id>')
def get_transaction(transaction_id):
    tx = Transaction.query.get(transaction_id)
    if tx:
        # İlgili recurring kaydını bul (ayrı isim tutulmadığından kategori/tip bazlı)
        rec = RecurringPayment.query.filter_by(name=tx.title, type=tx.type).first()
        cat_id = tx.category_id
        if not cat_id:
            cat_match = ExpenseCategory.query.filter_by(name=tx.title, type=tx.type).first()
            if not cat_match and tx.type == 'gider':
                cat_match = ExpenseCategory.query.filter_by(name=tx.title, type='gider').first()
            if not cat_match and tx.type == 'gelir':
                cat_match = ExpenseCategory.query.filter_by(name=tx.title, type='gelir').first()
            if cat_match:
                cat_id = cat_match.id
        return jsonify({
            'id': tx.id,
            'title': tx.title,
            'amount': tx.amount,
            'type': tx.type,
            'date': tx.date.isoformat() if tx.date else None,
            'category_id': cat_id,
            'is_recurring': tx.is_recurring or bool(rec),
            'recurring_day': rec.day_of_month if rec else None,
            'recurring_start': rec.start_month if rec else None,
            'recurring_end': rec.end_month if rec else None
        })
    return jsonify({'error': 'İşlem bulunamadı'}), 404

@app.route('/update_transaction', methods=['POST'])
def update_transaction():
    tx_id = request.form.get('transaction_id')
    if not tx_id:
        return redirect(request.referrer or '/')
    transaction = Transaction.query.get(int(tx_id))
    if not transaction:
        return redirect(request.referrer or '/')
    month_id = transaction.month_id

    transaction.title = request.form.get('title') or transaction.title
    transaction.amount = float(request.form.get('amount') or transaction.amount or 0)
    transaction.type = request.form.get('type') or transaction.type
    transaction.date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date() if request.form.get('date') else transaction.date
    category_id = request.form.get('category_id')
    if not category_id:
        return redirect(request.referrer or '/')
    transaction.category_id = int(category_id)
    transaction.is_recurring = request.form.get('is_recurring') == 'on'
    rec_day = int(request.form.get('recurring_day') or date.today().day)
    rec_start = request.form.get('recurring_start') or None
    rec_end = request.form.get('recurring_end') or None
    # Sipariş numarası aynı kalsın; drag-drop ile ayrıca güncellenebilir

    # Eğer birikim gideri ise ilgili birikimi güncelle
    if transaction.title in ('Birikim - Dolar', 'Birikim - Altın'):
        currency = 'USD' if 'Dolar' in transaction.title else 'GAU'
        saving = Saving.query.filter_by(month_id=transaction.month_id, currency=currency).order_by(Saving.date.desc()).first()
        if saving:
            saving.tl_amount = transaction.amount
            if currency == 'USD':
                saving.unit_amount = saving.purchase_rate and saving.purchase_rate > 0 and (saving.tl_amount / saving.purchase_rate) or saving.unit_amount
            else:
                saving.gold_type = saving.gold_type or 'Gram'
                saving.unit_amount = saving.unit_amount  # gram değişmediyse koru
            db.session.add(saving)

    db.session.commit()

    # Düzenli ödeme ise RecurringPayment ekle/güncelle
    if transaction.is_recurring:
        rec = RecurringPayment.query.filter_by(name=transaction.title, type=transaction.type).first()
        if rec:
            rec.amount = transaction.amount
            rec.day_of_month = rec_day
            rec.start_month = rec_start
            rec.end_month = rec_end
            rec.category_id = transaction.category_id
            rec.is_active = True
            db.session.add(rec)
        else:
            rec = RecurringPayment(
                name=transaction.title,
                amount=transaction.amount,
                type=transaction.type,
                day_of_month=rec_day,
                start_month=rec_start,
                end_month=rec_end,
                is_active=True,
                category_id=transaction.category_id,
                order_index=(db.session.query(db.func.max(RecurringPayment.order_index)).scalar() or 0) + 1
            )
            db.session.add(rec)
        db.session.commit()
        check_recurring_payments(get_month_year(month_id))
    else:
        # Eğer daha önce recurring kaydı vardı ve kaldırıldıysa pasif hale getir
        rec = RecurringPayment.query.filter_by(name=transaction.title, type=transaction.type).first()
        if rec:
            rec.is_active = False
            db.session.commit()

    recalculate_balances(get_month_year(month_id))
    return redirect(f'/month/{transaction.month_id}')

@app.route('/delete_debt/<int:debt_id>', methods=['POST'])
def delete_debt(debt_id):
    debt = Debt.query.get(debt_id)
    if debt:
        db.session.delete(debt)
        db.session.commit()
    return redirect(request.referrer)

@app.route('/get_expense_categories')
def get_expense_categories():
    categories = ExpenseCategory.query.filter_by(type='gider').order_by(ExpenseCategory.name).all()
    return jsonify([{'id': c.id, 'name': c.name} for c in categories])

@app.route('/add_expense_category', methods=['POST'])
def add_expense_category():
    data = request.json or {}
    name = str(data.get('name', '')).strip()

    if not name:
        return jsonify({'success': False, 'message': 'Kategori adı boş olamaz'})

    # Var olan kategoriyi kontrol et
    existing = ExpenseCategory.query.filter_by(name=name, type='gider').first()
    if existing:
        return jsonify({'success': False, 'message': 'Bu kategori zaten var'})

    category = ExpenseCategory(name=name, type='gider')
    db.session.add(category)
    db.session.commit()

    return jsonify({'success': True, 'category_id': category.id, 'name': category.name})

@app.route('/get_income_categories')
def get_income_categories():
    categories = ExpenseCategory.query.filter_by(type='gelir').order_by(ExpenseCategory.name).all()
    return jsonify([{'id': c.id, 'name': c.name} for c in categories])

@app.route('/add_category', methods=['POST'])
def add_category():
    data = request.json or {}
    name = str(data.get('name', '')).strip()
    type_ = str(data.get('type', 'gider')).strip().lower()
    if type_ not in ['gider', 'gelir']:
        type_ = 'gider'

    if not name:
        return jsonify({'success': False, 'message': 'Kategori adı boş olamaz'})

    existing = ExpenseCategory.query.filter_by(name=name, type=type_).first()
    if existing:
        return jsonify({'success': False, 'message': 'Bu kategori zaten var'})

    category = ExpenseCategory(name=name, type=type_)
    db.session.add(category)
    db.session.commit()

    return jsonify({'success': True, 'category_id': category.id, 'name': category.name})

@app.route('/add_income_category', methods=['POST'])
def add_income_category():
    data = request.json or {}
    name = str(data.get('name', '')).strip()

    if not name:
        return jsonify({'success': False, 'message': 'Kategori adı boş olamaz'})

    # Var olan kategoriyi kontrol et
    existing = ExpenseCategory.query.filter_by(name=name, type='gelir').first()
    if existing:
        return jsonify({'success': False, 'message': 'Bu kategori zaten var'})

    category = ExpenseCategory(name=name, type='gelir')
    db.session.add(category)
    db.session.commit()

    return jsonify({'success': True, 'category_id': category.id, 'name': category.name})

@app.route('/update_category/<int:category_id>', methods=['POST'])
def update_category(category_id):
    data = request.json or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'success': False, 'message': 'Yeni ad boş olamaz'})
    category = ExpenseCategory.query.get(category_id)
    if not category:
        return jsonify({'success': False, 'message': 'Kategori bulunamadı'}), 404
    # Aynı adda başka kategori var mı kontrol et
    existing = ExpenseCategory.query.filter(ExpenseCategory.id != category_id, ExpenseCategory.name == name, ExpenseCategory.type == category.type).first()
    if existing:
        return jsonify({'success': False, 'message': 'Bu isimde bir kategori zaten var'})
    category.name = name
    db.session.commit()
    return jsonify({'success': True})

@app.route('/rename_category/<int:category_id>', methods=['POST'])
def rename_category(category_id):
    return update_category(category_id)

@app.route('/delete_category/<int:category_id>', methods=['POST'])
def delete_category(category_id):
    category = ExpenseCategory.query.get(category_id)
    if not category:
        return jsonify({'success': False, 'message': 'Kategori bulunamadı'}), 404
    db.session.delete(category)
    db.session.commit()
    return jsonify({'success': True})

def init_categories():
    """İlk çalışmada varsayılan gider kategorilerini oluştur"""
    default_expense_categories = [
        'Ev Kirası',
        'Okul Taksiti',
        'Gıda',
        'Ulaşım',
        'Elektrik',
        'Su',
        'İnternet',
        'Sağlık',
        'Eğitim',
        'Eğlence',
        'Diğer'
    ]

    for cat_name in default_expense_categories:
        existing = ExpenseCategory.query.filter_by(name=cat_name, type='gider').first()
        if not existing:
            category = ExpenseCategory(name=cat_name, type='gider')
            db.session.add(category)

    default_income_categories = [
        'Maaş',
        'Prim',
        'Bonus',
        'Kira Geliri',
        'Faiz',
        'Diğer Gelir'
    ]
    for cat_name in default_income_categories:
        existing = ExpenseCategory.query.filter_by(name=cat_name, type='gelir').first()
        if not existing:
            category = ExpenseCategory(name=cat_name, type='gelir')
            db.session.add(category)

    db.session.commit()


# ---------- YARDIMCI FONKSİYONLAR ----------
def update_debt_progress(debt):
    """Borç için ödenen tutarı ve taksit adedini güncelle."""
    total_amount = debt.total_amount or 0
    today = date.today()
    paid_amount_unit = 0.0
    payments_this_month = 0
    payments_total_count = 0

    # Gerçekleşmiş ödemeleri borcun para biriminde hesapla (gelecek tarihli işlemleri sayma)
    txs = Transaction.query.filter_by(debt_id=debt.id, type='gider').all()
    for tx in txs:
        if tx.date and tx.date > today:
            continue
        if tx.date and tx.date.year == today.year and tx.date.month == today.month:
            payments_this_month += 1
        payments_total_count += 1
        if debt.currency == 'GAU':
            if tx.gold_grams:
                paid_amount_unit += tx.gold_grams
            elif tx.gold_tl_value and tx.gold_tl_value > 0 and tx.amount:
                paid_amount_unit += tx.amount / tx.gold_tl_value
        elif debt.currency == 'TRY':
            paid_amount_unit += (tx.amount or 0)
        else:
            if tx.purchase_rate and tx.purchase_rate > 0:
                paid_amount_unit += (tx.amount or 0) / tx.purchase_rate
            else:
                paid_amount_unit += (tx.amount or 0)

    remaining_amount = max(0.0, total_amount - paid_amount_unit)
    paid_amount = max(0.0, total_amount - remaining_amount)
    debt.remaining_amount = remaining_amount
    installments_paid = 0

    if debt.is_credit and (debt.total_installments or 0) > 0 and total_amount > 0:
        # Gerçekleşen ödeme aylarına göre üst sınır (gelecek aylar sayılmasın)
        first_tx = Transaction.query.filter_by(debt_id=debt.id, type='gider') \
            .order_by(Transaction.date.asc()).first()
        start_date = first_tx.date if first_tx and first_tx.date else (debt.created_at or today)
        months_elapsed = max(0, (today.year - start_date.year) * 12 + (today.month - start_date.month) + 1)
        if debt.currency == 'TRY' and (debt.installment_amount or 0) > 0:
            installments_paid_by_amount = int(paid_amount / debt.installment_amount)
            installments_paid = min(
                debt.total_installments,
                months_elapsed + max(0, payments_this_month - 1),
                installments_paid_by_amount,
                payments_total_count
            )
        else:
            paid_ratio = paid_amount / total_amount
            installments_paid_ratio = int(paid_ratio * debt.total_installments)
            installments_paid = min(
                debt.total_installments,
                months_elapsed + max(0, payments_this_month - 1),
                installments_paid_ratio,
                payments_total_count
            )

    debt.installments_paid = max(0, installments_paid)
    # Şablonda kullanmak için hesaplanan tutarı sakla (veritabanı kolonu değil)
    debt.paid_amount_calculated = paid_amount
    debt.payments_this_month = payments_this_month
    return paid_amount, debt.installments_paid

def refresh_all_debts():
    """Tüm borçlar için ödenen/kalan ve taksit durumunu tazele."""
    debts = Debt.query.all()
    for debt in debts:
        update_debt_progress(debt)
        db.session.add(debt)
    db.session.commit()

def ensure_debt_columns():
    db_path = os.path.join(app.instance_path, 'portfoy_tr.db')
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute('PRAGMA table_info(borclar)')
        existing = {row[1] for row in cur.fetchall()}
        if 'altin_cinsi' not in existing:
            try:
                cur.execute("ALTER TABLE borclar ADD COLUMN altin_cinsi VARCHAR(50)")
            except sqlite3.OperationalError:
                pass
        con.commit()
    finally:
        con.close()

def ensure_transaction_columns():
    """Transaction tablosu kolon kontrolleri."""
    db_path = os.path.join(app.instance_path, 'portfoy_tr.db')
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute('PRAGMA table_info(islemler)')
        existing = {row[1] for row in cur.fetchall()}
        if 'borc_id' not in existing:
            cur.execute("ALTER TABLE islemler ADD COLUMN borc_id INTEGER REFERENCES borclar(id)")
        if 'sira' not in existing:
            try:
                cur.execute("ALTER TABLE islemler ADD COLUMN sira INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        con.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()

def ensure_category_columns():
    """Eski tabloda 'tur' kolonu yoksa ekle ve varsayılanı gider olarak ata."""
    db_path = os.path.join(app.instance_path, 'portfoy_tr.db')
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute('PRAGMA table_info(gider_kategorileri)')
        existing = {row[1] for row in cur.fetchall()}
        if 'tur' not in existing:
            cur.execute("ALTER TABLE gider_kategorileri ADD COLUMN tur VARCHAR(10) DEFAULT 'gider'")
            cur.execute("UPDATE gider_kategorileri SET tur='gider' WHERE tur IS NULL")
            con.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()

def ensure_recurring_columns():
    """Duzenli odemeler tablosu için yeni kolonları ekle."""
    db_path = os.path.join(app.instance_path, 'portfoy_tr.db')
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute('PRAGMA table_info(duzenli_odemeler)')
        existing = {row[1] for row in cur.fetchall()}
        columns = {
            'kategori_id': "ALTER TABLE duzenli_odemeler ADD COLUMN kategori_id INTEGER",
            'borc_id': "ALTER TABLE duzenli_odemeler ADD COLUMN borc_id INTEGER",
            'birim_para': "ALTER TABLE duzenli_odemeler ADD COLUMN birim_para VARCHAR(10)",
            'birim_gram': "ALTER TABLE duzenli_odemeler ADD COLUMN birim_gram FLOAT",
            'baslangic_ayi': "ALTER TABLE duzenli_odemeler ADD COLUMN baslangic_ayi VARCHAR(7)",
            'bitis_ayi': "ALTER TABLE duzenli_odemeler ADD COLUMN bitis_ayi VARCHAR(7)",
            'sira': "ALTER TABLE duzenli_odemeler ADD COLUMN sira INTEGER DEFAULT 0"
        }
        for name, sql in columns.items():
            if name not in existing:
                try:
                    cur.execute(sql)
                except sqlite3.OperationalError:
                    pass
        con.commit()
    finally:
        con.close()

def ensure_saving_table():
    """Birikimler tablosunu oluştur (SQLAlchemy create_all yeterli, burada sadece çağrı için)."""
    db.create_all()

def fix_month_names(year):
    months = Month.query.filter_by(year=year).order_by(Month.id).all()
    changed = False
    for index, month in enumerate(months):
        expected = MONTH_NAMES[index % 12]
        if month.name != expected:
            month.name = expected
            changed = True
    if changed:
        db.session.commit()

def ensure_year_months(year):
    for i, month_name in enumerate(MONTH_NAMES, 1):
        if not Month.query.filter_by(name=month_name, year=year).first():
            month = Month(name=month_name, year=year)
            db.session.add(month)
    db.session.commit()

def get_month_year(month_id, fallback=None):
    if month_id:
        month = Month.query.get(int(month_id))
        if month:
            return month.year
    return fallback or date.today().year

def get_exchange_rates(return_all=False):
    """Döviz kurlarını al - Selling fiyatı (satış için). return_all=True ile tüm kodları döndürür."""
    try:
        response = requests.get('https://finans.truncgil.com/v4/today.json', timeout=5)
        data = response.json()

        def safe_float(val, default):
            try:
                return round(float(val), 4)
            except Exception:
                return default

        usd_rate = data.get('USD', {}).get('Selling', 43.27)
        eur_rate = data.get('EUR', {}).get('Selling', 50.20)
        gold_rate = data.get('GRA', {}).get('Selling', 6375.38)
        btc_rate = data.get('BTC', {}).get('TRY_Price', 4124679)

        rates = {
            'USD': safe_float(usd_rate, 43.27),
            'EUR': safe_float(eur_rate, 50.20),
            'GAU': safe_float(gold_rate, 6375.38),
            'BTC': safe_float(btc_rate, 4124679)
        }
        all_rates = []
        for code, info in data.items():
            if code in ['Update_Date', 'Timestamp']:
                continue
            if isinstance(info, dict):
                val = info.get('Selling') or info.get('TRY_Price')
                if val:
                    all_rates.append({'code': code, 'value': safe_float(val, 0)})
        return (rates, all_rates) if return_all else rates
    except Exception:
        fallback = {
            'USD': 43.27,
            'EUR': 50.20,
            'GAU': 6375.38,
            'BTC': 4124679
        }
        all_fallback = [{'code': k, 'value': v} for k, v in fallback.items()]
        return (fallback, all_fallback) if return_all else fallback

def get_exchange_rates_buying():
    """Döviz kurlarını al - Buying fiyatı (satın alma için)"""
    try:
        response = requests.get('https://finans.truncgil.com/v4/today.json', timeout=5)
        data = response.json()

        usd_rate = data.get('USD', {}).get('Buying', 43.27)
        eur_rate = data.get('EUR', {}).get('Buying', 50.20)
        gold_rate = data.get('GRA', {}).get('Buying', 6375.38)
        btc_rate = data.get('BTC', {}).get('TRY_Price', 4124679)

        return {
            'USD': round(float(usd_rate), 2),
            'EUR': round(float(eur_rate), 2),
            'GAU': round(float(gold_rate), 2),
            'BTC': round(float(btc_rate), 2)
        }
    except Exception:
        return {
            'USD': 43.27,
            'EUR': 50.20,
            'GAU': 6375.38,
            'BTC': 4124679
        }

def check_recurring_payments(year=None):
    """Aktif düzenli ödemeleri tüm aylara otomatik ekler."""
    months = Month.query.filter_by(year=year).order_by(Month.id).all()
    active_payments = RecurringPayment.query.filter_by(is_active=True).order_by(RecurringPayment.order_index.asc()).all()
    added = 0
    today = date.today()

    for month in months:
        month_index = next((i for i, m in enumerate(months) if m.id == month.id), None)
        month_number = (month_index % 12) + 1 if month_index is not None else None

        for payment in active_payments:
            # Başlangıç/bitiş ayı kontrolü (başlangıç yoksa bugünden başlat)
            try:
                if payment.start_month:
                    sy, sm = map(int, payment.start_month.split('-'))
                else:
                    today = date.today()
                    sy, sm = today.year, today.month
                if (month.year, month_number) < (sy, sm):
                    continue
                if payment.end_month:
                    ey, em = map(int, payment.end_month.split('-'))
                    if (month.year, month_number) > (ey, em):
                        continue
            except Exception:
                pass

            # Gelecek ayları borç ödemeleri için ekleme ve gerekirse temizle
            if payment.debt_id and (month.year, month_number) > (today.year, today.month):
                existing_future = Transaction.query.filter_by(
                    month_id=month.id,
                    title=payment.name,
                    is_recurring=True,
                    debt_id=payment.debt_id
                ).all()
                if existing_future:
                    debt = Debt.query.get(payment.debt_id)
                    for tx in existing_future:
                        if debt and debt.currency == 'TRY':
                            debt.remaining_amount += tx.amount
                        db.session.delete(tx)
                    if debt:
                        update_debt_progress(debt)
                        db.session.add(debt)
                    db.session.commit()
                continue

            # Bu ay için bu ödeme zaten eklenmiş mi kontrol et (aynı borç, aynı ay)
            existing = Transaction.query.filter_by(
                month_id=month.id,
                debt_id=payment.debt_id,
                type='gider'
            ).filter(Transaction.title.like('Borç ödemesi%')).first() if payment.debt_id else None

            if not existing:
                # Başka kontrol - aynı adla zaten var mı
                existing = Transaction.query.filter_by(
                    month_id=month.id,
                    title=payment.name,
                    is_recurring=True
                ).first()

            if existing:
                continue

            last_day = calendar.monthrange(month.year, month_number)[1]
            target_day = min(payment.day_of_month, last_day)
            target_date = date(month.year, month_number, target_day)

            transaction = Transaction(
                month_id=month.id,
                title=payment.name,
                amount=payment.amount,
                type=payment.type,
                date=target_date,
                is_recurring=True,
                debt_id=payment.debt_id,  # Borç referansını set et
                order_index= (db.session.query(db.func.max(Transaction.order_index)).filter_by(month_id=month.id).scalar() or 0) + 1
            )
            db.session.add(transaction)

            # Borç ödemesi ise taksit güncelle ve remaining amount'ı azalt
            if payment.debt_id:
                debt = Debt.query.get(payment.debt_id)
                if debt:
                    # Borçtan öde
                    debt.remaining_amount = max(0, debt.remaining_amount - payment.amount)
                    update_debt_progress(debt)
                    db.session.add(debt)  # Borç değişikliklerini kaydet

            added += 1

    if added > 0:
        db.session.commit()
    return added

def recalculate_balances(year):
    """Bakiye ve devreden işlemlerini ay bazında yeniden hesapla"""
    months = Month.query.filter_by(year=year).order_by(Month.id).all()
    previous_closing = 0.0

    for index, month in enumerate(months):
        month_number = (index % 12) + 1
        carryover = Transaction.query.filter_by(month_id=month.id, title=CARRYOVER_TITLE).first()

        if previous_closing != 0:
            carryover_type = 'gelir' if previous_closing >= 0 else 'gider'
            carryover_amount = abs(previous_closing)
            if carryover:
                carryover.type = carryover_type
                carryover.amount = carryover_amount
                carryover.date = date(month.year, month_number, 1)
            else:
                db.session.add(Transaction(
                    month_id=month.id,
                    title=CARRYOVER_TITLE,
                    amount=carryover_amount,
                    type=carryover_type,
                    date=date(month.year, month_number, 1)
                ))
        else:
            if carryover:
                db.session.delete(carryover)

        month.opening_balance = previous_closing

        transactions = Transaction.query.filter_by(month_id=month.id).all()
        total_income = sum(t.amount for t in transactions if t.type == 'gelir')
        total_expense = sum(t.amount for t in transactions if t.type == 'gider')
        month.closing_balance = total_income - total_expense

        previous_closing = month.closing_balance

    db.session.commit()

@app.route('/favicon.ico')
def favicon():
    return '', 204

# ---------- UYGULAMAYI BAŞLAT ----------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_debt_columns()
        # Varsayılan ayları oluştur
        if not Month.query.first():
            init_database()

    app.run(debug=True, host='0.0.0.0', port=5001)
