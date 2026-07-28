Object.assign(App, {
    // ==================================================================
    //  I/O CONTROL — Stop / Resume all I/O (serial + BLE)
    // ==================================================================
    _ioPaused: false,
    _ioPollingTimer: null,
    termHistory: [],
    termHistoryIdx: -1,

    /** Called on Service tab switch — loads I/O state */
    async refreshServiceState() {
        await this.ioRefreshState();
    },

    /** Quick-command buttons (HELP, HELP SET, etc.) */
    termCmd(cmd) {
        document.getElementById('term-in').value = cmd;
        this.sendServiceCmd();
    },

    /** Enter/Send button alias */
    termSubmit() { this.sendServiceCmd(); },

    async ioToggle(btnEl) {
        if (this._ioPaused) {
            await this.ioResume(btnEl);
        } else {
            await this.ioPause(btnEl);
        }
    },

    async ioPause(btnEl) {
        await this.withButton(btnEl, '⏸ Stop', async () => {
            const data = await this.api('/api/io/pause', 'POST');
            this._applyIoState(data);
            return { message: 'All I/O stopped' };
        });
    },

    async ioResume(btnEl) {
        await this.withButton(btnEl, '▶ Resume', async () => {
            const data = await this.api('/api/io/resume', 'POST');
            this._applyIoState(data);
            return { message: 'All I/O resumed' };
        });
    },

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

    async enterService(btnEl) {
        await this.withButton(btnEl, '🔌 Enter', async () => {
            return await this.api('/api/service/enter', 'POST');
        });
    },

    async exitService(btnEl) {
        await this.withButton(btnEl, '⏏ Exit', async () => {
            return await this.api('/api/service/exit', 'POST');
        });
    },
});
