// Global State
let currentMonth = null;
let allMonths = [];
let categories = { gelir: [], gider: [] };
let debts = [];
let recurring = [];
let savings = [];

const API_BASE = 'http://localhost:5001';

// Utilities
function formatCurrency(value) {
  if (value === null || value === undefined) return '₺0,00';
  return '₺' + parseFloat(value).toFixed(2).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function formatDate(date) {
  if (!date) return '';
  const d = new Date(date);
  return d.getDate() + '/' + (d.getMonth() + 1) + '/' + d.getFullYear();
}

function getTodayDate() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

// API Calls
async function api(endpoint, method = 'GET', data = null) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json' }
  };
  
  if (data) options.body = JSON.stringify(data);
  
  try {
    const res = await fetch(API_BASE + endpoint, options);
    return await res.json();
  } catch (err) {
    console.error('API Error:', err);
    return { error: err.message };
  }
}

// Auth
async function checkAuth() {
  const res = await api('/auth/status');
  if (res.authenticated) {
    showMainApp();
    loadData();
  } else {
    showAuthOverlay();
    if (res.locked) {
      updateAuthUI(null, res.remaining);
    }
  }
}

function showAuthOverlay() {
  document.getElementById('authOverlay').classList.remove('hidden');
  document.getElementById('mainApp').classList.add('hidden');
}

function showMainApp() {
  document.getElementById('authOverlay').classList.add('hidden');
  document.getElementById('mainApp').classList.remove('hidden');
}

function updateAuthUI(message, remaining) {
  const errorEl = document.getElementById('authError');
  const attemptsEl = document.getElementById('authAttempts');
  
  if (message) {
    errorEl.textContent = message;
  }
  
  if (remaining !== undefined) {
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    attemptsEl.textContent = `Kilitli: ${mins}:${String(secs).padStart(2, '0')}`;
  } else {
    attemptsEl.textContent = '';
  }
}

document.getElementById('authForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const password = document.getElementById('authPassword').value;
  const res = await api('/auth/login', 'POST', { password });
  
  if (res.success) {
    document.getElementById('authPassword').value = '';
    showMainApp();
    loadData();
  } else if (res.locked) {
    updateAuthUI('Çok fazla hatalı deneme. Lütfen bekleyin.', res.remaining);
  } else if (res.remaining_attempts !== undefined) {
    updateAuthUI(`Yanlış şifre. ${res.remaining_attempts} deneme kaldı.`, null);
  } else {
    updateAuthUI('Hata oluştu', null);
  }
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
  await api('/auth/logout', 'POST');
  showAuthOverlay();
  document.getElementById('authError').textContent = '';
  document.getElementById('authAttempts').textContent = '';
});

// Data Loading
async function loadData() {
  const res = await api('/api/months');
  allMonths = res;
  loadCategories();
  loadDebts();
  loadRecurring();
  updateMonthSelect();
}

async function loadMonth(monthId) {
  const res = await api('/api/month/' + monthId);
  if (res.error) return;
  
  currentMonth = res;
  document.getElementById('currentMonth').textContent = currentMonth.ad;
  
  loadCategories();
  loadDebts();
  loadRecurring();
  
  updateSummary();
  renderTransactions();
  renderRecurring();
  renderSavings();
}

async function loadCategories() {
  const res = await api('/api/categories');
  categories.gider = res.filter(c => c.tur === 'gider');
  categories.gelir = res.filter(c => c.tur === 'gelir');
  updateCategorySelects();
}

async function loadDebts() {
  const res = await api('/api/debts');
  debts = res || [];
  renderDebts();
}

async function loadRecurring() {
  const res = await api('/api/recurring');
  recurring = res || [];
}

function updateMonthSelect() {
  const select = document.getElementById('monthSelect');
  select.innerHTML = '<option>Ay seçin</option>';
  allMonths.forEach(month => {
    const opt = document.createElement('option');
    opt.value = month.id;
    opt.textContent = month.ad;
    select.appendChild(opt);
  });
}

function updateCategorySelects() {
  // Transaction modal
  let html = '<option value="">Seçin</option>';
  categories.gider.forEach(c => {
    html += `<option value="${c.id}">${c.ad}</option>`;
  });
  document.getElementById('transCategory').innerHTML = html;
  
  // Recurring modal
  html = '<option value="">Seçin</option>';
  categories.gider.forEach(c => {
    html += `<option value="${c.id}">${c.ad}</option>`;
  });
  categories.gelir.forEach(c => {
    html += `<option value="${c.id}">${c.ad}</option>`;
  });
  document.getElementById('recurringCategory').innerHTML = html;
}

// Summary
function updateSummary() {
  if (!currentMonth) return;
  
  const transactions = currentMonth.islemler || [];
  let gelir = 0, gider = 0;
  
  transactions.forEach(t => {
    if (t.tur === 'gelir') gelir += t.tutar;
    else gider += t.tutar;
  });
  
  const opening = currentMonth.acilis_bakiye || 0;
  const closing = opening + gelir - gider;
  
  document.getElementById('openingBalance').textContent = formatCurrency(opening);
  document.getElementById('totalIncome').textContent = formatCurrency(gelir);
  document.getElementById('totalExpense').textContent = formatCurrency(gider);
  document.getElementById('closingBalance').textContent = formatCurrency(closing);
}

// Transactions
function renderTransactions() {
  if (!currentMonth) return;
  
  const transactions = currentMonth.islemler || [];
  const incomeBody = document.querySelector('#incomeTable tbody');
  const expenseBody = document.querySelector('#expenseTable tbody');
  
  incomeBody.innerHTML = '';
  expenseBody.innerHTML = '';
  
  transactions.forEach(t => {
    const tr = document.createElement('tr');
    const categoryName = categories.gider.concat(categories.gelir).find(c => c.id === t.kategori_id)?.ad || '-';
    
    tr.innerHTML = `
      <td>${t.aciklama}</td>
      <td>${categoryName}</td>
      <td>${formatCurrency(t.tutar)}</td>
      <td>${formatDate(t.tarih)}</td>
      <td>
        <button class="btn-sm" onclick="editTransaction(${t.id})">✏️</button>
        <button class="btn-sm" onclick="deleteTransaction(${t.id})">🗑️</button>
      </td>
    `;
    
    if (t.tur === 'gelir') incomeBody.appendChild(tr);
    else expenseBody.appendChild(tr);
  });
}

async function editTransaction(id) {
  const res = await api('/api/transaction/' + id);
  if (res.error) return;
  
  document.getElementById('transactionId').value = id;
  document.getElementById('monthId').value = currentMonth.id;
  document.getElementById('transDesc').value = res.aciklama;
  document.getElementById('transAmount').value = res.tutar;
  document.getElementById('transCategory').value = res.kategori_id || '';
  document.getElementById('transDate').value = res.tarih;
  document.getElementById('transactionModalTitle').textContent = 'İşlem Düzenle';
  
  openModal('transactionModal');
}

async function deleteTransaction(id) {
  if (!confirm('İşlemi silmek istediğinize emin misiniz?')) return;
  
  const res = await api('/api/delete_transaction/' + id, 'POST');
  if (res.success) {
    loadMonth(currentMonth.id);
  }
}

function openAddTransaction(type) {
  document.getElementById('transactionId').value = '';
  document.getElementById('monthId').value = currentMonth.id;
  document.getElementById('transDesc').value = '';
  document.getElementById('transAmount').value = '';
  document.getElementById('transCategory').value = '';
  document.getElementById('transDate').value = getTodayDate();
  document.getElementById('transactionModalTitle').textContent = type === 'gelir' ? 'Gelir Ekle' : 'Gider Ekle';
  
  document.getElementById('transactionForm').dataset.type = type;
  openModal('transactionModal');
}

document.getElementById('transactionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const id = document.getElementById('transactionId').value;
  const data = {
    aciklama: document.getElementById('transDesc').value,
    tutar: parseFloat(document.getElementById('transAmount').value),
    tur: document.getElementById('transactionForm').dataset.type,
    kategori_id: parseInt(document.getElementById('transCategory').value) || null,
    tarih: document.getElementById('transDate').value
  };
  
  if (id) {
    const res = await api('/api/update_transaction/' + id, 'POST', data);
  } else {
    data.ay_id = currentMonth.id;
    const res = await api('/api/add_transaction', 'POST', data);
  }
  
  closeModal('transactionModal');
  loadMonth(currentMonth.id);
});

// Recurring Payments
function renderRecurring() {
  const body = document.querySelector('#recurringTable tbody');
  body.innerHTML = '';
  
  recurring.forEach(r => {
    const categoryName = categories.gider.concat(categories.gelir).find(c => c.id === r.kategori_id)?.ad || '-';
    
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.ad}</td>
      <td>${formatCurrency(r.tutar)}</td>
      <td>${r.tur === 'gelir' ? '📈 Gelir' : '📉 Gider'}</td>
      <td>${categoryName}</td>
      <td>
        <input type="checkbox" ${r.aktif ? 'checked' : ''} onchange="toggleRecurring(${r.id}, this.checked)">
      </td>
      <td>
        <button class="btn-sm" onclick="editRecurring(${r.id})">✏️</button>
        <button class="btn-sm" onclick="deleteRecurring(${r.id})">🗑️</button>
      </td>
    `;
    body.appendChild(tr);
  });
}

function openAddRecurring() {
  document.getElementById('recurringId').value = '';
  document.getElementById('recurringName').value = '';
  document.getElementById('recurringAmount').value = '';
  document.getElementById('recurringType').value = 'gider';
  document.getElementById('recurringCategory').value = '';
  document.getElementById('recurringDay').value = '1';
  document.getElementById('recurringActive').checked = true;
  
  openModal('recurringModal');
}

async function editRecurring(id) {
  const res = await api('/api/recurring/' + id);
  if (res.error) return;
  
  document.getElementById('recurringId').value = id;
  document.getElementById('recurringName').value = res.ad;
  document.getElementById('recurringAmount').value = res.tutar;
  document.getElementById('recurringType').value = res.tur;
  document.getElementById('recurringCategory').value = res.kategori_id || '';
  document.getElementById('recurringDay').value = res.ay_gunu || '1';
  document.getElementById('recurringActive').checked = res.aktif;
  
  openModal('recurringModal');
}

async function deleteRecurring(id) {
  if (!confirm('Silmek istediğinize emin misiniz?')) return;
  const res = await api('/api/delete_recurring/' + id, 'POST');
  if (res.success) loadRecurring();
}

async function toggleRecurring(id, aktif) {
  await api('/api/toggle_recurring/' + id, 'POST', { aktif });
  loadRecurring();
}

document.getElementById('recurringForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const id = document.getElementById('recurringId').value;
  const data = {
    ad: document.getElementById('recurringName').value,
    tutar: parseFloat(document.getElementById('recurringAmount').value),
    tur: document.getElementById('recurringType').value,
    kategori_id: parseInt(document.getElementById('recurringCategory').value) || null,
    ay_gunu: parseInt(document.getElementById('recurringDay').value),
    aktif: document.getElementById('recurringActive').checked
  };
  
  if (id) {
    data.recurring_id = id;
    await api('/api/add_recurring', 'POST', data);
  } else {
    const res = await api('/api/add_recurring', 'POST', data);
  }
  
  closeModal('recurringModal');
  loadRecurring();
});

// Debts
function renderDebts() {
  const grid = document.getElementById('debtsGrid');
  grid.innerHTML = '';
  
  debts.forEach(d => {
    const paidPercent = d.toplam_taksit > 0 ? (d.odenmis_taksit / d.toplam_taksit * 100) : 0;
    
    const card = document.createElement('div');
    card.className = 'debt-card';
    card.innerHTML = `
      <div class="debt-card-header">
        <div class="debt-card-name">${d.ad}</div>
        <div class="debt-card-currency">${d.para_birimi}</div>
      </div>
      
      ${d.toplam_taksit > 0 ? `
        <div class="debt-progress">
          <div class="debt-progress-bar">
            <div class="debt-progress-fill" style="width: ${paidPercent}%"></div>
          </div>
          <div class="debt-progress-text">${d.odenmis_taksit}/${d.toplam_taksit} taksit</div>
        </div>
      ` : ''}
      
      <div class="debt-amount">
        <div class="debt-amount-item">
          <div class="debt-amount-label">Toplam</div>
          <div class="debt-amount-value">${formatCurrency(d.toplam_tutar)}</div>
        </div>
        <div class="debt-amount-item">
          <div class="debt-amount-label">Kalan</div>
          <div class="debt-amount-value">${formatCurrency(d.kalan_tutar)}</div>
        </div>
      </div>
      
      <div class="debt-actions">
        <button class="btn-sm" onclick="openPayDebt(${d.id})">Ödeme Yap</button>
        <button class="btn-sm" onclick="deleteDebt(${d.id})">🗑️</button>
      </div>
    `;
    grid.appendChild(card);
  });
}

function openAddDebt() {
  document.getElementById('debtId').value = '';
  document.getElementById('debtName').value = '';
  document.getElementById('debtAmount').value = '';
  document.getElementById('debtCurrency').value = 'TRY';
  document.getElementById('debtIsCredit').checked = false;
  document.getElementById('creditFields').classList.add('hidden');
  
  openModal('debtModal');
}

document.getElementById('debtIsCredit').addEventListener('change', (e) => {
  document.getElementById('creditFields').classList.toggle('hidden', !e.target.checked);
});

document.getElementById('debtForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const id = document.getElementById('debtId').value;
  const data = {
    ad: document.getElementById('debtName').value,
    toplam_tutar: parseFloat(document.getElementById('debtAmount').value),
    para_birimi: document.getElementById('debtCurrency').value,
    kredi_mi: document.getElementById('debtIsCredit').checked,
    toplam_taksit: parseInt(document.getElementById('debtTotalInstallments').value) || 0,
    taksit_tutari: parseFloat(document.getElementById('debtInstallmentAmount').value) || 0
  };
  
  if (id) {
    data.debt_id = id;
  }
  
  const res = await api('/api/add_debt', 'POST', data);
  if (res.success || res.id) {
    closeModal('debtModal');
    loadDebts();
  }
});

function openPayDebt(debtId) {
  const debt = debts.find(d => d.id === debtId);
  if (!debt) return;
  
  const amount = prompt(`${debt.ad} için ödeme tutarını girin (Kalan: ${formatCurrency(debt.kalan_tutar)})`);
  if (!amount) return;
  
  api('/api/pay_debt', 'POST', {
    borç_id: debtId,
    tutar: parseFloat(amount)
  }).then(res => {
    if (res.success) loadDebts();
  });
}

async function deleteDebt(id) {
  if (!confirm('Borç silmek istediğinize emin misiniz?')) return;
  const res = await api('/api/delete_debt/' + id, 'POST');
  if (res.success) loadDebts();
}

// Savings
function renderSavings() {
  if (!currentMonth) return;
  
  const savings = currentMonth.birikimler || [];
  const body = document.querySelector('#savingsTable tbody');
  body.innerHTML = '';
  
  savings.forEach(s => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${s.para_birimi}</td>
      <td>${s.birim_miktar}</td>
      <td>${formatCurrency(s.alis_kuru)}</td>
      <td>${formatCurrency(s.tl_tutar)}</td>
      <td>${formatDate(s.tarih)}</td>
      <td>
        <button class="btn-sm" onclick="deleteSaving(${s.id})">🗑️</button>
      </td>
    `;
    body.appendChild(tr);
  });
}

function openAddSaving() {
  document.getElementById('savingId').value = '';
  document.getElementById('savingCurrency').value = 'USD';
  document.getElementById('savingUnitAmount').value = '';
  document.getElementById('savingRate').value = '';
  
  openModal('savingModal');
}

document.getElementById('savingForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const data = {
    ay_id: currentMonth.id,
    para_birimi: document.getElementById('savingCurrency').value,
    birim_miktar: parseFloat(document.getElementById('savingUnitAmount').value),
    alis_kuru: parseFloat(document.getElementById('savingRate').value),
    tl_tutar: parseFloat(document.getElementById('savingUnitAmount').value) * parseFloat(document.getElementById('savingRate').value)
  };
  
  const res = await api('/api/add_saving', 'POST', data);
  if (res.success || res.id) {
    closeModal('savingModal');
    loadMonth(currentMonth.id);
  }
});

async function deleteSaving(id) {
  if (!confirm('Silmek istediğinize emin misiniz?')) return;
  const res = await api('/api/delete_saving/' + id, 'POST');
  if (res.success) loadMonth(currentMonth.id);
}

// Months
function openAddMonth() {
  const year = new Date().getFullYear();
  document.getElementById('monthName').value = '';
  document.getElementById('monthYear').value = year;
  document.getElementById('monthOpeningBalance').value = '0';
  
  openModal('monthModal');
}

document.getElementById('monthForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const data = {
    ad: document.getElementById('monthName').value,
    yil: parseInt(document.getElementById('monthYear').value),
    acilis_bakiye: parseFloat(document.getElementById('monthOpeningBalance').value)
  };
  
  const res = await api('/api/month', 'POST', data);
  if (res.success || res.id) {
    closeModal('monthModal');
    loadData();
  }
});

// Tab Navigation
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tabName = btn.dataset.tab;
    
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    
    btn.classList.add('active');
    document.getElementById(tabName + 'Tab').classList.add('active');
  });
});

// Modal Management
function openModal(id) {
  document.getElementById(id).classList.add('active');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

document.querySelectorAll('.modal-close').forEach(btn => {
  btn.addEventListener('click', (e) => {
    const modal = e.target.closest('.modal');
    if (modal) modal.classList.remove('active');
  });
});

document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.remove('active');
  });
});

// Event Listeners
document.getElementById('addMonthBtn').addEventListener('click', openAddMonth);
document.getElementById('addIncomeBtn').addEventListener('click', () => openAddTransaction('gelir'));
document.getElementById('addExpenseBtn').addEventListener('click', () => openAddTransaction('gider'));
document.getElementById('addDebtBtn').addEventListener('click', openAddDebt);
document.getElementById('addRecurringBtn').addEventListener('click', openAddRecurring);
document.getElementById('addSavingBtn').addEventListener('click', openAddSaving);

document.getElementById('monthSelect').addEventListener('change', (e) => {
  if (e.target.value) {
    loadMonth(parseInt(e.target.value));
  }
});

document.getElementById('hamburger').addEventListener('click', () => {
  document.querySelector('.sidebar').classList.toggle('active');
});

// Idle Timer
let idleTimer;
function resetIdleTimer() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => {
    api('/auth/logout', 'POST').then(() => {
      showAuthOverlay();
    });
  }, 15 * 60 * 1000);
}

document.addEventListener('mousemove', resetIdleTimer);
document.addEventListener('keypress', resetIdleTimer);
document.addEventListener('click', resetIdleTimer);

// Initialize
checkAuth();
