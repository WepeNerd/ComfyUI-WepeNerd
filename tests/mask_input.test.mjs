import assert from "node:assert/strict";
import { test } from "node:test";
import { executionId, imageAncestors, upstreamNodes } from "../js/mask_input.mjs";

test("uses the serialized IMAGE source after reroute or bypass resolution", () => {
    const source = { class_type: "LoadImage", inputs: { image: "test.png" } };
    const full = { 1: source, 3: { inputs: { image: ["1", 0] } }, 4: { inputs: { mask: ["3", 0] } } };
    const result = imageAncestors(full, "3");
    assert.deepEqual(result.input, ["1", 0]);
    assert.deepEqual(result.output, { 1: source });
});

test("keeps the selected IMAGE output slot and all upstream dependencies", () => {
    const full = { 1: { inputs: {} }, 2: { inputs: { source: ["1", 0] } }, 3: { inputs: { image: ["2", 1] } } };
    const result = imageAncestors(full, "3");
    assert.deepEqual(result.input, ["2", 1]);
    assert.deepEqual(Object.keys(result.output), ["1", "2"]);
});

test("finds nested execution IDs without confusing equal local node IDs", () => {
    const nested = { nodes: [] };
    const root = { nodes: [{ id: 8, subgraph: nested, isSubgraphNode: () => true }] };
    const node = { id: 2, graph: nested };
    assert.equal(executionId(node, root), "8:2");
    const full = { 2: { inputs: {} }, "8:1": { inputs: {} }, "8:2": { inputs: { image: ["8:1", 0] } } };
    assert.deepEqual(Object.keys(imageAncestors(full, executionId(node, root)).output), ["8:1"]);
});

test("reports missing inputs and cycles without queueing downstream nodes", () => {
    assert.throws(() => imageAncestors({ 1: { inputs: {} } }, "1"), /source is unavailable/);
    const full = { 1: { inputs: { image: ["2", 0] } }, 2: { inputs: { image: ["1", 1] } } };
    assert.throws(() => imageAncestors(full, "1"), /cycle/);
});

test("selects queue widget hooks only for upstream execution nodes", () => {
    const source = { id: 1 }, downstream = { id: 3 };
    const nestedSource = { id: 1 };
    const group = { id: 8, isSubgraphNode: () => true, subgraph: { nodes: [nestedSource] } };
    const selected = upstreamNodes({ nodes: [source, downstream, group] }, { 1: {}, "8:1": {} });
    assert.deepEqual(selected, [source, nestedSource]);
});
