/**
 * Doc Scanner — Mobile-First Hybrid Clinic Application Controller
 * Connects the phone-first touch UI with Flask REST API & Vision AI.
 */

import { api, offlineQueue, registerOfflineSupport } from './api.js';
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
  recordsFilter: 'all',    // 'all' | 'needs_review' | 'reviewed' | 'high_risk'
  searchQuery: '',
  isExpandedView: false,
  doctorName: localStorage.getItem('clinic_doctor_name') || 'Attending Physician',
  lastQuality: null,
  qualityBypassed: false,
  qualityWarningsBypassed: [],
  offlineQueueLength: 0,   // Phase 4: encrypted offline scan count
  missingApprovedOverride: false,
  torchSupported: false
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

export function initTheme() {
  document.documentElement.setAttribute('data-theme', 'light');
  try {
    localStorage.removeItem('i2i_theme');
  } catch (e) {}
}

export function toggleTheme() {
  // Application is exclusively clinical light mode
  document.documentElement.setAttribute('data-theme', 'light');
}

export function toast(message, isError = false) {
  const t = $('toast');
  if (!t) return;
  t.textContent = message;
  t.style.background = isError ? 'var(--err)' : 'var(--toast-bg)';
  t.style.color = isError ? '#FFFFFF' : 'var(--toast-text)';
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
      onStateChanged: stateObj => {
        const torchBtn = $('btn-toggle-torch');
        if (torchBtn) {
          if (stateObj.torchSupported !== undefined) {
            torchBtn.style.display = stateObj.torchSupported ? 'inline-flex' : 'none';
          }
          if (stateObj.torchOn !== undefined) {
            const torchTxt = $('torch-btn-text');
            if (torchTxt) torchTxt.textContent = stateObj.torchOn ? 'Torch Off' : 'Torch';
            torchBtn.style.background = stateObj.torchOn ? '#FEF3C7' : '';
            torchBtn.style.color = stateObj.torchOn ? '#D97706' : '';
          }
        }
      },
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

export async function toggleTorch() {
  const sc = getScannerInstance();
  const isOn = await sc.toggleTorch();
  const btn = $('btn-toggle-torch');
  if (btn) {
    btn.innerHTML = isOn
      ? '<svg viewBox="0 0 24 24" style="width:18px;height:18px;fill:#F59E0B;"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg> Torch On'
      : '<svg viewBox="0 0 24 24" style="width:18px;height:18px;"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg> Torch';
    btn.style.color = isOn ? '#D97706' : 'var(--mute)';
  }
  toast(isOn ? 'Torch turned ON 🔦' : 'Torch turned OFF');
}

export function showQualityAlertModal(quality, onProceed, onRetake) {
  state.lastQuality = quality;
  const modal = $('modal-quality-alert');
  const msgEl = $('quality-alert-message');
  const lumEl = $('quality-metric-lum');
  const sharpEl = $('quality-metric-sharp');
  const torchBtn = $('quality-torch-btn');

  if (msgEl) {
    const issues = quality.issues && quality.issues.length ? quality.issues.join(' and ') : 'Low clarity detected';
    msgEl.textContent = `Warning: ${issues}. Text or vitals on the paper document may be unreadable for Vision AI.`;
  }
  if (lumEl) lumEl.textContent = quality.avgLum !== undefined ? `${quality.avgLum} / 255` : 'N/A';
  if (sharpEl) sharpEl.textContent = quality.blurScore !== undefined ? `${quality.blurScore}` : 'N/A';

  const sc = getScannerInstance();
  if (torchBtn) {
    torchBtn.style.display = (sc && sc.isCameraActive) ? 'inline-block' : 'none';
  }

  window._proceedQualityCallback = onProceed;
  window._retakeQualityCallback = onRetake;

  if (modal) modal.style.display = 'flex';
}

export function retryCaptureFromAlert() {
  const modal = $('modal-quality-alert');
  if (modal) modal.style.display = 'none';
  if (window._retakeQualityCallback) {
    const cb = window._retakeQualityCallback;
    window._retakeQualityCallback = null;
    cb();
  }
}

export async function turnOnTorchAndRetake() {
  const sc = getScannerInstance();
  await sc.toggleTorch(true);
  retryCaptureFromAlert();
  toast('Flashlight turned ON 🔦 Position form and capture');
}

export function bypassQualityAlert() {
  const modal = $('modal-quality-alert');
  if (modal) modal.style.display = 'none';
  state.qualityBypassed = true;
  if (state.lastQuality && state.lastQuality.issues && state.lastQuality.issues.length > 0) {
    const pageNum = state.currentScanPage + 1;
    state.qualityWarningsBypassed.push(`Page ${pageNum}: ${state.lastQuality.issues.join(', ')}`);
  }
  if (window._proceedQualityCallback) {
    const cb = window._proceedQualityCallback;
    window._proceedQualityCallback = null;
    cb();
  }
}

export async function startScan() {
  state.currentScanPage = 0;
  state.pageBlobs = [null, null];
  state.pageUrls = [null, null];
  state.qualityBypassed = false;
  state.qualityWarningsBypassed = [];

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

  const applyCapture = () => {
    state.pageBlobs[state.currentScanPage] = captured.blob;
    state.pageUrls[state.currentScanPage] = captured.url;

    if (state.currentScanPage === 0) {
      // Move to Page 2
      state.currentScanPage = 1;
      sc.currentPage = 1;
      state.qualityBypassed = false;
      updateScanStepsUI();
      toast('Page 1 captured! Now scan Page 2 (Back side)');
    } else {
      // Both pages captured -> Proceed to Pair Check
      sc.stopCamera();
      const video = $('camera-video');
      if (video) video.style.display = 'none';
      proceedToPairsScreen();
    }
  };

  if (captured && captured.quality && !captured.quality.ok && !state.qualityBypassed) {
    showQualityAlertModal(
      captured.quality,
      () => applyCapture(),
      () => {
        sc.removePage(state.currentScanPage);
        toast('Retake ready. Position camera steadily.');
      }
    );
    return;
  }

  applyCapture();
}

export async function onFileUploaded(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;

  const sc = getScannerInstance();
  try {
    const res = await sc.handleFileUpload(file);
    const applyUpload = () => {
      state.pageBlobs[state.currentScanPage] = res.blob;
      state.pageUrls[state.currentScanPage] = res.url;

      if (state.currentScanPage === 0) {
        state.currentScanPage = 1;
        sc.currentPage = 1;
        state.qualityBypassed = false;
        updateScanStepsUI();
        toast('Page 1 photo uploaded! Select Page 2 photo.');
      } else {
        sc.stopCamera();
        proceedToPairsScreen();
      }
    };

    if (res && res.quality && !res.quality.ok && !state.qualityBypassed) {
      showQualityAlertModal(
        res.quality,
        () => applyUpload(),
        () => {
          sc.removePage(state.currentScanPage);
          toast('Please choose a clearer or better-lit photo.');
        }
      );
      return;
    }

    applyUpload();
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

export function swapScanPages() {
  const tmpBlob = state.pageBlobs[0];
  state.pageBlobs[0] = state.pageBlobs[1];
  state.pageBlobs[1] = tmpBlob;

  const tmpUrl = state.pageUrls[0];
  state.pageUrls[0] = state.pageUrls[1];
  state.pageUrls[1] = tmpUrl;

  const sc = getScannerInstance();
  if (sc && sc.pages) {
    const tmpPage = sc.pages[0];
    sc.pages[0] = sc.pages[1];
    sc.pages[1] = tmpPage;
  }

  const prev1 = $('pairs-p1-preview');
  const prev2 = $('pairs-p2-preview');
  if (prev1 && state.pageUrls[0]) prev1.style.backgroundImage = `url('${state.pageUrls[0]}')`;
  if (prev2 && state.pageUrls[1]) prev2.style.backgroundImage = `url('${state.pageUrls[1]}')`;

  const swapAlert = $('pairs-swap-alert');
  if (swapAlert) swapAlert.style.display = 'none';

  toast('Pages swapped! Slot 1 and Slot 2 exchanged. 🔀');
}

function proceedToPairsScreen() {
  const p1 = state.pageUrls[0];
  const p2 = state.pageUrls[1];

  const prev1 = $('pairs-p1-preview');
  const prev2 = $('pairs-p2-preview');
  if (prev1 && p1) prev1.style.backgroundImage = `url('${p1}')`;
  if (prev2 && p2) prev2.style.backgroundImage = `url('${p2}')`;

  // Page 1 vs Page 2 Slot Swap Heuristic
  const sc = getScannerInstance();
  const n0 = ((sc && sc.pages && sc.pages[0] && sc.pages[0].name) || '').toLowerCase();
  const n1 = ((sc && sc.pages && sc.pages[1] && sc.pages[1].name) || '').toLowerCase();
  const p2Keywords = ['page2', 'page_2', 'p2', 'health', 'assessment', 'exam', 'system'];
  const p1Keywords = ['page1', 'page_1', 'p1', 'student', 'admission', 'personal'];
  const p0IsP2 = p2Keywords.some(k => n0.includes(k));
  const p1IsP1 = p1Keywords.some(k => n1.includes(k));
  const swapAlert = $('pairs-swap-alert');
  if (swapAlert) {
    swapAlert.style.display = (p0IsP2 && p1IsP1) ? 'block' : 'none';
  }

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
    const extractRes = await api.extractPages(p1, p2, campId);

    // ── Offline Queue sentinel: scan was encrypted + queued ──────────────
    if (extractRes && extractRes._offline_queued) {
      toast(extractRes.message || '📵 Scan saved to offline queue.');
      state.offlineQueueLength = extractRes.queue_length || (state.offlineQueueLength + 1);
      _updateOfflineBadge(state.offlineQueueLength);
      // Reset scanner for next patient
      if (scanner) scanner.resetAllPages();
      state.pageBlobs = [null, null];
      state.pageUrls = [null, null];
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = `✅ Saved Offline — Scan Next Patient`;
      }
      return;  // Skip normal record save flow
    }
    // ── End offline sentinel ──────────────────────────────────────────────

    const ext = (extractRes && extractRes.data) ? extractRes.data : (extractRes || {});

    // Prefer permanent backend image URLs over ephemeral blob URLs
    const page1Url = (extractRes && (extractRes.page1_url || (extractRes.data && extractRes.data.page1_url))) || state.pageUrls[0];
    const page2Url = (extractRes && (extractRes.page2_url || (extractRes.data && extractRes.data.page2_url))) || state.pageUrls[1];

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
      page1_preview: page1Url,
      page2_preview: page2Url,
      quality_warning_bypassed: (state.qualityWarningsBypassed && state.qualityWarningsBypassed.length > 0) ? state.qualityWarningsBypassed : []
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

    // Optical Quality Bypassed Telemetry
    const qualAlert = $('review-quality-bypassed-alert');
    const qualMsg = $('review-quality-bypassed-msg');
    const bypassed = record.quality_warning_bypassed || [];
    if (qualAlert) {
      if (Array.isArray(bypassed) && bypassed.length > 0) {
        qualAlert.style.display = 'flex';
        if (qualMsg) qualMsg.textContent = `Pre-flight optical warning was bypassed during intake (${bypassed.join(' · ')}). Verify all transcribed vitals against source document.`;
      } else {
        qualAlert.style.display = 'none';
      }
    }

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

    // Consent Gating initialization
    const consentGiven = (record.consent_given !== 0 && record.consent_given !== false) &&
      (getVal(p_info, 'consent_screening', '') !== 'No');
    if ($('rev-consent-screening')) $('rev-consent-screening').checked = consentGiven;
    onConsentChanged();

    // Focused Bounding Box Crop rendering
    renderFocusedCropSnippets(record);

    switchReviewTab('personal');
    go('review');

  } catch (err) {
    toast('Error loading record: ' + err.message, true);
  }
}

export function onConsentChanged() {
  const isConsentGiven = $('rev-consent-screening')?.checked ?? true;
  const alertWithheld = $('alert-consent-withheld');
  const approveBtn = $('approve-btn');
  if (alertWithheld) {
    alertWithheld.style.display = isConsentGiven ? 'none' : 'block';
  }
  if (approveBtn) {
    if (!isConsentGiven) {
      approveBtn.disabled = true;
      approveBtn.title = "Consent Withheld: Patient or parental consent is required before approval.";
    } else {
      approveBtn.disabled = false;
      approveBtn.title = "";
    }
  }
}

export function renderFocusedCropSnippets(record) {
  if (!record) return;
  const uncertain = record.uncertain_fields || [];
  const p1Src = record.page1_url || record.page1_preview || $('review-p1-img')?.src;
  const p2Src = record.page2_url || record.page2_preview || $('review-p2-img')?.src;

  const fieldCropDefs = {
    'person_name': { canvasId: 'crop-canvas-name', boxId: 'crop-preview-name', src: p1Src, region: { x: 0.08, y: 0.04, w: 0.55, h: 0.05 } },
    'blood_pressure': { canvasId: 'crop-canvas-bp', boxId: 'crop-preview-bp', src: p2Src, region: { x: 0.08, y: 0.16, w: 0.40, h: 0.06 } },
    'known_allergies': { canvasId: 'crop-canvas-allergies', boxId: 'crop-preview-allergies', src: p1Src, region: { x: 0.08, y: 0.38, w: 0.50, h: 0.06 } }
  };

  Object.entries(fieldCropDefs).forEach(([fieldKey, def]) => {
    const box = $(def.boxId);
    const canvas = $(def.canvasId);
    if (!box || !canvas) return;

    if (uncertain.includes(fieldKey) && def.src) {
      box.style.display = 'block';
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const sx = def.region.x * img.naturalWidth;
        const sy = def.region.y * img.naturalHeight;
        const sw = def.region.w * img.naturalWidth;
        const sh = def.region.h * img.naturalHeight;
        canvas.width = Math.min(sw, 480);
        canvas.height = Math.round(canvas.width * (sh / sw));
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
      };
      img.src = def.src;
    } else {
      box.style.display = 'none';
    }
  });
}

export function onVitalsChanged() {
  updateReviewClinicalAlerts();
}

export function updateReviewClinicalAlerts(record) {
  const alertsContainer = $('review-clinical-alerts');
  if (!alertsContainer) return;

  const heightVal = parseFloat($('rev-height')?.value || (record && record.height) || 0);
  const weightVal = parseFloat($('rev-weight')?.value || (record && record.weight) || 0);
  const ageVal = parseInt($('rev-age')?.value || (record && record.age) || 0, 10);
  const bpVal = ($('rev-bp')?.value || (record && record.blood_pressure) || '').trim();

  let hasAnyAlert = false;

  // 1. Live BMI Calculation & High BMI Staging
  const bmiPill = $('rev-calculated-bmi');
  const alertBmi = $('alert-high-bmi');
  const bmiBadge = $('bmi-alert-badge');
  const bmiTitle = $('bmi-alert-title');
  const bmiDesc = $('bmi-alert-desc');
  const pediatricNote = $('bmi-pediatric-note');

  let calculatedBmi = null;
  if (heightVal > 40 && weightVal > 2) {
    const heightMeters = heightVal / 100;
    calculatedBmi = parseFloat((weightVal / (heightMeters * heightMeters)).toFixed(1));
  }

  if (calculatedBmi !== null && !isNaN(calculatedBmi)) {
    let cat = 'Normal';
    let pillColor = '#059669';
    let pillBg = '#ECFDF5';

    if (calculatedBmi < 18.5) {
      cat = 'Underweight';
      pillColor = '#D97706';
      pillBg = '#FFFBEB';
    } else if (calculatedBmi >= 30.0) {
      cat = 'Obese (WHO Cl. II/III)';
      pillColor = '#DC2626';
      pillBg = '#FEF2F2';
    } else if (calculatedBmi >= 25.0) {
      cat = 'Obese (Asian-Indian) / Overweight';
      pillColor = '#DC2626';
      pillBg = '#FEF2F2';
    } else if (calculatedBmi >= 23.0) {
      cat = 'Overweight (Asian-Indian)';
      pillColor = '#EA580C';
      pillBg = '#FFF7ED';
    }

    if (bmiPill) {
      bmiPill.textContent = `${calculatedBmi} kg/m² (${cat})`;
      bmiPill.style.color = pillColor;
      bmiPill.style.background = pillBg;
    }

    // High BMI Clinical Triage Alert (>= 25.0 kg/m² for Asian-Indian / WHO)
    if (calculatedBmi >= 25.0) {
      hasAnyAlert = true;
      if (alertBmi) alertBmi.style.display = 'block';
      if (bmiBadge) bmiBadge.textContent = `${calculatedBmi} kg/m²`;
      if (calculatedBmi >= 30.0) {
        if (bmiTitle) bmiTitle.textContent = 'Severe Obesity Alert (BMI ≥ 30)';
        if (bmiDesc) bmiDesc.textContent = 'Patient BMI is in the Severe Obese category. Dietary counseling, endocrine workup and metabolic screening indicated.';
      } else {
        if (bmiTitle) bmiTitle.textContent = 'High BMI / Overweight Alert (BMI ≥ 25)';
        if (bmiDesc) bmiDesc.textContent = 'Patient exceeds healthy Asian-Indian cutoffs (≥25 kg/m²). Nutritional guidance and lifestyle intervention advised.';
      }

      if (pediatricNote) {
        pediatricNote.style.display = (ageVal > 0 && ageVal <= 18) ? 'block' : 'none';
      }
    } else {
      if (alertBmi) alertBmi.style.display = 'none';
    }
  } else {
    if (bmiPill) {
      bmiPill.textContent = '-- kg/m²';
      bmiPill.style.color = '#0284C7';
      bmiPill.style.background = '#E0F2FE';
    }
    if (alertBmi) alertBmi.style.display = 'none';
  }

  // 2. Hypertension / Blood Pressure Alert
  const alertBp = $('alert-hypertension');
  const bpTitle = $('bp-alert-title');
  const bpBadge = $('bp-alert-badge');
  const bpDesc = $('bp-alert-desc');

  if (bpVal) {
    const parts = bpVal.replace(/[^0-9/]/g, '').split('/');
    const sys = parseInt(parts[0], 10);
    const dia = parts[1] ? parseInt(parts[1], 10) : null;

    if (!isNaN(sys)) {
      if (sys >= 180 || (dia && dia >= 120)) {
        hasAnyAlert = true;
        if (alertBp) {
          alertBp.style.display = 'block';
          alertBp.classList.add('hypertensive-crisis-pulse');
        }
        if (bpTitle) bpTitle.textContent = '🚨 Hypertensive Crisis / Critical BP';
        if (bpBadge) {
          bpBadge.textContent = `${sys}/${dia || '--'} mmHg`;
          bpBadge.style.background = '#DC2626';
        }
        if (bpDesc) bpDesc.textContent = 'Critically elevated BP (Systolic ≥ 180 or Diastolic ≥ 120). Recheck immediately and provide urgent medical referral.';
      } else if (sys >= 140 || (dia && dia >= 90)) {
        hasAnyAlert = true;
        if (alertBp) {
          alertBp.style.display = 'block';
          alertBp.classList.remove('hypertensive-crisis-pulse');
        }
        if (bpTitle) bpTitle.textContent = 'Stage 2 Hypertension Alert';
        if (bpBadge) {
          bpBadge.textContent = `${sys}/${dia || '--'} mmHg`;
          bpBadge.style.background = '#D97706';
        }
        if (bpDesc) bpDesc.textContent = 'Blood pressure ≥ 140/90 mmHg. Clinical review and specialist follow-up consultation recommended.';
      } else if (sys >= 130 || (dia && dia >= 80)) {
        hasAnyAlert = true;
        if (alertBp) {
          alertBp.style.display = 'block';
          alertBp.classList.remove('hypertensive-crisis-pulse');
        }
        if (bpTitle) bpTitle.textContent = 'Stage 1 Hypertension / Elevated BP';
        if (bpBadge) {
          bpBadge.textContent = `${sys}/${dia || '--'} mmHg`;
          bpBadge.style.background = '#F59E0B';
        }
        if (bpDesc) bpDesc.textContent = 'Blood pressure in pre-hypertensive / Stage 1 range. Routine follow-up suggested.';
      } else {
        if (alertBp) {
          alertBp.style.display = 'none';
          alertBp.classList.remove('hypertensive-crisis-pulse');
        }
      }
    } else {
      if (alertBp) {
        alertBp.style.display = 'none';
        alertBp.classList.remove('hypertensive-crisis-pulse');
      }
    }
  } else {
    if (alertBp) {
      alertBp.style.display = 'none';
      alertBp.classList.remove('hypertensive-crisis-pulse');
    }
  }

  // 3. Missing Essential Fields Warning Card & Input Highlights
  const alertMissing = $('alert-missing-fields');
  const missingDesc = $('missing-fields-desc');
  const essentialFields = [
    { label: 'Patient Name', id: 'rev-name' },
    { label: 'Age', id: 'rev-age' },
    { label: 'Gender', id: 'rev-gender' },
    { label: 'Height', id: 'rev-height' },
    { label: 'Weight', id: 'rev-weight' },
    { label: 'Blood Pressure', id: 'rev-bp' },
    { label: 'Known Allergies', id: 'rev-allergies' }
  ];

  // Dynamic visual feedback: highlight blank essential inputs with pulsing amber border
  essentialFields.forEach(f => {
    const el = $(f.id);
    if (el) {
      if (!el.value.trim()) {
        el.classList.add('input-missing-essential');
      } else {
        el.classList.remove('input-missing-essential');
      }
    }
  });

  const blankEssentials = essentialFields
    .filter(f => !($(f.id)?.value.trim()))
    .map(f => f.label);

  if (blankEssentials.length > 0) {
    hasAnyAlert = true;
    if (alertMissing) alertMissing.style.display = 'block';
    if (missingDesc) {
      missingDesc.textContent = `Unrecorded essential fields: ${blankEssentials.join(', ')}. Please verify with the paper intake form.`;
    }
  } else {
    if (alertMissing) alertMissing.style.display = 'none';
  }

  // 4. Clinical Red Flags Alert (Pallor + Pulse, Cyanosis, Clubbing, Allergies)
  const alertRedFlags = $('alert-red-flags');
  const redFlags = [];

  const pulseVal = parseInt($('rev-pulse')?.value || '', 10);
  const pallor = ($('rev-pallor')?.value || '').toLowerCase();
  if (pallor && !['absent', 'nad', 'no', 'nil', 'none', 'na'].some(w => pallor.includes(w))) {
    if (!isNaN(pulseVal) && pulseVal > 100) {
      redFlags.push(`Severe Anemia Risk: Marked Pallor + Tachycardia (${pulseVal} bpm)`);
    } else {
      redFlags.push(`Pallor: ${$('rev-pallor').value.trim()}`);
    }
  }

  const cyanosis = ($('rev-cyanosis')?.value || '').toLowerCase();
  if (cyanosis && !['absent', 'nad', 'no', 'nil', 'none', 'na'].some(w => cyanosis.includes(w))) {
    redFlags.push(`Cyanosis: ${$('rev-cyanosis').value.trim()}`);
  }

  const clubbing = ($('rev-clubbing')?.value || '').toLowerCase();
  if (clubbing && !['absent', 'nad', 'no', 'nil', 'none', 'na'].some(w => clubbing.includes(w))) {
    redFlags.push(`Clubbing: ${$('rev-clubbing').value.trim()}`);
  }

  const edema = ($('rev-edema')?.value || '').toLowerCase();
  if (edema && !['absent', 'nad', 'no', 'nil', 'none', 'na'].some(w => edema.includes(w))) {
    redFlags.push(`Edema: ${$('rev-edema').value.trim()}`);
  }

  const allergies = ($('rev-allergies')?.value || '').toLowerCase();
  if (allergies && !['no known', 'nil', 'none', 'nad', 'no', 'na', 'no allergies'].some(w => allergies.includes(w))) {
    const criticalDrugs = ['penicillin', 'sulfa', 'nsaid', 'aspirin', 'paracetamol', 'amoxicillin', 'cipro', 'ibuprofen'];
    const isCritical = criticalDrugs.some(d => allergies.includes(d));
    if (isCritical) {
      redFlags.push(`⚠️ Critical Drug Allergy: ${$('rev-allergies').value.trim()}`);
    } else {
      redFlags.push(`Allergies: ${$('rev-allergies').value.trim()}`);
    }
  }

  if (redFlags.length > 0) {
    hasAnyAlert = true;
    if (alertRedFlags) {
      alertRedFlags.style.display = 'block';
      alertRedFlags.innerHTML = `🚩 <b>Clinical Red Flags:</b> ${redFlags.map(f => `<span style="display:inline-block;background:#FFE4E6;padding:2px 8px;border-radius:6px;margin:2px 4px 2px 0;">${escapeHtml(f)}</span>`).join('')}`;
    }
  } else {
    if (alertRedFlags) alertRedFlags.style.display = 'none';
  }

  // 4. Paediatric Screening Alerts (IAP LMS Centiles, BP Centile, ATR Caliper)
  const alertPaediatric = $('alert-paediatric-screening');
  const paedAlertsDesc = $('paediatric-alerts-desc');
  const paedBadge = $('paediatric-alerts-badge');

  const paedAlertsList = (record && (record.paediatric_alerts || (record.structured_record && record.structured_record.paediatric_alerts))) || [];
  const isPaed = (record && record.is_paediatric) || (ageVal > 0 && ageVal < 18);

  if (alertPaediatric) {
    if (isPaed && paedAlertsList.length > 0) {
      hasAnyAlert = true;
      alertPaediatric.style.display = 'block';
      if (paedBadge) paedBadge.textContent = `${paedAlertsList.length} alert${paedAlertsList.length > 1 ? 's' : ''}`;
      if (paedAlertsDesc) {
        paedAlertsDesc.innerHTML = paedAlertsList.map(a => `<div style="margin-bottom:2px;">• ${escapeHtml(a)}</div>`).join('');
      }
    } else {
      alertPaediatric.style.display = 'none';
    }
  }

  alertsContainer.style.display = hasAnyAlert ? 'flex' : 'none';
}

export function markPhysicalExamNormal() {
  const normGeneral = 'Absent (NAD)';
  if ($('rev-pallor')) $('rev-pallor').value = normGeneral;
  if ($('rev-icterus')) $('rev-icterus').value = normGeneral;
  if ($('rev-cyanosis')) $('rev-cyanosis').value = normGeneral;
  if ($('rev-edema')) $('rev-edema').value = normGeneral;
  if ($('rev-clubbing')) $('rev-clubbing').value = normGeneral;
  if ($('rev-lymph')) $('rev-lymph').value = normGeneral;

  if ($('rev-sys-rs')) $('rev-sys-rs').value = 'Normal vesicular breath sounds, no added sounds (NAD)';
  if ($('rev-sys-cvs')) $('rev-sys-cvs').value = 'S1 S2 heard, normal rhythm, no murmurs (NAD)';
  if ($('rev-sys-abdomen')) $('rev-sys-abdomen').value = 'Soft, non-tender, no organomegaly (NAD)';
  if ($('rev-sys-cns')) $('rev-sys-cns').value = 'Conscious, oriented, normal reflexes (NAD)';
  if ($('rev-sys-ent')) $('rev-sys-ent').value = 'Normal (NAD)';
  if ($('rev-sys-skin')) $('rev-sys-skin').value = 'Normal, no active dermatological lesions (NAD)';

  toast('Systemic exam & general signs populated as Normal (NAD) ✨');
  updateReviewClinicalAlerts();
}

export function buildFullStructuredReviewPayload() {
  const base = (state.activeRecord && state.activeRecord.structured_record) 
    ? JSON.parse(JSON.stringify(state.activeRecord.structured_record)) 
    : {};

  base.personal_information = Object.assign({}, base.personal_information, {
    full_name: $('rev-name')?.value.trim() || '',
    age: $('rev-age')?.value.trim() || '',
    gender: $('rev-gender')?.value.trim() || '',
    blood_group: $('rev-blood-group')?.value.trim() || '',
    dob: $('rev-dob')?.value.trim() || '',
    doc_id: $('rev-doc-id')?.value.trim() || '',
    admission_id: $('rev-adm-id')?.value.trim() || '',
    school_college_name: $('rev-school')?.value.trim() || '',
    mobile_number: $('rev-mobile')?.value.trim() || '',
    address: $('rev-address')?.value.trim() || ''
  });

  base.health_assessment = Object.assign({}, base.health_assessment, {
    height_cm: $('rev-height')?.value.trim() || '',
    weight_kg: $('rev-weight')?.value.trim() || '',
    blood_pressure_mmhg: $('rev-bp')?.value.trim() || '',
    pulse_bpm: $('rev-pulse')?.value.trim() || '',
    random_blood_sugar_mgdl: $('rev-rbs')?.value.trim() || '',
    waist_circumference_cm: $('rev-waist')?.value.trim() || '',
    pallor: $('rev-pallor')?.value.trim() || '',
    icterus: $('rev-icterus')?.value.trim() || '',
    cyanosis: $('rev-cyanosis')?.value.trim() || '',
    edema: $('rev-edema')?.value.trim() || '',
    clubbing: $('rev-clubbing')?.value.trim() || '',
    lymphadenopathy: $('rev-lymph')?.value.trim() || '',
    ecg_notes: $('rev-ecg')?.value.trim() || '',
    echo_notes: $('rev-echo')?.value.trim() || ''
  });

  base.medical_history = Object.assign({}, base.medical_history, {
    known_allergies: $('rev-allergies')?.value.trim() || '',
    previous_surgery_hospitalization: $('rev-surgery')?.value.trim() || '',
    current_previous_medications: $('rev-medications')?.value.trim() || '',
    abdominal_pain_indigestion_constipation: $('rev-abdominal')?.value.trim() || '',
    skin_hair_ear_nose_problems: $('rev-ent')?.value.trim() || '',
    physical_activity: $('rev-activity')?.value.trim() || '',
    social_history: $('rev-social')?.value.trim() || ''
  });

  base.family_history = Object.assign({}, base.family_history, {
    diabetes: $('rev-fam-diabetes')?.value.trim() || '',
    hypertension: $('rev-fam-hypertension')?.value.trim() || '',
    heart_disease: $('rev-fam-heart')?.value.trim() || '',
    thyroid: $('rev-fam-thyroid')?.value.trim() || '',
    asthma: $('rev-fam-asthma')?.value.trim() || '',
    other_conditions: $('rev-fam-other')?.value.trim() || ''
  });

  base.system_examination = Object.assign({}, base.system_examination, {
    respiratory_system: $('rev-sys-rs')?.value.trim() || '',
    cardiovascular_system: $('rev-sys-cvs')?.value.trim() || '',
    abdomen: $('rev-sys-abdomen')?.value.trim() || '',
    central_nervous_system: $('rev-sys-cns')?.value.trim() || '',
    ear: $('rev-sys-ent')?.value.trim() || '',
    skin: $('rev-sys-skin')?.value.trim() || ''
  });

  base.vision = Object.assign({}, base.vision, {
    distant_vision_right: $('rev-va-right')?.value.trim() || '',
    distant_vision_left: $('rev-va-left')?.value.trim() || '',
    vision_category: $('rev-vision-category')?.value.trim() || ''
  });

  base.dental = Object.assign({}, base.dental, {
    normal: $('rev-dental-status')?.value.trim() || '',
    advice: $('rev-dental-advice')?.value.trim() || ''
  });

  base.referral = Object.assign({}, base.referral, {
    specialist_referred_to: $('rev-referral')?.value.trim() || '',
    doctor_signature_present: $('rev-doc-sign')?.value.trim() || ''
  });

  return base;
}

export function showMissingConfirmationModal(blankList, onProceed) {
  const modal = $('modal-missing-confirm');
  const chipsContainer = $('missing-fields-chips');
  if (chipsContainer) {
    chipsContainer.innerHTML = blankList.map(f => 
      `<span style="background:#FEE2E2;color:#991B1B;padding:4px 10px;border-radius:12px;font-size:12px;font-weight:700;">${escapeHtml(f)}</span>`
    ).join('');
  }
  window._proceedMissingCallback = onProceed;
  if (modal) modal.style.display = 'flex';
}

export function closeMissingConfirmModal() {
  const modal = $('modal-missing-confirm');
  if (modal) modal.style.display = 'none';
  window._proceedMissingCallback = null;
}

export function confirmApproveDespiteMissing() {
  state.missingApprovedOverride = true;
  const cb = window._proceedMissingCallback;
  closeMissingConfirmModal();
  if (cb) cb();
}

export function printReferralSlip(recordId) {
  const id = recordId || (state.activeRecord && state.activeRecord.id);
  if (!id) {
    toast('No active record selected for referral slip.', true);
    return;
  }
  const slipUrl = `/api/records/${encodeURIComponent(id)}/referral-slip`;
  window.open(slipUrl, '_blank');
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

  const essentialFields = [
    { label: 'Patient Name', id: 'rev-name' },
    { label: 'Age', id: 'rev-age' },
    { label: 'Gender', id: 'rev-gender' },
    { label: 'Height', id: 'rev-height' },
    { label: 'Weight', id: 'rev-weight' },
    { label: 'Blood Pressure', id: 'rev-bp' },
    { label: 'Known Allergies', id: 'rev-allergies' }
  ];

  const blankEssentials = essentialFields
    .filter(f => !($(f.id)?.value.trim()))
    .map(f => f.label);

  if (blankEssentials.length > 0 && !state.missingApprovedOverride) {
    showMissingConfirmationModal(blankEssentials, () => {
      approveAndSaveRecord();
    });
    return;
  }

  // Consent Gating check
  const consentChecked = $('rev-consent-screening')?.checked ?? true;
  if (!consentChecked) {
    toast('Approval refused: Patient or parental consent is required before clinical approval.', true);
    return;
  }

  // Reset override flag for next record
  state.missingApprovedOverride = false;

  const btn = $('approve-btn');
  if (btn) btn.disabled = true;

  try {
    const fullStructured = buildFullStructuredReviewPayload();

    const updates = {
      person_name: $('rev-name')?.value.trim() || state.activeRecord.person_name,
      age: $('rev-age')?.value.trim() || state.activeRecord.age,
      gender: $('rev-gender')?.value.trim() || state.activeRecord.gender,
      height: $('rev-height')?.value.trim() || state.activeRecord.height,
      weight: $('rev-weight')?.value.trim() || state.activeRecord.weight,
      blood_pressure: $('rev-bp')?.value.trim() || state.activeRecord.blood_pressure,
      examination_details: $('rev-sys-rs')?.value.trim() || state.activeRecord.examination_details,
      referral_details: $('rev-referral')?.value.trim() || state.activeRecord.referral_details,
      reviewer_notes: $('rev-doctor-notes')?.value.trim() || 'Verified and approved.',
      structured_record: fullStructured,
      consent_given: consentChecked ? 1 : 0
    };

    // 1. Review edits with full structured 7-tab updates
    await api.reviewRecord(state.activeRecord.id, updates, state.doctorName);
    // 2. Explicit approval action (checks no unresolved flags remain, bills quota, marks approved)
    await api.approveRecord(state.activeRecord.id, state.doctorName);

    toast('Record approved and quota billed! 📋✅');
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
    const fullStructured = buildFullStructuredReviewPayload();

    const updates = {
      person_name: $('rev-name')?.value.trim() || state.activeRecord.person_name,
      age: $('rev-age')?.value.trim() || state.activeRecord.age,
      gender: $('rev-gender')?.value.trim() || state.activeRecord.gender,
      height: $('rev-height')?.value.trim() || state.activeRecord.height,
      weight: $('rev-weight')?.value.trim() || state.activeRecord.weight,
      blood_pressure: $('rev-bp')?.value.trim() || state.activeRecord.blood_pressure,
      examination_details: $('rev-sys-rs')?.value.trim() || state.activeRecord.examination_details,
      referral_details: $('rev-referral')?.value.trim() || state.activeRecord.referral_details,
      reviewer_notes: $('rev-doctor-notes')?.value.trim() || 'Draft review saved.',
      structured_record: fullStructured,
      status: 'needs_review'
    };

    await api.reviewRecord(state.activeRecord.id, updates, state.doctorName);
    toast('Draft review saved (record remains in To check).');
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
  ['rf-all', 'rf-warn', 'rf-ok', 'rf-highrisk'].forEach(id => {
    const el = $(id);
    if (el) el.classList.remove('active');
  });
  if (filter === 'all' && $('rf-all')) $('rf-all').classList.add('active');
  if (filter === 'needs_review' && $('rf-warn')) $('rf-warn').classList.add('active');
  if (filter === 'reviewed' && $('rf-ok')) $('rf-ok').classList.add('active');
  if (filter === 'high_risk' && $('rf-highrisk')) $('rf-highrisk').classList.add('active');
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

    // Status / Risk filter
    if (filter === 'needs_review' && r.status !== 'needs_review') return false;
    if (filter === 'reviewed' && r.status !== 'reviewed') return false;
    if (filter === 'high_risk') {
      const h = parseFloat(r.height);
      const w = parseFloat(r.weight);
      const bmi = (h > 40 && w > 2) ? (w / ((h / 100) * (h / 100))) : null;
      const bp = (r.blood_pressure || '').split('/');
      const sys = bp[0] ? parseInt(bp[0], 10) : null;
      const dia = bp[1] ? parseInt(bp[1], 10) : null;
      const hasHighBmi = bmi !== null && bmi >= 25.0;
      const hasHighBp = (sys !== null && sys >= 140) || (dia !== null && dia >= 90);
      const refStr = (r.referral_details || '').trim().toLowerCase();
      const hasReferral = Boolean(refStr.length > 0 && !['none', 'nil', 'na', 'normal', 'no', 'nad'].includes(refStr));
      const hasUncertain = Boolean(r.uncertain_fields && r.uncertain_fields.length > 0);
      if (!hasHighBmi && !hasHighBp && !hasReferral && !hasUncertain) return false;
    }
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
  const formType = $('export-form-type-select')?.value || (camp && camp.target_group) || null;
  const rawName = camp ? camp.name : (formType ? `${formType.toUpperCase()}_Records` : 'All_Organizations');
  const safeName = rawName.replace(/[^a-zA-Z0-9_\-]/g, '_').replace(/_+/g, '_') || 'Health_Records';
  const url = api.getExportUrl(targetCampId, onlyReviewed, 'xlsx', formType);
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

export function exportAdultExcel(campId = null) {
  const targetCampId = campId || ($('export-camp-select')?.value || null);
  const onlyReviewed = $('export-only-reviewed')?.checked || false;
  const url = api.getAdultExportUrl(targetCampId, onlyReviewed);
  const filename = `Adult_Health_Records_${targetCampId || 'all'}.xlsx`;
  triggerDownload(url, filename);
  toast(`Downloaded ${filename} (.xlsx)`);
  go('export');
  const ribTitle = $('excel-ribbon-filename');
  if (ribTitle) ribTitle.textContent = `${filename} (Live In-App Viewer)`;
  const fTypeSel = $('export-form-type-select');
  if (fTypeSel) fTypeSel.value = 'adult';
  renderExcelPreview(currentExcelSheet);
}

export function exportPaediatricExcel(campId = null) {
  const targetCampId = campId || ($('export-camp-select')?.value || null);
  const onlyReviewed = $('export-only-reviewed')?.checked || false;
  const url = api.getPaediatricExportUrl(targetCampId, onlyReviewed);
  const filename = `Paediatric_Health_Records_${targetCampId || 'all'}.xlsx`;
  triggerDownload(url, filename);
  toast(`Downloaded ${filename} (.xlsx)`);
  go('export');
  const ribTitle = $('excel-ribbon-filename');
  if (ribTitle) ribTitle.textContent = `${filename} (Live In-App Viewer)`;
  const fTypeSel = $('export-form-type-select');
  if (fTypeSel) fTypeSel.value = 'paediatric';
  renderExcelPreview(currentExcelSheet);
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
  const tAttr = $('extab-attributes');
  const tCmp = $('extab-camps');
  if (tRec) tRec.classList.toggle('active', sheetKey === 'records');
  if (tAttr) tAttr.classList.toggle('active', sheetKey === 'attributes');
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

export async function renderExcelPreview(sheetName = currentExcelSheet) {
  const container = $('excel-grid-container');
  if (!container) return;

  const selCampId = $('export-camp-select')?.value || null;
  const selCampObj = selCampId ? state.camps.find(c => c.id === selCampId) : null;
  const explicitFormType = $('export-form-type-select')?.value || null;
  const activeFormType = explicitFormType || (selCampObj && selCampObj.target_group) || 'adult';
  const onlyApproved = $('export-only-reviewed')?.checked || false;
  const campsCountEl = $('excel-camps-count');
  if (campsCountEl) campsCountEl.textContent = state.camps.length;

  if (sheetName === 'attributes') {
    // ── Sheet 2: Form Structure & Attributes Dictionary ────────────────────
    container.innerHTML = `
      <div style="padding:24px;text-align:center;color:var(--mute);">
        <div style="font-size:24px;animation:spin 1s linear infinite;display:inline-block;">⏳</div>
        <p style="font-size:13px;margin-top:6px;">Loading ${activeFormType.toUpperCase()} form attributes &amp; clinical data dictionary…</p>
      </div>
    `;

    try {
      const resp = await api.getFormAttributes(activeFormType);
      const attrs = (resp && resp.attributes) ? resp.attributes : [];
      const attrCountEl = $('excel-attributes-count');
      if (attrCountEl) attrCountEl.textContent = attrs.length;

      if (!attrs.length) {
        container.innerHTML = `<div style="padding:30px;text-align:center;color:var(--mute);">No attributes found for ${escapeHtml(activeFormType)} form.</div>`;
        return;
      }

      const headers = ["Attribute Key", "Clinical Label", "Page", "Section", "Data Type", "Recorded By", "Required", "Excel Header", "Validation Rule"];
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

      attrs.forEach((a, idx) => {
        html += `<tr>`;
        html += `<td class="row-num">${idx + 1}</td>`;
        html += `<td><code>${escapeHtml(a.key || '')}</code></td>`;
        html += `<td><b>${escapeHtml(a.label || '')}</b></td>`;
        html += `<td style="text-align:center;">P${a.page_number || 1}</td>`;
        html += `<td>${escapeHtml(a.section_title || '')}</td>`;
        html += `<td><span class="tag" style="font-size:10px;background:#F1F5F9;color:#334155;">${escapeHtml(a.type || '')}</span></td>`;
        html += `<td>${escapeHtml((a.writer || '').toUpperCase())}</td>`;
        html += `<td style="text-align:center;"><span class="tag ${a.required ? 'e' : ''}" style="font-size:10px;">${a.required ? 'Mandatory' : 'Optional'}</span></td>`;
        html += `<td>${escapeHtml(a.export_column || a.key || '')}</td>`;
        html += `<td><small style="color:var(--mute);">${escapeHtml(a.validation || 'None')}</small></td>`;
        html += `</tr>`;
      });

      html += `</tbody></table>`;
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div style="padding:30px;text-align:center;color:var(--err);">Failed to load attributes: ${escapeHtml(e.message || String(e))}</div>`;
    }

  } else if (sheetName === 'records') {
    // ── Sheet 1: Clinical Records ──────────────────────────────────────────
    let list = state.records.slice();
    if (selCampId) {
      list = list.filter(r => r.camp_id === selCampId);
    }
    if (onlyApproved) {
      list = list.filter(r => r.status === 'reviewed' || r.status === 'approved');
    }
    if (activeFormType === 'paediatric') {
      list = list.filter(r => r.target_group === 'paediatric' || (selCampObj && selCampObj.target_group === 'paediatric') || r.is_paediatric);
    } else if (activeFormType === 'adult' && explicitFormType) {
      list = list.filter(r => r.target_group !== 'paediatric' && (!selCampObj || selCampObj.target_group !== 'paediatric'));
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
          <p style="font-size:12.5px;margin-top:4px;">Select "All Health Camps" or clear your search to view all clinical records.</p>
        </div>
      `;
      return;
    }

    const isPaediatric = activeFormType === 'paediatric';
    const headers = isPaediatric ? [
      "Record ID", "School / Camp", "Class & Sec", "Admission ID", "Student Name", "Status",
      "Age", "Gender", "Blood Group", "Parent / Contact", "Height (cm)", "Weight (kg)",
      "BMI", "BMI Centile", "Blood Pressure", "BP Centile", "Pulse", "ATR Caliper (°)",
      "Gait", "Otoscopy Right", "Otoscopy Left", "Vision OD", "Vision OS", "Dental Caries",
      "Referral Specialist", "Screening Alerts"
    ] : [
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
      const vis = sr.vision || sr.vision_assessment || {};
      const den = sr.dental || sr.dental_assessment || {};
      const ref = sr.referral || {};
      const paed = sr.paediatric_growth || {};
      const pscreen = sr.paediatric_screening || {};

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

      let rowVals = [];
      if (isPaediatric) {
        const atrVal = getVal(paed, 'spinal_caliper_atr', r.spinal_caliper_atr || '');
        const alerts = r.paediatric_alerts || pscreen.alerts || [];
        const alertsStr = Array.isArray(alerts) ? alerts.join(', ') : (alerts || 'None');
        rowVals = [
          r.id,
          r.camp_name || 'School Camp',
          r.class_and_section || getVal(p, 'class_and_section', ''),
          r.admission_id || getVal(p, 'admission_id', ''),
          getVal(p, 'full_name', r.person_name || 'Student'),
          r.status === 'reviewed' || r.status === 'approved' ? 'Approved' : 'To check',
          getVal(p, 'age', r.age || ''),
          getVal(p, 'gender', r.gender || ''),
          getVal(p, 'blood_group', r.blood_group || ''),
          getVal(p, 'parent_or_spouse', r.parent_guardian_name || ''),
          hVal,
          wVal,
          getVal(paed, 'bmi', bmi),
          getVal(paed, 'bmi_centile', r.bmi_centile || pscreen.bmi_centile || ''),
          getVal(h, 'blood_pressure_mmhg', r.blood_pressure || ''),
          getVal(paed, 'bp_percentile', r.bp_percentile || pscreen.bp_percentile || ''),
          getVal(h, 'pulse_bpm', r.pulse || ''),
          atrVal,
          r.gait_status || getVal(sr, 'gait_status', 'Normal'),
          r.otoscopy_right || getVal(sr, 'otoscope_right_ear', 'Normal'),
          r.otoscopy_left || getVal(sr, 'otoscope_left_ear', 'Normal'),
          getVal(vis, 'right_eye', '6/6'),
          getVal(vis, 'left_eye', '6/6'),
          r.dental_caries || getVal(den, 'dental_abnormality', 'None'),
          getVal(ref, 'specialist_referred_to', r.referral_details || 'None'),
          alertsStr
        ];
      } else {
        rowVals = [
          r.id,
          r.camp_name || 'Camp',
          getVal(p, 'full_name', r.person_name || 'Patient'),
          r.status === 'reviewed' || r.status === 'approved' ? 'Approved' : 'To check',
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
      }

      html += `<tr>`;
      html += `<td class="row-num">${rowIdx + 1}</td>`;
      rowVals.forEach((val, cIdx) => {
        let cellContent = escapeHtml(String(val || ''));
        const statusIdx = isPaediatric ? 5 : 3;
        if (cIdx === statusIdx) {
          const isApp = val === 'Approved';
          cellContent = `<span class="tag ${isApp ? '' : 'w'}" style="font-size:10.5px;padding:2px 8px;">${cellContent}</span>`;
        }
        if (isPaediatric && cIdx === 17 && parseFloat(String(val)) >= 5) {
          cellContent = `<span class="tag e" style="font-size:10px;">ATR ${cellContent}°</span>`;
        }
        html += `<td>${cellContent}</td>`;
      });
      html += `</tr>`;
    });

    html += `</tbody></table>`;
    container.innerHTML = html;

  } else {
    // ── Sheet 3: Camps Summary Table ──────────────────────────────────────
    const campsCountEl = $('excel-camps-count');
    if (campsCountEl) campsCountEl.textContent = state.camps.length;

    let html = `<table class="excel-table"><thead><tr>`;
    html += `<th class="col-corner">#</th>`;
    const campHeaders = ["Camp ID", "Camp Name", "Target Group", "Date", "Target Quota", "Total Scanned", "To Check", "Reviewed", "Progress %", "Created At"];
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
      const isPaed = c.target_group === 'paediatric';
      html += `<tr>`;
      html += `<td class="row-num">${idx + 1}</td>`;
      html += `<td>${escapeHtml(c.id)}</td>`;
      html += `<td><b>${escapeHtml(c.name)}</b></td>`;
      html += `<td><span class="tag" style="font-size:10px;background:${isPaed ? '#E0F2FE;color:#0284C7' : '#FCE7F3;color:#BE185D'};">${isPaed ? 'Paediatric' : 'Adult'}</span></td>`;
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

// ── Phase 4: Offline Queue Badge & Status helpers ────────────────────────
function _updateOfflineBadge(count) {
  // Update the floating offline banner badge
  const badge = document.getElementById('offline-queue-badge');
  if (badge) badge.textContent = count > 0 ? `${count} pending` : '';
  // Also update any persistent icon in the header if present
  const headerBadge = document.getElementById('header-offline-badge');
  if (headerBadge) {
    headerBadge.textContent = count > 0 ? String(count) : '';
    headerBadge.style.display = count > 0 ? 'inline-flex' : 'none';
  }
}

function showOfflineQueueStatus() {
  offlineQueue.length().then((n) => {
    if (n === 0) {
      toast('✅ Offline queue is empty — all scans uploaded.');
    } else {
      toast(`📵 ${n} scan(s) encrypted in offline queue. Will auto-sync on network restore.`);
    }
  });
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
  swapScanPages,
  saveAndExtract,
  openReview,
  toggleReviewDocs,
  switchReviewTab,
  initSignaturePad,
  clearSignature,
  approveAndSaveRecord,
  saveDraftReview,
  onVitalsChanged,
  onConsentChanged,
  updateReviewClinicalAlerts,
  markPhysicalExamNormal,
  showMissingConfirmationModal,
  closeMissingConfirmModal,
  confirmApproveDespiteMissing,
  printReferralSlip,
  toggleTorch,
  showQualityAlertModal,
  retryCaptureFromAlert,
  turnOnTorchAndRetake,
  bypassQualityAlert,
  eraseActiveRecord,
  syncSupabaseCloud,
  renderRecords,
  setRecordsFilter,
  filterRecords,
  exportExcel,
  exportAdultExcel,
  exportPaediatricExcel,
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
  toggleTheme,
  initTheme,
  finishSplash: () => window.finishSplash && window.finishSplash(),
  showOfflineQueueStatus,
};

// Initial App Boot Sequence
function initApp() {
  initTheme();
  updateDoctorDisplays();
  renderHome();
  initPinBoxes();
  resetSessionIdleTimer();

  // ── Phase 4: Encrypted Offline Queue + ServiceWorker ────────────────────
  (async () => {
    try {
      const { supported, queueLength } = await offlineQueue.init((evt) => {
        // Update badge on every queue event
        offlineQueue.length().then((n) => {
          state.offlineQueueLength = n;
          _updateOfflineBadge(n);
        });
        if (evt.type === 'synced') {
          toast(`✅ Queued scan #${evt.id} synced to server.`);
        } else if (evt.type === 'drain_complete' && evt.synced > 0) {
          toast(`📡 ${evt.synced} offline scan(s) uploaded successfully.`);
        } else if (evt.type === 'expired') {
          toast(`⏱ Queued scan #${evt.id} expired (> 48 h) and was purged.`, 'warning');
        } else if (evt.type === 'permanent_error') {
          toast(`❌ Queued scan #${evt.id} failed permanently and was removed.`, 'error');
        }
      });

      if (supported) {
        state.offlineQueueLength = queueLength;
        _updateOfflineBadge(queueLength);
        // Register SW and wire drain to the direct extract API call
        await registerOfflineSupport(
          (p1, p2, hdrs) => api.extractPagesDirect(p1, p2, hdrs)
        );
        // Drain immediately if we're online and have pending scans
        if (navigator.onLine && queueLength > 0) {
          offlineQueue.drain((p1, p2, hdrs) => api.extractPagesDirect(p1, p2, hdrs));
        }
      }
    } catch (err) {
      console.warn('[OfflineQueue] Init error:', err);
    }
  })();

  // Network status banner
  function _updateNetworkBanner() {
    const online = navigator.onLine;
    let banner = document.getElementById('offline-network-banner');
    if (!online) {
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'offline-network-banner';
        banner.setAttribute('role', 'alert');
        banner.style.cssText = [
          'position:fixed;bottom:0;left:0;right:0;z-index:9999',
          'background:linear-gradient(90deg,#1e293b,#0f172a)',
          'color:#f8fafc;padding:10px 18px;display:flex;align-items:center;gap:10px',
          'font-size:13px;font-weight:600;letter-spacing:.01em;box-shadow:0 -2px 12px rgba(0,0,0,.4)',
          'border-top:2px solid #f59e0b',
        ].join(';');
        banner.innerHTML = `
          <span style="font-size:18px">📵</span>
          <span>You are offline. Scans will be <strong>encrypted &amp; queued</strong> and synced when connectivity returns.</span>
          <span id="offline-queue-badge" style="margin-left:auto;background:#f59e0b;color:#0f172a;border-radius:20px;padding:2px 10px;font-size:12px;font-weight:700"></span>`;
        document.body.appendChild(banner);
      }
    } else if (banner) {
      banner.remove();
    }
  }
  window.addEventListener('online', _updateNetworkBanner);
  window.addEventListener('offline', _updateNetworkBanner);
  _updateNetworkBanner();
  // ── End Phase 4 ──────────────────────────────────────────────────────────


  const splashBar = document.getElementById('splash-progress-bar');
  const splashText = document.getElementById('splash-status-text');
  const splashPct = document.getElementById('splash-pct');
  const splashPillText = document.getElementById('splash-status-pill-text');
  const splashEl = document.getElementById('splash');

  let transitioned = false;
  let failsafeTimeout = null;
  let animFrameId = null;

  const finishSplash = () => {
    if (transitioned) return;
    transitioned = true;
    if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }
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
    if (splashPillText) splashPillText.textContent = 'CLINICAL VISION AI · READY';

    const sp = document.getElementById('splash');
    if (sp) {
      sp.style.transition = 'opacity 0.45s cubic-bezier(0.16, 1, 0.3, 1), transform 0.45s cubic-bezier(0.16, 1, 0.3, 1), filter 0.45s ease';
      sp.style.opacity = '0';
      sp.style.transform = 'scale(1.03)';
      sp.style.filter = 'blur(8px)';
      sp.style.pointerEvents = 'none';
      setTimeout(() => {
        sp.classList.add('hidden');
        sp.style.display = 'none';
        if (state.currentScreen === 'splash') {
          go('landing');
        }
      }, 450);
    } else {
      if (state.currentScreen === 'splash') {
        go('landing');
      }
    }
  };

  // Expose globally for skip button
  window.finishSplash = finishSplash;

  // Fluid CSS / JS Optical Scanner Animation Timeline
  const stages = [
    { at: 0, text: "Initializing Optical Vision AI Core...", pill: "CLINICAL VISION AI · INITIALIZING" },
    { at: 22, text: "Calibrating Dual-Page Intake Grids...", pill: "DUAL-PAGE ALIGNMENT · CALIBRATING" },
    { at: 48, text: "Connecting Medical Camp Store & SQLite...", pill: "ENCRYPTED STORAGE · CONNECTED" },
    { at: 74, text: "Syncing Clinical Vitals Knowledge Engine...", pill: "VITALS KNOWLEDGE ENGINE · SYNCED" },
    { at: 92, text: "Ready · Launching Optical Intake Platform", pill: "CLINICAL VISION AI · READY" }
  ];

  const isFrozenDemo = window.location.search.includes('freeze');
  const splashDurationMs = isFrozenDemo ? 30000 : 2600;
  const splashStartTime = performance.now();

  function stepSplash(currentTime) {
    if (transitioned) return;
    const elapsed = currentTime - splashStartTime;
    const progress = Math.min(1, elapsed / splashDurationMs);
    const pct = Math.min(100, Math.round(progress * 100));

    if (splashBar) splashBar.style.width = `${pct}%`;
    if (splashPct) splashPct.textContent = `${pct}%`;

    const currentStage = [...stages].reverse().find(s => pct >= s.at);
    if (currentStage) {
      if (splashText) splashText.textContent = currentStage.text;
      if (splashPillText) splashPillText.textContent = currentStage.pill;
    }

    if (progress < 1) {
      animFrameId = requestAnimationFrame(stepSplash);
    } else {
      setTimeout(finishSplash, 200);
    }
  }

  animFrameId = requestAnimationFrame(stepSplash);

  // Allow clicking splash to instantly enter (unless in freeze mode)
  if (splashEl && !isFrozenDemo) {
    splashEl.addEventListener('click', () => {
      finishSplash();
    }, { once: true });
  }

  // Backup failsafe timer
  failsafeTimeout = setTimeout(finishSplash, isFrozenDemo ? 35000 : 3500);
}

// Run boot sequence reliably
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// --- Unified AI Pipeline Settings Modal Controller ---
window.openAISettingsModal = async function() {
  const modal = document.getElementById('ai-settings-modal');
  if (!modal) return;
  modal.style.display = 'flex';
  const input = document.getElementById('modal-ai-api-key');
  const storedKey = localStorage.getItem('i2i_ai_api_key') || '';
  if (input) {
    input.value = storedKey;
  }
  window.updateAIKeyDetection(storedKey);
  try {
    const status = await api.getAIStatus();
    if (status && status.unified_pipeline) {
      if (!storedKey) {
        const provEl = document.getElementById('modal-ai-active-provider');
        if (provEl) provEl.textContent = `${status.provider_name} (Server .env)`;
        const pModel = document.getElementById('modal-ai-primary-model');
        if (pModel) pModel.textContent = status.primary_model || '-';
        const fbModel = document.getElementById('modal-ai-fallback-model');
        if (fbModel) fbModel.textContent = status.fallback_model || '-';
      }
    }
  } catch (e) {
    console.warn("Could not fetch /api/ai/status:", e);
  }
};

window.closeAISettingsModal = function() {
  const modal = document.getElementById('ai-settings-modal');
  if (modal) modal.style.display = 'none';
};

window.updateAIKeyDetection = function(keyVal) {
  const k = (keyVal || '').trim();
  const dot = document.getElementById('modal-ai-detect-dot');
  const label = document.getElementById('modal-ai-detect-label');
  const provEl = document.getElementById('modal-ai-active-provider');
  const pModel = document.getElementById('modal-ai-primary-model');
  const fbModel = document.getElementById('modal-ai-fallback-model');

  if (!k) {
    if (dot) dot.style.background = '#94a3b8';
    if (label) label.textContent = 'Auto-detect ready: Paste any supported AI key';
    if (provEl) provEl.textContent = 'Using Server Default (.env)';
    if (pModel) pModel.textContent = '-';
    if (fbModel) fbModel.textContent = '-';
    return;
  }

  let provider = 'unknown';
  let provName = 'Unknown Provider';
  let primaryModel = '-';
  let fallbackModel = '-';

  if (k.startsWith('nvapi-')) {
    provider = 'nvidia';
    provName = 'NVIDIA NIM (Auto-detected)';
    primaryModel = 'meta/llama-3.2-11b-vision-instruct';
    fallbackModel = 'meta/llama-3.2-90b-vision-instruct';
  } else if (k.startsWith('gsk_')) {
    provider = 'groq';
    provName = 'Groq Vision (Auto-detected)';
    primaryModel = 'qwen/qwen3.8-27b';
    fallbackModel = 'qwen/qwen3.8-27b';
  } else if (k.startsWith('AIzaSy') || k.startsWith('AQ.')) {
    provider = 'gemini';
    provName = 'Google Gemini Vision (Auto-detected)';
    primaryModel = 'gemini-3.6-flash';
    fallbackModel = 'gemini-3.1-pro-preview';
  } else if (k.startsWith('sk-ant-')) {
    provider = 'anthropic';
    provName = 'Anthropic Claude (Auto-detected)';
    primaryModel = 'claude-3-5-sonnet-20241022';
    fallbackModel = 'claude-3-5-sonnet-20241022';
  } else if (k.startsWith('sk-or-')) {
    provider = 'openrouter';
    provName = 'OpenRouter (Auto-detected)';
    primaryModel = 'google/gemini-flash-1.5';
    fallbackModel = 'meta-llama/llama-3.2-11b-vision-instruct';
  } else if (k.startsWith('sk-')) {
    provider = 'openai';
    provName = 'OpenAI Vision (Auto-detected)';
    primaryModel = 'gpt-4o-mini';
    fallbackModel = 'gpt-4o';
  } else {
    provName = 'Custom Key (Auto-detecting provider)';
  }

  if (dot) dot.style.background = provider !== 'unknown' ? '#10B981' : '#F59E0B';
  if (label) label.textContent = provider !== 'unknown' ? `Valid format recognized: ${provName}` : 'Custom key format entered';
  if (provEl) provEl.textContent = provName;
  if (pModel) pModel.textContent = primaryModel;
  if (fbModel) fbModel.textContent = fallbackModel;
};

window.saveAISettings = function() {
  const input = document.getElementById('modal-ai-api-key');
  const k = input ? input.value.trim() : '';
  if (k) {
    localStorage.setItem('i2i_ai_api_key', k);
    toast('AI API key activated for all future extractions! ⚡');
  } else {
    localStorage.removeItem('i2i_ai_api_key');
  }
  window.closeAISettingsModal();
};

window.clearAISettings = function() {
  localStorage.removeItem('i2i_ai_api_key');
  localStorage.removeItem('i2i_ai_provider');
  localStorage.removeItem('i2i_ai_model');
  const input = document.getElementById('modal-ai-api-key');
  if (input) input.value = '';
  window.updateAIKeyDetection('');
  toast('Reset to default server environment keys (.env)');
  window.closeAISettingsModal();
};


