const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../../static/js/bulk-actions.js'),
    'utf8'
);

for (const moduleName of ['ar', 'ap']) {
    test(`${moduleName.toUpperCase()} invoice Export uses applied filters and downloads directly`, () => {
        const destinations = [];
        const fetchCalls = [];
        const context = {
            URL,
            URLSearchParams,
            CustomEvent: class {},
            window: {
                location: {
                    origin: 'https://erp.example.com',
                    search: '?search=INV-123&status=POSTED&page=2',
                    assign: (url) => destinations.push(url),
                },
                dispatchEvent: () => {},
            },
            fetch: (...args) => fetchCalls.push(args),
        };
        vm.runInNewContext(source, context);

        context.window.exportAll(`/finance/${moduleName}/invoices/export`);

        assert.equal(destinations.length, 1);
        const download = new URL(destinations[0], context.window.location.origin);
        assert.equal(download.pathname, `/finance/${moduleName}/invoices/export`);
        assert.equal(download.searchParams.get('search'), 'INV-123');
        assert.equal(download.searchParams.get('status'), 'POSTED');
        assert.equal(download.searchParams.has('page'), false);
        assert.equal(fetchCalls.length, 0);
    });
}
