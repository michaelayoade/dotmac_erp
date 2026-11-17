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
    test(`${moduleName.toUpperCase()} invoice Export posts applied filters for inline or queued download`, () => {
        const fetchCalls = [];
        const context = {
            URL,
            URLSearchParams,
            CustomEvent: class {},
            document: { querySelector: () => null },
            window: {
                location: {
                    origin: 'https://erp.example.com',
                    search: '?search=INV-123&status=POSTED&page=2',
                },
                dispatchEvent: () => {},
            },
            fetch: (...args) => {
                fetchCalls.push(args);
                return new Promise(() => {});
            },
        };
        vm.runInNewContext(source, context);

        context.window.exportAll(`/finance/${moduleName}/invoices/export`);

        assert.equal(fetchCalls.length, 1);
        const download = new URL(fetchCalls[0][0], context.window.location.origin);
        assert.equal(download.pathname, `/finance/${moduleName}/invoices/export`);
        assert.equal(download.searchParams.get('search'), 'INV-123');
        assert.equal(download.searchParams.get('status'), 'POSTED');
        assert.equal(download.searchParams.has('page'), false);
        assert.equal(fetchCalls[0][1].method, 'POST');
    });
}

test('small invoice export downloads the inline XLSX without polling', async () => {
    const messages = [];
    let downloads = 0;
    const context = {
        URL,
        URLSearchParams,
        CustomEvent: class {
            constructor(_name, options) { this.detail = options.detail; }
        },
        document: { querySelector: () => null },
        window: {
            location: { origin: 'https://erp.example.com', search: '' },
            dispatchEvent: (event) => messages.push(event.detail.message),
        },
        fetch: () => Promise.resolve({
            ok: true,
            headers: { get: () => 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
        }),
    };
    vm.runInNewContext(source, context);
    context.saveResponseAsDownload = () => {
        downloads += 1;
        return Promise.resolve();
    };
    context.pollBackgroundExport = () => assert.fail('Inline export must not poll');

    context.window.exportAll('/finance/ar/invoices/export');
    await new Promise(setImmediate);

    assert.equal(downloads, 1);
    assert(messages.some((message) => message.includes('downloaded successfully')));
});

test('large invoice export polls the queued job status', async () => {
    const polls = [];
    const context = {
        URL,
        URLSearchParams,
        CustomEvent: class {},
        document: { querySelector: () => null },
        window: {
            location: { origin: 'https://erp.example.com', search: '' },
            dispatchEvent: () => {},
        },
        fetch: () => Promise.resolve({
            ok: true,
            headers: { get: () => 'application/json' },
            json: () => Promise.resolve({ status_url: '/finance/ar/invoices/exports/job/status' }),
        }),
    };
    vm.runInNewContext(source, context);
    context.pollBackgroundExport = (...args) => polls.push(args);

    context.window.exportAll('/finance/ar/invoices/export');
    await new Promise(setImmediate);

    assert.equal(polls.length, 1);
    assert.equal(polls[0][0], '/finance/ar/invoices/exports/job/status');
});

test('HTML error shows a useful message without trying to parse JSON', async () => {
    const messages = [];
    const context = {
        URL,
        URLSearchParams,
        CustomEvent: class {
            constructor(_name, options) { this.detail = options.detail; }
        },
        console: { warn: () => {} },
        document: { querySelector: () => null },
        window: {
            location: { origin: 'https://erp.example.com', search: '' },
            dispatchEvent: (event) => messages.push(event.detail),
        },
        fetch: () => Promise.resolve({
            ok: false,
            status: 400,
            headers: { get: () => 'text/html' },
            json: () => assert.fail('HTML must not be parsed as JSON'),
        }),
    };
    vm.runInNewContext(source, context);

    context.window.exportAll('/finance/ap/invoices/export');
    await new Promise(setImmediate);

    assert(messages.some((event) => event.type === 'error' && event.message.includes('HTTP 400')));
});
