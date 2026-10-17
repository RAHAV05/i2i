/**
 * Doc Scanner — Mobile-First Hybrid Clinic Application Controller
 * Connects the phone-first touch UI with Flask REST API & Vision AI.
 */

import { api } from './api.js';
import { DocumentScanner } from './scanner.js';

// DOM Selector Utility
const $ = id => document.getElementById(id);

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
  doctorName: 'Dr. Priya'
};

// Initialize Camera Module
let scanner = null;

// ==========================================================================
// 1. Screen Navigation & Frame Toggle
// ==========================================================================

export function go(screenId) {
  state.currentScreen = screenId;

  // Toggle screens
  document.querySelectorAll('.scr').forEach(s => {
    s.classList.toggle('on', s.id === screenId);
  });

  // Update bottom navigation bar active states
  const navMap = {
    home: ['nav-btn-home', 'nav-btn-home-2', 'nav-btn-home-3', 'nav-btn-home-4'],
    records: ['nav-btn-records', 'nav-btn-records-2', 'nav-btn-records-3', 'nav-btn-records-4'],
    export: ['nav-btn-export', 'nav-btn-export-2', 'nav-btn-export-3', 'nav-btn-export-4'],
    settings: ['nav-btn-settings', 'nav-btn-settings-2', 'nav-btn-settings-3', 'nav-btn-settings-4']
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
  } else if (screenId === 'settings') {
    renderSettings();
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

export function login() {
  const email = $('login-email')?.value.trim() || '';
  const password = $('login-password')?.value || '';
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

  const namePart = email.split('@')[0].replace(/[._-]/g, ' ');
  state.doctorName = 'Dr. ' + namePart.charAt(0).toUpperCase() + namePart.slice(1);
  updateDoctorDisplays();
  go('home');
  toast(`Signed in as ${state.doctorName}`);
}

export function loginDemo() {
  state.doctorName = 'Dr. Priya';
  updateDoctorDisplays();
  go('home');
  toast('Signed in with Demo Account');
}

function updateDoctorDisplays() {
  const dName = $('home-doctor-name');
  const dAv = $('home-avatar');
  if (dName) dName.textContent = state.doctorName;
  if (dAv) dAv.textContent = state.doctorName.replace('Dr. ', '').charAt(0) || 'D';
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
    if ($('kpi-scanned')) $('kpi-scanned').textContent = state.stats.total_scanned || 0;
    if ($('kpi-tocheck')) $('kpi-tocheck').textContent = state.stats.to_check || 0;
    if ($('kpi-camps')) $('kpi-camps').textContent = state.camps.length || 0;

    // Update Active Camp Hero Card
    const activeCamp = state.camps.find(c => c.id === state.selectedCampId) || state.camps[0];
    if (activeCamp) {
      const pct = Math.min(100, Math.round(activeCamp.progress_pct || 0));
      const ring = $('home-ring');
      if (ring) ring.style.setProperty('--p', pct);
      if ($('home-ring-pct')) $('home-ring-pct').textContent = `${pct}%`;
      if ($('home-camp-title')) $('home-camp-title').textContent = activeCamp.name;
      if ($('home-camp-subtitle')) {
        $('home-camp-subtitle').textContent = `${activeCamp.total_scanned || 0} of ${activeCamp.expected_records || 100} forms · ${activeCamp.date_str}`;
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
      <div style="padding:16px;text-align:center;color:var(--mute);background:#fff;border-radius:16px;border:1.5px dashed var(--line);">
        No health camps registered. Tap "+ Add Camp" to start.
      </div>
    `;
    return;
  }

  container.innerHTML = state.camps.map(c => {
    const isSelected = c.id === state.selectedCampId;
    const pct = Math.min(100, Math.round(c.progress_pct || 0));
    return `
      <div class="camp ${isSelected ? 'sel' : ''}" onclick="window.app.selectActiveCamp('${c.id}')">
        <div class="r">
          <b>${escapeHtml(c.name)}</b>
          <small>${escapeHtml(c.date_str || 'Today')}</small>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
          <small>${c.total_scanned || 0} of ${c.expected_records || 100} scanned${c.to_check ? ` · <span style="color:#8A5A00;font-weight:600;">${c.to_check} to check</span>` : ''}</small>
          <button class="btn ghost" style="width:auto;padding:3px 8px;font-size:11px;" onclick="event.stopPropagation();window.app.exportCampExcel('${c.id}')" title="Download Excel for this camp">
            Export Excel
          </button>
        </div>
        <div class="pb"><i style="width:${pct}%;"></i></div>
      </div>
    `;
  }).join('');
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

export function selectActiveCamp(campId) {
  state.selectedCampId = campId;
  renderHome();
  const c = state.camps.find(x => x.id === campId);
  if (c) toast(`Active Camp: ${c.name}`);
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
    $('hnt').textContent = 'Camera unavailable. Tap "Upload Photo" or "Load 2-Page Sample".';
  }
}

export function closeScan() {
  const sc = getScannerInstance();
  sc.stopCamera();
  const video = $('camera-video');
  if (video) video.style.display = 'none';
  go('home');
}

export async function flipCamera() {
  const sc = getScannerInstance();
  await sc.switchCamera();
  toast('Flipping camera…');
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

    // Build the record payload with extracted structured data
    const personName = nameInput || extractRes.person_name || 'Patient';
    const recordPayload = {
      camp_id: campId,
      person_name: personName,
      age: extractRes.age,
      gender: extractRes.gender,
      height: extractRes.height,
      weight: extractRes.weight,
      blood_pressure: extractRes.blood_pressure,
      examination_details: extractRes.examination_details,
      referral_details: extractRes.referral_details,
      missing_fields: extractRes.missing_fields || [],
      uncertain_fields: extractRes.uncertain_fields || [],
      extraction_notes: extractRes.extraction_notes || 'Extracted via Groq Vision',
      structured_record: extractRes.structured_record || {},
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
        extraction_notes: 'Vision AI offline or unconfigured. Manual entry enabled.'
      };
      const saved = await api.saveRecord(fallbackPayload);
      toast('Vision AI offline. Manual entry opened.', true);
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
    if ($('rev-gender')) $('rev-gender').value = getVal(p_info, 'gender', record.gender || 'Male');
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
    if ($('rev-pallor')) $('rev-pallor').value = getVal(h_asmt, 'pallor', 'Absent');
    if ($('rev-icterus')) $('rev-icterus').value = getVal(h_asmt, 'icterus', 'Absent');
    if ($('rev-cyanosis')) $('rev-cyanosis').value = getVal(h_asmt, 'cyanosis', 'Absent');
    if ($('rev-edema')) $('rev-edema').value = getVal(h_asmt, 'edema', 'Absent');
    if ($('rev-clubbing')) $('rev-clubbing').value = getVal(h_asmt, 'clubbing', 'Absent');
    if ($('rev-lymph')) $('rev-lymph').value = getVal(h_asmt, 'lymphadenopathy', 'Absent');
    if ($('rev-ecg')) $('rev-ecg').value = getVal(h_asmt, 'ecg_notes', 'Normal');
    if ($('rev-echo')) $('rev-echo').value = getVal(h_asmt, 'echo_notes', 'Normal');

    // Tab 5: Systemic
    if ($('rev-sys-rs')) $('rev-sys-rs').value = getVal(s_exam, 'respiratory_system', record.examination_details || 'Normal');
    if ($('rev-sys-cvs')) $('rev-sys-cvs').value = getVal(s_exam, 'cardiovascular_system', 'S1 S2 heard, normal');
    if ($('rev-sys-abdomen')) $('rev-sys-abdomen').value = getVal(s_exam, 'abdomen', 'Soft, non-tender');
    if ($('rev-sys-cns')) $('rev-sys-cns').value = getVal(s_exam, 'central_nervous_system', 'Normal');
    if ($('rev-sys-ent')) $('rev-sys-ent').value = getVal(s_exam, 'ear', 'Normal');
    if ($('rev-sys-skin')) $('rev-sys-skin').value = getVal(s_exam, 'skin', 'Normal');

    // Tab 6: Vision & Dental
    if ($('rev-va-right')) $('rev-va-right').value = getVal(vis, 'distant_vision_right', '6/6');
    if ($('rev-va-left')) $('rev-va-left').value = getVal(vis, 'distant_vision_left', '6/6');
    if ($('rev-vision-category')) $('rev-vision-category').value = getVal(vis, 'vision_category', 'Unaided');
    if ($('rev-dental-status')) $('rev-dental-status').value = getVal(den, 'normal', 'Normal');
    if ($('rev-dental-advice')) $('rev-dental-advice').value = getVal(den, 'advice', '');

    // Tab 7: Referral & Doctor Notes
    if ($('rev-referral')) $('rev-referral').value = getVal(ref, 'specialist_referred_to', record.referral_details || 'None');
    if ($('rev-doc-sign')) $('rev-doc-sign').value = getVal(ref, 'doctor_signature_present', 'Present');
    if ($('rev-doctor-notes')) $('rev-doctor-notes').value = record.reviewer_notes || 'Verified by clinic physician.';

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

    await api.reviewRecord(state.activeRecord.id, updates, state.doctorName);
    toast('Record approved and persisted to database!');
    go('home');
  } catch (err) {
    toast('Failed to save record: ' + err.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ==========================================================================
// 7. Patient Records Screen
// ==========================================================================

export async function renderRecords() {
  try {
    const records = await api.getRecords(null, null);
    state.records = records || [];
    filterRecords();
  } catch (err) {
    console.error('Error fetching records:', err);
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
  const container = $('records-full-list');
  if (!container) return;

  const filtered = state.records.filter(r => {
    const nameMatch = (r.person_name || '').toLowerCase().includes(query) ||
                      (r.camp_name || '').toLowerCase().includes(query) ||
                      (r.id || '').toLowerCase().includes(query);
    if (!nameMatch) return false;
    if (filter === 'needs_review' && r.status !== 'needs_review') return false;
    if (filter === 'reviewed' && r.status !== 'reviewed') return false;
    return true;
  });

  if (filtered.length === 0) {
    container.innerHTML = `
      <div style="padding:24px;text-align:center;color:var(--mute);">
        No records match your search filter.
      </div>
    `;
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
  const url = api.getExportUrl(targetCampId, onlyReviewed, 'xlsx');
  const filename = targetCampId ? `clinic_records_${targetCampId}.xlsx` : 'clinic_records_all.xlsx';
  
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
            <br>You can also view, search, and inspect all 95+ clinical fields and sheets directly in the interactive spreadsheet below.
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
  const url = api.getCampExportUrl(campId, 'xlsx');
  const filename = `camp_${campId}_records.xlsx`;
  triggerDownload(url, filename);
  toast(`Downloaded camp Excel report (${filename})`);

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
          <b style="color:var(--teal2);font-size:14.5px;display:block;margin-bottom:2px;">Camp Excel Report Downloaded!</b>
          <p style="font-size:13px;color:var(--ink);margin:0;line-height:1.4;">
            File saved to Downloads folder: <code style="background:#fff;padding:2px 6px;border-radius:4px;border:1px solid var(--teal);font-weight:700;">${filename}</code>.
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
      html += `<th>${colLetter}: ${escapeHtml(h)}</th>`;
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
      html += `<th>${colLetter}: ${escapeHtml(h)}</th>`;
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
    if (status) {
      if ($('settings-ai-provider')) {
        $('settings-ai-provider').textContent = status.ai_provider || 'Groq Vision (qwen/qwen3.8-27b)';
      }
      if ($('settings-ai-status')) {
        $('settings-ai-status').textContent = status.ai_configured ? 'Connected' : 'Manual Demo';
        $('settings-ai-status').className = status.ai_configured ? 'tag' : 'tag w';
      }
    }
  } catch (err) {
    console.error('Error in renderSettings:', err);
  }
}

export async function resetDemoData() {
  if (!confirm('Reset database to clean default synthetic Chennai health camp records?')) {
    return;
  }

  try {
    await api.resetDemoData();
    await renderHome();
    toast('Demo data reset to clean synthetic records!');
    go('home');
  } catch (err) {
    toast('Failed to reset demo: ' + err.message, true);
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
  login,
  loginDemo,
  toggleFrame,
  renderHome,
  selectActiveCamp,
  openCampPicker,
  startScan,
  closeScan,
  flipCamera,
  captureCurrentPage,
  onFileUploaded,
  loadDemoSamples,
  saveAndExtract,
  openReview,
  toggleReviewDocs,
  switchReviewTab,
  approveAndSaveRecord,
  renderRecords,
  setRecordsFilter,
  filterRecords,
  exportExcel,
  exportCampExcel,
  executeExport,
  toggleExcelFullscreen,
  switchExcelSheetTab,
  renderExcelPreview,
  filterExcelPreview,
  onExportFilterChanged,
  openNewCampModal,
  closeNewCampModal,
  saveNewCamp,
  renderSettings,
  resetDemoData
};

// Initial App Boot Sequence
window.addEventListener('DOMContentLoaded', () => {
  renderHome();
  // Splash timer transition
  setTimeout(() => {
    if (state.currentScreen === 'splash') {
      go('landing');
    }
  }, 2000);
});
