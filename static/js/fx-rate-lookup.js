/**
 * FX Rate Lookup — auto-fills exchange rate fields on currency change.
 *
 * Usage in Alpine.js x-data:
 *   @change="FXRateLookup.onCurrencyChange($data, 'currency_code', 'exchange_rate', 'invoice_date', '{{ default_currency_code }}')"
 *
 * The module caches (currency, date) pairs to avoid duplicate fetches.
 */
window.FXRateLookup = {
    /** @type {Object<string, string|null>} */
    _cache: {},

    /**
     * Build a cache key from currency + date.
     * @param {string} currency
     * @param {string} date
     * @returns {string}
     */
    _key(currency, date) {
        return `${currency}:${date}`;
    },

    /**
     * Fetch rate from the API. Returns the rate string or null.
     * @param {string} toCurrency - target currency code (e.g. "USD")
     * @param {string} [date]     - effective date YYYY-MM-DD (default: today)
     * @returns {Promise<{rate: string|null, inverse_rate?: string, effective_date?: string, source?: string}>}
     */
    async fetchRate(toCurrency, date) {
        const d = date || new Date().toISOString().split('T')[0];
        const key = this._key(toCurrency, d);

        if (key in this._cache) {
            return this._cache[key];
        }

        try {
            const params = new URLSearchParams({ to: toCurrency, date: d });
            const resp = await fetch(`/fx/rate?${params}`, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json' },
            });
            if (!resp.ok) {
                this._cache[key] = { rate: null };
                return this._cache[key];
            }
            const data = await resp.json();
            this._cache[key] = data;
            return data;
        } catch {
            return { rate: null };
        }
    },

    /**
     * Handle currency change on a form.
     *
     * @param {Object} formObj          - Alpine x-data object (e.g. $data)
     * @param {string} currencyField    - dot-path to currency field in form (e.g. 'form.currency_code')
     * @param {string} rateField        - dot-path to exchange_rate field (e.g. 'form.exchange_rate')
     * @param {string} dateField        - dot-path to date field (e.g. 'form.invoice_date')
     * @param {string} functionalCurrency - org's functional currency code
     */
    async onCurrencyChange(formObj, currencyField, rateField, dateField, functionalCurrency) {
        const currency = this._resolve(formObj, currencyField);
        const dateVal = this._resolve(formObj, dateField);

        if (!currency) return;

        // Same as functional currency → rate is 1
        if (currency.toUpperCase() === functionalCurrency.toUpperCase()) {
            this._set(formObj, rateField, '1');
            return;
        }

        const result = await this.fetchRate(currency, dateVal);
        if (result && result.rate) {
            this._set(formObj, rateField, result.rate);
        } else {
            // Fallback: keep current value or default to 1
            const current = this._resolve(formObj, rateField);
            if (!current || current === '' || current === '0') {
                this._set(formObj, rateField, '1');
            }
        }
    },

    /**
     * Resolve a dot-path on an object.  e.g. _resolve(obj, 'form.currency_code')
     * @param {Object} obj
     * @param {string} path
     * @returns {*}
     */
    _resolve(obj, path) {
        return path.split('.').reduce((o, k) => (o ? o[k] : undefined), obj);
    },

    /**
     * Set a value at a dot-path on an object.
     * @param {Object} obj
     * @param {string} path
     * @param {*} value
     */
    _set(obj, path, value) {
        const parts = path.split('.');
        let target = obj;
        for (let i = 0; i < parts.length - 1; i++) {
            target = target[parts[i]];
            if (!target) return;
        }
        target[parts[parts.length - 1]] = value;
    },
};
