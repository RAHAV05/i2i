/**
 * Document Scanner & Camera Module.
 * Real camera streaming (getUserMedia), frame capture to canvas,
 * device image upload, validation, and retake/replace management.
 */

export class DocumentScanner {
  constructor(options = {}) {
    this.videoEl = options.videoEl;
    this.canvasEl = options.canvasEl;
    this.onThumbnailsUpdated = options.onThumbnailsUpdated || (() => {});
    this.onStateChanged = options.onStateChanged || (() => {});

    this.stream = null;
    this.facingMode = 'environment'; // Back camera default for documents
    this.currentPage = 0; // 0 for Page 1, 1 for Page 2
    this.pages = [null, null]; // [ { blob, url, name }, { blob, url, name } ]
    this.isCameraActive = false;
  }

  async startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('Camera is not supported on this device/browser. Please use the Upload button.');
    }

    this.stopCamera();

    try {
      const constraints = {
        video: {
          facingMode: { ideal: this.facingMode },
          width: { ideal: 1920 },
          height: { ideal: 1080 }
        },
        audio: false
      };

      this.stream = await navigator.mediaDevices.getUserMedia(constraints);
      if (this.videoEl) {
        this.videoEl.srcObject = this.stream;
        await this.videoEl.play();
      }
      this.isCameraActive = true;
      this.onStateChanged({ cameraActive: true });
    } catch (err) {
      this.isCameraActive = false;
      this.onStateChanged({ cameraActive: false, error: err.message });
      // If environment camera fails, try any available video device
      if (this.facingMode === 'environment') {
        this.facingMode = 'user';
        return this.startCamera();
      }
      throw new Error('Camera access was denied or is unavailable. You can upload document photos instead.');
    }
  }

  stopCamera() {
    if (this.stream) {
      this.stream.getTracks().forEach(track => track.stop());
      this.stream = null;
    }
    if (this.videoEl) {
      this.videoEl.srcObject = null;
    }
    this.isCameraActive = false;
    this.onStateChanged({ cameraActive: false });
  }

  async switchCamera() {
    this.facingMode = (this.facingMode === 'environment') ? 'user' : 'environment';
    await this.startCamera();
  }

  async captureCurrentFrame() {
    if (!this.isCameraActive || !this.videoEl) {
      throw new Error('Camera is not active. Please start camera or upload an image.');
    }

    const video = this.videoEl;
    const canvas = this.canvasEl || document.createElement('canvas');
    
    // Use actual video feed resolution
    const width = video.videoWidth || 1280;
    const height = video.videoHeight || 720;
    canvas.width = width;
    canvas.height = height;

    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, width, height);

    return new Promise((resolve, reject) => {
      canvas.toBlob(blob => {
        if (!blob) {
          reject(new Error('Failed to create image from camera frame.'));
          return;
        }
        const url = URL.createObjectURL(blob);
        this.setPageImage(this.currentPage, blob, url, `camera_page${this.currentPage + 1}.jpg`);
        resolve({ blob, url });
      }, 'image/jpeg', 0.92);
    });
  }

  async handleFileUpload(file) {
    if (!file) return;

    const validMimes = ['image/jpeg', 'image/png', 'image/webp', 'image/jpg'];
    if (!validMimes.includes(file.type)) {
      throw new Error('Please select a valid image file (JPEG, PNG, or WebP).');
    }

    const maxSize = 15 * 1024 * 1024; // 15MB
    if (file.size > maxSize) {
      throw new Error('File size exceeds the 15 MB limit.');
    }

    try {
      const optimizedBlob = await this._resizeImageFile(file, 720);
      const url = URL.createObjectURL(optimizedBlob);
      this.setPageImage(this.currentPage, optimizedBlob, url, file.name);
      return { blob: optimizedBlob, url };
    } catch {
      const url = URL.createObjectURL(file);
      this.setPageImage(this.currentPage, file, url, file.name);
      return { blob: file, url };
    }
  }

  _resizeImageFile(fileOrBlob, maxDim = 720) {
    return new Promise((resolve) => {
      const img = new Image();
      const objUrl = URL.createObjectURL(fileOrBlob);
      img.onload = () => {
        URL.revokeObjectURL(objUrl);
        let width = img.width;
        let height = img.height;
        if (width > maxDim || height > maxDim) {
          if (width > height) {
            height = Math.round((height * maxDim) / width);
            width = maxDim;
          } else {
            width = Math.round((width * maxDim) / height);
            height = maxDim;
          }
        }
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, width, height);
        canvas.toBlob(blob => {
          resolve(blob || fileOrBlob);
        }, 'image/jpeg', 0.85);
      };
      img.onerror = () => {
        URL.revokeObjectURL(objUrl);
        resolve(fileOrBlob);
      };
      img.src = objUrl;
    });
  }

  setPageImage(pageIndex, blob, url, name = '') {
    // Revoke previous URL if present to prevent memory leaks
    if (this.pages[pageIndex] && this.pages[pageIndex].url) {
      URL.revokeObjectURL(this.pages[pageIndex].url);
    }

    this.pages[pageIndex] = { blob, url, name };
    this.onThumbnailsUpdated(this.pages);
  }

  removePage(pageIndex) {
    if (this.pages[pageIndex] && this.pages[pageIndex].url) {
      URL.revokeObjectURL(this.pages[pageIndex].url);
    }
    this.pages[pageIndex] = null;
    this.onThumbnailsUpdated(this.pages);
  }

  resetAllPages() {
    this.pages.forEach((p, idx) => {
      if (p && p.url) URL.revokeObjectURL(p.url);
      this.pages[idx] = null;
    });
    this.currentPage = 0;
    this.onThumbnailsUpdated(this.pages);
  }

  hasBothPages() {
    return Boolean(this.pages[0] && this.pages[1]);
  }

  getPageBlobs() {
    if (!this.hasBothPages()) {
      throw new Error('Both Page 1 and Page 2 are required.');
    }
    return {
      page1: this.pages[0].blob,
      page2: this.pages[1].blob
    };
  }

  async loadActualFixtureSample(type = 'filled') {
    const p1Name = type === 'filled' ? 'filled_sample_page_1.jpeg' : 'blank_page_1.jpeg';
    const p2Name = type === 'filled' ? 'filled_sample_page_2.jpeg' : 'blank_page_2.jpeg';

    const [res1, res2] = await Promise.all([
      fetch(`assets/fixtures/${p1Name}`),
      fetch(`assets/fixtures/${p2Name}`)
    ]);

    if (!res1.ok || !res2.ok) {
      throw new Error('Could not load fixture images from assets/fixtures/');
    }

    const [blob1, blob2] = await Promise.all([res1.blob(), res2.blob()]);
    const opt1 = await this._resizeImageFile(blob1, 720);
    const opt2 = await this._resizeImageFile(blob2, 720);
    this.setPageImage(0, opt1, URL.createObjectURL(opt1), p1Name);
    this.setPageImage(1, opt2, URL.createObjectURL(opt2), p2Name);
    return { p1Name, p2Name };
  }

  async loadSyntheticSample(type = 'filled') {
    const p1 = await this.generateSyntheticSample(1);
    const p2 = await this.generateSyntheticSample(2);
    this.setPageImage(0, p1.blob, p1.url, 'sample_intake_page_1.png');
    this.setPageImage(1, p2.blob, p2.url, 'sample_intake_page_2.png');
    return { p1, p2 };
  }

  /**
   * Generates a realistic synthetic test clinic form on a canvas
   * for zero-friction demonstrations when a webcam or physical form is unavailable.
   */
  async generateSyntheticSample(pageNum = 1) {
    const canvas = document.createElement('canvas');
    canvas.width = 900;
    canvas.height = 1200;
    const ctx = canvas.getContext('2d');

    // Paper background
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Subtle paper grid texture
    ctx.strokeStyle = '#EEF2F5';
    ctx.lineWidth = 1;
    for (let y = 40; y < canvas.height; y += 30) {
      ctx.beginPath();
      ctx.moveTo(40, y);
      ctx.lineTo(canvas.width - 40, y);
      ctx.stroke();
    }

    // Border line
    ctx.strokeStyle = '#14233A';
    ctx.lineWidth = 3;
    ctx.strokeRect(40, 40, canvas.width - 80, canvas.height - 80);

    // Header
    ctx.fillStyle = '#0E7C6B';
    ctx.fillRect(40, 40, canvas.width - 80, 80);
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 26px sans-serif';
    ctx.fillText('HEALTH CAMP INTAKE FORM (SYNTHETIC DEMO)', 70, 90);

    ctx.fillStyle = '#14233A';
    ctx.font = 'bold 20px sans-serif';
    ctx.fillText(`PAGE ${pageNum} OF 2 — ${pageNum === 1 ? 'PATIENT VITALS & IDENTIFICATION' : 'CLINICAL EXAM & REFERRAL'}`, 70, 160);

    ctx.font = '16px monospace';
    ctx.fillStyle = '#5E6E7E';
    ctx.fillText('Form Ref: HC-2026-SYN-042 | Camp: St. Mary\'s School, Adyar', 70, 190);

    ctx.strokeStyle = '#D5E0DD';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(70, 210);
    ctx.lineTo(canvas.width - 70, 210);
    ctx.stroke();

    ctx.fillStyle = '#14233A';
    ctx.font = 'bold 18px sans-serif';

    if (pageNum === 1) {
      const fields = [
        ['Patient Full Name:', 'Kavitha Raman'],
        ['Age:', '36 years'],
        ['Gender:', 'Female'],
        ['Height:', '162 cm'],
        ['Weight:', '58 kg'],
        ['Blood Pressure (BP):', '118/76 mmHg'],
        ['Known Allergies:', 'None reported'],
        ['Primary Complaint:', 'Routine general checkup at community camp']
      ];

      let y = 270;
      fields.forEach(([label, val]) => {
        ctx.fillStyle = '#5E6E7E';
        ctx.font = 'bold 17px sans-serif';
        ctx.fillText(label, 70, y);

        // Handwritten-style blue ink value
        ctx.fillStyle = '#0A3B7B';
        ctx.font = 'italic 20px cursive, sans-serif';
        ctx.fillText(val, 320, y);

        ctx.strokeStyle = '#CFDAD7';
        ctx.beginPath();
        ctx.moveTo(310, y + 6);
        ctx.lineTo(canvas.width - 70, y + 6);
        ctx.stroke();

        y += 75;
      });
    } else {
      const examNotes = [
        ['Cardiovascular:', 'S1 S2 regular rate and rhythm. No murmurs.'],
        ['Respiratory:', 'Bilateral air entry clear. No wheeze or crackles.'],
        ['ENT & Oral Check:', 'Mild dental calculus lower incisors. Throat clear.'],
        ['Abdomen:', 'Soft, non-tender, no organomegaly.'],
        ['Referral Decision:', 'Referred to Dental OPD for scaling and polishing.'],
        ['Follow-up Window:', 'Within 3 weeks at District Hospital Dental Clinic.'],
        ['Attending Doctor:', 'Dr. Priya, MBBS (Reg. 84920)']
      ];

      let y = 270;
      examNotes.forEach(([label, val]) => {
        ctx.fillStyle = '#5E6E7E';
        ctx.font = 'bold 17px sans-serif';
        ctx.fillText(label, 70, y);

        ctx.fillStyle = '#0A3B7B';
        ctx.font = 'italic 19px cursive, sans-serif';
        ctx.fillText(val, 70, y + 32);

        ctx.strokeStyle = '#CFDAD7';
        ctx.beginPath();
        ctx.moveTo(70, y + 42);
        ctx.lineTo(canvas.width - 70, y + 42);
        ctx.stroke();

        y += 90;
      });
    }

    // Footer stamp
    ctx.strokeStyle = '#0E7C6B';
    ctx.lineWidth = 2;
    ctx.strokeRect(canvas.width - 290, canvas.height - 150, 220, 80);
    ctx.fillStyle = '#0E7C6B';
    ctx.font = 'bold 14px sans-serif';
    ctx.fillText('COMMUNITY HEALTH CAMP', canvas.width - 275, canvas.height - 120);
    ctx.fillText('VERIFIED PHYSICIAN STAMP', canvas.width - 275, canvas.height - 95);

    return new Promise(resolve => {
      canvas.toBlob(blob => {
        const url = URL.createObjectURL(blob);
        resolve({ blob, url });
      }, 'image/png');
    });
  }
}
