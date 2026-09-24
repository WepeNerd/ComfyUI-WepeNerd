import { executionId, imageAncestors, upstreamNodes } from "./mask_input.mjs";

export async function loadConnectedImage(node, app, api, signal) {
    const graph = app.rootGraph || app.graph;
    const id = executionId(node, graph);
    let { output: full } = await app.graphToPrompt();
    let { input, output } = imageAncestors(full, id);
    const ancestors = upstreamNodes(graph, output);
    for (const ancestor of ancestors) for (const widget of ancestor.widgets || []) await widget.beforeQueued?.({ isPartialExecution: true });
    ({ output: full } = await app.graphToPrompt());
    ({ input, output } = imageAncestors(full, id));
    if (graph !== (app.rootGraph || app.graph)) throw new Error("The workflow changed. Retry Load.");
    signal.throwIfAborted();
    const sink = `wn_speedpaint_snapshot_${crypto.randomUUID()}`;
    output[sink] = { class_type: "WN_MaskedLoraSnapshot", inputs: { image: input } };
    const queued = await api.queuePrompt(0, { output, workflow: { nodes: [], links: [] } });
    for (const ancestor of ancestors) for (const widget of ancestor.widgets || []) widget.afterQueued?.({ isPartialExecution: true });
    // History also covers jobs that finish before queuePrompt returns and reconnects.
    while (true) {
        signal.throwIfAborted();
        const response = await api.fetchApi(`/history/${encodeURIComponent(queued.prompt_id)}`, { signal });
        if (!response.ok) throw new Error("Could not read the upstream image run. Retry Load.");
        const result = (await response.json())[queued.prompt_id];
        if (result?.status?.status_str === "error") throw new Error("Upstream image run failed or was interrupted. Existing painting was kept.");
        const asset = result?.outputs?.[sink]?.images?.[0];
        if (asset) {
            const image = await api.fetchApi(`/view?${new URLSearchParams(asset)}`, { signal });
            if (!image.ok) throw new Error("Could not load the upstream image. Retry Load.");
            return image.blob();
        }
        if (result?.status?.completed) throw new Error("Upstream run returned no image. Existing painting was kept.");
        await new Promise((resolve, reject) => {
            const cancel = () => { clearTimeout(timer); reject(signal.reason); };
            const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, 500);
            signal.addEventListener("abort", cancel, { once: true });
        });
    }
}
