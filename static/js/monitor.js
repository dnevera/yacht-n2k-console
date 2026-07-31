Object.assign(App, {
    // ==================================================================
    //  MONITOR
    // ==================================================================
    toggleMonitor() {
        if (this.isMonitoring) {
            this.stopMonitor();
        } else {
            this.startMonitor();
        }
    },

    startMonitor() {
        const btn = document.getElementById('btn-mon-toggle');
        const dot = document.getElementById('live-dot');
        const lbl = document.getElementById('live-label');

        this.wsMonitor = this.connectWs('/ws/monitor');
        this.wsMonitor.onopen = () => {
            this.wsMonitor.send(JSON.stringify({ duration: 300 }));
            this.isMonitoring = true;
            this.monitorCount = 0;
            this.monitorErrors = 0;
            this.monitorStart = Date.now();
            btn.textContent = '⏹ Stop';
            btn.classList.remove('accent');
            btn.classList.add('danger');
            dot.classList.add('on');
            lbl.textContent = 'LIVE';
        };
        this.wsMonitor.onmessage = (ev) => {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'frame') {
                this.monitorCount++;
                this.addMonitorLine(msg);
            } else if (msg.type === 'raw') {
                this.addMonitorRaw(msg.line);
            } else if (msg.type === 'error') {
                this.monitorErrors++;
                this.toast(msg.message, true);
            }
            this.updateMonitorStats();
        };
        this.wsMonitor.onclose = () => this.stopMonitor();
        this.wsMonitor.onerror = () => { this.monitorErrors++; this.updateMonitorStats(); };
    },

    stopMonitor() {
        if (this.wsMonitor) { this.wsMonitor.close(); this.wsMonitor = null; }
        this.isMonitoring = false;
        const btn = document.getElementById('btn-mon-toggle');
        const dot = document.getElementById('live-dot');
        const lbl = document.getElementById('live-label');
        btn.textContent = '▶ Start';
        btn.classList.add('accent');
        btn.classList.remove('danger');
        dot.classList.remove('on');
        lbl.textContent = 'IDLE';
    },

    addMonitorLine(msg) {
        const log = document.getElementById('monitor-log');
        const fPgn = document.getElementById('f-pgn').value;
        const fSrc = document.getElementById('f-src').value;
        const fTxt = document.getElementById('f-text').value.toLowerCase();

        // Apply filters
        if (fPgn && String(msg.pgn) !== fPgn) return;
        if (fSrc && String(msg.src) !== fSrc) return;
        const filterLine = `${msg.raw} ${msg.decoded || ''}`;
        if (fTxt && !filterLine.toLowerCase().includes(fTxt)) return;

        const el = document.createElement('div');
        el.className = 'log-line';
        if (msg.pgn === 127505) el.classList.add('pgn-127505');
        else if (msg.pgn === 60928) el.classList.add('pgn-60928');
        else if (msg.pgn === 126993) el.classList.add('pgn-126993');
        else if (msg.pgn === 126996) el.classList.add('pgn-126996');

        // Line 1: raw CAN frame (small font)
        const raw = document.createElement('div');
        raw.className = 'log-raw';
        raw.textContent = msg.raw;
        el.appendChild(raw);

        let isErrorFrame = false;

        // Line 2: parsed fields (large font + bold values)
        if (msg.decoded) {
            const dec = document.createElement('div');
            dec.className = 'log-decoded';

            // Clean up leading redundant PGN prefix if present in msg.decoded
            let decStr = msg.decoded.replace(/^\[PGN \d+ [^\]]+\]\s*(Src:\d+\s*)?/, '').trim();
            decStr = decStr.replace(/^PGN:\d+\s*\[[^\]]+\]\s*(Src:\d+\s*)?/, '').trim();

            let html = `  ↳ PGN:${msg.pgn} [${msg.pgn_name || ''}] Src:${msg.src}`;

            if (decStr) {
                const regex = /(?:^|\s+)([\w\s-]+?):([^\s:]+(?:\s+[^\s:]+)*(?=\s+[\w\s-]+?:|$))/g;
                let match;
                let hasPairs = false;

                while ((match = regex.exec(decStr)) !== null) {
                    hasPairs = true;
                    const key = match[1].trim();
                    const val = match[2].trim();
                    const isErr = /error|fault|fail|bus off/i.test(val);
                    if (isErr) isErrorFrame = true;
                    const valClass = isErr ? 'log-val log-val-error' : 'log-val';
                    html += ` ${key}:<strong class="${valClass}">${val}</strong>`;
                }

                if (!hasPairs && decStr) {
                    const isErr = /error|fault|fail|bus off/i.test(decStr);
                    if (isErr) isErrorFrame = true;
                    const valClass = isErr ? 'log-val log-val-error' : 'log-val';
                    html += ` <strong class="${valClass}">${decStr}</strong>`;
                }
            }

            dec.innerHTML = html;
            el.appendChild(dec);
        }

        if (isErrorFrame) {
            this.monitorErrors++;
            this.updateMonitorStats();
        }

        log.appendChild(el);

        // Trim to 500 lines
        while (log.children.length > 500) log.removeChild(log.firstChild);

        // Auto-scroll
        if (document.getElementById('f-scroll').checked) {
            requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
        }

        // PGN 127505 display is handled by Dashboard.refreshSensorsCard() via /api/sensors
    },

    addMonitorRaw(text) {
        const log = document.getElementById('monitor-log');
        const el = document.createElement('div');
        el.className = 'log-line log-raw';
        el.textContent = text;
        log.appendChild(el);
        while (log.children.length > 500) log.removeChild(log.firstChild);
        if (document.getElementById('f-scroll').checked) requestAnimationFrame(() => { log.scrollTop = log.scrollHeight; });
    },

    clearMonitor() {
        document.getElementById('monitor-log').innerHTML = '';
        this.monitorCount = 0;
        this.monitorErrors = 0;
        this.monitorStart = Date.now();
        this.updateMonitorStats();
    },

    updateMonitorStats() {
        document.getElementById('mon-count').textContent = this.monitorCount;
        document.getElementById('mon-errors').textContent = this.monitorErrors;
        const elapsed = Math.max(1, (Date.now() - this.monitorStart) / 1000);
        document.getElementById('mon-rate').textContent = (this.monitorCount / elapsed).toFixed(1);
    },

    // ==================================================================
    //  ERROR LOG MODAL & HISTORY API
    // ==================================================================
    openErrorLogModal() {
        const modal = document.getElementById('modal-error-log');
        if (modal) modal.style.display = 'block';
        this.fetchErrorLog();
    },

    closeErrorLogModal() {
        const modal = document.getElementById('modal-error-log');
        if (modal) modal.style.display = 'none';
    },

    async fetchErrorLog() {
        const summary = document.getElementById('err-log-summary');
        const list = document.getElementById('err-log-list');
        if (!list) return;

        if (summary) summary.textContent = 'Fetching error history...';

        try {
            const res = await this.api('/api/errors');
            const errors = res.errors || [];
            if (summary) summary.textContent = `${res.count || errors.length} total error event(s) recorded`;

            if (errors.length === 0) {
                list.innerHTML = '<div class="empty-state">No error events recorded yet.</div>';
                return;
            }

            let html = '';
            for (const err of errors) {
                const dateStr = err.timestamp ? new Date(err.timestamp * 1000).toLocaleTimeString() : (err.time_str || '');
                const fieldsHtml = (err.error_fields || []).map(f =>
                    `<span class="log-key">${f.key}:</span><strong class="log-val log-val-error">${f.val}</strong>`
                ).join(' ');

                html += `
                <div class="err-log-item">
                    <div class="err-log-header">
                        <span class="err-time">${dateStr}</span>
                        <span class="err-device">📍 <strong>${err.device_name || 'Src:' + err.src}</strong> (SA:${err.src})</span>
                        <span class="err-pgn">PGN ${err.pgn} [${err.pgn_name || ''}]</span>
                    </div>
                    <div class="err-log-detail">${fieldsHtml || err.decoded}</div>
                    <div class="log-raw err-raw">${err.raw}</div>
                </div>`;
            }
            list.innerHTML = html;
        } catch (e) {
            if (summary) summary.textContent = 'Failed to fetch error log: ' + e;
        }
    },

    async clearErrorLogHistory() {
        if (!confirm('Are you sure you want to clear the server-side error log history?')) return;
        try {
            await this.api('/api/errors', 'DELETE');
            this.monitorErrors = 0;
            this.updateMonitorStats();
            this.fetchErrorLog();
            this.toast('Error log history cleared');
        } catch (e) {
            this.toast('Failed to clear error log: ' + e, true);
        }
    }
});
