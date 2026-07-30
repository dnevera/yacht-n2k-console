Object.assign(App, {
    // ==================================================================
    //  DASHBOARD
    // ==================================================================
    // Lightweight state-only refresh (doesn't touch serial port)
    async refreshState() {
        try {
            const svc = await this.api('/api/service/state');
            this._updateStateUI(svc.state);
        } catch(e) { /* non-critical */ }
    },

    _updateStateUI(rawState) {
        const stateEl = document.getElementById('v-state');
        const toggleBtn = document.getElementById('btn-state-toggle');
        const state = (rawState || 'UNKNOWN').toUpperCase();
        stateEl.textContent = state;
        const colors = { IDLE: 'var(--green)', SERVICE: 'var(--orange)', MONITORING: 'var(--teal)' };
        stateEl.style.color = colors[state] || 'var(--red)';
        if (toggleBtn) {
            if (state === 'IDLE') {
                toggleBtn.textContent = '→ Service';
                toggleBtn.disabled = false;
            } else if (state === 'SERVICE') {
                toggleBtn.textContent = '→ Exit';
                toggleBtn.disabled = false;
            } else {
                toggleBtn.textContent = '⏳ Busy';
                toggleBtn.disabled = true;
            }
        }
    },

    async refreshInfo(btnEl) {
        if (this.infoLoading) return;
        // Don't hit serial when IO is paused (manual button press still works)
        if (this._ioPaused && !btnEl) return;
        this.infoLoading = true;
        const btn = btnEl || document.getElementById('btn-refresh');
        const ld = document.getElementById('info-loading');
        const panel = btn ? btn.closest('.panel') : null;
        if (panel) panel.classList.add('busy');
        const origText = btn ? btn.textContent : '';
        if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }
        if (ld) ld.style.display = 'block';

        try {
            const data = await this.api('/api/info?force=true');
            if (data.state === 'offline') throw new Error(data.error || 'offline');

            document.getElementById('v-fw').textContent = data.firmware_version || '--';
            document.getElementById('v-serial').textContent = data.serial_number || '--';
            document.getElementById('v-mode').textContent = data.previous_mode || '--';
            document.getElementById('v-silent').textContent = data.silent_mode || '--';
            document.getElementById('v-port').textContent = data.port || '--';
            if (ld) ld.style.display = 'none';
            this.setOnline(true);

            // Refresh state (after info, state is IDLE because _service_operation exits)
            this._updateStateUI('IDLE');

            // Highlight active mode button
            const mode = (data.previous_mode || '').toUpperCase();
            document.querySelectorAll('[onclick^="App.setMode"]').forEach(b => {
                b.classList.toggle('active', b.textContent.trim() === mode);
            });
            // Highlight active silent button
            const silent = (data.silent_mode || '').toUpperCase();
            document.querySelectorAll('[onclick^="App.setSilent"]').forEach(b => {
                b.classList.toggle('active', b.textContent.trim() === silent);
            });
        } catch (e) {
            this.setOnline(false);
            if (ld) { ld.style.display = 'block'; ld.textContent = '❌ Connection failed — ' + e.message; }
            console.error('refreshInfo:', e);
        } finally {
            this.infoLoading = false;
            if (panel) panel.classList.remove('busy');
            if (btn) { btn.disabled = false; btn.textContent = origText; }
        }
    },

    async setMode(mode, btnEl) {
        await this.withButton(btnEl, 'Mode → ' + mode.toUpperCase(), async () => {
            await this.api('/api/mode/' + mode, 'POST');
            this.refreshInfo();
            return { message: mode.toUpperCase() };
        });
    },

    async setSilent(state, btnEl) {
        await this.withButton(btnEl, 'Silent → ' + state.toUpperCase(), async () => {
            await this.api('/api/silent/' + state, 'POST');
            this.refreshInfo();
            return { message: state.toUpperCase() };
        });
    },

    resetMCU(btnEl) {
        this.apiAction('/api/reset/mcu', 'Reboot MCU', null, btnEl);
    },

    async toggleService(btnEl) {
        const svc = await this.api('/api/service/state');
        const state = (svc.state || '').toUpperCase();
        if (state === 'SERVICE') {
            await this.withButton(btnEl || 'btn-state-toggle', 'Exiting service', async () => {
                await this.api('/api/service/exit', 'POST');
                this.refreshInfo();
                return { message: 'IDLE' };
            });
        } else if (state === 'IDLE') {
            await this.withButton(btnEl || 'btn-state-toggle', 'Entering service', async () => {
                await this.api('/api/service/enter', 'POST');
                this.refreshInfo();
                return { message: 'SERVICE' };
            });
        } else {
            this.toast('Gateway busy: ' + state, true);
        }
    },

    // ==================================================================
    //  UNIFIED SENSOR CARDS (Dashboard)
    // ==================================================================
    _sensorInterval: null,

    startSensorPolling() {
        this.startPolling('_sensorInterval', this.refreshSensorCards, 2000);
    },

    stopSensorPolling() {
        this.stopPolling('_sensorInterval');
    },

    async refreshSensorCards() {
        const box = document.getElementById('sensor-cards');
        if (!box) return;
        try {
            const data = await this.api('/api/dashboard/sensors');
            const sensors = data.sensors || [];
            const emptyEl = document.getElementById('sensor-cards-empty');

            if (!sensors.length) {
                if (emptyEl) {
                    emptyEl.style.display = '';
                    emptyEl.textContent = '⚠️ No sensors registered. Use Gobius C or Mopeka tabs to add sensors.';
                }
                box.querySelectorAll('.sensor-card').forEach(c => c.remove());
                return;
            }

            if (emptyEl) emptyEl.style.display = 'none';

            sensors.forEach(s => {
                const cardId = 'sc-' + s.mac.replace(/:/g, '');
                let card = document.getElementById(cardId);
                if (!card) {
                    card = document.createElement('div');
                    card.id = cardId;
                    card.className = 'card sensor-card';
                    box.appendChild(card);
                }
                card.innerHTML = this._renderSensorCard(s);
            });

            const activeMacs = new Set(sensors.map(s => 'sc-' + s.mac.replace(/:/g, '')));
            box.querySelectorAll('.sensor-card').forEach(c => {
                if (!activeMacs.has(c.id)) c.remove();
            });
        } catch (e) {
            console.error('refreshSensorCards:', e);
        }
    },

    _renderSensorCard(s) {
        const icon = s.type === 'gobius' ? '🌊' : '💧';
        const statusClass = s.online ? 'badge-on' : 'badge-off';
        const statusText = s.online ? (s.age_sec != null ? s.age_sec.toFixed(0) + 's ago' : 'LIVE') : 'OFFLINE';

        let html = `
            <div class="card-head">
                <h2>${icon} ${s.name}</h2>
                <span class="badge ${statusClass}">${statusText}</span>
            </div>`;

        // Render each data channel as a section
        const channels = s.channels || [];
        channels.forEach(ch => {
            const isReg = ch.name === 'Registry';
            const chAge = isReg ? 'config' : (ch.live ? 'live' : (ch.age_sec != null ? ch.age_sec.toFixed(0) + 's ago' : 'last read'));
            const chIcon = ch.name === 'NMEA 2000' ? '📡' : (isReg ? '📋' : '📶');
            const liveClass = ch.live ? 'channel-live' : 'channel-stale';

            html += `<div class="channel-header ${liveClass}">
                <span>${chIcon} ${ch.name}</span>
                <span class="channel-age">${chAge}</span>
            </div>`;

            if (ch.fields && ch.fields.length) {
                const rows = ch.fields.map(f => [f[0], f[1] || '--', isReg ? undefined : (f[2] ? 'accent' : undefined)]);
                const tableHtml = this.renderInfoTable(rows);
                if (isReg) {
                    html += `<div class="registry-channel">${tableHtml}</div>`;
                } else {
                    html += tableHtml;
                }
            }
        });

        if (!channels.length) {
            html += `<div class="channel-header channel-stale"><span>⚠️ No data</span></div>`;
        }

        return html;
    },
});
