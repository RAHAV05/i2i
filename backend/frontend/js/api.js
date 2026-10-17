/**
 * API Service for Doc Scanner - Minimal Clinic Demo MVP.
 * Handles all HTTP communications with the Flask backend.
 */

const API_BASE = (window.location.origin && window.location.origin.startsWith('http'))
  ? '' 
  : 'http://localhost:5001';

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  try {
    const res = await fetch(url, options);
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

  async extractPages(page1Blob, page2Blob) {
    const formData = new FormData();
    formData.append('page1', page1Blob, 'page1.jpg');
    formData.append('page2', page2Blob, 'page2.jpg');

    return request('/api/extract', {
      method: 'POST',
      body: formData
    });
  },

  async saveRecord(recordData) {
    return request('/api/records', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(recordData)
    });
  },

  async reviewRecord(recordId, updateData, reviewer = 'Dr. Priya (Demo)') {
    return request(`/api/records/${encodeURIComponent(recordId)}/review`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...updateData, reviewed_by: reviewer })
    });
  },

  async resetDemoData() {
    return request('/api/reset-demo', {
      method: 'POST'
    });
  },

  getExportUrl(campId = null, onlyReviewed = false, format = 'xlsx') {
    const params = new URLSearchParams();
    if (campId) params.append('camp_id', campId);
    if (onlyReviewed) params.append('only_reviewed', 'true');
    params.append('format', format);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return `${API_BASE}/api/export${qs}`;
  },

  getCampExportUrl(campId, format = 'xlsx') {
    return `${API_BASE}/api/camps/${encodeURIComponent(campId)}/export?format=${format}`;
  }
};
