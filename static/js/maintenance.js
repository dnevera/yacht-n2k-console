Object.assign(App, {
    // ==================================================================
    //  MAINTENANCE — BACKUPS
    // ==================================================================
    async loadBackups() {
        const box = document.getElementById('backup-list');
        try {
            const data = await this.api('/api/backups');
            if (!data.backups || data.backups.length === 0) {
                box.innerHTML = '<span class="muted">No backups yet</span>';
                return;
            }
            box.innerHTML = data.backups.map(b =>
                `<div class="backup-item">
                    <span>${b.filename} <span class="muted">(${(b.size/1024).toFixed(1)} KB)</span></span>
                    <a href="/api/backup/download/${b.filename}" download>⬇ Download</a>
                </div>`
            ).join('');
        } catch (e) {
            box.innerHTML = '<span class="red">Error: ' + e.message + '</span>';
        }
    },

    async createBackup(btnEl) {
        await this.withButton(btnEl, 'Backup', async () => {
            const data = await this.api('/api/backup', 'POST');
            this.loadBackups();
            return data;
        });
    },

    // ==================================================================
    //  MAINTENANCE — RESET
    // ==================================================================
    resetHardware() {
        document.getElementById('reset-input').value = '';
        document.getElementById('modal-reset').classList.add('open');
    },

    closeModal() {
        document.getElementById('modal-reset').classList.remove('open');
    },

    confirmReset() {
        const v = document.getElementById('reset-input').value;
        if (v !== 'RESET') {
            this.toast("Type 'RESET' exactly", true);
            return;
        }
        this.closeModal();
        this.apiAction('/api/reset/hardware', 'Factory Reset', { confirm: 'RESET' });
    },

    // ==================================================================
    //  FIRMWARE
    // ==================================================================
    async checkFirmware(btnEl) {
        await this.withButton(btnEl, 'Check Firmware', async () => {
            const el = document.getElementById('v-fw-latest');
            const status = document.getElementById('fw-status');
            el.textContent = '⏳...';
            status.textContent = '';
            const [data, info] = await Promise.all([
                this.api('/api/firmware/latest'),
                this.api('/api/info')
            ]);
            const fwRaw = info.firmware_version || '--';
            const parts = fwRaw.split(' ');
            const installedVer = parts[0];
            const installedDate = parts[1] || '';
            document.getElementById('v-fw2').textContent = installedVer +
                (installedDate ? ' (' + installedDate + ')' : '');

            if (data.latest_version) {
                el.textContent = data.latest_version +
                    (data.release_date ? ' (' + data.release_date + ')' : '');
                if (installedVer === data.latest_version) {
                    status.textContent = '✅ Up to date';
                    status.style.color = 'var(--green)';
                } else {
                    let info2 = data.changelog ? ' — ' + data.changelog : '';
                    status.innerHTML = '⬆️ Update available' + info2 +
                        ' <button id="btn-fw-update" onclick="App.updateFirmware()" style="margin-left:8px;padding:4px 14px;' +
                        'background:var(--teal);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:0.85em;font-weight:600">' +
                        '⬆️ Update</button>';
                    status.style.color = 'var(--orange)';
                }
            } else {
                el.textContent = '?';
                status.innerHTML = data.message || 'Check <a href="' + data.url + '" target="_blank">yachtd.com</a>';
            }
            return { message: 'Checked' };
        });
    },

    async updateFirmware() {
        if (!confirm('Update firmware to latest version?\n\nThis will:\n1. Download firmware from yachtd.com\n2. Auto-backup current settings\n3. Flash new firmware\n\nDo NOT disconnect during flashing!')) return;
        const status = document.getElementById('fw-status');
        const btn = document.getElementById('btn-fw-update');
        if (btn) btn.disabled = true;

        const STAGES = {
            idle: '⏳ Preparing', starting: '⏳ Starting',
            downloading: '⬇️ Downloading', backup: '💾 Backup',
            flashing: '⚡ Flashing', done: '✅ Done'
        };

        function showProgress(stage, pct) {
            const label = STAGES[stage] || stage;
            status.innerHTML = `<div style="margin:8px 0">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:0.9em">
                    <span>${label}...</span><span>${pct}%</span>
                </div>
                <div style="background:rgba(255,255,255,0.1);border-radius:8px;height:10px;overflow:hidden">
                    <div style="background:var(--teal);height:100%;width:${pct}%;transition:width 0.3s;border-radius:8px"></div>
                </div>
            </div>`;
            status.style.color = 'var(--teal)';
        }

        let pollTimer = null;
        function startPolling() {
            pollTimer = setInterval(async () => {
                try {
                    const p = await fetch('/api/firmware/progress').then(r => r.json());
                    if (p.stage !== 'idle') showProgress(p.stage, p.percent);
                } catch(e) { /* ignore */ }
            }, 300);
        }
        function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

        try {
            // Step 1: Download ZIP from yachtd.com
            showProgress('downloading', 10);
            const dl = await this.api('/api/firmware/download', 'POST');
            if (!dl.files || !dl.files.length) throw new Error('No .BIN in download');
            const filename = dl.files[0].filename;
            showProgress('downloading', 100);

            // Brief pause so user sees download complete
            await new Promise(r => setTimeout(r, 500));

            // Step 2: Flash (backup + write) — poll progress
            showProgress('backup', 0);
            startPolling();
            const flash = await this.api('/api/firmware/flash/' + filename, 'POST');
            stopPolling();

            // Always show final success (even if polling missed it)
            showProgress('done', 100);
            await new Promise(r => setTimeout(r, 300));
            status.innerHTML = '✅ ' + (flash.message || 'Update complete!');
            status.style.color = 'var(--green)';
            this.toast('Firmware updated successfully!');

            // Refresh info after 5s (device reboots)
            setTimeout(() => this.refreshInfo && this.refreshInfo(), 5000);
        } catch (e) {
            stopPolling();
            status.innerHTML = '❌ Update failed: ' + e.message;
            status.style.color = 'var(--red)';
            this.toast('Update failed: ' + e.message, true);
            if (btn) btn.disabled = false;
        }
    },

    _uploadedFilename: null,

    async uploadFirmware(file) {
        this.toast('Uploading ' + file.name + '...');
        const form = new FormData();
        form.append('file', file);
        try {
            const res = await fetch('/api/firmware/upload', { method: 'POST', body: form });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Upload failed');
            this._uploadedFilename = data.filename;
            document.getElementById('fw-uploaded').style.display = 'flex';
            document.getElementById('fw-filename').textContent = '📁 ' + data.filename + ' (' + (data.size/1024).toFixed(1) + ' KB)';
            this.toast('Uploaded: ' + data.filename);
        } catch (e) {
            this.toast('Upload failed: ' + e.message, true);
        }
    },

    async flashFirmware(btnEl) {
        if (!this._uploadedFilename) {
            this.toast('Upload a .BIN file first', true);
            return;
        }
        if (!confirm('Flash firmware ' + this._uploadedFilename + '?\n\nAuto-backup will be created first.\nDo NOT disconnect during flashing!')) return;
        await this.withButton(btnEl, 'Flashing', async () => {
            const data = await this.api('/api/firmware/flash/' + this._uploadedFilename, 'POST');
            this._uploadedFilename = null;
            document.getElementById('fw-uploaded').style.display = 'none';
            return data;
        });
    },

    // ==================================================================
    //  FIRMWARE DROP ZONE
    // ==================================================================
    initDropZone() {
        const dz = document.getElementById('fw-drop');
        if (!dz) return;
        dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
        dz.addEventListener('dragleave', (e) => { e.preventDefault(); dz.classList.remove('over'); });
        dz.addEventListener('drop', (e) => {
            e.preventDefault();
            dz.classList.remove('over');
            const file = e.dataTransfer.files[0];
            if (file && file.name.toUpperCase().endsWith('.BIN')) {
                this.uploadFirmware(file);
            } else {
                this.toast('Please use a .BIN file', true);
            }
        });
        // Also support click to select
        dz.addEventListener('click', () => {
            const inp = document.createElement('input');
            inp.type = 'file';
            inp.accept = '.bin';
            inp.onchange = () => { if (inp.files[0]) this.uploadFirmware(inp.files[0]); };
            inp.click();
        });
    }
});
