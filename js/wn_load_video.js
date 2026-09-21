import { app } from "../../scripts/app.js";

function autoplayPreview(node) {
    const observers = new Map();
    const players = new Set();
    function watch(container) {
        if (!container || observers.has(container)) return;
        const update = () => {
            for (const player of players) {
                if (!player.isConnected) {
                    player.pause();
                    players.delete(player);
                }
            }
            for (const player of container.querySelectorAll("video")) {
                if (players.has(player)) continue;
                players.add(player);
                player.autoplay = true;
                player.defaultMuted = true;
                player.muted = true;
                player.loop = true;
                player.playsInline = true;
                player.controls = true;
                player.play().catch(() => { /* Playback controls remain available if autoplay is blocked. */ });
            }
        };
        const observer = new MutationObserver(update);
        observer.observe(container, { childList: true, subtree: true });
        observers.set(container, observer);
        update();
    }
    const addDOMWidget = node.addDOMWidget;
    node.addDOMWidget = function (name, type, element, options) {
        const widget = addDOMWidget.apply(this, arguments);
        if (type === "video") {
            watch(element);
            const onRemove = widget.onRemove;
            widget.onRemove = function () {
                observers.get(element)?.disconnect();
                observers.delete(element);
                for (const player of element.querySelectorAll("video")) {
                    player.pause();
                    players.delete(player);
                }
                return onRemove?.apply(this, arguments);
            };
        }
        return widget;
    };
    watch(node.videoContainer);
    const onRemoved = node.onRemoved;
    node.onRemoved = function () {
        for (const observer of observers.values()) observer.disconnect();
        for (const player of players) player.pause();
        observers.clear();
        players.clear();
        return onRemoved?.apply(this, arguments);
    };
}

app.registerExtension({
    name: "WepeNerd.LoadVideo",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "WN_LoadVideo") return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            autoplayPreview(this);
            return result;
        };
    },
});
