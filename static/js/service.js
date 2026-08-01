Object.assign(App, {
    // ==================================================================
    //  I/O CONTROL — Stop / Resume all I/O (serial + BLE)
    // ==================================================================
    _ioPaused: false,
    _ioPollingTimer: null,
    termHistory: [],
    termHistoryIdx: -1,

    /** Called on Service tab switch — loads I/O state, service mode state, and GW settings */
    async refreshServiceState() {
        await this.ioRefreshState();
        await this._refreshSvcState();
        await this.loadGwSettings();
    },

    /** Quick-command buttons (HELP, HELP SET, etc.) */
    termCmd(cmd) {
        document.getElementById('term-in').value = cmd;
        this.sendServiceCmd();
    },

    /** Submit terminal command from input field */
    termSubmit() { this.sendServiceCmd(); },

    /** Toggle I/O pause/resume state */
    async ioToggle(btnEl) {
        if (this._ioPaused) {
            await this.ioResume(btnEl);
        } else {
            await this.ioPause(btnEl);
        }
    },

    /** Pause I/O forwarding */
    async ioPause(btnEl) {
        await this.withButton(btnEl, '⏸ Stop', async () => {
            const data = await this.api('/api/io/pause', 'POST');
            this._applyIoState(data);
            return { message: 'All I/O stopped' };
        });
    },

    /** Resume I/O forwarding */
    async ioResume(btnEl) {
        await this.withButton(btnEl, '▶ Resume', async () => {
            const data = await this.api('/api/io/resume', 'POST');
            this._applyIoState(data);
            return { message: 'All I/O resumed' };
        });
    },

    /** Refresh current I/O state from API */
    async ioRefreshState() {
        try {
            const data = await this.api('/api/io/state');
            this._applyIoState(data);
        } catch(e) {}
    },

    /** Start periodic IO state polling (every 5s) */
    startIoPolling() {
        this.ioRefreshState();
        if (this._ioPollingTimer) clearInterval(this._ioPollingTimer);
        this._ioPollingTimer = setInterval(() => this.ioRefreshState(), 5000);
    },

    /** Apply I/O state data to UI elements */
    _applyIoState(data) {
        this._ioPaused = data.paused;
        
        // Port name
        const portEl = document.getElementById('io-serial-port');
        if (portEl) portEl.textContent = data.port || '--';
        
        // Per-service states
        this._setIoServiceState('io-serial-state', data.serial);
        this._setIoServiceState('io-gobius-state', data.gobius);
        this._setIoServiceState('io-mopeka-state', data.mopeka);

        // Toggle button — stable, only set once
        const btn = document.getElementById('btn-io-toggle');
        if (btn) {
            if (this._ioPaused) {
                btn.textContent = '▶ Resume';
                btn.classList.remove('warn');
                btn.classList.add('accent');
            } else {
                btn.textContent = '⏸ Stop';
                btn.classList.remove('accent');
                btn.classList.add('warn');
            }
        }

        // Header status badge
        this.setOnline(!this._ioPaused);
    },

    /** Update service state badge in UI */
    _setIoServiceState(elId, state) {
        const el = document.getElementById(elId);
        if (!el) return;
        el.textContent = state || '--';
        el.className = '';
        if (state === 'STOPPED') el.className = 'val-red';
        else if (state === 'LISTENING' || state === 'CONNECTED' || state === 'SCANNING') el.className = 'val-green';
        else if (state === 'NO_DEVICE' || state === 'CONNECTING' || state === 'IDLE') el.className = 'val-amber';
    },

    // ==================================================================
    //  SERVICE — FILTERS
    // ==================================================================
    /** Load and display active CAN filters */
    async loadFilters(btnEl) {
        const box = document.getElementById('filters-box');
        box.innerHTML = '<span class="muted">Loading filters...</span>';
        await this.withButton(btnEl, 'Load Filters', async () => {
            const data = await this.api('/api/filters');
            let html = '';
            for (const [name, info] of Object.entries(data.filters)) {
                html += `<div class="filter-block">
                    <div class="filter-head" onclick="this.nextElementSibling.classList.toggle('open')">
                        <span>${name}</span>
                        <span>${info.records} rec · ${info.type}</span>
                    </div>
                    <div class="filter-body">${info.raw || 'empty'}</div>
                </div>`;
            }
            box.innerHTML = html || '<span class="muted">No filters</span>';
            return { message: 'OK' };
        });
    },

    // ==================================================================
    //  SERVICE — DIAGNOSTICS
    // ==================================================================
    /** Load diagnostics for selected scope */
    async loadDiag(btnEl) {
        const scope = document.getElementById('diag-scope').value;
        const out = document.getElementById('diag-out');
        out.textContent = 'Loading ' + scope + '...';
        await this.withButton(btnEl, 'Diagnostics', async () => {
            const data = await this.api('/api/diag/' + scope);
            out.textContent = data.data || 'empty';
            return { message: scope };
        });
    },

    // ==================================================================
    //  SERVICE — TERMINAL
    // ==================================================================
    /** Initialize terminal keyboard event handlers */
    initTermKey() {
        const inp = document.getElementById('term-in');
        if (!inp) return;
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (this.termHistoryIdx < this.termHistory.length - 1) {
                    this.termHistoryIdx++;
                    inp.value = this.termHistory[this.termHistory.length - 1 - this.termHistoryIdx];
                }
            } else if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (this.termHistoryIdx > 0) {
                    this.termHistoryIdx--;
                    inp.value = this.termHistory[this.termHistory.length - 1 - this.termHistoryIdx];
                } else {
                    this.termHistoryIdx = -1;
                    inp.value = '';
                }
            }
        });
    },

    /** Send raw command to service terminal */
    async sendServiceCmd() {
        const inp = document.getElementById('term-in');
        const out = document.getElementById('term-out');
        const cmd = inp.value.trim();
        if (!cmd) return;
        this.termHistory.push(cmd);
        this.termHistoryIdx = -1;
        out.textContent += `\n> ${cmd}\n`;
        inp.value = '';
        try {
            const data = await this.api('/api/service/cmd', 'POST', { cmd });
            out.textContent += data.response + '\n';
        } catch (e) {
            out.textContent += `Error: ${e.message}\n`;
        }
        out.scrollTop = out.scrollHeight;
    },

    /** Enter YDNU-02 service/firmware mode */
    async enterService(btnEl) {
        await this.withButton(btnEl, '🔌 Enter', async () => {
            const data = await this.api('/api/service/enter', 'POST');
            this._updateSvcState(data?.state);
            return { message: data?.state || 'OK' };
        });
    },

    /** Exit service mode and resume normal operation */
    async exitService(btnEl) {
        await this.withButton(btnEl, '⏏ Exit', async () => {
            const data = await this.api('/api/service/exit', 'POST');
            this._updateSvcState(data?.state);
            return { message: data?.state || 'OK' };
        });
    },

    /** Poll /api/service/state and sync badge */
    async _refreshSvcState() {
        try {
            const data = await this.api('/api/service/state');
            this._updateSvcState(data?.state);
        } catch (e) {}
    },

    /** Update the #svc-state badge text and color */
    _updateSvcState(state) {
        const el = document.getElementById('svc-state');
        if (!el) return;
        el.textContent = state || 'IDLE';
        el.className = state === 'SERVICE' ? 'val-green' : 'muted';
    },

    // ==================================================================
    //  GATEWAY SETTINGS — KI-001 NMEA ISO Replay workaround
    // ==================================================================

    /** Load current GatewaySettings from API and populate the UI controls. */
    async loadGwSettings() {
        try {
            const data = await this.api('/api/gw-settings');
            const cbIso  = document.getElementById('gw-iso-replay-enabled');
            const inpIso = document.getElementById('gw-iso-replay-interval');
            const cbSer  = document.getElementById('gw-serial-tx-enabled');
            const inpSer = document.getElementById('gw-serial-temp-interval');
            const inpTcp = document.getElementById('gw-tcp-temp-interval');
            const st     = document.getElementById('gw-settings-status');

            if (cbIso)  cbIso.checked  = !!data.ha_iso_replay_enabled;
            if (inpIso) inpIso.value   = data.ha_iso_replay_interval_s ?? 60;
            if (cbSer)  cbSer.checked  = data.n2k_serial_tx_enabled !== false;
            if (inpSer) inpSer.value   = data.n2k_serial_temp_interval_s ?? 5;
            if (inpTcp) inpTcp.value   = data.n2k_tcp_temp_interval_s ?? 3;

            if (st) {
                st.textContent = data.n2k_serial_tx_enabled
                    ? `Serial TX Active · Bus ${data.n2k_serial_temp_interval_s}s · TCP ${data.n2k_tcp_temp_interval_s}s`
                    : 'Serial TX Disabled';
                st.className = data.n2k_serial_tx_enabled ? 'val-green' : 'muted';
            }
        } catch (e) {
            console.warn('loadGwSettings failed:', e);
        }
    },

    /** Save GatewaySettings from UI controls via API. */
    async saveGwSettings(btnEl) {
        const cbIso  = document.getElementById('gw-iso-replay-enabled');
        const inpIso = document.getElementById('gw-iso-replay-interval');
        const cbSer  = document.getElementById('gw-serial-tx-enabled');
        const inpSer = document.getElementById('gw-serial-temp-interval');
        const inpTcp = document.getElementById('gw-tcp-temp-interval');

        const payload = {
            ha_iso_replay_enabled:      cbIso ? cbIso.checked : true,
            ha_iso_replay_interval_s:   inpIso ? (parseFloat(inpIso.value) || 60) : 60,
            n2k_serial_tx_enabled:      cbSer ? cbSer.checked : true,
            n2k_serial_temp_interval_s: inpSer ? (parseFloat(inpSer.value) || 5) : 5,
            n2k_tcp_temp_interval_s:    inpTcp ? (parseFloat(inpTcp.value) || 3) : 3,
        };

        await this.withButton(btnEl, 'Save Settings', async () => {
            const data = await this.api('/api/gw-settings', 'POST', payload);
            const st = document.getElementById('gw-settings-status');
            if (st) {
                st.textContent = data.n2k_serial_tx_enabled
                    ? `Serial TX Active · Bus ${data.n2k_serial_temp_interval_s}s · TCP ${data.n2k_tcp_temp_interval_s}s`
                    : 'Serial TX Disabled';
                st.className = data.n2k_serial_tx_enabled ? 'val-green' : 'muted';
            }
            return { message: 'Gateway Settings Saved' };
        });
    },
});
