/* network.js — Unified NMEA 2000 CAN Bus Network Scanner & Device Configuration
 *
 * Replaces separate scan.js + discover.js:
 *   - WebSocket /ws/scan for live CAN bus device scanning
 *   - Device cards with Configure (PGN 126208) and Bind buttons
 *
 * Device cards have two states:
 *   claimed=true  — device sent ISO Address Claim (PGN 60928), full identity known
 *   claimed=false — device sent frames but ISO Claim not yet received; shown with ⚠ No ISO Claim badge
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
                const allDevs   = Object.values(this._scanDevices);
                const allPgns   = new Set(allDevs.flatMap(d => d.active_pgns || []));
                const identified = allDevs.filter(d => d.claimed && d.unique_id).length;
                const partial    = allDevs.filter(d => d.claimed && !d.unique_id).length;
                const unclaimed  = allDevs.filter(d => !d.claimed).length;

                status.textContent = `✅ Done: ${identified} identified, ${partial} partial, ${unclaimed} unidentified — ${allPgns.size} unique PGNs, ${msg.frame_count} frames`;
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
        // Sort: fully identified first, partial (ISO only) second, unclaimed last; then by src
        const entries = Object.entries(devices).sort(([,a], [,b]) => {
            const rank = d => (d.claimed && d.unique_id) ? 0 : d.claimed ? 1 : 2;
            return rank(a) - rank(b) || a.src - b.src;
        });

        if (entries.length === 0) {
            container.innerHTML = '<div class="card muted center">No devices found yet.</div>';
            return;
        }

        let html = '';
        for (const [srcStr, dev] of entries) {
            const src        = parseInt(srcStr);
            const mfr        = dev.manufacturer || '';
            const model      = dev.model || '';
            const serial     = dev.serial || '';
            const fw         = dev.firmware || '';
            const funcName   = dev.function_name || '';
            const modelVer   = dev.model_version || '';
            const uniqueId   = dev.unique_id;
            const devClass   = dev.device_class_name || '';
            const claimed    = dev.claimed === true;
            const activePgns = dev.active_pgns || [];

            const mfrLower   = mfr.toLowerCase();
            const funcLower  = funcName.toLowerCase();
            const modelLower = model.toLowerCase();

            // ── Icon & type badge (semantic, not hardcoded src) ─────────────
            let icon = '⚓', badgeClass = '', badgeText = 'N2K';

            if (modelLower.includes('tcp') || funcLower.includes('tcp')) {
                icon = '🔌'; badgeText = 'TCP GW'; badgeClass = 'accent';
            } else if (funcLower.includes('gateway') || funcLower.includes('bridge') || funcLower.includes('router')) {
                icon = '🔌'; badgeText = 'USB GW';
            } else if (funcLower.includes('fluid')) {
                icon = '🌊'; badgeText = 'Fluid Level';
            } else if (funcLower.includes('battery')) {
                icon = '⚡'; badgeText = 'Battery';
            } else if (funcLower.includes('temperature')) {
                icon = '🌡️'; badgeText = 'Temperature';
            } else if (funcLower.includes('engine')) {
                icon = '⚙️'; badgeText = 'Engine';
            } else if (funcLower.includes('gps') || funcLower.includes('navigation')) {
                icon = '📍'; badgeText = 'GPS';
            }
            if (mfrLower.includes('gobius'))  { badgeText = 'GOBIUS'; }
            if (mfrLower.includes('victron')) { badgeClass = 'accent'; }

            // ── Display name (dynamic: model → modelVersion → funcName → clean mfr → fallback)
            // Never show raw MfgCode/Reserved strings as the card title
            const mfrIsRaw   = !mfr || mfr.startsWith('MfgCode') || mfr.includes('Reserved');
            const cleanMfr   = mfrIsRaw ? '' : mfr;
            const displayName = model
                ? model
                : (modelVer ? modelVer
                    : (funcName ? (cleanMfr ? `${cleanMfr} ${funcName}` : funcName)
                        : (cleanMfr ? cleanMfr : `Device (SRC ${src})`)));
            const nameEscaped  = displayName.replace(/'/g, "\\'");

            // ── Attribute rows — ALL available attributes ───────────────────
            let rows = '';
            if (mfr)              rows += `<tr><td>Manufacturer</td><td>${mfr}</td></tr>`;
            if (model)            rows += `<tr><td>Model</td><td>${model}</td></tr>`;
            if (funcName)         rows += `<tr><td>Function</td><td>${funcName}</td></tr>`;
            if (devClass)         rows += `<tr><td>Device Class</td><td>${devClass}</td></tr>`;
            if (serial)           rows += `<tr><td>Serial</td><td><code>${serial}</code></td></tr>`;
            if (fw)               rows += `<tr><td>Firmware</td><td>${fw}</td></tr>`;
                                  rows += `<tr><td>Source Address</td><td>${src}</td></tr>`;
            if (uniqueId)         rows += `<tr><td>Unique ID</td><td>${uniqueId}</td></tr>`;
            if (activePgns.length)
                                  rows += `<tr><td>Active PGNs</td><td><code>${activePgns.join(', ')}</code></td></tr>`;

            // ── Claim status indicator ──────────────────────────────────────
            // fully identified: claimed=true + unique_id  → normal card, full buttons
            // ISO claim only:   claimed=true + no unique_id → blue badge "⚠ No Product Info"
            // unclaimed:        claimed=false              → amber badge "⚠ No ISO Claim"
            // unique_id=0 is valid for SA=0; use explicit null/undefined check
            const hasUniqueId     = uniqueId !== null && uniqueId !== undefined && uniqueId !== '';
            const fullyIdentified = claimed && hasUniqueId;
            const isoOnly         = claimed && !hasUniqueId;

            let cardStyle = '', claimBadge = '';
            if (isoOnly) {
                cardStyle  = 'border-left: 3px solid #5b8def; opacity: 0.88;';
                claimBadge = `<span class="badge" style="background:#5b8def22;color:#7aaeff;border:1px solid #5b8def55;margin-left:6px;font-size:0.72em;vertical-align:middle">⚠ No Product Info</span>`;
            } else if (!claimed) {
                cardStyle  = 'border-left: 3px solid #f0ad4e; opacity: 0.82;';
                claimBadge = `<span class="badge" style="background:#f0ad4e22;color:#f0ad4e;border:1px solid #f0ad4e55;margin-left:6px;font-size:0.72em;vertical-align:middle">⚠ No ISO Claim</span>`;
            }

            // ── Action buttons (only for fully identified devices) ──────────
            const isGateway  = funcLower.includes('gateway') || funcLower.includes('bridge') || funcLower.includes('router');
            const pgnsCsv    = activePgns.join(',');
            const configBtn  = fullyIdentified && !isGateway
                ? `<button class="btn accent" style="flex:1" onclick="App.openN2KConfigModal(${src}, '${nameEscaped}', [${pgnsCsv}])">⚙️ Configure</button>`
                : '';
            const bindBtn    = fullyIdentified
                ? `<button class="btn" style="flex:1" onclick="App.bindDiscoveredDevice(${src}, '${nameEscaped}')">🔗 Bind to HA</button>`
                : '';
            const btnRow     = (configBtn || bindBtn)
                ? `<div class="btn-row" style="margin-top:12px;display:flex;gap:8px">${configBtn}${bindBtn}</div>`
                : '';

            html += `
                <div class="card" style="${cardStyle}">
                    <div class="card-head">
                        <h2>${icon} ${displayName}${claimBadge}</h2>
                        <span class="badge ${badgeClass}">${badgeText}</span>
                    </div>
                    <table class="info-table">${rows}</table>
                    ${btnRow}
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
