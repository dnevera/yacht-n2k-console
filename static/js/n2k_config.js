/* n2k_config.js — Dynamic NMEA 2000 device configuration module.
 *
 * Fetches PGN field metadata from API, builds config forms dynamically.
 * Sends commands via POST /api/n2k/devices/{src}/config/{pgn}.
 * Reads current values via GET /api/n2k/devices/{src}/config/{pgn}.
 */

Object.assign(App, {
    // ==================================================================
    //  N2K CONFIG — Dynamic Form from API Metadata
    // ==================================================================

    /** Metadata cache: { pgn: { name, fields: [...] } } */
    _n2kMetaCache: {},

    /** Open N2K config modal for a specific device SRC */
    async openN2KConfigModal(src, deviceName, activePgns) {
        // Remove existing modal if any
        const existing = document.getElementById('modal-n2k-config');
        if (existing) existing.remove();

        // Build modal shell with loading state
        const modal = document.createElement('div');
        modal.className = 'modal-bg open';
        modal.id = 'modal-n2k-config';
        modal.innerHTML = `
            <div class="modal" style="max-width:560px">
                <h3>⚙️ Device Configuration</h3>
                <p class="muted">Configure <b>${deviceName || 'Device'}</b> (SRC ${src})</p>

                <div class="form-group" style="margin-top:12px">
                    <label>Target Source Address (SRC)</label>
                    <input type="number" id="n2k-cfg-src" class="input" value="${src}" readonly style="opacity:0.7">
                </div>

                <div class="form-group" style="margin-top:10px">
                    <label>Configuration PGN</label>
                    <select id="n2k-cfg-pgn" class="input" onchange="App._onPgnSelect()">
                        <option value="">Loading PGN list...</option>
                    </select>
                </div>

                <div id="n2k-cfg-fields" style="margin-top:8px">
                    <p class="muted">Select a PGN above to see configurable fields.</p>
                </div>

                <div id="n2k-cfg-status" style="margin-top:8px; display:none"></div>

                <div class="modal-btns" style="margin-top:16px">
                    <button class="btn" onclick="document.getElementById('modal-n2k-config').remove()">Cancel</button>
                    <button class="btn" id="btn-n2k-read" onclick="App.readN2KConfig(this)" style="display:none">📖 Read</button>
                    <button class="btn accent" id="btn-n2k-send" onclick="App.writeN2KConfig(this)" style="display:none">📡 Write</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Discover configurable PGNs
        await this._loadPgnList(src, activePgns || []);
    },

    /** Load PGN options into the select */
    async _loadPgnList(src, activePgns) {
        const select = document.getElementById('n2k-cfg-pgn');
        if (!select) return;

        // Get metadata for each active PGN to find configurable ones
        const configurablePgns = [];
        for (const pgn of activePgns) {
            try {
                const meta = await this._getPgnMetadata(pgn);
                if (meta && meta.fields && meta.fields.some(f => f.configurable)) {
                    configurablePgns.push({ pgn, name: meta.name, fields: meta.fields });
                }
            } catch (e) {
                // Skip PGNs that fail metadata lookup
            }
        }

        if (configurablePgns.length === 0) {
            select.innerHTML = '<option value="">No configurable PGNs found</option>';
            return;
        }

        select.innerHTML = configurablePgns.map(p =>
            `<option value="${p.pgn}">${p.pgn} — ${p.name}</option>`
        ).join('');

        // Auto-select first and render fields
        this._onPgnSelect();
    },

    /** Fetch and cache PGN metadata */
    async _getPgnMetadata(pgn) {
        if (this._n2kMetaCache[pgn]) return this._n2kMetaCache[pgn];

        const res = await fetch(`/api/n2k/pgn/${pgn}/metadata`);
        if (!res.ok) return null;

        const data = await res.json();
        this._n2kMetaCache[pgn] = data;
        return data;
    },

    /** Handle PGN selection change */
    async _onPgnSelect() {
        const select = document.getElementById('n2k-cfg-pgn');
        const container = document.getElementById('n2k-cfg-fields');
        const btnRead = document.getElementById('btn-n2k-read');
        const btnSend = document.getElementById('btn-n2k-send');
        if (!select || !container) return;

        const pgn = parseInt(select.value);
        if (isNaN(pgn)) {
            container.innerHTML = '<p class="muted">Select a PGN to see configurable fields.</p>';
            if (btnRead) btnRead.style.display = 'none';
            if (btnSend) btnSend.style.display = 'none';
            return;
        }

        const meta = await this._getPgnMetadata(pgn);
        if (!meta || !meta.fields) {
            container.innerHTML = '<p class="muted">No field metadata available for this PGN.</p>';
            return;
        }

        this._renderDynamicFields(meta.fields);
        if (btnRead) btnRead.style.display = '';
        if (btnSend) btnSend.style.display = '';
    },

    /** Render fields dynamically from API metadata */
    _renderDynamicFields(fields) {
        const container = document.getElementById('n2k-cfg-fields');
        if (!container) return;

        let html = '';
        for (const field of fields) {
            // Skip non-configurable and binary fields in the form
            if (field.type === 'binary') continue;

            const readOnly = !field.configurable;
            const inputId = `n2k-field-${field.id}`;

            html += `<div class="form-group" style="margin-top:10px">`;
            html += `<label>${field.name}`;
            if (field.unit) html += ` <small class="muted">(${field.unit})</small>`;
            if (readOnly) html += ` <small class="muted">🔒 read-only</small>`;
            html += `</label>`;

            if (field.type === 'lookup' && field.options) {
                // Render as <select>
                const disabled = readOnly ? 'disabled' : '';
                html += `<select id="${inputId}" class="input" ${disabled}>`;
                for (const [rawVal, label] of Object.entries(field.options)) {
                    html += `<option value="${rawVal}">${rawVal} — ${label}</option>`;
                }
                html += `</select>`;
            } else if (field.type === 'string') {
                const disabled = readOnly ? 'readonly' : '';
                html += `<input type="text" id="${inputId}" class="input" ${disabled}>`;
            } else {
                // Number (default)
                const disabled = readOnly ? 'readonly' : '';
                html += `<input type="number" id="${inputId}" class="input" ${disabled} placeholder="${field.name}">`;
            }

            html += `</div>`;
        }

        container.innerHTML = html || '<p class="muted">No configurable fields.</p>';
    },

    /** Fill form with current values from device */
    async readN2KConfig(btnEl) {
        const src = parseInt(document.getElementById('n2k-cfg-src')?.value);
        const pgn = parseInt(document.getElementById('n2k-cfg-pgn')?.value);
        if (isNaN(src) || isNaN(pgn)) return;

        await this.withButton(btnEl, 'Reading...', async () => {
            const res = await this.api(`/api/n2k/devices/${src}/config/${pgn}`);

            if (res.status === 'timeout') {
                this._showN2KStatus('⏱️ Device did not respond. Read Fields may not be supported.', 'warn');
                return res;
            }

            // Fill form fields with current values
            const fields = res.fields || {};
            for (const [fid, info] of Object.entries(fields)) {
                const el = document.getElementById(`n2k-field-${fid}`);
                if (el) {
                    const val = (typeof info === 'object') ? info.raw_value : info;
                    if (val !== null && val !== undefined) {
                        el.value = val;
                    }
                }
            }

            this._showN2KStatus('✅ Current values loaded from device.', 'ok');
            return res;
        });
    },

    /** Write config to device via API */
    async writeN2KConfig(btnEl) {
        const src = parseInt(document.getElementById('n2k-cfg-src')?.value);
        const pgn = parseInt(document.getElementById('n2k-cfg-pgn')?.value);
        if (isNaN(src) || isNaN(pgn)) return;

        const meta = await this._getPgnMetadata(pgn);
        if (!meta) return;

        // Collect only configurable field values
        const fields = {};
        for (const field of meta.fields) {
            if (!field.configurable) continue;
            const el = document.getElementById(`n2k-field-${field.id}`);
            if (el && el.value !== '') {
                fields[field.id] = field.type === 'number' ? parseFloat(el.value) : parseInt(el.value);
            }
        }

        if (Object.keys(fields).length === 0) {
            this._showN2KStatus('⚠️ No fields to write.', 'warn');
            return;
        }

        await this.withButton(btnEl, 'Writing...', async () => {
            const res = await this.api(`/api/n2k/devices/${src}/config/${pgn}`, 'POST', { fields });

            if (res.status === 'error') {
                this._showN2KStatus(`❌ ${res.message}`, 'error');
                return res;
            }

            // Show diff
            if (res.diff) {
                let diffHtml = '<table style="width:100%;margin-top:6px"><tr><th>Field</th><th>Old</th><th>New</th></tr>';
                for (const [fid, d] of Object.entries(res.diff)) {
                    const changed = d.old !== d.new;
                    const cls = changed ? 'style="color:var(--accent)"' : '';
                    diffHtml += `<tr><td>${fid}</td><td>${d.old ?? '—'}</td><td ${cls}>${d.new ?? '—'}</td></tr>`;
                }
                diffHtml += '</table>';
                this._showN2KStatus(
                    `${res.status === 'ok' ? '✅ Command acknowledged.' : '⚠️ Sent (no ACK).'} ${res.message || ''}${diffHtml}`,
                    res.status === 'ok' ? 'ok' : 'warn'
                );
            } else {
                this._showN2KStatus(`${res.status === 'ok' ? '✅' : '⚠️'} ${res.message || 'Done.'}`, res.status === 'ok' ? 'ok' : 'warn');
            }

            return res;
        });
    },

    /** Show status message in config modal */
    _showN2KStatus(html, level) {
        const el = document.getElementById('n2k-cfg-status');
        if (!el) return;
        el.style.display = '';
        const colors = { ok: 'var(--ok)', warn: '#f0ad4e', error: 'var(--danger)' };
        el.style.borderLeft = `3px solid ${colors[level] || '#888'}`;
        el.style.padding = '8px 12px';
        el.style.borderRadius = '4px';
        el.style.background = 'var(--bg-card)';
        el.innerHTML = html;
    },
});
