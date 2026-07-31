/**
 * YDNU-02 Web Console — Application Logic
 *
 * Clean separation: no inline handlers except simple App.method() calls.
 * All state managed in the App namespace.
 */

const App = {
    // ---- State ----
    infoLoading: false,
    wsMonitor: null,
    wsScan: null,
    isMonitoring: false,
    monitorCount: 0,
    monitorErrors: 0,
    monitorStart: 0,
    termHistory: [],
    termHistoryIdx: -1,
    _loadedTabs: {},  // Track which tabs have been lazy-loaded

    // ---- Init ----
    init() {
        this.initTabs();
        this.fetchAppVersion();
        // Eagerly load the default active tab (dashboard)
        this.loadTab('dashboard').then(() => {
            this.initTabActions();
            this.initTermKey();
            this.initDropZone();
            this.refreshInfo();
            this.startIoPolling();
            this.startSensorPolling();
            this.startMopekaPolling();
        });
    },

    async fetchAppVersion() {
        try {
            const data = await this.api('/api/version');
            if (data && data.version) {
                const el = document.getElementById('app-version');
                if (el) el.textContent = 'v' + data.version;
                document.title = `YDNU-02 NMEA 2000 Console v${data.version}`;
            }
        } catch (e) {}
    },

    /** Generate ➕ Add Sensor buttons for all tabs with data-sensor-type attribute */
    initTabActions() {
        document.querySelectorAll('.tab-actions[data-sensor-type]').forEach(el => {
            if (el.querySelector('.btn-add-sensor')) return; // already initialized
            const type = el.dataset.sensorType;
            const btn = document.createElement('button');
            btn.className = 'btn btn-add-sensor';
            btn.title = 'Add ' + type + ' sensor';
            btn.textContent = '➕ Add Sensor';
            btn.addEventListener('click', () => this.bleScanModal(type));
            el.appendChild(btn);
        });
    },

    // ==================================================================
    //  TABS — Lazy Loading
    // ==================================================================

    /** Lazy-load tab HTML from /static/tabs/{name}.html */
    async loadTab(name) {
        if (this._loadedTabs[name]) return;
        const section = document.getElementById('tab-' + name);
        if (!section) return;
        try {
            const resp = await fetch(`/static/tabs/${name}.html?v=1001`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            section.innerHTML = await resp.text();
            this._loadedTabs[name] = true;
        } catch (e) {
            console.error(`Failed to load tab ${name}:`, e);
            section.innerHTML = `<div class="card muted center">Failed to load tab content.</div>`;
        }
    },

    initTabs() {
        document.querySelectorAll('.tab').forEach(btn => {
            btn.addEventListener('click', async () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');

                const tabName = btn.dataset.tab;
                // Lazy-load tab HTML on first click
                await this.loadTab(tabName);

                const target = document.getElementById('tab-' + tabName);
                if (target) target.classList.add('active');

                // Re-init tab actions (e.g. Add Sensor buttons) after lazy load
                this.initTabActions();

                // Auto-load data on tab switch
                if (tabName === 'dashboard') {
                    this.refreshState();
                    this.startSensorPolling();
                } else {
                    this.stopSensorPolling();
                }
                if (tabName === 'gobius') {
                    this.startGobiusPolling();
                } else {
                    this.stopGobiusPolling();
                }
                if (tabName === 'service') this.refreshServiceState();
                if (tabName === 'mopeka') this.startMopekaPolling();
                if (tabName === 'maintenance') this.loadBackups();
            });
        });
    },

    // ==================================================================
    //  TOASTS
    // ==================================================================
    toast(msg, isErr = false) {
        const c = document.getElementById('toasts');
        const el = document.createElement('div');
        el.className = 'toast' + (isErr ? ' err' : '');
        el.textContent = (isErr ? '❌ ' : '✅ ') + msg;
        c.appendChild(el);
        setTimeout(() => el.remove(), 3500);
    },

    // ==================================================================
    //  STATUS LED
    // ==================================================================
    setOnline(ok) {
        const led = document.getElementById('status-led');
        const txt = document.getElementById('status-text');
        if (ok) {
            led.classList.add('on');
            txt.textContent = 'Connected';
        } else {
            led.classList.remove('on');
            txt.textContent = 'Disconnected';
        }
    },

    // ==================================================================
    //  API HELPERS
    // ==================================================================
    async api(url, method = 'GET', body = null) {
        const opts = { method };
        if (body) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), 30000);
        opts.signal = ctrl.signal;
        try {
            const res = await fetch(url, opts);
            clearTimeout(tid);
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || `HTTP ${res.status}`);
            }
            return data;
        } catch (e) {
            clearTimeout(tid);
            throw e;
        }
    },

    /**
     * Wraps any async action with: lock panel → spinner → try/catch → unlock.
     * @param {string|HTMLElement|null} btnRef  - button ID or element (also used to find parent .panel)
     * @param {string}                  label   - human label for toast messages
     * @param {Function}                fn      - async () => result
     */
    async withButton(btnRef, label, fn) {
        const btn = typeof btnRef === 'string' ? document.getElementById(btnRef) : btnRef;
        // Lock the entire panel
        const panel = btn ? btn.closest('.panel') : null;
        if (panel) panel.classList.add('busy');
        const origText = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.classList.add('loading');
            btn.textContent = label + '...';
        }
        this.toast(label + '...');
        try {
            const result = await fn();
            this.toast(label + ': ' + (result?.message || result?.response || 'OK'));
            if (btn) {
                btn.classList.add('result-ok');
                btn.textContent = '✅ ' + label;
                setTimeout(() => { btn.classList.remove('result-ok'); btn.textContent = origText; }, 1500);
            }
            return result;
        } catch (e) {
            this.toast(label + ' failed: ' + e.message, true);
            if (btn) {
                btn.classList.add('result-err');
                btn.textContent = '❌ Failed';
                setTimeout(() => { btn.classList.remove('result-err'); btn.textContent = origText; }, 2000);
            }
            return null;
        } finally {
            if (panel) panel.classList.remove('busy');
            if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
        }
    },

    async apiAction(url, label, body = null, btnEl = null) {
        return this.withButton(btnEl, label, () => this.api(url, 'POST', body));
    },

    // ==================================================================
    //  SHARED SENSOR HELPERS (used by gobius.js + mopeka.js)
    // ==================================================================

    /** Update read-only text fields by ID map. {elementId: displayValue} */
    setFields(map) {
        for (const [id, val] of Object.entries(map)) {
            const el = document.getElementById(id);
            if (el) el.textContent = val ?? '--';
        }
    },

    /** Load input/select values ONCE (flag prevents overwrite on refresh).
     *  map = {elementId: value}, flag = '_someFlag' on App */
    loadInputs(map, flag) {
        if (this[flag]) return;
        for (const [id, val] of Object.entries(map)) {
            const el = document.getElementById(id);
            if (!el) continue;
            if (el.type === 'checkbox') el.checked = !!val;
            else el.value = val ?? '';
        }
        this[flag] = true;
    },

    /** Read current input/select values by config map.
     *  map = {key: {id, type:'int'|'float'|'str'|'bool'}} → {key: value} */
    readInputs(map) {
        const result = {};
        for (const [key, cfg] of Object.entries(map)) {
            const el = document.getElementById(cfg.id);
            if (!el) continue;
            const t = cfg.type || 'str';
            if (t === 'int') result[key] = parseInt(el.value) || 0;
            else if (t === 'float') result[key] = parseFloat(el.value) || 0;
            else if (t === 'bool') result[key] = el.checked;
            else result[key] = el.value || '';
        }
        return result;
    },

    // ==================================================================
    //  BLE SENSOR MANAGEMENT (shared: gobius + mopeka)
    // ==================================================================

    _bleScanType: null,  // filter for modal ('gobius', 'mopeka', or null for all)

    bleScanModal(type) {
        this._bleScanType = type || null;
        // Create modal dynamically if not in DOM
        if (!document.getElementById('modal-ble-scan')) {
            const div = document.createElement('div');
            div.innerHTML = `
                <div class="modal-bg" id="modal-ble-scan">
                    <div class="modal" style="max-width:500px">
                        <h3>🔍 BLE Sensor Scan</h3>
                        <p class="muted" id="ble-scan-status">Press Scan to search for sensors...</p>
                        <div id="ble-scan-results"></div>
                        <div class="modal-btns">
                            <button class="btn" onclick="document.getElementById('modal-ble-scan').classList.remove('open')">Close</button>
                            <button class="btn accent" id="btn-ble-scan" onclick="App.bleScanStart(this)">📡 Scan</button>
                        </div>
                    </div>
                </div>`;
            document.body.appendChild(div.firstElementChild);
        }
        document.getElementById('ble-scan-results').innerHTML = '';
        document.getElementById('ble-scan-status').textContent = 'Press Scan to search for sensors...';
        document.getElementById('modal-ble-scan').classList.add('open');
    },

    async bleScanStart(btnEl) {
        await this.withButton(btnEl, 'Scanning', async () => {
            document.getElementById('ble-scan-status').textContent = 'Scanning BLE (10s)...';
            document.getElementById('ble-scan-results').innerHTML = '';
            const data = await this.api('/api/ble/scan?duration=10');
            const devices = data.devices || [];
            const filtered = this._bleScanType
                ? devices.filter(d => d.type === this._bleScanType)
                : devices;
            if (!filtered.length) {
                document.getElementById('ble-scan-status').textContent = 'No sensors found. Try again.';
                return { message: 'No sensors found' };
            }
            document.getElementById('ble-scan-status').textContent = `Found ${filtered.length} sensor(s):`;
            const box = document.getElementById('ble-scan-results');
            box.innerHTML = filtered.map(d => `
                <div class="ble-device ${d.registered ? 'registered' : ''}">
                    <div class="ble-info">
                        <div class="ble-name">${d.name || 'Unknown'}</div>
                        <div class="ble-mac">${d.mac} (${d.rssi} dBm)</div>
                    </div>
                    <span class="ble-type ${d.type}">${d.type}</span>
                    ${d.registered
                        ? '<span class="muted">✅ Added</span>'
                        : `<button class="btn accent" onclick="App.bleAddFromScan('${d.mac}','${d.type}','${(d.name||'').replace(/'/g,"\\'")}')">➕ Add</button>`
                    }
                </div>
            `).join('');
            return { message: `Found ${filtered.length}` };
        });
    },

    async bleAddFromScan(mac, type, name) {
        const body = { mac, type, name: name || (type + ' Sensor') };
        try {
            await this.api('/api/ble/sensors', 'POST', body);
            this.toast('✅ Sensor added: ' + mac);
            document.getElementById('modal-ble-scan').classList.remove('open');
            // Refresh current tab data without page reload
            this._refreshCurrentTab();
        } catch (e) {
            this.toast('❌ ' + (e.message || e), true);
        }
    },

    async bleRemoveSensor(mac) {
        if (!mac || mac === '--') {
            this.toast('No sensor selected', true);
            return;
        }
        if (!confirm('Remove sensor ' + mac + '?')) return;
        try {
            await this.api('/api/ble/sensors/' + mac, 'DELETE');
            this.toast('🗑 Sensor removed: ' + mac);
            this._refreshCurrentTab();
        } catch (e) {
            this.toast('❌ ' + (e.message || e), true);
        }
    },

    _refreshCurrentTab() {
        const active = document.querySelector('.tab.active');
        const tab = active ? active.dataset.tab : 'dashboard';
        if (tab === 'dashboard') this.refreshSensorCards();
        if (tab === 'gobius') this.loadGobius();
        if (tab === 'mopeka') this.loadMopekaSensors();
    },

    // ==================================================================
    //  SHARED UTILITIES (no duplication allowed!)
    // ==================================================================

    /** Create a WebSocket to the given path (e.g. '/ws/monitor').
     *  Handles protocol detection (ws:/wss:) in one place. */
    connectWs(path) {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return new WebSocket(proto + '//' + location.host + path);
    },

    /** Start an interval poll. Returns nothing — stores timer in App[timerProp].
     *  @param {string}   timerProp  - property name on App to store the interval ID
     *  @param {Function} callback   - async function to call
     *  @param {number}   ms         - interval in milliseconds */
    startPolling(timerProp, callback, ms) {
        if (this[timerProp]) return;
        callback.call(this);
        this[timerProp] = setInterval(() => callback.call(this), ms);
    },

    /** Stop a poll started with startPolling. */
    stopPolling(timerProp) {
        if (this[timerProp]) {
            clearInterval(this[timerProp]);
            this[timerProp] = null;
        }
    },

    /** Render an info-table from an array of [label, value, cssClass?] tuples.
     *  Shared by dashboard sensor cards, mopeka detail, gobius detail. */
    renderInfoTable(rows) {
        return '<table class="info-table">' +
            rows.map(([label, value, cls]) =>
                `<tr><td>${label}</td><td${cls ? ' class="' + cls + '"' : ''}>${value ?? '--'}</td></tr>`
            ).join('') +
            '</table>';
    },

    /** Escape string for use in HTML attributes */
    escAttr(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
};

// ---- Boot ----
document.addEventListener('DOMContentLoaded', () => App.init());
