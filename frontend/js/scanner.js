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
    this.torchOn = false;
  }

  async isTorchSupported() {
    if (!this.stream) return false;
    const track = this.stream.getVideoTracks()[0];
    if (!track || !track.getCapabilities) return false;
    const caps = track.getCapabilities();
    return Boolean(caps && caps.torch);
  }

  async toggleTorch(forceState = null) {
    if (!this.stream) return false;
    const track = this.stream.getVideoTracks()[0];
    if (!track || !track.getCapabilities) return false;
    const caps = track.getCapabilities();
    if (!caps || !caps.torch) return false;
    this.torchOn = forceState !== null ? forceState : !this.torchOn;
    try {
      await track.applyConstraints({ advanced: [{ torch: this.torchOn }] });
      this.onStateChanged({ torchOn: this.torchOn });
      return this.torchOn;
    } catch (err) {
      console.warn('Torch constraint error:', err);
      return false;
    }
  }

  checkImageQuality(canvas) {
    if (!canvas) return { ok: true, isLowLight: false, isOverexposed: false, isBlurry: false, score: 100 };
    try {
      const ctx = canvas.getContext('2d');
      const w = canvas.width;
      const h = canvas.height;
      if (w < 10 || h < 10) return { ok: true, isLowLight: false, isOverexposed: false, isBlurry: false, score: 100 };
      
      const imgData = ctx.getImageData(0, 0, w, h);
      const data = imgData.data;
      
      let sumLum = 0;
      let sumGrad = 0;
      let count = 0;
      const step = Math.max(1, Math.floor(Math.min(w, h) / 120)); // fast subsample

      // Fast luminance & gradient pass
      for (let y = 1; y < h - 1; y += step) {
        for (let x = 1; x < w - 1; x += step) {
          const idx = (y * w + x) * 4;
          const center = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
          const right = 0.299 * data[idx + 4] + 0.587 * data[idx + 5] + 0.114 * data[idx + 6];
          const down = 0.299 * data[idx + w * 4] + 0.587 * data[idx + w * 4 + 1] + 0.114 * data[idx + w * 4 + 2];
          
          sumLum += center;
          sumGrad += Math.abs(center - right) + Math.abs(center - down);
          count++;
        }
      }

      if (count === 0) return { ok: true, isLowLight: false, isOverexposed: false, isBlurry: false, score: 100 };

      const avgLum = sumLum / count;
      const blurScore = sumGrad / count;

      // Center 256x256 Laplacian Edge Variance
      const winW = Math.min(256, w - 2);
      const winH = Math.min(256, h - 2);
      const startX = Math.max(1, Math.floor((w - winW) / 2));
      const startY = Math.max(1, Math.floor((h - winH) / 2));

      const lapVals = [];
      let lapSum = 0;
      for (let y = startY; y < startY + winH; y += 2) {
        for (let x = startX; x < startX + winW; x += 2) {
          const idx = (y * w + x) * 4;
          const c = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
          const up = 0.299 * data[((y - 1) * w + x) * 4] + 0.587 * data[((y - 1) * w + x) * 4 + 1] + 0.114 * data[((y - 1) * w + x) * 4 + 2];
          const dn = 0.299 * data[((y + 1) * w + x) * 4] + 0.587 * data[((y + 1) * w + x) * 4 + 1] + 0.114 * data[((y + 1) * w + x) * 4 + 2];
          const lf = 0.299 * data[(y * w + (x - 1)) * 4] + 0.587 * data[(y * w + (x - 1)) * 4 + 1] + 0.114 * data[(y * w + (x - 1)) * 4 + 2];
          const rt = 0.299 * data[(y * w + (x + 1)) * 4] + 0.587 * data[(y * w + (x + 1)) * 4 + 1] + 0.114 * data[(y * w + (x + 1)) * 4 + 2];
          const lap = Math.abs(up + dn + lf + rt - 4 * c);
          lapVals.push(lap);
          lapSum += lap;
        }
      }

      let lapVar = 0;
      if (lapVals.length > 0) {
        const lapMean = lapSum / lapVals.length;
        let sqDiffSum = 0;
        for (let i = 0; i < lapVals.length; i++) {
          sqDiffSum += (lapVals[i] - lapMean) * (lapVals[i] - lapMean);
        }
        lapVar = sqDiffSum / lapVals.length;
      }

      // Thresholds: low light < 50, overexposed > 230, blur variance < 14 or gradient < 8.5
      const isLowLight = avgLum < 50;
      const isOverexposed = avgLum > 230;
      const isBlurry = lapVar < 14.0 || blurScore < 8.5;

      const issues = [];
      if (isLowLight) issues.push('Low lighting / underexposed');
      if (isOverexposed) issues.push('Severe glare / overexposed');
      if (isBlurry) issues.push('Blurry / motion-blurred document');

      return {
        ok: !isLowLight && !isOverexposed && !isBlurry,
        isLowLight,
        isOverexposed,
        isBlurry,
        avgLum: Math.round(avgLum),
        blurScore: Math.round(blurScore * 10) / 10,
        laplacianVariance: Math.round(lapVar * 10) / 10,
        issues,
        message: issues.join(' and ')
      };
    } catch (err) {
      console.warn('Image quality check failed:', err);
      return { ok: true, isLowLight: false, isOverexposed: false, isBlurry: false, score: 100 };
    }
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
      const torchAvail = await this.isTorchSupported();
      this.onStateChanged({ cameraActive: true, torchSupported: torchAvail });
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
    this.torchOn = false;
    this.onStateChanged({ cameraActive: false, torchOn: false });
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

    // Pre-flight image quality check
    const quality = this.checkImageQuality(canvas);

    return new Promise((resolve, reject) => {
      canvas.toBlob(blob => {
        if (!blob) {
          reject(new Error('Failed to create image from camera frame.'));
          return;
        }
        const url = URL.createObjectURL(blob);
        this.setPageImage(this.currentPage, blob, url, `camera_page${this.currentPage + 1}.jpg`);
        resolve({ blob, url, quality });
      }, 'image/jpeg', 0.92);
    });
  }

  async handleFileUpload(file) {
    if (!file) return;

    const validMimes = [
      'image/jpeg', 'image/png', 'image/webp', 'image/jpg',
      'image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence'
    ];
    const isHeic = file.name && /\.(heic|heif)$/i.test(file.name);
    if (!validMimes.includes(file.type) && !isHeic) {
      throw new Error('Please select a valid image file (JPEG, PNG, WebP, or iPhone HEIC).');
    }

    const maxSize = 20 * 1024 * 1024; // 20MB
    if (file.size > maxSize) {
      throw new Error('File size exceeds the 20 MB limit.');
    }

    try {
      const { optimizedBlob, quality } = await this._resizeAndAnalyzeImage(file, 1600);
      const url = URL.createObjectURL(optimizedBlob);
      this.setPageImage(this.currentPage, optimizedBlob, url, file.name);
      return { blob: optimizedBlob, url, quality };
    } catch {
      const url = URL.createObjectURL(file);
      this.setPageImage(this.currentPage, file, url, file.name);
      return { blob: file, url, quality: { ok: true, isLowLight: false, isOverexposed: false, isBlurry: false } };
    }
  }

  _resizeAndAnalyzeImage(fileOrBlob, maxDim = 1600) {
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

        const quality = this.checkImageQuality(canvas);

        canvas.toBlob(blob => {
          resolve({ optimizedBlob: blob || fileOrBlob, quality });
        }, 'image/jpeg', 0.88);
      };
      img.onerror = () => {
        URL.revokeObjectURL(objUrl);
        resolve({ optimizedBlob: fileOrBlob, quality: { ok: true } });
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

  async loadActualFixtureSample() {
    throw new Error('Demo sample fixtures are disabled in production. Please capture or upload real clinic intake forms.');
  }

  async loadSyntheticSample() {
    throw new Error('Synthetic sample generation is disabled in production. Please capture or upload real clinic intake forms.');
  }
}

