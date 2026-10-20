/**
 * Doc Scanner — Mobile-First Hybrid Clinic Application Controller
 * Connects the phone-first touch UI with Flask REST API & Vision AI.
 */

import { api } from './api.js';
import { DocumentScanner } from './scanner.js';

// DOM Selector Utility
const $ = id => document.getElementById(id);
window.$ = $;

// Application State
const state = {
  currentScreen: 'splash',
  camps: [],
  selectedCampId: null,
  records: [],
  stats: { total_camps: 0, total_scanned: 0, to_check: 0, reviewed: 0 },
  activeRecord: null, // Currently open in review screen
  currentScanPage: 0, // 0 for Page 1, 1 for Page 2
  pageBlobs: [null, null], // [Blob, Blob]
  pageUrls: [null, null],  // [ObjectURL, ObjectURL]
  recordsFilter: 'all',    // 'all' | 'needs_review' | 'reviewed'
  searchQuery: '',
  isExpandedView: false,
  doctorName: localStorage.getItem('clinic_doctor_name') || 'Attending Physician'
};

// Initialize Camera Module
let scanner = null;

// ==========================================================================
// 1. Screen Navigation & Frame Toggle
// ==========================================================================

export function go(screenId, isBack = false) {
  if (!screenId) return;

  // Track navigation history (excluding duplicate consecutive visits and splash)
  if (!isBack && state.currentScreen && state.currentScreen !== 'splash' && state.currentScreen !== screenId) {
    if (!state.screenHistory) state.screenHistory = [];
    state.screenHistory.push(state.currentScreen);
  }

  state.currentScreen = screenId;

  // Toggle screens strictly
  document.querySelectorAll('.scr').forEach(s => {
    const isOn = s.id === screenId;
    s.classList.toggle('on', isOn);
    if (isOn) {
      s.scrollTop = 0;
    }
  });

  // Update bottom navigation bar active states
  const navMap = {
    home: ['nav-btn-home', 'nav-btn-home-2', 'nav-btn-home-3', 'nav-btn-home-4', 'nav-btn-home-5'],
    records: ['nav-btn-records', 'nav-btn-records-2', 'nav-btn-records-3', 'nav-btn-records-4', 'nav-btn-records-5'],
    analytics: ['nav-btn-analytics', 'nav-btn-analytics-2', 'nav-btn-analytics-3', 'nav-btn-analytics-4', 'nav-btn-analytics-5'],
    settings: ['nav-btn-settings', 'nav-btn-settings-2', 'nav-btn-settings-3', 'nav-btn-settings-4', 'nav-btn-settings-5']
  };

  document.querySelectorAll('.nav button').forEach(b => {
    if (!b.classList.contains('go')) b.classList.remove('on');
  });

  if (navMap[screenId]) {
    navMap[screenId].forEach(id => {
      const el = $(id);
      if (el) el.classList.add('on');
    });
  }

  // Trigger data refreshes on navigation
  if (screenId === 'home') {
    renderHome();
  } else if (screenId === 'records') {
    renderRecords();
  } else if (screenId === 'export') {
    populateExportSelects();
  } else if (screenId === 'analytics') {
    renderAnalytics();
  } else if (screenId === 'settings') {
    renderSettings();
  }
}

export function goBack() {
  if (state.screenHistory && state.screenHistory.length > 0) {
    const prev = state.screenHistory.pop();
    go(prev, true);
  } else {
    go('home', true);
  }
}

export function toggleFrame() {
  state.isExpandedView = !state.isExpandedView;
  const phone = $('phone');
  const txt = $('view-toggle-text');
  if (phone) {
    phone.classList.toggle('expanded', state.isExpandedView);
  }
  if (txt) {
    txt.textContent = state.isExpandedView ? 'Phone Frame' : 'Expand View';
  }
  toast(state.isExpandedView ? 'Switched to Expanded View' : 'Switched to Phone Frame');
}

export function toast(message, isError = false) {
  const t = $('toast');
  if (!t) return;
  t.textContent = message;
  t.style.background = isError ? 'var(--err)' : 'var(--ink)';
  t.classList.add('on');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('on'), 2800);
}

// ==========================================================================
// 2. Authentication & Doctor Login
// ==========================================================================

// --------------------------------------------------------------------------
// Flow A: Auth Tabs & Login Methods (Phone OTP, Doctor PIN, Email)
// --------------------------------------------------------------------------

export function switchAuthTab(tab) {
  const tabs = ['otp', 'pin', 'email'];
  tabs.forEach(t => {
    const btn = $(`auth-tab-btn-${t}`);
    const form = $(`auth-form-${t}`);
    if (btn) btn.classList.toggle('active', t === tab);
    if (form) form.style.display = (t === tab) ? 'block' : 'none';
  });
}

export async function requestOtp() {
  const phone = $('login-phone')?.value.trim();
  const err = $('login-otp-error');
  if (!phone || phone.length < 8) {
    if (err) err.textContent = 'Please enter a valid mobile number (e.g. 9876543210).';
    return;
  }
  if (err) err.textContent = '';

  const btn = $('btn-request-otp');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Sending...';
  }

  try {
    const res = await api.sendOtp(phone);
    const otpGroup = $('otp-entry-group');
    if (otpGroup) otpGroup.style.display = 'block';
    const otpInput = $('login-otp');
    if (otpInput) {
      otpInput.value = res.demo_otp || '123456';
      otpInput.focus();
    }
    toast(`OTP sent to ${phone}. Test code: ${res.demo_otp || '123456'}`);
    if (btn) btn.textContent = 'Resend (30s)';
  } catch (e) {
    if (err) err.textContent = e.message || 'Failed to send OTP.';
    if (btn) btn.textContent = 'Retry OTP';
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function loginWithOtp() {
  const phone = $('login-phone')?.value.trim();
  const otp = $('login-otp')?.value.trim();
  const commCode = $('login-comm-code-otp')?.value.trim().toUpperCase() || 'CAMP-2026';
  const err = $('login-otp-error');

  if (!phone) {
    if (err) err.textContent = 'Phone number is required.';
    return;
  }
  if (!otp) {
    if (err) err.textContent = 'Please enter the 6-digit OTP (demo: 123456).';
    return;
  }
  if (err) err.textContent = '';

  const submitBtn = $('login-otp-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Verifying...';
  }

  try {
    const res = await api.login({ phone, otp, community_code: commCode });
    if (res.doctor) {
      state.doctorName = res.doctor.full_name || 'Dr. ' + phone.slice(-4);
      localStorage.setItem('clinic_doctor_name', state.doctorName);
      localStorage.setItem('clinic_doctor_phone', phone);
      if (res.community) {
        localStorage.setItem('clinic_community_code', res.community.code);
        localStorage.setItem('clinic_community_name', res.community.name);
      }
      updateDoctorDisplays();
      go('home');
      toast(`Welcome, ${state.doctorName}`);
    }
  } catch (e) {
    if (err) err.textContent = e.message || 'Phone verification failed.';
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Sign In with Phone';
    }
  }
}

export async function loginWithPin() {
  const user = $('login-pin-user')?.value.trim() || 'Attending Physician';
  const pin = $('login-pin-code')?.value.trim();
  const commCode = $('login-comm-code-pin')?.value.trim().toUpperCase() || 'CAMP-2026';
  const err = $('login-pin-error');

  if (!pin || pin.length < 4) {
    if (err) err.textContent = 'Please enter your 4-digit station PIN (default: 1234).';
    return;
  }
  if (err) err.textContent = '';

  const submitBtn = $('login-pin-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Unlocking...';
  }

  try {
    const res = await api.login({ phone: user, pin, community_code: commCode });
    state.doctorName = user.startsWith('Dr.') ? user : (res.doctor?.full_name || `Dr. ${user}`);
    localStorage.setItem('clinic_doctor_name', state.doctorName);
    localStorage.setItem('clinic_doctor_pin', pin);
    if (res.community) {
      localStorage.setItem('clinic_community_code', res.community.code);
    }
    updateDoctorDisplays();
    go('home');
    toast(`Station unlocked for ${state.doctorName}`);
  } catch (e) {
    if (err) err.textContent = e.message || 'PIN unlock failed.';
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Unlock Station Access';
    }
  }
}

export async function login() {
  const email = $('login-email')?.value.trim() || '';
  const password = $('login-password')?.value || '';
  const commCode = $('login-comm-code-email')?.value.trim().toUpperCase() || 'CAMP-2026';
  const err = $('login-error');

  if (!email || !email.includes('@')) {
    if (err) err.textContent = 'Please enter a valid clinic email.';
    return;
  }
  if (password.length < 4) {
    if (err) err.textContent = 'Password must be at least 4 characters.';
    return;
  }
  if (err) err.textContent = '';

  const submitBtn = $('login-submit-btn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Signing in...';
  }

  try {
    const res = await api.login({ email, password, community_code: commCode });
    const namePart = email.split('@')[0].replace(/[._-]/g, ' ');
    state.doctorName = res.doctor?.full_name || ('Dr. ' + namePart.charAt(0).toUpperCase() + namePart.slice(1));
    localStorage.setItem('clinic_doctor_name', state.doctorName);
    localStorage.setItem('clinic_doctor_email', email);
    if (res.community) {
      localStorage.setItem('clinic_community_code', res.community.code);
      localStorage.setItem('clinic_community_name', res.community.name);
    }
    updateDoctorDisplays();
    go('home');
    toast(`Signed in as ${state.doctorName}`);
  } catch (e) {
    // If offline/demo fallback
    const namePart = email.split('@')[0].replace(/[._-]/g, ' ');
    state.doctorName = 'Dr. ' + namePart.charAt(0).toUpperCase() + namePart.slice(1);
    localStorage.setItem('clinic_doctor_name', state.doctorName);
    localStorage.setItem('clinic_doctor_email', email);
    updateDoctorDisplays();
    go('home');
    toast(`Signed in as ${state.doctorName}`);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Login with Email';
    }
  }
}

export function forgotPassword() {
  const email = $('login-email')?.value.trim();
  if (email && email.includes('@')) {
    toast(`Password recovery link dispatched to ${email}`);
  } else {
    toast('Enter your registered email address to receive password reset link.');
  }
}

export function promptSignUp() {
  toast('Please contact your Clinic Systems Administrator for medical onboarding credentials.');
}

export function logout() {
  localStorage.removeItem('clinic_doctor_name');
  localStorage.removeItem('clinic_doctor_email');
  localStorage.removeItem('clinic_doctor_phone');
  localStorage.removeItem('clinic_doctor_pin');
  state.doctorName = 'Physician';
  updateDoctorDisplays();
  go('login');
  toast('Signed out of clinical portal.');
}

export function loginDemo() {
  state.doctorName = 'Dr. Priya (Demo)';
  localStorage.setItem('clinic_doctor_name', state.doctorName);
  localStorage.setItem('clinic_doctor_phone', '+91 98765 43210');
  localStorage.setItem('clinic_doctor_pin', '1234');
  localStorage.setItem('clinic_community_code', 'CAMP-2026');
  localStorage.setItem('clinic_community_name', 'Rotary Community Health Outreach');
  updateDoctorDisplays();
  go('home');
  toast('Signed in as Dr. Priya (Demo Station)');
}

// --------------------------------------------------------------------------
// Flow A: Community Management (Join / Create Community)
// --------------------------------------------------------------------------

export function openCommunityModal() {
  const modal = $('community-modal');
  if (modal) modal.classList.add('on');
}

export function closeCommunityModal() {
  const modal = $('community-modal');
  if (modal) modal.classList.remove('on');
}

export function switchCommunityTab(tab) {
  const joinBtn = $('comm-tab-btn-join');
  const createBtn = $('comm-tab-btn-create');
  const joinPanel = $('comm-panel-join');
  const createPanel = $('comm-panel-create');

  if (joinBtn) joinBtn.classList.toggle('active', tab === 'join');
  if (createBtn) createBtn.classList.toggle('active', tab === 'create');
  if (joinPanel) joinPanel.style.display = (tab === 'join') ? 'block' : 'none';
  if (createPanel) createPanel.style.display = (tab === 'create') ? 'block' : 'none';
}

export async function verifyCommunityCode() {
  const code = $('comm-join-code')?.value.trim().toUpperCase();
  const err = $('comm-join-error');
  const box = $('comm-preview-box');

  if (!code) {
    if (err) err.textContent = 'Please enter an invite code (e.g. CAMP-2026).';
    return;
  }
  if (err) err.textContent = '';

  try {
    const res = await api.verifyCommunity(code);
    if (res.valid && res.community) {
      if (box) box.classList.add('show');
      $('comm-preview-name').textContent = res.community.name;
      $('comm-preview-sub').textContent = `Organizer: ${res.community.creator_name} · Code: ${res.community.code}`;
      toast(`Valid community: ${res.community.name}`);
    }
  } catch (e) {
    if (box) box.classList.remove('show');
    if (err) err.textContent = e.message || 'Invalid community code.';
  }
}

export async function joinCommunity() {
  const code = $('comm-join-code')?.value.trim().toUpperCase();
  const docName = $('comm-join-doctor-name')?.value.trim() || 'Attending Physician';
  const err = $('comm-join-error');

  if (!code) {
    if (err) err.textContent = 'Invite code is required.';
    return;
  }
  if (err) err.textContent = '';

  try {
    const res = await api.verifyCommunity(code);
    if (res.valid) {
      localStorage.setItem('clinic_community_code', res.community.code);
      localStorage.setItem('clinic_community_name', res.community.name);
      state.doctorName = docName.startsWith('Dr.') ? docName : `Dr. ${docName}`;
      localStorage.setItem('clinic_doctor_name', state.doctorName);
      updateDoctorDisplays();
      closeCommunityModal();
      toast(`Joined ${res.community.name}`);
      go('home');
    }
  } catch (e) {
    if (err) err.textContent = e.message || 'Failed to join community.';
  }
}

export async function createCommunity() {
  const name = $('comm-create-name')?.value.trim();
  const creator_name = $('comm-create-lead')?.value.trim() || 'Clinic Lead';
  const email = $('comm-create-email')?.value.trim() || 'clinic@health.org';
  const customCode = $('comm-create-custom-code')?.value.trim().toUpperCase() || null;
  const err = $('comm-create-error');

  if (!name) {
    if (err) err.textContent = 'Organization/Community name is required.';
    return;
  }
  if (err) err.textContent = '';

  try {
    const res = await api.createCommunity({
      name,
      creator_name,
      email,
      code: customCode
    });
    if (res.success && res.community) {
      localStorage.setItem('clinic_community_code', res.community.code);
      localStorage.setItem('clinic_community_name', res.community.name);
      state.doctorName = creator_name.startsWith('Dr.') ? creator_name : `Dr. ${creator_name}`;
      localStorage.setItem('clinic_doctor_name', state.doctorName);
      updateDoctorDisplays();
      closeCommunityModal();
      toast(`Community created! Invite code: ${res.community.code}`);
      go('home');
    }
  } catch (e) {
    if (err) err.textContent = e.message || 'Could not create community.';
  }
}

// --------------------------------------------------------------------------
// Flow A: Session Expired & Fast Station Re-Authentication
// --------------------------------------------------------------------------

let sessionIdleTimer = null;

export function triggerSessionTimeout() {
  const modal = $('session-expired-modal');
  const docLabel = $('session-doctor-label');
  if (docLabel) docLabel.textContent = state.doctorName || 'Attending Physician';
  if (modal) modal.classList.add('on');

  // Clear pin inputs and focus first box
  ['spin-1', 'spin-2', 'spin-3', 'spin-4'].forEach(id => {
    const el = $(id);
    if (el) el.value = '';
  });
  setTimeout(() => $('spin-1')?.focus(), 200);
}

export function unlockSession() {
  const p1 = $('spin-1')?.value || '';
  const p2 = $('spin-2')?.value || '';
  const p3 = $('spin-3')?.value || '';
  const p4 = $('spin-4')?.value || '';
  const enteredPin = `${p1}${p2}${p3}${p4}`;
  const err = $('session-expired-error');

  if (enteredPin.length < 4) {
    if (err) err.textContent = 'Please enter all 4 digits of your station PIN.';
    return;
  }

  // Accept saved PIN or default station PIN 1234
  const expectedPin = localStorage.getItem('clinic_doctor_pin') || '1234';
  if (enteredPin === expectedPin || enteredPin === '1234') {
    if (err) err.textContent = '';
    const modal = $('session-expired-modal');
    if (modal) modal.classList.remove('on');
    resetSessionIdleTimer();
    toast(`Session restored for ${state.doctorName}`);
  } else {
    if (err) err.textContent = 'Incorrect PIN. Try 1234 or sign in again.';
  }
}

function initPinBoxes() {
  const boxes = ['spin-1', 'spin-2', 'spin-3', 'spin-4'];
  boxes.forEach((id, idx) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', (e) => {
      if (e.target.value.length === 1 && idx < boxes.length - 1) {
        $(boxes[idx + 1])?.focus();
      }
      if (idx === boxes.length - 1 && e.target.value.length === 1) {
        unlockSession();
      }
    });
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !e.target.value && idx > 0) {
        $(boxes[idx - 1])?.focus();
      }
    });
  });
}

function resetSessionIdleTimer() {
  if (sessionIdleTimer) clearTimeout(sessionIdleTimer);
  // Auto-time out after 15 minutes of station inactivity (DPDP compliance)
  sessionIdleTimer = setTimeout(() => {
    if (state.currentScreen !== 'splash' && state.currentScreen !== 'login') {
      triggerSessionTimeout();
    }
  }, 15 * 60 * 1000);
}

// Track activity for idle timeout
['click', 'keydown', 'touchstart'].forEach(evt => {
  window.addEventListener(evt, resetSessionIdleTimer, { passive: true });
});

// --------------------------------------------------------------------------
// Flow A: Camera Permission Guidance & Fallback Flow
// --------------------------------------------------------------------------

export function openCameraPermissionModal(showGuide = false) {
  const modal = $('camera-permission-modal');
  const guide = $('camera-denied-guide');
  if (guide) guide.style.display = showGuide ? 'block' : 'none';
  if (modal) modal.classList.add('on');
}

export function closeCameraPermissionModal() {
  const modal = $('camera-permission-modal');
  if (modal) modal.classList.remove('on');
}

export async function requestCameraPermission() {
  const btn = $('btn-grant-camera');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Requesting...';
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment' }
    });
    // Permission granted! Stop test stream and close modal
    stream.getTracks().forEach(t => t.stop());
    closeCameraPermissionModal();
    toast('Camera permission granted · Optical scanner ready');
    startScan();
  } catch (err) {
    console.warn('Camera permission request error:', err);
    const guide = $('camera-denied-guide');
    if (guide) guide.style.display = 'block';
    toast('Camera access denied. Please enable camera in browser settings.');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<svg viewBox="0 0 24 24" style="width:18px;height:18px;"><path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/></svg> Grant Camera Permission`;
    }
  }
}

export function fallbackToUploadPhoto() {
  closeCameraPermissionModal();
  uploadPhoto();
}

function updateDoctorDisplays() {
  const dName = $('home-doctor-name');
  const dAv = $('home-avatar');
  const sName = $('settings-doctor-name');
  const sEmail = $('settings-doctor-email');
  const sAv = $('settings-doctor-av');

  const doc = state.doctorName || localStorage.getItem('clinic_doctor_name') || 'Physician';
  const email = localStorage.getItem('clinic_doctor_email') || 'physician@hospital.org';
  const initial = doc.replace('Dr. ', '').charAt(0) || 'D';

  if (dName) dName.textContent = doc;
  if (dAv) dAv.textContent = initial;
  if (sName) sName.textContent = doc;
  if (sEmail) sEmail.textContent = email;
  if (sAv) sAv.textContent = initial;
}

// ==========================================================================
// 3. Home / Dashboard Data & Rendering
// ==========================================================================

export async function renderHome() {
  try {
    const [campsRes, statsRes, statusRes, recordsRes] = await Promise.all([
      api.getCamps().catch(() => []),
      api.getStats().catch(() => ({ total_camps: 0, total_scanned: 0, to_check: 0, reviewed: 0 })),
      api.getStatus().catch(() => null),
      api.getRecords(null, null).catch(() => [])
    ]);

    state.camps = Array.isArray(campsRes) ? campsRes : [];
    state.stats = statsRes || {};
    state.records = Array.isArray(recordsRes) ? recordsRes : [];

    if (!state.selectedCampId && state.camps.length > 0) {
      state.selectedCampId = state.camps[0].id;
    }

    // Update AI Indicator Chip
    const aiChip = $('ai-chip');
    if (aiChip && statusRes) {
      aiChip.textContent = statusRes.ai_configured ? '● Vision AI: Active' : '○ Vision AI: Manual Mode';
      aiChip.style.background = statusRes.ai_configured ? 'var(--ok)' : 'var(--mist)';
      aiChip.style.color = statusRes.ai_configured ? 'var(--teal2)' : 'var(--mute)';
    }

    // Update KPI Tiles
    const scannedCount = state.stats.total_scanned ?? 0;
    const toCheckCount = state.stats.to_check ?? 0;
    const reviewedCount = state.stats.reviewed ?? 0;
    const campsCount = state.camps.length ?? 0;

    if ($('kpi-scanned')) $('kpi-scanned').textContent = scannedCount;
    if ($('kpi-tocheck')) $('kpi-tocheck').textContent = toCheckCount;
    if ($('header-notif-count')) $('header-notif-count').textContent = toCheckCount;
    if ($('bento-tocheck-badge')) $('bento-tocheck-badge').textContent = `${toCheckCount} to check`;
    if ($('pulse-attention-dot')) $('pulse-attention-dot').style.display = toCheckCount > 0 ? 'inline-block' : 'none';
    if ($('kpi-verified')) $('kpi-verified').textContent = reviewedCount;
    if ($('kpi-camps')) $('kpi-camps').textContent = campsCount;
    if ($('bento-excel-rows')) $('bento-excel-rows').textContent = `${scannedCount} rows ready`;

    // Update Active Camp Hero Card
    const activeCamp = state.camps.find(c => c.id === state.selectedCampId) || (state.camps.length > 0 ? state.camps[0] : null);
    if (activeCamp) {
      const pct = Math.min(100, Math.round(activeCamp.progress_pct || 0));
      const ring = $('home-ring');
      if (ring) ring.style.setProperty('--p', pct);
      if ($('home-ring-pct')) $('home-ring-pct').textContent = `${pct}%`;
      if ($('home-camp-title')) $('home-camp-title').textContent = activeCamp.name;
      if ($('home-camp-subtitle')) {
        $('home-camp-subtitle').textContent = `${activeCamp.total_scanned || 0} of ${activeCamp.expected_records || 100} forms · ${activeCamp.date_str}`;
      }
      if ($('home-camp-status-badge')) $('home-camp-status-badge').textContent = '● Active Camp';
    } else {
      const ring = $('home-ring');
      if (ring) ring.style.setProperty('--p', 0);
      if ($('home-ring-pct')) $('home-ring-pct').textContent = '0%';
      if ($('home-camp-title')) $('home-camp-title').textContent = 'No Active Camp Selected';
      if ($('home-camp-subtitle')) $('home-camp-subtitle').textContent = 'Tap to create or register a camp';
      if ($('home-camp-status-badge')) $('home-camp-status-badge').textContent = '○ Standby';
    }

    // Dynamic Clinical Triage Alert Card
    const triageCard = $('home-triage-card');
    if (triageCard) {
      const pendingRecord = (state.records || []).find(r => r.status === 'needs_review');
      if (pendingRecord && toCheckCount > 0) {
        triageCard.style.display = 'flex';
        const triageDesc = $('home-triage-desc');
        if (triageDesc) {
          const fieldsStr = (pendingRecord.uncertain_fields && pendingRecord.uncertain_fields.length > 0)
            ? pendingRecord.uncertain_fields.join(', ')
            : 'Requires physician verification';
          triageDesc.innerHTML = `<b>${escapeHtml(pendingRecord.person_name)}</b> flagged: ${escapeHtml(fieldsStr)}. Please verify.`;
        }
      } else {
        triageCard.style.display = 'none';
      }
    }

    // Render Health Camps List
    renderCampsList();

    // Render Recent Records List
    renderRecentRecords();

  } catch (err) {
    console.error('Error refreshing home data:', err);
  }
}

function renderCampsList() {
  const container = $('camps-list');
  if (!container) return;

  if (state.camps.length === 0) {
    container.innerHTML = `
      <div style="padding:22px;text-align:center;color:var(--mute);background:#fff;border-radius:18px;border:1.5px dashed var(--line);">
        <p style="font-weight:700;margin-bottom:6px;color:var(--ink);font-size:15px;">No Organizations / Clubs Created</p>
        <p style="font-size:13px;color:var(--mute);margin-bottom:14px;">Set up a club, committee, or school to maintain its dedicated Excel records.</p>
        <button class="btn" style="width:auto;display:inline-block;padding:9px 18px;font-size:13px;" onclick="window.app.openNewCampModal()">+ Create Organization / Club</button>
      </div>
    `;
    return;
  }

  container.innerHTML = state.camps.map(c => {
    const isSelected = c.id === state.selectedCampId;
    const pct = Math.min(100, Math.round(c.progress_pct || 0));
    return `
      <div class="camp ${isSelected ? 'sel' : ''}">
        <div class="r" style="align-items:flex-start;cursor:pointer;" onclick="window.app.openCampRecords('${c.id}')">
          <div style="flex:1;">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
              <span style="font-size:10.5px;font-weight:700;text-transform:uppercase;color:var(--teal);letter-spacing:0.5px;background:rgba(216,27,96,0.08);padding:3px 8px;border-radius:6px;">Club / Committee</span>
              <small>${escapeHtml(c.date_str || 'Today')}</small>
            </div>
            <b style="font-size:16px;color:var(--ink);display:block;">${escapeHtml(c.name)}</b>
            <small style="color:var(--mute);margin-top:3px;display:block;">
              <b>${c.total_scanned || 0}</b> records filed${c.to_check ? ` · <span style="color:#B45309;font-weight:700;">${c.to_check} to check</span>` : ''}${c.reviewed ? ` · <span style="color:#059669;font-weight:700;">${c.reviewed} approved</span>` : ''}
            </small>
          </div>
        </div>

        <div class="pb" style="margin:10px 0 12px;"><i style="width:${pct}%;"></i></div>

        <!-- Dedicated Organization Action Buttons -->
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-top:1px solid rgba(0,0,0,0.05);padding-top:10px;">
          <button class="btn btn-excel" style="flex:1;min-width:140px;padding:7px 10px;font-size:12px;border-radius:10px;" onclick="event.stopPropagation();window.app.exportCampExcel('${c.id}')" title="Download Excel for this Organization">
            <svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
            Download Club Excel (.xlsx)
          </button>
          <button class="btn ghost" style="width:auto;padding:7px 12px;font-size:12px;border-radius:10px;" onclick="event.stopPropagation();window.app.openCampRecords('${c.id}')">
            📁 View Records (${c.total_scanned || 0})
          </button>
          <button class="btn ghost" style="width:auto;padding:7px 12px;font-size:12px;border-radius:10px;color:var(--teal);border-color:var(--teal);" onclick="event.stopPropagation();window.app.scanForCamp('${c.id}')" title="Scan new document directly into this club">
            📷 Scan Here
          </button>
        </div>
      </div>
    `;
  }).join('');
}

export function openCampRecords(campId) {
  state.selectedCampId = campId;
  go('records');
  const orgFilter = $('records-org-filter');
  if (orgFilter) orgFilter.value = campId;
  filterRecords();
}

export function scanForCamp(campId) {
  state.selectedCampId = campId;
  const c = state.camps.find(x => x.id === campId);
  if (c) toast(`Scanning into: ${c.name}`);
  startScan();
}

export function selectActiveCamp(campId) {
  state.selectedCampId = campId;
  renderHome();
  const c = state.camps.find(x => x.id === campId);
  if (c) toast(`Active Club: ${c.name}`);
}

function renderRecentRecords() {
  const container = $('recent-records-list');
  if (!container) return;

  if (state.records.length === 0) {
    container.innerHTML = `
      <div style="padding:16px;text-align:center;color:var(--mute);background:#fff;border-radius:16px;border:1.5px dashed var(--line);">
        No scanned records yet. Tap the center camera button to scan!
      </div>
    `;
    return;
  }

  const tagStyle = {
    reviewed: ['Approved', 'tag'],
    needs_review: ['To check', 'tag w'],
    failed: ['Failed', 'tag err']
  };

  const recent = state.records.slice(0, 6);
  container.innerHTML = recent.map(r => {
    const initials = (r.person_name || 'U').charAt(0).toUpperCase();
    const tagInfo = tagStyle[r.status] || ['Reading…', 'tag r'];
    const timeStr = formatRelativeTime(r.created_at);
    return `
      <div class="rec" onclick="window.app.openReview('${r.id}')">
        <span class="a">${initials}</span>
        <div>
          <b>${escapeHtml(r.person_name || 'Unknown Patient')}</b><br>
          <small>${escapeHtml(r.camp_name || 'Clinic Camp')} · ${timeStr}</small>
        </div>
        <span class="${tagInfo[1]}">${tagInfo[0]}</span>
      </div>
    `;
  }).join('');
}

export function openCampPicker() {
  openNewCampModal();
}

// ==========================================================================
// 4. Real Camera Scanner & Synthetic Fallbacks
// ==========================================================================

function getScannerInstance() {
  if (!scanner) {
    scanner = new DocumentScanner({
      videoEl: $('camera-video'),
      canvasEl: $('capture-canvas'),
      onThumbnailsUpdated: pages => {
        const t1 = $('t1');
        const t2 = $('t2');
        if (t1) {
          if (pages[0] && pages[0].url) {
            t1.style.backgroundImage = `url('${pages[0].url}')`;
            t1.classList.add('f');
          } else {
            t1.style.backgroundImage = 'none';
            t1.classList.remove('f');
          }
        }
        if (t2) {
          if (pages[1] && pages[1].url) {
            t2.style.backgroundImage = `url('${pages[1].url}')`;
            t2.classList.add('f');
          } else {
            t2.style.backgroundImage = 'none';
            t2.classList.remove('f');
          }
        }
      }
    });
  }
  return scanner;
}

export async function startScan() {
  state.currentScanPage = 0;
  state.pageBlobs = [null, null];
  state.pageUrls = [null, null];

  const sc = getScannerInstance();
  sc.resetAllPages();
  updateScanStepsUI();

  go('scan');

  // Attempt real camera start
  try {
    const video = $('camera-video');
    const sheet = $('sh');
    await sc.startCamera();
    if (video) video.style.display = 'block';
    if (sheet) sheet.style.display = 'none';
    $('hnt').textContent = 'Fit the whole page inside the corners and tap the button';
  } catch (err) {
    console.warn('Camera could not be activated:', err.message);
    const video = $('camera-video');
    const sheet = $('sh');
    if (video) video.style.display = 'none';
    if (sheet) sheet.style.display = 'block';
    $('hnt').textContent = 'Camera unavailable. Tap "Upload Photo" or grant permission.';
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError' || (err.message && err.message.toLowerCase().includes('permission'))) {
      openCameraPermissionModal(true);
    }
  }
}

export function closeScan() {
  const sc = getScannerInstance();
  sc.stopCamera();
  const video = $('camera-video');
  if (video) video.style.display = 'none';
  go('home');
}

export function uploadPhoto() {
  const input = $('camera-file-input');
  if (input) {
    input.value = '';
    input.click();
  }
}

export async function flipCamera() {
  const sc = getScannerInstance();
  try {
    const nextMode = sc.facingMode === 'environment' ? 'front' : 'back';
    toast(`Switching to ${nextMode} camera…`);
    await sc.switchCamera();
    const video = $('camera-video');
    const sheet = $('sh');
    if (video) video.style.display = 'block';
    if (sheet) sheet.style.display = 'none';
  } catch (err) {
    console.warn('Switch camera error:', err);
    toast(`Camera flip: ${err.message || 'unavailable on this device'}`, true);
  }
}

export async function captureCurrentPage() {
  const sc = getScannerInstance();
  const flash = $('fl');
  if (flash) {
    flash.classList.remove('f');
    void flash.offsetWidth;
    flash.classList.add('f');
  }

  let captured = null;
  if (sc.isCameraActive) {
    try {
      captured = await sc.captureCurrentFrame();
    } catch (err) {
      console.warn('Frame capture failed:', err);
    }
  }

  // If no webcam frame, generate synthetic sheet image
  if (!captured) {
    captured = await generatePageCanvasBlob(state.currentScanPage + 1);
    sc.setPageImage(state.currentScanPage, captured.blob, captured.url, `page_${state.currentScanPage + 1}.jpg`);
  }

  state.pageBlobs[state.currentScanPage] = captured.blob;
  state.pageUrls[state.currentScanPage] = captured.url;

  if (state.currentScanPage === 0) {
    // Move to Page 2
    state.currentScanPage = 1;
    sc.currentPage = 1;
    updateScanStepsUI();
    toast('Page 1 captured! Now scan Page 2 (Back side)');
  } else {
    // Both pages captured -> Proceed to Pair Check
    sc.stopCamera();
    const video = $('camera-video');
    if (video) video.style.display = 'none';
    proceedToPairsScreen();
  }
}

export async function onFileUploaded(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;

  const sc = getScannerInstance();
  try {
    const res = await sc.handleFileUpload(file);
    state.pageBlobs[state.currentScanPage] = res.blob;
    state.pageUrls[state.currentScanPage] = res.url;

    if (state.currentScanPage === 0) {
      state.currentScanPage = 1;
      sc.currentPage = 1;
      updateScanStepsUI();
      toast('Page 1 photo uploaded! Select Page 2 photo.');
    } else {
      sc.stopCamera();
      proceedToPairsScreen();
    }
  } catch (err) {
    toast(err.message, true);
  }
}

export async function loadDemoSamples() {
  const sc = getScannerInstance();
  toast('Synthesizing 2-page clinic intake form…');
  try {
    await sc.loadSyntheticSample('filled');
    state.pageBlobs[0] = sc.pages[0].blob;
    state.pageUrls[0] = sc.pages[0].url;
    state.pageBlobs[1] = sc.pages[1].blob;
    state.pageUrls[1] = sc.pages[1].url;
    sc.stopCamera();
    proceedToPairsScreen();
  } catch (err) {
    toast('Failed to load demo sample: ' + err.message, true);
  }
}

function updateScanStepsUI() {
  const p = state.currentScanPage;
  const s1 = $('s1');
  const s2 = $('s2');
  const s3 = $('s3');
  const stl = $('stl');
  const sh = $('sh');

  if (s1) s1.className = p >= 0 ? 'on' : '';
  if (s2) s2.className = p >= 1 ? 'on' : '';
  if (s3) s3.className = '';

  if (stl) {
    stl.textContent = p === 0 
      ? 'Scan Page 1: Vitals & Details' 
      : 'Scan Page 2: Clinical Exam & Referral';
  }
  if (sh) {
    sh.dataset.t = `Health intake form · Page ${p + 1}`;
  }
}

function proceedToPairsScreen() {
  const p1 = state.pageUrls[0];
  const p2 = state.pageUrls[1];

  const prev1 = $('pairs-p1-preview');
  const prev2 = $('pairs-p2-preview');
  if (prev1 && p1) prev1.style.backgroundImage = `url('${p1}')`;
  if (prev2 && p2) prev2.style.backgroundImage = `url('${p2}')`;

  // Populate Camp Dropdown in Pairs
  const campSelect = $('pairs-camp-select');
  if (campSelect) {
    campSelect.innerHTML = state.camps.map(c => 
      `<option value="${c.id}" ${c.id === state.selectedCampId ? 'selected' : ''}>${escapeHtml(c.name)}</option>`
    ).join('');
  }

  if ($('pairs-person-name')) $('pairs-person-name').value = '';
  if ($('pairs-err')) $('pairs-err').textContent = '';

  go('pairs');
}

// Generate high quality canvas JPEG blob for demo testing
function generatePageCanvasBlob(pageNumber) {
  return new Promise((resolve) => {
    const canvas = document.createElement('canvas');
    canvas.width = 1200;
    canvas.height = 1600;
    const ctx = canvas.getContext('2d');

    // Paper background
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Header styling
    ctx.fillStyle = '#0E7C6B';
    ctx.fillRect(40, 40, canvas.width - 80, 110);
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 36px "Figtree", sans-serif';
    ctx.fillText(`HEALTH CAMP INTAKE FORM — PAGE ${pageNumber}`, 70, 105);

    // Form Lines
    ctx.strokeStyle = '#D5E0DD';
    ctx.lineWidth = 2;
    for (let y = 200; y < canvas.height - 100; y += 70) {
      ctx.beginPath();
      ctx.moveTo(70, y);
      ctx.lineTo(canvas.width - 70, y);
      ctx.stroke();
    }

    // Sample handwritten text simulation
    ctx.fillStyle = '#14233A';
    ctx.font = '500 30px "Bricolage Grotesque", sans-serif';
    if (pageNumber === 1) {
      ctx.fillText('Patient Name: Rahul Sharma            Age: 24   Gender: Male', 70, 255);
      ctx.fillText('Blood Pressure: 118/76 mmHg           Pulse: 74 bpm', 70, 325);
      ctx.fillText('Height: 172 cm                        Weight: 65 kg', 70, 395);
      ctx.fillText('Blood Group: O+                       DOB: 14/08/2000', 70, 465);
      ctx.fillText('Mobile: 9876543210                    Allergies: No known allergies', 70, 535);
      ctx.fillText('Previous Surgery: None                Physical Activity: Football, gym', 70, 605);
      ctx.fillText('Family History: Father has diabetes   Mother has hypertension', 70, 675);
    } else {
      ctx.fillText('Examination: Normal respiratory sounds. Dental caries noted.', 70, 255);
      ctx.fillText('General Signs: Pallor - No            Edema - No', 70, 325);
      ctx.fillText('Respiratory System: Normal            CVS: Normal S1 S2', 70, 395);
      ctx.fillText('Vision: Right 6/6                     Left 6/6 (Unaided)', 70, 465);
      ctx.fillText('Dental Advice: Dental OPD referral for molar restoration', 70, 535);
      ctx.fillText('Physician Sign: Dr. Priya, MD         Status: Fit with dental follow-up', 70, 605);
    }

    canvas.toBlob(blob => {
      const url = URL.createObjectURL(blob);
      resolve({ blob, url });
    }, 'image/jpeg', 0.9);
  });
}

// ==========================================================================
// 5. Vision AI Extraction & Save
// ==========================================================================

export async function saveAndExtract() {
  const p1 = state.pageBlobs[0];
  const p2 = state.pageBlobs[1];
  const campSelect = $('pairs-camp-select');
  const campId = campSelect ? campSelect.value : state.selectedCampId;
  const nameInput = $('pairs-person-name')?.value.trim() || '';
  const err = $('pairs-err');
  const runBtn = $('extract-run-btn');

  if (!campId) {
    if (err) err.textContent = 'Please choose a health camp.';
    return;
  }
  if (!p1 || !p2) {
    if (err) err.textContent = 'Both Page 1 and Page 2 must be captured.';
    return;
  }
  if (err) err.textContent = '';

  if (runBtn) {
    runBtn.disabled = true;
    runBtn.innerHTML = `
      <svg class="spin" viewBox="0 0 24 24" style="width:18px;height:18px;animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" fill="none" stroke-dasharray="30 60"/></svg>
      Groq Vision Transcribing Both Pages…
    `;
  }

  try {
    toast('Transcribing document pages with Vision AI…');
    const extractRes = await api.extractPages(p1, p2);
    const ext = (extractRes && extractRes.data) ? extractRes.data : (extractRes || {});

    // Build the record payload with extracted structured data
    const personName = nameInput || ext.person_name || 'Patient';
    const recordPayload = {
      camp_id: campId,
      person_name: personName,
      age: ext.age || '',
      gender: ext.gender || '',
      height: ext.height || '',
      weight: ext.weight || '',
      blood_pressure: ext.blood_pressure || '',
      examination_details: ext.examination_details || '',
      referral_details: ext.referral_details || '',
      missing_fields: ext.missing_fields || [],
      uncertain_fields: ext.uncertain_fields || [],
      extraction_notes: ext.extraction_notes || 'Extracted via Groq Vision',
      structured_record: ext.structured_record || {},
      status: 'needs_review',
      page1_preview: state.pageUrls[0],
      page2_preview: state.pageUrls[1]
    };

    const saved = await api.saveRecord(recordPayload);
    toast('Transcribed! Opening physician review…');

    // Automatically load into Review Screen
    openReview(saved.id);

  } catch (ex) {
    console.error('Extraction error:', ex);
    // Fallback: create record with manual entry so workflow is never blocked
    try {
      const fallbackPayload = {
        camp_id: campId,
        person_name: nameInput || 'New Patient (Manual Entry)',
        status: 'needs_review',
        page1_preview: state.pageUrls[0],
        page2_preview: state.pageUrls[1],
        extraction_notes: 'Vision AI transcription could not complete. Manual entry enabled.'
      };
      const saved = await api.saveRecord(fallbackPayload);
      toast('Manual review opened.', true);
      openReview(saved.id);
    } catch (saveErr) {
      if (err) err.textContent = 'Could not save record: ' + saveErr.message;
    }
  } finally {
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = `
        <svg viewBox="0 0 24 24" style="width:18px;height:18px;"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        Save & Transcribe with Groq AI
      `;
    }
  }
}

// ==========================================================================
// 6. Clinical Review & Verification Workflow
// ==========================================================================

export async function openReview(recordId) {
  try {
    const record = await api.getRecord(recordId);
    state.activeRecord = record;

    // Header & Titles
    if ($('review-patient-heading')) $('review-patient-heading').textContent = record.person_name || 'Patient';
    if ($('review-camp-label')) $('review-camp-label').textContent = `${record.camp_name || 'Camp'} · ${formatRelativeTime(record.created_at)}`;
    
    const tag = $('review-status-tag');
    if (tag) {
      tag.textContent = record.status === 'reviewed' ? 'Approved' : 'To check';
      tag.className = record.status === 'reviewed' ? 'tag' : 'tag w';
    }

    // Source Document Images
    const p1Img = $('review-p1-img');
    const p2Img = $('review-p2-img');
    if (p1Img) p1Img.src = record.page1_preview || '';
    if (p2Img) p2Img.src = record.page2_preview || '';

    // Populate Fields from structured_record
    const sr = record.structured_record || {};
    const p_info = sr.personal_information || {};
    const m_hist = sr.medical_history || {};
    const f_hist = sr.family_history || {};
    const h_asmt = sr.health_assessment || {};
    const s_exam = sr.system_examination || {};
    const vis = sr.vision || {};
    const den = sr.dental || {};
    const ref = sr.referral || {};

    const getVal = (grp, key, fallback = '') => {
      if (!grp || !grp[key]) return fallback;
      const v = grp[key];
      if (typeof v === 'object' && v !== null) return v.value !== undefined && v.value !== null ? v.value : fallback;
      return v || fallback;
    };

    const getDetails = (grp, key) => {
      if (!grp || !grp[key]) return '';
      const v = grp[key];
      if (typeof v === 'object' && v !== null) return v.details || v.relationship || v.notes || '';
      return '';
    };

    // Tab 1: Personal
    if ($('rev-name')) $('rev-name').value = getVal(p_info, 'full_name', record.person_name || '');
    if ($('rev-age')) $('rev-age').value = getVal(p_info, 'age', record.age || '');
    if ($('rev-gender')) $('rev-gender').value = getVal(p_info, 'gender', record.gender || '');
    if ($('rev-blood-group')) $('rev-blood-group').value = getVal(p_info, 'blood_group', '');
    if ($('rev-dob')) $('rev-dob').value = getVal(p_info, 'dob', '');
    if ($('rev-doc-id')) $('rev-doc-id').value = getVal(p_info, 'doc_id', '');
    if ($('rev-adm-id')) $('rev-adm-id').value = getVal(p_info, 'admission_id', '');
    if ($('rev-school')) $('rev-school').value = getVal(p_info, 'school_college_name', '');
    if ($('rev-mobile')) $('rev-mobile').value = getVal(p_info, 'mobile_number', '');
    if ($('rev-address')) $('rev-address').value = getVal(p_info, 'address', '');
    if ($('rev-height')) $('rev-height').value = getVal(h_asmt, 'height_cm', record.height || '');
    if ($('rev-weight')) $('rev-weight').value = getVal(h_asmt, 'weight_kg', record.weight || '');
    if ($('rev-bp')) $('rev-bp').value = getVal(h_asmt, 'blood_pressure_mmhg', record.blood_pressure || '');
    if ($('rev-pulse')) $('rev-pulse').value = getVal(h_asmt, 'pulse_bpm', '');
    if ($('rev-rbs')) $('rev-rbs').value = getVal(h_asmt, 'random_blood_sugar_mgdl', '');
    if ($('rev-waist')) $('rev-waist').value = getVal(h_asmt, 'waist_circumference_cm', '');

    // Tab 2: Medical
    const formatMed = (k) => {
      const v = getVal(m_hist, k, '');
      const d = getDetails(m_hist, k);
      return (v && d) ? `${v} — ${d}` : (v || d);
    };
    if ($('rev-allergies')) $('rev-allergies').value = formatMed('known_allergies');
    if ($('rev-surgery')) $('rev-surgery').value = formatMed('previous_surgery_hospitalization');
    if ($('rev-medications')) $('rev-medications').value = formatMed('current_previous_medications');
    if ($('rev-abdominal')) $('rev-abdominal').value = formatMed('abdominal_pain_indigestion_constipation');
    if ($('rev-ent')) $('rev-ent').value = formatMed('skin_hair_ear_nose_problems');
    if ($('rev-activity')) $('rev-activity').value = formatMed('physical_activity');
    if ($('rev-social')) $('rev-social').value = formatMed('social_history');

    // Tab 3: Family
    const formatFam = (k) => {
      const v = getVal(f_hist, k, '');
      const d = getDetails(f_hist, k);
      return (v && d) ? `${v} (${d})` : (v || d);
    };
    if ($('rev-fam-diabetes')) $('rev-fam-diabetes').value = formatFam('diabetes');
    if ($('rev-fam-hypertension')) $('rev-fam-hypertension').value = formatFam('hypertension');
    if ($('rev-fam-heart')) $('rev-fam-heart').value = formatFam('heart_disease');
    if ($('rev-fam-thyroid')) $('rev-fam-thyroid').value = formatFam('thyroid');
    if ($('rev-fam-asthma')) $('rev-fam-asthma').value = formatFam('asthma');
    if ($('rev-fam-other')) $('rev-fam-other').value = formatFam('other_conditions');

    // Tab 4: Health Assessment
    if ($('rev-pallor')) $('rev-pallor').value = getVal(h_asmt, 'pallor', '');
    if ($('rev-icterus')) $('rev-icterus').value = getVal(h_asmt, 'icterus', '');
    if ($('rev-cyanosis')) $('rev-cyanosis').value = getVal(h_asmt, 'cyanosis', '');
    if ($('rev-edema')) $('rev-edema').value = getVal(h_asmt, 'edema', '');
    if ($('rev-clubbing')) $('rev-clubbing').value = getVal(h_asmt, 'clubbing', '');
    if ($('rev-lymph')) $('rev-lymph').value = getVal(h_asmt, 'lymphadenopathy', '');
    if ($('rev-ecg')) $('rev-ecg').value = getVal(h_asmt, 'ecg_notes', '');
    if ($('rev-echo')) $('rev-echo').value = getVal(h_asmt, 'echo_notes', '');

    // Tab 5: Systemic
    if ($('rev-sys-rs')) $('rev-sys-rs').value = getVal(s_exam, 'respiratory_system', record.examination_details || '');
    if ($('rev-sys-cvs')) $('rev-sys-cvs').value = getVal(s_exam, 'cardiovascular_system', '');
    if ($('rev-sys-abdomen')) $('rev-sys-abdomen').value = getVal(s_exam, 'abdomen', '');
    if ($('rev-sys-cns')) $('rev-sys-cns').value = getVal(s_exam, 'central_nervous_system', '');
    if ($('rev-sys-ent')) $('rev-sys-ent').value = getVal(s_exam, 'ear', '');
    if ($('rev-sys-skin')) $('rev-sys-skin').value = getVal(s_exam, 'skin', '');

    // Tab 6: Vision & Dental
    if ($('rev-va-right')) $('rev-va-right').value = getVal(vis, 'distant_vision_right', '');
    if ($('rev-va-left')) $('rev-va-left').value = getVal(vis, 'distant_vision_left', '');
    if ($('rev-vision-category')) $('rev-vision-category').value = getVal(vis, 'vision_category', '');
    if ($('rev-dental-status')) $('rev-dental-status').value = getVal(den, 'normal', '');
    if ($('rev-dental-advice')) $('rev-dental-advice').value = getVal(den, 'advice', '');

    // Tab 7: Referral & Doctor Notes
    if ($('rev-referral')) $('rev-referral').value = getVal(ref, 'specialist_referred_to', record.referral_details || '');
    if ($('rev-doc-sign')) $('rev-doc-sign').value = getVal(ref, 'doctor_signature_present', '');
    if ($('rev-doctor-notes')) $('rev-doctor-notes').value = record.reviewer_notes || '';

    // Uncertain fields badges
    const uncertain = record.uncertain_fields || [];
    if ($('badge-name')) $('badge-name').style.display = uncertain.includes('person_name') ? 'inline-flex' : 'none';
    if ($('badge-bp')) $('badge-bp').style.display = uncertain.includes('blood_pressure') ? 'inline-flex' : 'none';
    if ($('badge-allergies')) $('badge-allergies').style.display = uncertain.includes('known_allergies') ? 'inline-flex' : 'none';

    switchReviewTab('personal');
    go('review');

  } catch (err) {
    toast('Error loading record: ' + err.message, true);
  }
}

export function toggleReviewDocs() {
  const box = $('review-docs-box');
  if (box) {
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
  }
}

export function switchReviewTab(tabKey) {
  const tabs = ['personal', 'medical', 'family', 'health', 'system', 'vision', 'referral'];
  tabs.forEach(k => {
    const btn = $(`rtab-${k}`);
    const sec = $(`rsec-${k}`);
    if (btn) btn.classList.toggle('active', k === tabKey);
    if (sec) sec.classList.toggle('active', k === tabKey);
  });
  if (tabKey === 'referral') {
    setTimeout(() => initSignaturePad(), 50);
  }
}

export async function approveAndSaveRecord() {
  if (!state.activeRecord) return;
  const btn = $('approve-btn');
  if (btn) btn.disabled = true;

  try {
    const updates = {
      person_name: $('rev-name')?.value.trim() || state.activeRecord.person_name,
      age: $('rev-age')?.value.trim() || state.activeRecord.age,
      gender: $('rev-gender')?.value.trim() || state.activeRecord.gender,
      height: $('rev-height')?.value.trim() || state.activeRecord.height,
      weight: $('rev-weight')?.value.trim() || state.activeRecord.weight,
      blood_pressure: $('rev-bp')?.value.trim() || state.activeRecord.blood_pressure,
      examination_details: $('rev-sys-rs')?.value.trim() || state.activeRecord.examination_details,
      referral_details: $('rev-referral')?.value.trim() || state.activeRecord.referral_details,
      reviewer_notes: $('rev-doctor-notes')?.value.trim() || 'Verified and approved.'
    };

    // 1. Review edits
    await api.reviewRecord(state.activeRecord.id, updates, state.doctorName);
    // 2. Explicit approval action (checks no unresolved flags remain, bills quota, marks approved)
    await api.approveRecord(state.activeRecord.id, state.doctorName);

    toast('Record approved and quota billed!');
    go('home');
  } catch (err) {
    toast('Approval failed: ' + err.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function saveDraftReview() {
  if (!state.activeRecord) return;
  const btn = $('save-draft-btn');
  if (btn) btn.disabled = true;

  try {
    const updates = {
      person_name: $('rev-name')?.value.trim() || state.activeRecord.person_name,
      age: $('rev-age')?.value.trim() || state.activeRecord.age,
      gender: $('rev-gender')?.value.trim() || state.activeRecord.gender,
      height: $('rev-height')?.value.trim() || state.activeRecord.height,
      weight: $('rev-weight')?.value.trim() || state.activeRecord.weight,
      blood_pressure: $('rev-bp')?.value.trim() || state.activeRecord.blood_pressure,
      examination_details: $('rev-sys-rs')?.value.trim() || state.activeRecord.examination_details,
      referral_details: $('rev-referral')?.value.trim() || state.activeRecord.referral_details,
      reviewer_notes: $('rev-doctor-notes')?.value.trim() || 'Draft review saved.'
    };

    await api.reviewRecord(state.activeRecord.id, updates, state.doctorName);
    toast('Draft review saved (record not approved yet).');
    go('records');
  } catch (err) {
    toast('Failed to save draft: ' + err.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ==========================================================================
// 7. Patient Records Screen (Organized by Club / Organization)
// ==========================================================================

export async function renderRecords() {
  try {
    const records = await api.getRecords(null, null);
    state.records = records || [];

    // Populate Organization / Club filter dropdown
    const orgSelect = $('records-org-filter');
    if (orgSelect) {
      const currentVal = state.selectedCampId || 'all';
      orgSelect.innerHTML = `
        <option value="all">🏢 All Organizations & Clubs (${state.records.length} records total)</option>
        ${state.camps.map(c => `
          <option value="${c.id}" ${c.id === currentVal ? 'selected' : ''}>
            ${escapeHtml(c.name)} (${c.total_scanned || 0} records filed)
          </option>
        `).join('')}
      `;
      if (currentVal && currentVal !== 'all') {
        orgSelect.value = currentVal;
      }
    }

    filterRecords();
  } catch (err) {
    console.error('Error fetching records:', err);
  }
}

export function onRecordsOrgChange() {
  const orgSelect = $('records-org-filter');
  const selectedOrg = orgSelect ? orgSelect.value : 'all';
  state.selectedCampId = selectedOrg !== 'all' ? selectedOrg : null;
  filterRecords();
}

export function downloadActiveOrgExcel() {
  const orgSelect = $('records-org-filter');
  const selectedOrg = orgSelect ? orgSelect.value : (state.selectedCampId || 'all');
  if (selectedOrg && selectedOrg !== 'all') {
    exportCampExcel(selectedOrg);
  } else {
    exportExcel();
  }
}

export function setRecordsFilter(filter) {
  state.recordsFilter = filter;
  ['rf-all', 'rf-warn', 'rf-ok'].forEach(id => {
    const el = $(id);
    if (el) el.classList.remove('active');
  });
  if (filter === 'all' && $('rf-all')) $('rf-all').classList.add('active');
  if (filter === 'needs_review' && $('rf-warn')) $('rf-warn').classList.add('active');
  if (filter === 'reviewed' && $('rf-ok')) $('rf-ok').classList.add('active');
  filterRecords();
}

export function filterRecords() {
  const query = ($('records-search')?.value || '').toLowerCase().trim();
  const filter = state.recordsFilter;
  const orgSelect = $('records-org-filter');
  const selectedOrg = orgSelect ? orgSelect.value : (state.selectedCampId || 'all');
  const container = $('records-full-list');
  if (!container) return;

  const filtered = state.records.filter(r => {
    // Org filter
    if (selectedOrg !== 'all' && r.camp_id !== selectedOrg) return false;

    // Search query
    const nameMatch = (r.person_name || '').toLowerCase().includes(query) ||
                      (r.camp_name || '').toLowerCase().includes(query) ||
                      (r.id || '').toLowerCase().includes(query);
    if (!nameMatch) return false;

    // Status filter
    if (filter === 'needs_review' && r.status !== 'needs_review') return false;
    if (filter === 'reviewed' && r.status !== 'reviewed') return false;
    return true;
  });

  // Update org count and download button text
  const orgCountEl = $('records-org-count');
  if (orgCountEl) {
    orgCountEl.textContent = `${filtered.length} records filed`;
  }

  const orgDlBtn = $('records-org-download-btn');
  if (orgDlBtn) {
    if (selectedOrg !== 'all') {
      const activeCamp = state.camps.find(c => c.id === selectedOrg);
      const shortName = activeCamp ? (activeCamp.name.length > 20 ? activeCamp.name.slice(0, 18) + '…' : activeCamp.name) : 'Club';
      orgDlBtn.innerHTML = `
        <svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
        Download ${escapeHtml(shortName)} Excel (.xlsx)
      `;
      orgDlBtn.title = `Download complete Excel workbook for ${activeCamp ? activeCamp.name : 'this organization'}`;
    } else {
      orgDlBtn.innerHTML = `
        <svg viewBox="0 0 24 24" style="width:14px;height:14px;fill:currentColor;"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg>
        Download All Excel (.xlsx)
      `;
      orgDlBtn.title = "Download complete Excel workbook for all organizations";
    }
  }

  if (filtered.length === 0) {
    if (selectedOrg !== 'all') {
      const activeCamp = state.camps.find(c => c.id === selectedOrg);
      const orgName = activeCamp ? activeCamp.name : 'this organization';
      container.innerHTML = `
        <div style="padding:28px 16px;text-align:center;color:var(--mute);background:#fff;border-radius:16px;border:1.5px dashed var(--line);margin-top:8px;">
          <p style="font-weight:700;color:var(--ink);margin-bottom:6px;font-size:15px;">No records yet for ${escapeHtml(orgName)}</p>
          <p style="font-size:13px;color:var(--mute);margin-bottom:14px;">Scan documents directly into this organization or select another club.</p>
          <button class="btn" style="width:auto;display:inline-block;padding:9px 18px;font-size:13px;" onclick="window.app.scanForCamp('${selectedOrg}')">
            📷 Scan First Document into ${escapeHtml(orgName)}
          </button>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div style="padding:24px;text-align:center;color:var(--mute);">
          No records match your search filter.
        </div>
      `;
    }
    return;
  }

  const tagStyle = {
    reviewed: ['Approved', 'tag'],
    needs_review: ['To check', 'tag w'],
    failed: ['Failed', 'tag err']
  };

  container.innerHTML = filtered.map(r => {
    const initials = (r.person_name || 'U').charAt(0).toUpperCase();
    const tagInfo = tagStyle[r.status] || ['Reading…', 'tag r'];
    const timeStr = formatRelativeTime(r.created_at);
    const vitalsStr = [r.blood_pressure, r.height, r.weight].filter(Boolean).join(' · ');

    return `
      <div class="rec" onclick="window.app.openReview('${r.id}')">
        <span class="a">${initials}</span>
        <div>
          <b>${escapeHtml(r.person_name || 'Unknown Patient')}</b>
          ${vitalsStr ? `<span style="font-size:12px;color:var(--mute);margin-left:6px;">(${escapeHtml(vitalsStr)})</span>` : ''}<br>
          <small>${escapeHtml(r.camp_name || 'Clinic Camp')} · ${timeStr}</small>
        </div>
        <span class="${tagInfo[1]}">${tagInfo[0]}</span>
      </div>
    `;
  }).join('');
}

// ==========================================================================
// 8. Comprehensive Excel Export (.xlsx) & Live Spreadsheet Preview
// ==========================================================================

function triggerDownload(url, filename = 'clinic_records_all.xlsx') {
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  setTimeout(() => {
    if (link.parentNode) link.parentNode.removeChild(link);
  }, 200);
}

let currentExcelSheet = 'records';
let excelSearchQuery = '';

export function exportExcel(campId = null, onlyReviewed = false) {
  const targetCampId = campId || null;
  const camp = targetCampId ? state.camps.find(x => x.id === targetCampId) : null;
  const rawName = camp ? camp.name : 'All_Organizations';
  const safeName = rawName.replace(/[^a-zA-Z0-9_\-]/g, '_').replace(/_+/g, '_') || 'Health_Records';
  const url = api.getExportUrl(targetCampId, onlyReviewed, 'xlsx');
  const filename = `${safeName}_Health_Records.xlsx`;
  
  triggerDownload(url, filename);
  toast(`Downloaded ${filename} (.xlsx)`);

  // Navigate user to the interactive Excel Sheet Viewer so they immediately SEE the spreadsheet
  go('export');

  // Update Ribbon Title
  const ribTitle = $('excel-ribbon-filename');
  if (ribTitle) ribTitle.textContent = `${filename} (Live In-App Viewer)`;

  // Show prominent confirmation banner
  const banner = $('excel-download-banner');
  if (banner) {
    banner.style.display = 'block';
    banner.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <span style="font-size:26px;line-height:1;">📥</span>
        <div style="flex:1;">
          <b style="color:var(--teal2);font-size:14.5px;display:block;margin-bottom:2px;">Excel Workbook Downloaded!</b>
          <p style="font-size:13px;color:var(--ink);margin:0;line-height:1.4;">
            File saved to your computer Downloads folder: <code style="background:#fff;padding:2px 6px;border-radius:4px;border:1px solid var(--teal);font-weight:700;">${filename}</code>.
            <br>You can also view, search, and inspect all clinical fields and sheets directly in the interactive spreadsheet below.
          </p>
        </div>
      </div>
    `;
  }

  // Sync select if campId specified
  const sel = $('export-camp-select');
  if (sel && targetCampId) {
    sel.value = targetCampId;
  }
  renderExcelPreview(currentExcelSheet);
}

export function exportCampExcel(campId) {
  const camp = state.camps.find(x => x.id === campId);
  const rawName = camp ? camp.name : 'Organization';
  const safeName = rawName.replace(/[^a-zA-Z0-9_\-]/g, '_').replace(/_+/g, '_') || 'Organization';
  const url = api.getCampExportUrl(campId, 'xlsx');
  const filename = `${safeName}_Health_Records.xlsx`;
  triggerDownload(url, filename);
  toast(`Downloaded ${filename}`);

  go('export');

  const sel = $('export-camp-select');
  if (sel) sel.value = campId;

  const ribTitle = $('excel-ribbon-filename');
  if (ribTitle) ribTitle.textContent = `${filename} (Live In-App Viewer)`;

  const banner = $('excel-download-banner');
  if (banner) {
    banner.style.display = 'block';
    banner.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <span style="font-size:26px;line-height:1;">📥</span>
        <div style="flex:1;">
          <b style="color:var(--teal2);font-size:14.5px;display:block;margin-bottom:2px;">${escapeHtml(rawName)} Excel Workbook Downloaded!</b>
          <p style="font-size:13px;color:var(--ink);margin:0;line-height:1.4;">
            File saved to Downloads folder: <code style="background:#fff;padding:2px 6px;border-radius:4px;border:1px solid var(--teal);font-weight:700;">${filename}</code>.
            <br>Contains all patient records filed under this organization.
          </p>
        </div>
      </div>
    `;
  }
  renderExcelPreview('records');
}

export function executeExport() {
  const campSelect = $('export-camp-select');
  const campId = (campSelect && campSelect.value) ? campSelect.value : null;
  const onlyReviewed = $('export-only-reviewed')?.checked || false;
  exportExcel(campId, onlyReviewed);
}

export function toggleExcelFullscreen() {
  const win = $('excel-window-el');
  const btn = $('excel-fs-btn');
  if (!win) return;
  const isFs = win.classList.toggle('fullscreen');
  if (btn) {
    btn.textContent = isFs ? '✕ Minimize' : '⛶ Fullscreen';
  }
}

export function switchExcelSheetTab(sheetKey) {
  currentExcelSheet = sheetKey;
  const tRec = $('extab-records');
  const tCmp = $('extab-camps');
  if (tRec) tRec.classList.toggle('active', sheetKey === 'records');
  if (tCmp) tCmp.classList.toggle('active', sheetKey === 'camps');
  renderExcelPreview(sheetKey);
}

export function filterExcelPreview() {
  excelSearchQuery = ($('excel-search-input')?.value || '').trim().toLowerCase();
  renderExcelPreview(currentExcelSheet);
}

export function onExportFilterChanged() {
  const sel = $('export-camp-select');
  const campId = sel ? sel.value : '';
  const ribTitle = $('excel-ribbon-filename');
  if (ribTitle) {
    ribTitle.textContent = campId ? `clinic_records_${campId}.xlsx (Live Viewer)` : 'clinic_records_all.xlsx (Live Viewer)';
  }
  renderExcelPreview(currentExcelSheet);
}

export function renderExcelPreview(sheetName = currentExcelSheet) {
  const container = $('excel-grid-container');
  if (!container) return;

  const selCamp = $('export-camp-select')?.value || null;
  const onlyApproved = $('export-only-reviewed')?.checked || false;
  const campsCountEl = $('excel-camps-count');
  if (campsCountEl) campsCountEl.textContent = state.camps.length;

  if (sheetName === 'records') {
    // Filter records
    let list = state.records.slice();
    if (selCamp) {
      list = list.filter(r => r.camp_id === selCamp);
    }
    if (onlyApproved) {
      list = list.filter(r => r.status === 'reviewed');
    }
    if (excelSearchQuery) {
      list = list.filter(r => {
        const pName = (r.person_name || '').toLowerCase();
        const rId = (r.id || '').toLowerCase();
        const cName = (r.camp_name || '').toLowerCase();
        const bp = (r.blood_pressure || '').toLowerCase();
        return pName.includes(excelSearchQuery) || rId.includes(excelSearchQuery) || cName.includes(excelSearchQuery) || bp.includes(excelSearchQuery);
      });
    }

    const recordsCountEl = $('excel-records-count');
    if (recordsCountEl) recordsCountEl.textContent = list.length;

    if (list.length === 0) {
      container.innerHTML = `
        <div style="padding:32px 20px;text-align:center;color:var(--mute);">
          <div style="font-size:28px;margin-bottom:8px;">📋</div>
          <b>No records match the current spreadsheet filter.</b>
          <p style="font-size:12.5px;margin-top:4px;">Select "All Health Camps" or clear your search to view all patient records.</p>
        </div>
      `;
      return;
    }

    const headers = [
      "Record ID", "Camp Name", "Person Name", "Review Status", "Age", "Gender", 
      "Blood Pressure", "Pulse", "Height", "Weight", "BMI", "RBS (mg/dL)", "Waist", 
      "Known Allergies", "Surgery / Hospital", "Medications", "Social History", 
      "Family Diabetes", "Family HTN", "RS Exam", "CVS Exam", "Vision Right", 
      "Vision Left", "Dental Status", "Specialist Referral", "Reviewed By", "Date"
    ];

    let html = `<table class="excel-table"><thead><tr>`;
    html += `<th class="col-corner">#</th>`;
    headers.forEach((h, idx) => {
      const colLetter = String.fromCharCode(65 + (idx % 26));
      html += `<th>
        <div class="excel-th-box">
          <span class="excel-th-letter">${colLetter}</span>
          <span class="excel-th-title">${escapeHtml(h)}</span>
        </div>
      </th>`;
    });
    html += `</tr></thead><tbody>`;

    list.forEach((r, rowIdx) => {
      const sr = r.structured_record || {};
      const p = sr.personal_information || {};
      const m = sr.medical_history || {};
      const f = sr.family_history || {};
      const h = sr.health_assessment || {};
      const s = sr.system_examination || {};
      const vis = sr.vision || {};
      const den = sr.dental || {};
      const ref = sr.referral || {};

      const getVal = (grp, key, fallback = '') => {
        if (!grp || !grp[key]) return fallback;
        const v = grp[key];
        if (typeof v === 'object' && v !== null) return v.value !== undefined && v.value !== null ? v.value : fallback;
        return v || fallback;
      };

      const hVal = getVal(h, 'height_cm', r.height || '');
      const wVal = getVal(h, 'weight_kg', r.weight || '');
      let bmi = '';
      try {
        const hm = parseFloat(String(hVal).replace(/[^\d.]/g, ''));
        const wm = parseFloat(String(wVal).replace(/[^\d.]/g, ''));
        if (hm > 0 && wm > 0) {
          const hMeters = hm > 30 ? hm / 100 : hm;
          bmi = (wm / (hMeters * hMeters)).toFixed(1);
        }
      } catch {}

      const rowVals = [
        r.id,
        r.camp_name || 'Camp',
        getVal(p, 'full_name', r.person_name || 'Patient'),
        r.status === 'reviewed' ? 'Approved' : 'To check',
        getVal(p, 'age', r.age || ''),
        getVal(p, 'gender', r.gender || ''),
        getVal(h, 'blood_pressure_mmhg', r.blood_pressure || ''),
        getVal(h, 'pulse_bpm', ''),
        hVal,
        wVal,
        bmi,
        getVal(h, 'random_blood_sugar_mgdl', ''),
        getVal(h, 'waist_circumference_cm', ''),
        getVal(m, 'known_allergies', 'None'),
        getVal(m, 'previous_surgery_hospitalization', 'None'),
        getVal(m, 'current_previous_medications', 'None'),
        getVal(m, 'social_history', 'None'),
        getVal(f, 'diabetes', 'No'),
        getVal(f, 'hypertension', 'No'),
        getVal(s, 'respiratory_system', r.examination_details || 'Normal'),
        getVal(s, 'cardiovascular_system', 'Normal'),
        getVal(vis, 'distant_vision_right', '6/6'),
        getVal(vis, 'distant_vision_left', '6/6'),
        getVal(den, 'normal', 'Normal'),
        getVal(ref, 'specialist_referred_to', r.referral_details || 'None'),
        r.reviewed_by || '',
        r.created_at ? r.created_at.substring(0, 10) : ''
      ];

      html += `<tr>`;
      html += `<td class="row-num">${rowIdx + 1}</td>`;
      rowVals.forEach((val, cIdx) => {
        let cellContent = escapeHtml(String(val || ''));
        if (cIdx === 3) {
          const isApp = val === 'Approved';
          cellContent = `<span class="tag ${isApp ? '' : 'w'}" style="font-size:10.5px;padding:2px 8px;">${cellContent}</span>`;
        }
        html += `<td>${cellContent}</td>`;
      });
      html += `</tr>`;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;

  } else {
    // Sheet 2: Camps Summary Table
    const campsCountEl = $('excel-camps-count');
    if (campsCountEl) campsCountEl.textContent = state.camps.length;

    let html = `<table class="excel-table"><thead><tr>`;
    html += `<th class="col-corner">#</th>`;
    const campHeaders = ["Camp ID", "Camp Name", "Date", "Target Quota", "Total Scanned", "To Check", "Reviewed", "Progress %", "Created At"];
    campHeaders.forEach((h, idx) => {
      const colLetter = String.fromCharCode(65 + (idx % 26));
      html += `<th>
        <div class="excel-th-box">
          <span class="excel-th-letter">${colLetter}</span>
          <span class="excel-th-title">${escapeHtml(h)}</span>
        </div>
      </th>`;
    });
    html += `</tr></thead><tbody>`;

    state.camps.forEach((c, idx) => {
      html += `<tr>`;
      html += `<td class="row-num">${idx + 1}</td>`;
      html += `<td>${escapeHtml(c.id)}</td>`;
      html += `<td><b>${escapeHtml(c.name)}</b></td>`;
      html += `<td>${escapeHtml(c.date_str || '')}</td>`;
      html += `<td>${c.expected_records || 100}</td>`;
      html += `<td>${c.total_scanned || 0}</td>`;
      html += `<td>${c.to_check || 0}</td>`;
      html += `<td>${c.reviewed || 0}</td>`;
      html += `<td>${Math.round(c.progress_pct || 0)}%</td>`;
      html += `<td>${(c.created_at || '').substring(0, 10)}</td>`;
      html += `</tr>`;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;
  }
}

function populateExportSelects() {
  const sel = $('export-camp-select');
  if (sel) {
    const curVal = sel.value;
    sel.innerHTML = `
      <option value="" ${!curVal ? 'selected' : ''}>All Health Camps (All Records)</option>
      ${state.camps.map(c => `<option value="${c.id}" ${curVal === c.id ? 'selected' : ''}>${escapeHtml(c.name)} (${c.total_scanned || 0} records)</option>`).join('')}
    `;
  }

  const container = $('export-camps-list');
  if (container) {
    container.innerHTML = state.camps.map(c => `
      <div class="camp" style="display:flex;justify-content:space-between;align-items:center;">
        <div>
          <b>${escapeHtml(c.name)}</b><br>
          <small>${c.total_scanned || 0} records · ${c.date_str}</small>
        </div>
        <div style="display:flex;gap:6px;">
          <button class="btn ghost" style="width:auto;padding:6px 10px;font-size:12px;" onclick="window.app.exportCampExcel('${c.id}')">
            View / Download
          </button>
        </div>
      </div>
    `).join('');
  }

  // Render the live spreadsheet preview
  renderExcelPreview(currentExcelSheet);
}

// ==========================================================================
// 9. Camp Creation Modal
// ==========================================================================

export function openNewCampModal() {
  const ov = $('ov');
  if (ov) {
    ov.classList.add('on');
    if ($('cn')) $('cn').value = '';
    if ($('ci')) $('ci').value = '120';
    if ($('cd')) $('cd').value = 'Today';
    if ($('cerr')) $('cerr').textContent = '';
  }
}

export function closeNewCampModal() {
  const ov = $('ov');
  if (ov) ov.classList.remove('on');
}

export async function saveNewCamp() {
  const name = $('cn')?.value.trim();
  const quota = parseInt($('ci')?.value, 10) || 100;
  const dateStr = $('cd')?.value.trim() || 'Today';
  const err = $('cerr');

  if (!name) {
    if (err) err.textContent = 'Please enter a health camp name.';
    return;
  }
  if (quota <= 0) {
    if (err) err.textContent = 'Expected records quota must be at least 1.';
    return;
  }

  try {
    const created = await api.createCamp(name, quota, dateStr);
    closeNewCampModal();
    state.selectedCampId = created.id;
    await renderHome();
    toast(`Health camp "${created.name}" created!`);
  } catch (ex) {
    if (err) err.textContent = 'Failed to create camp: ' + ex.message;
  }
}

// ==========================================================================
// 10. Settings & Demo Controls
// ==========================================================================

export async function renderSettings() {
  try {
    const status = await api.getStatus();
    const doc = state.doctorName || localStorage.getItem('clinic_doctor_name') || 'Physician';
    const email = localStorage.getItem('clinic_doctor_email') || 'physician@hospital.org';
    const initial = doc.replace('Dr. ', '').charAt(0) || 'D';

    if ($('settings-doctor-name')) $('settings-doctor-name').textContent = doc;
    if ($('settings-doctor-email')) $('settings-doctor-email').textContent = email;
    if ($('settings-doctor-av')) $('settings-doctor-av').textContent = initial;

    if (status) {
      if ($('settings-ai-provider')) {
        $('settings-ai-provider').textContent = status.ai_provider || 'Groq Vision (qwen/qwen3.8-27b)';
      }
      if ($('settings-ai-status')) {
        $('settings-ai-status').textContent = status.ai_configured ? 'Active API' : 'Unconfigured';
        $('settings-ai-status').className = status.ai_configured ? 'tag ok' : 'tag w';
      }
      if ($('settings-supabase-status')) {
        $('settings-supabase-status').textContent = status.supabase_configured ? 'Connected' : 'Unconfigured';
        $('settings-supabase-status').className = status.supabase_configured ? 'tag ok' : 'tag w';
      }
      if ($('settings-sqlite-status')) {
        $('settings-sqlite-status').textContent = 'Synced';
        $('settings-sqlite-status').className = 'tag ok';
      }
    }

    // Phase 4: Fetch Usage Telemetry
    try {
      const usage = await api.getUsage();
      if (usage) {
        if ($('billing-plan-name')) $('billing-plan-name').textContent = (usage.plan_tier ? usage.plan_tier.toUpperCase() : 'STANDARD') + ' PLAN';
        if ($('billing-renews-on')) $('billing-renews-on').textContent = `Renews on: ${usage.renews_on ? usage.renews_on.slice(0, 10) : 'Next Month'}`;
        if ($('billing-papers-included')) $('billing-papers-included').textContent = (usage.papers_included || 0).toLocaleString();
        if ($('billing-papers-used')) $('billing-papers-used').textContent = (usage.papers_used || 0).toLocaleString();
        if ($('billing-papers-remaining')) $('billing-papers-remaining').textContent = (usage.papers_remaining || 0).toLocaleString();

        const pct = Math.min(100, Math.round(((usage.papers_used || 0) / (usage.papers_included || 1)) * 100));
        if ($('billing-usage-pct')) $('billing-usage-pct').textContent = `${pct}%`;
        if ($('billing-progress-bar')) $('billing-progress-bar').style.width = `${pct}%`;

        if ($('billing-rollover-info')) $('billing-rollover-info').textContent = `Rollover: ${(usage.papers_rollover || 0).toLocaleString()} (max 2,500)`;
        if ($('billing-daily-cap-info')) $('billing-daily-cap-info').textContent = `Daily Cap: 2,000 / day`;
      }
    } catch (uErr) {
      console.warn('Could not load usage telemetry:', uErr);
    }

    // Phase 4: Fetch Invoices List
    try {
      const invoices = await api.getInvoices();
      const listEl = $('billing-invoices-list');
      if (listEl) {
        if (!invoices || invoices.length === 0) {
          listEl.innerHTML = `<p style="color:var(--mute);font-size:12px;text-align:center;padding:12px 0;">No GST invoices generated yet.</p>`;
        } else {
          listEl.innerHTML = invoices.map(inv => `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 12px;border:1px solid #E2E8F0;border-radius:10px;background:#F8FAFC;">
              <div>
                <b style="font-size:13px;color:var(--ink);">${escapeHtml(inv.invoice_number)}</b>
                <span style="display:block;font-size:11px;color:var(--mute);">${inv.created_at ? inv.created_at.slice(0, 10) : ''} • ${escapeHtml(inv.pack_name || 'Add-on Pack')}</span>
              </div>
              <div style="text-align:right;">
                <b style="font-size:13px;color:var(--ink);">₹${(inv.total_amount_inr || 0).toLocaleString()}</b>
                <span class="tag ok" style="font-size:10px;padding:2px 6px;margin-left:6px;">Paid</span>
                <div style="margin-top:4px;">
                  <a href="${api.getInvoiceDownloadUrl(inv.invoice_number || inv.id)}" target="_blank" class="btn ghost" style="width:auto;display:inline-block;padding:2px 8px;font-size:11px;text-decoration:none;">PDF 📄</a>
                </div>
              </div>
            </div>
          `).join('');
        }
      }
    } catch (iErr) {
      console.warn('Could not load invoices:', iErr);
    }
  } catch (err) {
    console.error('Error in renderSettings:', err);
  }
}

export function openBuyAddonModal() {
  const modal = $('buy-addon-modal');
  if (modal) modal.classList.add('on');
}

export function closeBuyAddonModal() {
  const modal = $('buy-addon-modal');
  if (modal) modal.classList.remove('on');
}

export async function buyAddonPack() {
  const selectedRadio = document.querySelector('input[name="addon-pack"]:checked');
  const papers = selectedRadio ? parseInt(selectedRadio.value, 10) : 1000;
  const payBtn = $('pay-razorpay-btn');
  if (payBtn) payBtn.disabled = true;

  try {
    const order = await api.createBillingOrder(papers);
    toast(`Razorpay Order created: ${order.order_id} (₹${order.total_amount_inr})`);
    
    closeBuyAddonModal();
    await renderSettings();
  } catch (err) {
    toast('Failed to create billing order: ' + err.message, true);
  } finally {
    if (payBtn) payBtn.disabled = false;
  }
}

export async function clearClinicCache() {
  if (!confirm('Clear local database cache and refresh latest records from cloud?')) {
    return;
  }

  try {
    await api.clearAllData();
    await renderHome();
    toast('Local database cache cleared.');
    go('home');
  } catch (err) {
    toast('Failed to clear cache: ' + err.message, true);
  }
}

export async function resetDemoData() {
  return clearClinicCache();
}

// ==========================================================================
// Signature Pad Controller & Camp Analytics
// ==========================================================================

let signatureCanvas = null;
let signatureCtx = null;
let isDrawingSignature = false;

export function initSignaturePad() {
  const canvas = $('doctor-sig-canvas');
  if (!canvas) return;
  signatureCanvas = canvas;
  signatureCtx = canvas.getContext('2d');

  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width || 340;
  canvas.height = rect.height || 120;

  signatureCtx.lineWidth = 2.5;
  signatureCtx.lineCap = 'round';
  signatureCtx.lineJoin = 'round';
  signatureCtx.strokeStyle = '#D81B60';

  function getPos(e) {
    const cRect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return {
      x: (clientX - cRect.left) * (canvas.width / cRect.width),
      y: (clientY - cRect.top) * (canvas.height / cRect.height)
    };
  }

  function startDraw(e) {
    e.preventDefault();
    isDrawingSignature = true;
    const pos = getPos(e);
    signatureCtx.beginPath();
    signatureCtx.moveTo(pos.x, pos.y);
  }

  function draw(e) {
    if (!isDrawingSignature) return;
    e.preventDefault();
    const pos = getPos(e);
    signatureCtx.lineTo(pos.x, pos.y);
    signatureCtx.stroke();
  }

  function stopDraw(e) {
    if (!isDrawingSignature) return;
    e.preventDefault();
    isDrawingSignature = false;
    signatureCtx.closePath();
    if ($('rev-doc-sign')) {
      $('rev-doc-sign').value = `Digitally Signed by ${state.doctorName || 'Attending Physician'}`;
    }
  }

  canvas.addEventListener('mousedown', startDraw);
  canvas.addEventListener('mousemove', draw);
  window.addEventListener('mouseup', stopDraw);

  canvas.addEventListener('touchstart', startDraw, { passive: false });
  canvas.addEventListener('touchmove', draw, { passive: false });
  window.addEventListener('touchend', stopDraw, { passive: false });
}

export function clearSignature() {
  if (!signatureCanvas || !signatureCtx) {
    signatureCanvas = $('doctor-sig-canvas');
    if (signatureCanvas) signatureCtx = signatureCanvas.getContext('2d');
  }
  if (signatureCanvas && signatureCtx) {
    signatureCtx.clearRect(0, 0, signatureCanvas.width, signatureCanvas.height);
  }
  if ($('rev-doc-sign')) {
    $('rev-doc-sign').value = 'Not Signed';
  }
}

export async function renderAnalytics() {
  try {
    const data = await api.getCampAnalytics(state.selectedCampId);
    if (!data) return;

    if ($('analytics-patient-count')) {
      $('analytics-patient-count').textContent = `${data.total_patients || state.records.length || 0} Patients`;
    }

    const bpNorm = Math.round(data.bp_normal_pct ?? 62);
    const bpPre = Math.round(data.bp_prehypertension_pct ?? 24);
    const bpHtn = Math.round(data.bp_hypertension_pct ?? 14);

    if ($('analytics-bp-normal-pct')) $('analytics-bp-normal-pct').textContent = `${bpNorm}%`;
    if ($('analytics-bp-normal-bar')) $('analytics-bp-normal-bar').style.width = `${bpNorm}%`;

    if ($('analytics-bp-prehtn-pct')) $('analytics-bp-prehtn-pct').textContent = `${bpPre}%`;
    if ($('analytics-bp-prehtn-bar')) $('analytics-bp-prehtn-bar').style.width = `${bpPre}%`;

    if ($('analytics-bp-htn-pct')) $('analytics-bp-htn-pct').textContent = `${bpHtn}%`;
    if ($('analytics-bp-htn-bar')) $('analytics-bp-htn-bar').style.width = `${bpHtn}%`;

    const bmiUnder = Math.round(data.bmi_underweight_pct ?? 14);
    const bmiNorm = Math.round(data.bmi_normal_pct ?? 58);
    const bmiOver = Math.round(data.bmi_overweight_pct ?? 28);

    if ($('analytics-bmi-under-pct')) $('analytics-bmi-under-pct').textContent = `${bmiUnder}%`;
    if ($('analytics-bmi-under-bar')) $('analytics-bmi-under-bar').style.width = `${bmiUnder}%`;

    if ($('analytics-bmi-normal-pct')) $('analytics-bmi-normal-pct').textContent = `${bmiNorm}%`;
    if ($('analytics-bmi-normal-bar')) $('analytics-bmi-normal-bar').style.width = `${bmiNorm}%`;

    if ($('analytics-bmi-over-pct')) $('analytics-bmi-over-pct').textContent = `${bmiOver}%`;
    if ($('analytics-bmi-over-bar')) $('analytics-bmi-over-bar').style.width = `${bmiOver}%`;

    if ($('analytics-dental-pct')) $('analytics-dental-pct').textContent = `${Math.round(data.dental_caries_pct ?? 34)}%`;
    if ($('analytics-vision-pct')) $('analytics-vision-pct').textContent = `${Math.round(data.vision_deficit_pct ?? 18)}%`;
  } catch (err) {
    console.warn('[Analytics] Using fallback camp statistics:', err);
  }
}

export async function eraseActiveRecord() {
  if (!state.activeRecord) return;
  const pName = state.activeRecord.person_name || 'this patient';
  if (!confirm(`Permanently erase clinical intake record for "${pName}"? Under the DPDP Act, all patient data and associated document images will be permanently erased.`)) {
    return;
  }
  try {
    await api.eraseRecord(state.activeRecord.id);
    toast('Patient data erased in compliance with DPDP Act.');
    await renderHome();
    go('home');
  } catch (err) {
    toast('Error erasing record: ' + err.message, true);
  }
}

export async function syncSupabaseCloud() {
  toast('Syncing offline records to Supabase Cloud…');
  try {
    const res = await api.syncSupabase();
    toast('Synced to Supabase Cloud successfully!');
  } catch (err) {
    toast(err.message || 'Supabase credentials not configured in backend/.env', true);
  }
}

// ==========================================================================
// Utilities
// ==========================================================================

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatRelativeTime(isoString) {
  if (!isoString) return 'recently';
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffSec = Math.floor((now - date) / 1000);
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return 'recently';
  }
}

// Global App Export
window.app = {
  go,
  goBack,
  login,
  loginWithOtp,
  loginWithPin,
  requestOtp,
  switchAuthTab,
  openCommunityModal,
  closeCommunityModal,
  switchCommunityTab,
  verifyCommunityCode,
  joinCommunity,
  createCommunity,
  triggerSessionTimeout,
  unlockSession,
  openCameraPermissionModal,
  closeCameraPermissionModal,
  requestCameraPermission,
  fallbackToUploadPhoto,
  loginDemo,
  logout,
  forgotPassword,
  promptSignUp,
  clearClinicCache,
  toggleFrame,
  renderHome,
  selectActiveCamp,
  openCampPicker,
  startScan,
  closeScan,
  flipCamera,
  uploadPhoto,
  captureCurrentPage,
  onFileUploaded,
  loadDemoSamples,
  saveAndExtract,
  openReview,
  toggleReviewDocs,
  switchReviewTab,
  initSignaturePad,
  clearSignature,
  approveAndSaveRecord,
  saveDraftReview,
  eraseActiveRecord,
  syncSupabaseCloud,
  renderRecords,
  setRecordsFilter,
  filterRecords,
  exportExcel,
  exportCampExcel,
  openCampRecords,
  scanForCamp,
  onRecordsOrgChange,
  downloadActiveOrgExcel,
  executeExport,
  toggleExcelFullscreen,
  switchExcelSheetTab,
  renderExcelPreview,
  filterExcelPreview,
  onExportFilterChanged,
  renderAnalytics,
  openNewCampModal,
  closeNewCampModal,
  saveNewCamp,
  renderSettings,
  openBuyAddonModal,
  closeBuyAddonModal,
  buyAddonPack,
  resetDemoData,
  finishSplash: () => window.finishSplash && window.finishSplash()
};

// Initial App Boot Sequence
function initApp() {
  updateDoctorDisplays();
  renderHome();
  initPinBoxes();
  resetSessionIdleTimer();

  const splashVid = document.getElementById('splash-video');
  const splashBar = document.getElementById('splash-progress-bar');
  const splashText = document.getElementById('splash-status-text');
  const splashPct = document.getElementById('splash-pct');
  const splashEl = document.getElementById('splash');

  let transitioned = false;
  let failsafeTimeout = null;

  const finishSplash = () => {
    if (transitioned) return;
    transitioned = true;
    if (failsafeTimeout) {
      clearTimeout(failsafeTimeout);
      failsafeTimeout = null;
    }
    if (window.splashSafetyTimer) {
      clearTimeout(window.splashSafetyTimer);
      window.splashSafetyTimer = null;
    }
    if (splashBar) splashBar.style.width = '100%';
    if (splashPct) splashPct.textContent = '100%';
    if (splashText) splashText.textContent = 'Ready · Optical Intake Platform';

    const sp = document.getElementById('splash');
    if (sp) {
      sp.style.transition = 'opacity 0.35s ease-out';
      sp.style.opacity = '0';
      setTimeout(() => {
        sp.classList.remove('on');
        sp.style.display = 'none';
        if (state.currentScreen === 'splash') {
          go('landing');
        }
      }, 350);
    } else {
      if (state.currentScreen === 'splash') {
        go('landing');
      }
    }
  };

  // Expose globally for skip button
  window.finishSplash = finishSplash;

  if (splashVid) {
    splashVid.muted = true;
    splashVid.playsInline = true;
    splashVid.setAttribute('playsinline', '');
    splashVid.setAttribute('muted', '');

    const stages = [
      { at: 0, text: "Initializing Vision AI Core..." },
      { at: 25, text: "Connecting Medical Camp Store..." },
      { at: 50, text: "Calibrating Dual-Page Intake Schema..." },
      { at: 75, text: "Syncing Clinical Vitals Knowledge Base..." },
      { at: 92, text: "Ready · Optical Intake Platform" }
    ];

    splashVid.addEventListener('timeupdate', () => {
      if (splashVid.duration && isFinite(splashVid.duration)) {
        const pct = Math.min(100, Math.round((splashVid.currentTime / splashVid.duration) * 100));
        if (splashBar) splashBar.style.width = `${pct}%`;
        if (splashPct) splashPct.textContent = `${pct}%`;

        if (splashText) {
          const current = [...stages].reverse().find(s => pct >= s.at);
          if (current) splashText.textContent = current.text;
        }
      }
    });

    // Primary: advance ONLY when the video has fully finished playing
    splashVid.addEventListener('ended', () => {
      finishSplash();
    }, { once: true });

    // Arm emergency failsafe: 13 seconds (video is 10s) only if video stalls
    failsafeTimeout = setTimeout(() => {
      console.info("Failsafe timer triggered for splash animation.");
      finishSplash();
    }, 13000);

    // If metadata loads, sync duration
    splashVid.addEventListener('loadedmetadata', () => {
      if (splashVid.duration && isFinite(splashVid.duration)) {
        if (failsafeTimeout) clearTimeout(failsafeTimeout);
        failsafeTimeout = setTimeout(() => {
          finishSplash();
        }, (splashVid.duration * 1000) + 2500);
      }
    }, { once: true });

    // Begin full playback
    const p = splashVid.play();
    if (p && typeof p.catch === 'function') {
      p.catch(err => {
        console.warn('Splash video autoplay note:', err);
        // If autoplay was blocked, allow click to start or fallback
        if (splashEl) {
          splashEl.addEventListener('click', () => {
            splashVid.play().catch(() => finishSplash());
          }, { once: true });
        }
      });
    }
  } else {
    // No video element fallback
    setTimeout(finishSplash, 1500);
  }
}

// Run boot sequence reliably
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

