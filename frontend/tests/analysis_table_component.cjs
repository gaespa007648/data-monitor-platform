const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "../analysis_table_component/index.html"), "utf8");
const script = html.split("<script>")[1]?.split("</script>")[0];
assert.ok(script);

function element(tag) {
  return {
    tag, children: [], style: {setProperty() {}}, scrollHeight: 100,
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(name, value) { this[name] = value; },
  };
}

const messages = [];
const handlers = {};
const root = element("root");
let nonce = 0;
const window = {
  parent: {postMessage(message) { messages.push(message); }},
  addEventListener(name, handler) { handlers[name] = handler; },
};
const document = {
  body: element("body"),
  documentElement: {style: {setProperty() {}}},
  getElementById() { return root; },
  createElement: element,
};
vm.runInNewContext(script, {
  window, document, crypto: {randomUUID: () => "event-" + (++nonce)},
  ResizeObserver: class { observe() {} },
});
assert.equal(messages[0].type, "streamlit:componentReady");
handlers.message({source: window.parent, data: {type: "streamlit:render", args: {
  dataset_id: 7, columns: ["id", "score"],
  rows: [{record_id: 11, can_delete: true, data: {id: 1, score: 2}},
         {record_id: 12, can_delete: false, data: {id: 2, score: 3}}],
  sort_by: null, sort_dir: "asc",
}}});
const table = root.children[0].children[0];
const headers = table.children[0].children[0].children;
headers[1].children[0].onclick();
assert.equal(messages.at(-1).value.action, "sort");
assert.equal(messages.at(-1).value.column, "score");
const rows = table.children[1].children;
assert.equal(rows[0].children[2].children[0].textContent, "刪除");
assert.equal(rows[1].children[2].children.length, 0);
rows[0].children[2].children[0].onclick();
assert.equal(messages.at(-1).value.action, "delete");
assert.equal(messages.at(-1).value.record_id, 11);
assert.notEqual(messages.at(-1).value.event_id, messages.at(-2).value.event_id);
console.log("PASS: table component handshake, sort and row delete events");
