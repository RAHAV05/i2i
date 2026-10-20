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

  async loadActualFixtureSample() {
    throw new Error('Demo sample fixtures are disabled in production. Please capture or upload real clinic intake forms.');
  }

  async loadSyntheticSample() {
    throw new Error('Synthetic sample generation is disabled in production. Please capture or upload real clinic intake forms.');
  }
}

