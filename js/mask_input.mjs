export function executionId(node, graph, prefix = "") {
    if (node.graph === graph) return prefix + node.id;
    for (const child of graph.nodes || graph._nodes || []) {
        if (!child.isSubgraphNode?.()) continue;
        const id = executionId(node, child.subgraph, `${prefix}${child.id}:`);
        if (id != null) return id;
    }
    return null;
}

export function imageAncestors(full, nodeId) {
    // Serialized inputs already resolve reroutes, bypassed nodes and subgraphs.
    const input = full[nodeId]?.inputs.image;
    if (!Array.isArray(input) || input.length !== 2) throw new Error("IMAGE source is unavailable. Check its connection and enabled state.");
    const output = {}, visiting = new Set();
    function visit(id) {
        id = String(id);
        if (id === String(nodeId) || visiting.has(id)) throw new Error("IMAGE creates a cycle through this node. Use a saved image.");
        if (output[id]) return;
        if (!full[id]) throw new Error("Upstream image source is unavailable.");
        visiting.add(id);
        for (const value of Object.values(full[id].inputs)) {
            if (Array.isArray(value) && value.length === 2 && full[String(value[0])]) visit(value[0]);
        }
        visiting.delete(id);
        output[id] = full[id];
    }
    visit(input[0]);
    return { input, output };
}

export function upstreamNodes(graph, output, prefix = "") {
    const result = [];
    for (const node of graph.nodes || graph._nodes || []) {
        const id = prefix + node.id;
        if (output[id]) result.push(node);
        if (node.isSubgraphNode?.()) result.push(...upstreamNodes(node.subgraph, output, `${id}:`));
    }
    return result;
}
