/**
 * API Service for Doc Scanner - Minimal Clinic Demo MVP.
 * Handles all HTTP communications with the Flask backend.
 * Offline-capable: extractPages() enqueues encrypted blobs when offline.
 */

import { offlineQueue, registerOfflineSupport } from './offline-queue.js';
export { offlineQueue, registerOfflineSupport };

const API_BASE = (window.location.origin && window.location.origin.startsWith('http'))
  ? '' 
  : 'http://localhost:5001';

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const opts = { ...options };
  const headers = opts.headers ? { ...opts.headers } : {};
  const customKey = localStorage.getItem('i2i_ai_api_key');
  if (customKey && !headers['X-AI-API-Key']) {
    headers['X-AI-API-Key'] = customKey.trim();
  }
  const customProvider = localStorage.getItem('i2i_ai_provider');
  if (customProvider && !headers['X-AI-Provider']) {
    headers['X-AI-Provider'] = customProvider.trim();
  }
  const customModel = localStorage.getItem('i2i_ai_model');
  if (customModel && !headers['X-AI-Model']) {
    headers['X-AI-Model'] = customModel.trim();
  }
  opts.headers = headers;
  try {
    const res = await fetch(url, opts);
    const contentType = res.headers.get('content-type') || '';
    let data;
    if (contentType.includes('application/json')) {
      data = await res.json();
    } else {
      data = await res.text();
    }
    
    if (!res.ok) {
      const errMsg = (data && data.error) ? data.error : `Request failed (${res.status})`;
      const errorObj = new Error(errMsg);
      errorObj.status = res.status;
      errorObj.data = data;
      throw errorObj;
    }
    return data;
  } catch (err) {
    if (err.message && err.message.includes('Failed to fetch')) {
      throw new Error('Could not connect to Doc Scanner server. Please ensure the backend is running on port 5001.');
    }
    throw err;
  }
}

export const api = {
  async getStatus() {
    return request('/api/status');
  },

  async getStats() {
    return request('/api/stats');
  },

  async sendOtp(phone) {
    return request('/api/auth/send-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone })
    });
  },

  async login(payload) {
    return request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
  },

  async verifyCommunity(code) {
    return request('/api/community/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    });
  },

  async createCommunity(data) {
    return request('/api/community/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  },

  async getCamps() {
    return request('/api/camps');
  },

  async createCamp(name, expectedRecords = 100, dateStr = 'Today') {
    return request('/api/camps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, expected_records: expectedRecords, date_str: dateStr })
    });
  },

  async getRecords(campId = null, status = null) {
    const params = new URLSearchParams();
    if (campId) params.append('camp_id', campId);
    if (status) params.append('status', status);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return request(`/api/records${qs}`);
  },

  async getRecord(recordId) {
    return request(`/api/records/${encodeURIComponent(recordId)}`);
  },

  async extractPages(page1Blob, page2Blob, campId = null) {
    // ── Offline path: encrypt + enqueue ────────────────────────────────────
    if (!navigator.onLine) {
      const apiHeaders = {};
      const customKey = localStorage.getItem('i2i_ai_api_key');
      if (customKey) apiHeaders['X-AI-API-Key'] = customKey.trim();
      const customProvider = localStorage.getItem('i2i_ai_provider');
      if (customProvider) apiHeaders['X-AI-Provider'] = customProvider.trim();
      const customModel = localStorage.getItem('i2i_ai_model');
      if (customModel) apiHeaders['X-AI-Model'] = customModel.trim();

      const id = await offlineQueue.enqueue(page1Blob, page2Blob, { campId, apiHeaders });
      const queueLength = await offlineQueue.length();
      // Return a sentinel so callers can distinguish queued from real response
      return {
        _offline_queued: true,
        queue_id: id,
        queue_length: queueLength,
        message: `📵 No network — scan saved to encrypted offline queue (#${id}). Will sync automatically when connectivity is restored.`,
      };
    }

    // ── Online path: normal upload ──────────────────────────────────────────
    const formData = new FormData();
    formData.append('page1', page1Blob, 'page1.jpg');
    formData.append('page2', page2Blob, 'page2.jpg');

    return request('/api/extract', {
      method: 'POST',
      body: formData
    });
  },

  /**
   * Direct extract function for internal queue-drain usage.
   * Always makes the live HTTP call (no offline check).
   */
  async extractPagesDirect(page1Blob, page2Blob, extraHeaders = {}) {
    const formData = new FormData();
    formData.append('page1', page1Blob, 'page1.jpg');
    formData.append('page2', page2Blob, 'page2.jpg');
    return request('/api/extract', {
      method: 'POST',
      body: formData,
      headers: extraHeaders,
    });
  },

  async saveRecord(recordData) {
    return request('/api/records', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(recordData)
    });
  },

  async reviewRecord(recordId, updateData, reviewer = 'Attending Physician') {
    return request(`/api/records/${encodeURIComponent(recordId)}/review`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...updateData, reviewed_by: reviewer })
    });
  },

  async clearAllData() {
    return request('/api/clear-data', {
      method: 'POST'
    });
  },

  async resetDemoData() {
    return this.clearAllData();
  },

  async getCampAnalytics(campId = null) {
    const qs = campId ? `?camp_id=${encodeURIComponent(campId)}` : '';
    return request(`/api/analytics${qs}`);
  },

  async eraseRecord(recordId) {
    return request(`/api/records/${encodeURIComponent(recordId)}/erase`, {
      method: 'POST'
    });
  },

  async checkDuplicate(campId, personName, phone = '', excludeId = null) {
    return request('/api/records/check-duplicate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        camp_id: campId,
        person_name: personName,
        phone,
        exclude_id: excludeId
      })
    });
  },

  async syncSupabase() {
    return request('/api/sync/supabase', {
      method: 'POST'
    });
  },

  async getAdminBilling() {
    return request('/api/admin/billing');
  },

  async lockRecord(recordId, doctorName = 'Attending Physician') {
    return request(`/api/records/${encodeURIComponent(recordId)}/lock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doctor_name: doctorName })
    });
  },

  async unlockRecord(recordId, doctorName = 'Attending Physician') {
    return request(`/api/records/${encodeURIComponent(recordId)}/unlock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doctor_name: doctorName })
    });
  },

  async approveRecord(recordId, approvedBy = 'Attending Physician') {
    return request(`/api/records/${encodeURIComponent(recordId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved_by: approvedBy })
    });
  },

  async getUsage() {
    return request('/api/usage', {
      headers: { 'X-Admin-Key': 'admin_secret_key' }
    });
  },

  async getUsageLedger(page = 1, limit = 50) {
    return request(`/api/usage/ledger?page=${page}&limit=${limit}`, {
      headers: { 'X-Admin-Key': 'admin_secret_key' }
    });
  },

  async createBillingOrder(papers = 1000) {
    return request('/api/billing/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ papers })
    });
  },

  async getInvoices() {
    return request('/api/billing/invoices');
  },

  getInvoiceDownloadUrl(invoiceNumber) {
    return `${API_BASE}/api/billing/invoices/${encodeURIComponent(invoiceNumber)}/download`;
  },

  async setAiPause(orgId = null, paused = true) {
    return request('/api/admin/ai-pause', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': 'admin_secret_key' },
      body: JSON.stringify({ org_id: orgId, paused })
    });
  },

  async cleanupExpiredReservations(expiryDays = 7) {
    return request('/api/admin/cleanup-expired-reservations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': 'admin_secret_key' },
      body: JSON.stringify({ expiry_days: expiryDays })
    });
  },

  getExportUrl(campId = null, onlyReviewed = false, format = 'xlsx', formType = null) {
    const params = new URLSearchParams();
    if (campId) params.append('camp_id', campId);
    if (onlyReviewed) params.append('only_reviewed', 'true');
    if (formType) params.append('type', formType);
    params.append('format', format);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return `${API_BASE}/api/export${qs}`;
  },

  getAdultExportUrl(campId = null, onlyReviewed = false) {
    const params = new URLSearchParams();
    if (campId) params.append('camp_id', campId);
    if (onlyReviewed) params.append('only_reviewed', 'true');
    const qs = params.toString() ? `?${params.toString()}` : '';
    return `${API_BASE}/api/export/adult/excel${qs}`;
  },

  getPaediatricExportUrl(campId = null, onlyReviewed = false) {
    const params = new URLSearchParams();
    if (campId) params.append('camp_id', campId);
    if (onlyReviewed) params.append('only_reviewed', 'true');
    const qs = params.toString() ? `?${params.toString()}` : '';
    return `${API_BASE}/api/export/paediatric/excel${qs}`;
  },

  getCampExportUrl(campId, format = 'xlsx', formType = null) {
    const params = new URLSearchParams();
    params.append('format', format);
    if (formType) params.append('type', formType);
    return `${API_BASE}/api/camps/${encodeURIComponent(campId)}/export?${params.toString()}`;
  },

  async getForms() {
    return request('/api/forms');
  },

  async getFormStructure(formId) {
    return request(`/api/forms/${encodeURIComponent(formId)}`);
  },

  async getFormAttributes(formId) {
    return request(`/api/forms/${encodeURIComponent(formId)}/attributes`);
  },

  async getFormSchema(formId) {
    return request(`/api/forms/${encodeURIComponent(formId)}/schema`);
  },

  async getAIStatus() {
    return request('/api/ai/status');
  }
};

