const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'auth_storage_component', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const messages = [];
const store = new Map();
const parent = {postMessage: message => messages.push(message)};
let receive;
const window = {parent, addEventListener: (_, handler) => { receive = handler; }};
vm.runInNewContext(script, {
  window,
  localStorage: {
    getItem: key => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, value),
    removeItem: key => store.delete(key),
  },
});
const render = (action, token) => {
  receive({source: parent, data: {type: 'streamlit:render', args: {action, token}}});
  return messages.at(-1).value;
};
assert.equal(messages[0].type, 'streamlit:componentReady');
assert.equal(render('read').token, null);
assert.equal(render('save', 'test-token').action, 'save');
assert.equal(store.get('data-monitor-platform.access-token'), 'test-token');
assert.equal(render('read').token, 'test-token');
assert.equal(render('clear').action, 'clear');
assert.equal(store.size, 0);
console.log('PASS: browser token save, restore and clear');
