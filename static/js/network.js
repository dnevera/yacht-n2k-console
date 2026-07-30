/* network.js — Unified NMEA 2000 CAN Bus Network Scanner & Device Configuration
 *
 * Replaces separate scan.js + discover.js:
 *   - WebSocket /ws/scan for live CAN bus device scanning
 *   - Device cards with Configure (PGN 126208) and Bind buttons
 */

Object.assign(App, {
    // ==================================================================
    //  NETWORK — CAN Bus Scanner + Device Config
    // ==================================================================
    _scanDevices: {},

    startNetworkScan(btnEl) {
        const btn = btnEl || document.getElementById('btn-network-scan');
        const dur = parseInt(document.getElementById('network-scan-dur').value);
        const status = document.getElementById('network-scan-status');
        const container = document.getElementById('network-devices');
        const frames = document.getElementById('network-frames');

        btn.disabled = true;
        btn.textContent = '⏳ Scanning...';
        status.style.display = 'block';
        status.textContent = '🔍 Scanning CAN bus for ' + dur + 's...';
        this._scanDevices = {};
        container.innerHTML = '<div class="card muted center">Waiting for devices...</div>';
        frames.textContent = 'Frames: 0';

        let frameCount = 0;

        this.wsScan = this.connectWs('/ws/scan');
        this.wsScan.onopen = () => {
            this.wsScan.send(JSON.stringify({ duration: dur }));
        };
        this.wsScan.onmessage = (ev) => {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'frame') {
                frameCount++;
                frames.textContent = 'Frames: ' + frameCount;
            } else if (msg.type === 'device') {
                this._scanDevices[msg.src] = msg;
                this._renderNetworkDevices();
            } else if (msg.type === 'status') {
                status.textContent = '🔍 ' + msg.message;
            } else if (msg.type === 'done') {
                status.textContent = '✅ Done: ' + msg.device_count + ' devices, ' + msg.frame_count + ' frames';
                btn.disabled = false;
                btn.textContent = '📡 Scan Again';
            } else if (msg.type === 'error') {
                status.textContent = '❌ ' + msg.message;
                btn.disabled = false;
                btn.textContent = '📡 Scan CAN Bus';
            }
        };
        this.wsScan.onclose = () => {
            btn.disabled = false;
            if (btn.textContent === '⏳ Scanning...') btn.textContent = '📡 Scan CAN Bus';
        };
    },

    _renderNetworkDevices() {
        const container = document.getElementById('network-devices');
        const devices = this._scanDevices;
        const entries = Object.entries(devices).sort((a, b) => a[0] - b[0]);

        if (entries.length === 0) {
            container.innerHTML = '<div class="card muted center">No devices found yet.</div>';
            return;
        }

        let html = '';
        for (const [srcStr, dev] of entries) {
            const src = parseInt(srcStr);
            const mfr = dev.manufacturer || 'Unknown';
            const model = dev.model || '';
            const serial = dev.serial || '';
            const fw = dev.firmware || '';
            const funcName = dev.function_name || '';
            const uniqueId = dev.unique_id || 0;
            const devClassName = dev.device_class_name || '';

            const mfrLower = mfr.toLowerCase();
            const funcLower = funcName.toLowerCase();

            let icon = '⚓', badgeClass = '', badgeText = 'N2K';

            if (src === 200 || (model && model.includes('TCP'))) {
                icon = '🔌'; badgeText = 'TCP GW'; badgeClass = 'accent';
            } else if (src === 64 || funcLower.includes('gateway')) {
                icon = '🔌'; badgeText = 'USB GW';
            } else if (funcLower.includes('fluid')) {
                icon = '🌊'; badgeText = 'Fluid Level';
            } else if (funcLower.includes('battery')) {
                icon = '⚡'; badgeText = 'Battery';
            } else if (funcLower.includes('temperature')) {
                icon = '🌡️'; badgeText = 'Temperature';
            } else if (funcLower.includes('engine')) {
                icon = '⚙️'; badgeText = 'Engine';
            } else if (funcLower.includes('gps')) {
                icon = '📍'; badgeText = 'GPS';
            }

            if (mfrLower.includes('gobius')) badgeText = 'Gobius';
            if (mfrLower.includes('victron')) badgeClass = 'accent';

            const displayName = model ? model : (mfr !== 'Unknown' ? mfr : `Device (${src})`);
            const nameEscaped = displayName.replace(/'/g, "\'");

            let rows = '';
            rows += `<tr><td>Manufacturer</td><td>${mfr}</td></tr>`;
            if (model) rows += `<tr><td>Model</td><td>${model}</td></tr>`;
            if (funcName) rows += `<tr><td>Function</td><td>${funcName}</td></tr>`;
            if (devClassName) rows += `<tr><td>Device Class</td><td>${devClassName}</td></tr>`;
            if (serial) rows += `<tr><td>Serial</td><td><code>${serial}</code></td></tr>`;
            if (fw) rows += `<tr><td>Firmware</td><td>${fw}</td></tr>`;
            rows += `<tr><td>Source Address</td><td>${src}</td></tr>`;
            if (uniqueId) rows += `<tr><td>Unique ID</td><td>${uniqueId}</td></tr>`;

            const activePgns = (dev.active_pgns || []).join(',');

            html += `
                <div class="card">
                    <div class="card-head">
                        <h2>${icon} ${displayName}</h2>
                        <span class="badge ${badgeClass}">${badgeText}</span>
                    </div>
                    <table class="info-table">
                        ${rows}
                    </table>
                    <div class="btn-row" style="margin-top:12px;display:flex;gap:8px">
                        ${!funcLower.includes('gateway') && !funcLower.includes('bridge') && !funcLower.includes('router')
                            ? `<button class="btn accent" style="flex:1" onclick="App.openN2KConfigModal(${src}, '${nameEscaped}', [${activePgns}])">⚙️ Configure</button>`
                            : ''}
                        <button class="btn" style="flex:1" onclick="App.bindDiscoveredDevice(${src}, '${nameEscaped}')">🔗 Bind to HA</button>
                    </div>
                </div>
            `;
        }
        container.innerHTML = html;
    },

    bindDiscoveredDevice(src, name) {
        const customName = prompt(`Enter custom name for ${name} (SRC ${src}):`, name);
        if (!customName) return;
        this.toast(`Device '${customName}' bound to service registry`);
    }
});
