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

        // Line 1: timestamp + raw CAN frame
        const raw = document.createElement('div');
        raw.textContent = msg.raw;
        el.appendChild(raw);

        // Line 2: parsed fields (if available)
        if (msg.decoded) {
            const dec = document.createElement('div');
            dec.className = 'log-decoded';
            dec.textContent = `  ↳ PGN:${msg.pgn} [${msg.pgn_name}] Src:${msg.src} ${msg.decoded}`;
            el.appendChild(dec);
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
        el.className = 'log-line';
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
    }
});
