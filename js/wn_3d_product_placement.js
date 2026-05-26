import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import * as THREE from "./vendor/three.module.js";
import { OBJLoader } from "./vendor/OBJLoader.js";

const EXT_NAME = "WepeNerd.3DProductPlacement";
const NODE_NAME = "WN_3DProductPlacement";
const LOAD_OBJ_NODE_NAME = "WN_LoadOBJ";

const PAD = 10;
const VIEW_H = 300;
const CLAY_COLOR = 0x94928c;
const DEFAULT_BUMP_SCALE = 0.03;

const DIFFUSE_TEXTURE_WIDGETS = ["diffuse_texture", "diffuse_image", "albedo_texture", "texture_image"];
const BUMP_TEXTURE_WIDGETS = ["bump_map", "bump_texture", "height_map", "displacement_map"];

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function getLinkedNode(node, inputName) {
    const input = node.inputs?.find((entry) => entry.name === inputName);
    if (!input?.link || !app.graph?.links) return null;

    const link = app.graph.links[input.link];
    if (!link) return null;

    return app.graph.getNodeById?.(link.origin_id) ?? null;
}

function getLinkedWidgetValue(node, inputName, widgetNames) {
    const source = getLinkedNode(node, inputName);
    if (!source) return null;

    for (const name of widgetNames) {
        const widget = getWidget(source, name);
        if (widget?.value !== undefined && widget?.value !== null && widget.value !== "") {
            return widget.value;
        }
    }

    return null;
}

function widgetValue(node, name, fallback = 0) {
    const value = Number(getWidget(node, name)?.value);
    return Number.isFinite(value) ? value : fallback;
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function setWidget(node, name, value) {
    const widget = getWidget(node, name);
    if (!widget) return;

    const options = widget.options ?? {};
    let next = Number(value);
    if (Number.isFinite(options.min)) next = Math.max(options.min, next);
    if (Number.isFinite(options.max)) next = Math.min(options.max, next);
    if (Number.isFinite(options.step) && options.step > 0) {
        const decimals = String(options.step).split(".")[1]?.length ?? 0;
        next = Number((Math.round(next / options.step) * options.step).toFixed(decimals));
    }

    if (widget.value !== next) {
        widget.value = next;
        widget.callback?.(next);
    }
}

function markDirty(node) {
    node.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
}

function setStringWidget(node, name, value) {
    const widget = getWidget(node, name);
    if (!widget) return;
    widget.value = value;
    widget.callback?.(value);
    markDirty(node);
}

function setHiddenWidget(node, name, value) {
    const widget = getWidget(node, name);
    if (widget) widget.value = value;
}

function getHiddenCaptureValue(node, name) {
    const state = initState(node);
    if (name === "viewport_capture") return state.viewportCaptureDataUrl || "";
    if (name === "viewport_mask_capture") return state.viewportMaskDataUrl || "";
    return "";
}

function hideWidget(node, name) {
    const widget = getWidget(node, name);
    if (!widget || widget._wnHidden) return;
    widget._wnHidden = true;
    widget.computeSize = () => [0, -4];
    widget.type = "hidden";
}

async function uploadObjFile(file) {
    if (!file) return null;
    if (!file.name?.toLowerCase().endsWith(".obj")) {
        throw new Error("Only .obj files can be uploaded.");
    }

    const formData = new FormData();
    formData.append("file", file, file.name);

    const response = await fetch(api.apiURL("/wepenerd/3d_product_placement/upload_obj"), {
        method: "POST",
        body: formData,
    });

    if (!response.ok) {
        throw new Error(await response.text());
    }

    return await response.json();
}

async function chooseObjFileForNode(node) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".obj";

    input.onchange = async () => {
        const file = input.files?.[0];
        if (!file) return;
        try {
            const result = await uploadObjFile(file);
            setStringWidget(node, "obj_path", result.path);
        } catch (error) {
            console.error("[WepeNerd] OBJ upload failed", error);
            alert(`OBJ upload failed: ${error.message}`);
        }
    };

    input.click();
}

async function uploadDroppedObj(node, event) {
    const file = [...(event?.dataTransfer?.files ?? [])].find((item) =>
        item.name?.toLowerCase().endsWith(".obj")
    );
    if (!file) return false;

    event.preventDefault?.();
    try {
        const result = await uploadObjFile(file);
        setStringWidget(node, "obj_path", result.path);
        return true;
    } catch (error) {
        console.error("[WepeNerd] OBJ drop upload failed", error);
        alert(`OBJ upload failed: ${error.message}`);
        return true;
    }
}

function installLoadObjUpload(node) {
    if (node._wnLoadObjUploadInstalled) return;
    node._wnLoadObjUploadInstalled = true;
    node.addWidget("button", "choose .obj to upload", null, () => chooseObjFileForNode(node));
}

function drawLoadObjDropHint(node, ctx) {
    const widgetCount = node.widgets?.length ?? 0;
    const top = Math.max(0, widgetCount * ((LiteGraph.NODE_WIDGET_HEIGHT || 20) + 4) + 32);
    const h = 46;
    const w = Math.max(120, node.size[0] - PAD * 2);

    if (node.size[1] < top + h + 10) node.size[1] = top + h + 10;

    ctx.save();
    ctx.fillStyle = "rgba(255,255,255,0.055)";
    ctx.strokeStyle = "rgba(255,255,255,0.22)";
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    if (ctx.roundRect) {
        ctx.roundRect(PAD, top, w, h, 6);
    } else {
        ctx.rect(PAD, top, w, h);
    }
    ctx.fill();
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255,255,255,0.72)";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Drop .obj file here", PAD + w / 2, top + h / 2);
    ctx.restore();
}

function canvasTop(node) {
    let y = 0;

    if (node.outputs?.length) {
        y += node.outputs.length * (LiteGraph.NODE_SLOT_HEIGHT || 20);
    }

    for (const widget of node.widgets ?? []) {
        const size = widget.computeSize
            ? widget.computeSize(node.size[0])
            : [node.size[0], LiteGraph.NODE_WIDGET_HEIGHT || 20];
        y += size[1] + 4;
    }

    return y + 10;
}

function viewportLayout(node) {
    const top = canvasTop(node);
    const areaW = Math.max(120, node.size[0] - PAD * 2);
    const state = initState(node);
    let areaH = VIEW_H;

    if (state.bgImage?.complete && state.bgImage.naturalWidth && state.bgImage.naturalHeight) {
        const aspect = state.bgImage.naturalHeight / state.bgImage.naturalWidth;
        areaH = clamp(areaW * aspect, 180, 520);
    }

    return { x: PAD, y: top, w: areaW, h: areaH };
}

function ensureNodeSize(node) {
    if (node.size[0] < 360) node.size[0] = 360;

    const layout = viewportLayout(node);
    const needed = layout.y + layout.h + 14;
    if (node.size[1] < needed) node.size[1] = needed;
}

function imageUrlFromValue(value) {
    if (!value) return null;

    value = String(value).trim();
    if (/^(data:|blob:|https?:\/\/|\/)/i.test(value)) {
        return value;
    }

    let filename = value;
    let type = "input";
    let subfolder = "";

    const annotated = value.match(/^(.*)\s+\[(input|output|temp)\]$/);
    if (annotated) {
        filename = annotated[1];
        type = annotated[2];
    }

    const parts = filename.replaceAll("\\", "/").split("/");
    if (parts.length > 1) {
        filename = parts.pop();
        subfolder = parts.join("/");
    }

    const params = new URLSearchParams({ filename, type });
    if (subfolder) params.set("subfolder", subfolder);
    return api.apiURL(`/view?${params}`);
}


function getImageInputValue(node, inputName, widgetFallbackNames = []) {
    const names = [inputName, ...widgetFallbackNames];

    for (const name of names) {
        const widget = getWidget(node, name);
        if (widget?.value !== undefined && widget?.value !== null && widget.value !== "") {
            return String(widget.value).trim();
        }
    }

    for (const name of names) {
        const value = getLinkedWidgetValue(node, name, names.concat(["image"]));
        if (value !== undefined && value !== null && value !== "") {
            return String(value).trim();
        }
    }

    return "";
}

function getDiffuseTextureValue(node) {
    return getImageInputValue(node, "diffuse_texture", DIFFUSE_TEXTURE_WIDGETS.filter((name) => name !== "diffuse_texture"));
}

function getBumpTextureValue(node) {
    return getImageInputValue(node, "bump_map", BUMP_TEXTURE_WIDGETS.filter((name) => name !== "bump_map"));
}

function setTextureColorSpace(texture, colorSpace) {
    if (!texture || !colorSpace) return;
    if ("colorSpace" in texture) {
        texture.colorSpace = colorSpace;
    } else if (colorSpace === THREE.SRGBColorSpace && THREE.sRGBEncoding) {
        texture.encoding = THREE.sRGBEncoding;
    }
}

function configureTexture(texture, role, state) {
    texture.wrapS = THREE.ClampToEdgeWrapping;
    texture.wrapT = THREE.ClampToEdgeWrapping;
    texture.flipY = true;
    texture.anisotropy = Math.min(state.renderer.capabilities.getMaxAnisotropy?.() || 1, 8);
    texture.needsUpdate = true;

    if (role === "diffuse") {
        setTextureColorSpace(texture, THREE.SRGBColorSpace);
    } else if ("colorSpace" in texture && THREE.NoColorSpace) {
        texture.colorSpace = THREE.NoColorSpace;
    }
}

function createSurfaceMaterial() {
    return new THREE.MeshStandardMaterial({
        color: CLAY_COLOR,
        roughness: 0.95,
        metalness: 0.0,
    });
}

function updateModelMaterials(node) {
    const state = initState(node);
    if (!state.model) return;

    const hasDiffuse = Boolean(state.diffuseTexture);
    const hasBump = Boolean(state.bumpTexture);
    const bumpScale = widgetValue(node, "bump_scale", DEFAULT_BUMP_SCALE);
    let hasUvs = false;

    state.model.traverse((child) => {
        if (!child.isMesh) return;

        hasUvs = hasUvs || Boolean(child.geometry?.attributes?.uv);
        if (!child.geometry?.attributes?.normal) {
            child.geometry?.computeVertexNormals?.();
        }

        let material = child.material;
        if (Array.isArray(material) || !material?.isMeshStandardMaterial) {
            const oldMaterials = Array.isArray(material) ? material : [material];
            material = createSurfaceMaterial();
            for (const oldMaterial of oldMaterials) {
                if (oldMaterial && oldMaterial !== material) oldMaterial.dispose?.();
            }
            child.material = material;
        }

        material.color.setHex(CLAY_COLOR);
        material.roughness = 0.95;
        material.metalness = 0.0;
        material.map = state.diffuseTexture || null;
        material.bumpMap = state.bumpTexture || null;
        material.bumpScale = bumpScale;
        material.normalMap = null;
        material.displacementMap = null;
        material.needsUpdate = true;

        child.castShadow = false;
        child.receiveShadow = false;
    });

    if ((hasDiffuse || hasBump) && !hasUvs && !state.warnedMissingUvs) {
        state.warnedMissingUvs = true;
        console.warn("[WepeNerd] OBJ has no UVs; texture placement may not work.");
    }
}

function applySurfaceMaterial(node) {
    updateModelMaterials(node);
}

function loadTextureForNode(node, state, role, value, previousValueKey, textureKey) {
    if (value === state[previousValueKey]) return;

    state[previousValueKey] = value;
    state.textureError = "";

    if (state[textureKey]) {
        state[textureKey].dispose?.();
        state[textureKey] = null;
    }

    const url = imageUrlFromValue(value);
    if (!url) {
        applySurfaceMaterial(node);
        stateCaptureDirty(node);
        markDirty(node);
        return;
    }

    state.loadingTexture = role;
    state.textureLoader.load(
        url,
        (texture) => {
            if (state[previousValueKey] !== value) {
                texture.dispose?.();
                return;
            }
            configureTexture(texture, role, state);
            state[textureKey] = texture;
            state.loadingTexture = "";
            applySurfaceMaterial(node);
            stateCaptureDirty(node);
            markDirty(node);
        },
        undefined,
        (error) => {
            if (state[previousValueKey] !== value) return;
            state.loadingTexture = "";
            state.textureError = "Texture load failed";
            console.warn(`[WepeNerd] Could not load ${role} texture`, error);
            applySurfaceMaterial(node);
            stateCaptureDirty(node);
            markDirty(node);
        }
    );
}

function loadSurfaceTextures(node) {
    const state = initState(node);
    loadTextureForNode(
        node,
        state,
        "diffuse",
        getDiffuseTextureValue(node),
        "diffuseValue",
        "diffuseTexture"
    );
    loadTextureForNode(
        node,
        state,
        "bump",
        getBumpTextureValue(node),
        "bumpValue",
        "bumpTexture"
    );
    applySurfaceMaterial(node);
}

function clearModel(state) {
    if (!state.model) return;

    state.scene.remove(state.model);
    if (state.wireframeModel) {
        state.scene.remove(state.wireframeModel);
    }
    state.model.traverse((child) => {
        child.geometry?.dispose?.();
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        for (const mat of materials) {
            mat?.dispose?.();
        }
    });
    state.wireframeModel?.traverse((child) => {
        child.geometry?.dispose?.();
        child.material?.dispose?.();
    });
    state.model = null;
    state.wireframeModel = null;
}

function disposeStateTextures(state) {
    state.diffuseTexture?.dispose?.();
    state.bumpTexture?.dispose?.();
    state.diffuseTexture = null;
    state.bumpTexture = null;
    state.diffuseValue = null;
    state.bumpValue = null;
}

function disposeNodeState(node) {
    const state = node._wn3dState;
    if (!state) return;

    clearModel(state);
    disposeStateTextures(state);
    state.renderer?.dispose?.();
    if (state.captureTimer) clearTimeout(state.captureTimer);
    node._wn3dState = null;
}

function applyPlacement(node) {
    const state = initState(node);
    if (!state.model) return;

    state.model.position.set(
        widgetValue(node, "x_offset", 0),
        -widgetValue(node, "y_offset", 0),
        widgetValue(node, "z_offset", 0)
    );
    state.model.rotation.set(
        THREE.MathUtils.degToRad(widgetValue(node, "rotate_x", 0)),
        THREE.MathUtils.degToRad(widgetValue(node, "rotate_y", 0)),
        THREE.MathUtils.degToRad(widgetValue(node, "rotate_z", 0))
    );
    const scale = widgetValue(node, "scale", 300);
    state.model.scale.setScalar(scale);
    if (state.wireframeModel) {
        state.wireframeModel.position.copy(state.model.position);
        state.wireframeModel.rotation.copy(state.model.rotation);
        state.wireframeModel.scale.copy(state.model.scale);
        state.wireframeModel.visible = Boolean(getWidget(node, "wireframe_overlay")?.value);
    }
}

function captureSignature(node, state) {
    const names = [
        "x_offset",
        "y_offset",
        "z_offset",
        "scale",
        "rotate_x",
        "rotate_y",
        "rotate_z",
        "camera_zoom",
        "light_yaw",
        "light_pitch",
        "light_intensity",
        "wireframe_overlay",
        "opacity",
        "bump_scale",
        "bump_strength",
        "bump_intensity",
    ];
    const values = names.map((name) => `${name}:${getWidget(node, name)?.value ?? ""}`);
    values.push(`bg:${state.bgValue ?? ""}`);
    values.push(`obj:${state.objValue ?? ""}`);
    values.push(`diffuse:${state.diffuseValue ?? ""}:${state.diffuseTexture?.uuid ?? ""}`);
    values.push(`bump:${state.bumpValue ?? ""}:${state.bumpTexture?.uuid ?? ""}`);
    values.push(`size:${state.bgImage?.naturalWidth ?? 0}x${state.bgImage?.naturalHeight ?? 0}`);
    return values.join("|");
}

function updateViewportCapture(node, force = false) {
    const state = initState(node);
    state.captureTimer = null;
    if (!state.bgImage?.complete || !state.model) {
        state.viewportCaptureDataUrl = "";
        state.viewportMaskDataUrl = "";
        setHiddenWidget(node, "viewport_capture", "");
        setHiddenWidget(node, "viewport_mask_capture", "");
        state.captureSignature = "";
        return;
    }

    const signature = captureSignature(node, state);
    if (!force && signature === state.captureSignature) return;
    state.captureSignature = signature;

    const width = state.bgImage.naturalWidth || 1;
    const height = state.bgImage.naturalHeight || 1;
    const previousOpacity = clamp(widgetValue(node, "opacity", 1), 0, 1);

    state.renderer.setSize(width, height, false);
    updateCamera(node, width, height);
    applyPlacement(node);
    syncWireframeVisibility(node);
    updateLight(node);
    loadSurfaceTextures(node);

    state.renderer.render(state.scene, state.camera);
    const objectDataUrl = state.renderer.domElement.toDataURL("image/png");

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const captureCtx = canvas.getContext("2d");
    captureCtx.drawImage(state.bgImage, 0, 0, width, height);
    captureCtx.globalAlpha = previousOpacity;
    captureCtx.drawImage(state.renderer.domElement, 0, 0, width, height);
    captureCtx.globalAlpha = 1;

    state.viewportCaptureDataUrl = canvas.toDataURL("image/png");
    state.viewportMaskDataUrl = objectDataUrl;
    setHiddenWidget(node, "viewport_capture", "");
    setHiddenWidget(node, "viewport_mask_capture", "");
}

function scheduleViewportCapture(node, delay = 350) {
    const state = initState(node);
    if (state.drag) return;

    if (state.captureTimer) {
        clearTimeout(state.captureTimer);
    }
    state.captureTimer = setTimeout(() => {
        updateViewportCapture(node);
    }, delay);
}

function updateCamera(node, width, height) {
    const state = initState(node);
    const zoom = clamp(widgetValue(node, "camera_zoom", 1), 0.05, 10);
    state.camera.left = -width / (2 * zoom);
    state.camera.right = width / (2 * zoom);
    state.camera.top = height / (2 * zoom);
    state.camera.bottom = -height / (2 * zoom);
    state.camera.near = 0.01;
    state.camera.far = 100000;
    state.camera.position.set(0, 0, Math.max(width, height, widgetValue(node, "scale", 300) * 8, 1000));
    state.camera.lookAt(0, 0, 0);
    state.camera.updateProjectionMatrix();
}

function updateLight(node) {
    const state = initState(node);
    const yaw = THREE.MathUtils.degToRad(widgetValue(node, "light_yaw", 45));
    const pitch = THREE.MathUtils.degToRad(widgetValue(node, "light_pitch", 45));
    const distance = Math.max(1000, widgetValue(node, "scale", 300) * 8);
    state.keyLight.position.set(
        Math.cos(pitch) * Math.sin(yaw) * distance,
        Math.sin(pitch) * distance,
        Math.cos(pitch) * Math.cos(yaw) * distance
    );
    state.keyLight.intensity = widgetValue(node, "light_intensity", 3);
}

function fitObjectToUnit(object) {
    const box = new THREE.Box3().setFromObject(object);
    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(center);

    const maxExtent = Math.max(size.x, size.y, size.z);
    if (maxExtent <= 0) return;

    object.position.sub(center);
    object.scale.setScalar(1 / maxExtent);
}

function loadBackground(node) {
    const state = initState(node);
    const value = getWidget(node, "background_image")?.value
        ?? getLinkedWidgetValue(node, "background_image", ["image", "background_image"])
        ?? "";
    if (value === state.bgValue) return;

    state.bgValue = value;
    state.bgImage = null;

    const url = imageUrlFromValue(value);
    if (!url) {
        markDirty(node);
        return;
    }

    const img = new Image();
    img.onload = () => {
        state.bgImage = img;
        ensureNodeSize(node);
        stateCaptureDirty(node);
        markDirty(node);
    };
    img.onerror = () => {
        state.bgError = "Could not load background preview";
        markDirty(node);
    };
    img.src = url;
}

function loadObj(node) {
    const state = initState(node);
    const value = String(
        getWidget(node, "obj_path")?.value
        ?? getLinkedWidgetValue(node, "obj_model", ["obj_path"])
        ?? ""
    ).trim();
    if (value === state.objValue || state.loadingObj === value) return;

    state.objValue = value;
    state.objError = "";
    clearModel(state);

    if (!value) {
        markDirty(node);
        return;
    }

    state.loadingObj = value;
    const url = api.apiURL(`/wepenerd/3d_product_placement/obj?path=${encodeURIComponent(value)}`);
    state.loader.load(
        url,
        (object) => {
            state.loadingObj = "";
            clearModel(state);

            fitObjectToUnit(object);
            state.model = object;
            state.warnedMissingUvs = false;
            updateModelMaterials(node);
            loadSurfaceTextures(node);
            state.scene.add(object);

            const wireframeGroup = new THREE.Group();
            const wireframeMaterial = new THREE.LineBasicMaterial({
                color: 0x111111,
                transparent: true,
                opacity: 0.9,
                depthTest: true,
                depthWrite: false,
            });
            object.traverse((child) => {
                if (!child.isMesh || !child.geometry) return;
                const edges = new THREE.EdgesGeometry(child.geometry, 25);
                const line = new THREE.LineSegments(edges, wireframeMaterial.clone());
                line.position.copy(child.position);
                line.rotation.copy(child.rotation);
                line.scale.copy(child.scale);
                wireframeGroup.add(line);
            });
            state.wireframeModel = wireframeGroup;
            state.scene.add(wireframeGroup);
            applyPlacement(node);
            stateCaptureDirty(node);
            markDirty(node);
        },
        undefined,
        (error) => {
            state.loadingObj = "";
            state.objError = error?.message || "Could not load OBJ preview";
            markDirty(node);
        }
    );
}

function installWidgetCallbacks(node) {
    if (node._wn3dCallbacksInstalled) return;
    node._wn3dCallbacksInstalled = true;
    hideWidget(node, "viewport_capture");
    hideWidget(node, "viewport_mask_capture");
    for (const name of ["viewport_capture", "viewport_mask_capture"]) {
        const hidden = getWidget(node, name);
        if (hidden && !hidden._wnSerializeCaptureInstalled) {
            hidden._wnSerializeCaptureInstalled = true;
            hidden.value = "";
            hidden.serializeValue = function () {
                return "";
            };
        }
    }

    for (const widget of node.widgets ?? []) {
        if (widget.name === "viewport_capture" || widget.name === "viewport_mask_capture") {
            continue;
        }
        const original = widget.callback;
        widget.callback = function (value) {
            original?.call(this, value);
            loadBackground(node);
            loadObj(node);
            loadSurfaceTextures(node);
            applyPlacement(node);
            stateCaptureDirty(node);
            updateLight(node);
            markDirty(node);
        };
    }
}

function stateCaptureDirty(node) {
    const state = initState(node);
    state.captureSignature = "";
    scheduleViewportCapture(node);
}

function installPromptCaptureHook() {
    if (app._wn3dPromptCaptureHookInstalled || typeof app.graphToPrompt !== "function") return;
    app._wn3dPromptCaptureHookInstalled = true;

    const originalGraphToPrompt = app.graphToPrompt.bind(app);
    app.graphToPrompt = async function (...args) {
        const patchedWidgets = [];
        const nodes = app.graph?._nodes ?? [];

        for (const node of nodes) {
            if (node.type !== NODE_NAME) continue;
            installWidgetCallbacks(node);
            loadBackground(node);
            loadObj(node);
            loadSurfaceTextures(node);
            applyPlacement(node);
            syncWireframeVisibility(node);
            updateLight(node);
            updateViewportCapture(node, true);

            for (const name of ["viewport_capture", "viewport_mask_capture"]) {
                const widget = getWidget(node, name);
                if (!widget) continue;
                patchedWidgets.push([widget, widget.serializeValue]);
                widget.serializeValue = () => getHiddenCaptureValue(node, name);
            }
        }

        try {
            return await originalGraphToPrompt(...args);
        } finally {
            for (const [widget, serializeValue] of patchedWidgets) {
                widget.serializeValue = serializeValue;
            }
        }
    };
}

function initState(node) {
    if (node._wn3dState) return node._wn3dState;

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-180, 180, 150, -150, 0.01, 100000);
    camera.position.set(0, 0, 1000);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);

    const ambient = new THREE.AmbientLight(0xffffff, 0.35);
    const keyLight = new THREE.DirectionalLight(0xffffff, 3);
    scene.add(ambient);
    scene.add(keyLight);

    node._wn3dState = {
        scene,
        camera,
        renderer,
        keyLight,
        loader: new OBJLoader(),
        textureLoader: new THREE.TextureLoader(),
        diffuseTexture: null,
        bumpTexture: null,
        diffuseValue: null,
        bumpValue: null,
        loadingTexture: "",
        textureError: "",
        viewportCaptureDataUrl: "",
        viewportMaskDataUrl: "",
        bgImage: null,
        bgValue: null,
        objValue: null,
        loadingObj: "",
        objError: "",
        bgError: "",
        warnedMissingUvs: false,
        model: null,
        wireframeModel: null,
        drag: null,
        startMouse: [0, 0],
        startValues: {},
        captureSignature: "",
        captureTimer: null,
    };

    return node._wn3dState;
}

function syncWireframeVisibility(node) {
    const state = initState(node);
    if (state.wireframeModel) {
        state.wireframeModel.visible = Boolean(getWidget(node, "wireframe_overlay")?.value);
    }
}

function drawStatus(ctx, layout, text) {
    ctx.fillStyle = "rgba(0,0,0,0.58)";
    ctx.fillRect(layout.x, layout.y + layout.h - 30, layout.w, 30);
    ctx.fillStyle = "rgba(255,255,255,0.86)";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, layout.x + layout.w / 2, layout.y + layout.h - 15);
}

function drawViewport(node, ctx) {
    const state = initState(node);
    loadBackground(node);
    loadObj(node);
    loadSurfaceTextures(node);
    applyPlacement(node);
    syncWireframeVisibility(node);
    updateLight(node);
    ensureNodeSize(node);

    const layout = viewportLayout(node);
    const renderW = Math.max(2, Math.round(layout.w));
    const renderH = Math.max(2, Math.round(layout.h));
    const worldW = state.bgImage?.naturalWidth || renderW;
    const worldH = state.bgImage?.naturalHeight || renderH;
    state.previewWorldW = worldW;
    state.previewWorldH = worldH;
    state.previewCanvasW = layout.w;
    state.previewCanvasH = layout.h;
    state.renderer.setSize(renderW, renderH, false);
    updateCamera(node, worldW, worldH);

    ctx.save();
    ctx.beginPath();
    ctx.rect(layout.x, layout.y, layout.w, layout.h);
    ctx.clip();

    ctx.fillStyle = "rgba(18,18,18,0.96)";
    ctx.fillRect(layout.x, layout.y, layout.w, layout.h);

    if (state.bgImage?.complete) {
        ctx.drawImage(state.bgImage, layout.x, layout.y, layout.w, layout.h);
    } else {
        ctx.fillStyle = "rgba(255,255,255,0.12)";
        ctx.font = "13px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("Connect Load Image", layout.x + layout.w / 2, layout.y + layout.h / 2);
    }

    state.renderer.render(state.scene, state.camera);
    ctx.globalAlpha = clamp(widgetValue(node, "opacity", 1), 0, 1);
    ctx.drawImage(state.renderer.domElement, layout.x, layout.y, layout.w, layout.h);
    ctx.globalAlpha = 1;

    ctx.strokeStyle = "rgba(255,255,255,0.28)";
    ctx.lineWidth = 1;
    ctx.strokeRect(layout.x + 0.5, layout.y + 0.5, layout.w - 1, layout.h - 1);

    if (state.loadingObj) {
        drawStatus(ctx, layout, "Loading OBJ...");
    } else if (state.objError) {
        drawStatus(ctx, layout, state.objError);
    } else if (!state.model) {
        drawStatus(ctx, layout, "Connect Load OBJ (WepeNerd)");
    } else if (state.loadingTexture) {
        drawStatus(ctx, layout, `Loading ${state.loadingTexture} texture...`);
    } else if (state.textureError) {
        drawStatus(ctx, layout, state.textureError);
    }

    ctx.restore();
}

function hitViewport(node, localPos) {
    if (!localPos) return false;
    const layout = viewportLayout(node);
    const [x, y] = localPos;
    if (x < layout.x || x > layout.x + layout.w || y < layout.y || y > layout.y + layout.h) {
        return false;
    }
    return true;
}

function resetPlacement(node) {
    setWidget(node, "x_offset", 0);
    setWidget(node, "y_offset", 0);
    setWidget(node, "z_offset", 0);
    setWidget(node, "scale", 300);
    setWidget(node, "rotate_x", 0);
    setWidget(node, "rotate_y", 0);
    setWidget(node, "rotate_z", 0);
    setWidget(node, "camera_zoom", 1);
    setWidget(node, "light_yaw", 45);
    setWidget(node, "light_pitch", 45);
    setWidget(node, "light_intensity", 3);
    setWidget(node, "opacity", 1);
    applyPlacement(node);
    updateLight(node);
    stateCaptureDirty(node);
    markDirty(node);
}

app.registerExtension({
    name: EXT_NAME,

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === LOAD_OBJ_NODE_NAME) {
            const origOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                origOnNodeCreated?.apply(this, arguments);
                installLoadObjUpload(this);
                markDirty(this);
            };

            const origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                origOnConfigure?.apply(this, arguments);
                requestAnimationFrame(() => {
                    installLoadObjUpload(this);
                    markDirty(this);
                });
            };

            const origDrawForeground = nodeType.prototype.onDrawForeground;
            nodeType.prototype.onDrawForeground = function (ctx) {
                origDrawForeground?.apply(this, arguments);
                drawLoadObjDropHint(this, ctx);
            };

            const origDragOver = nodeType.prototype.onDragOver;
            nodeType.prototype.onDragOver = function (event) {
                const hasObj = [...(event?.dataTransfer?.items ?? [])].some((item) =>
                    item.kind === "file" && (item.type === "" || item.type === "model/obj")
                ) || [...(event?.dataTransfer?.files ?? [])].some((file) =>
                    file.name?.toLowerCase().endsWith(".obj")
                );
                if (hasObj || event?.dataTransfer?.types?.includes?.("Files")) {
                    event.preventDefault?.();
                    return true;
                }
                return origDragOver?.apply(this, arguments);
            };

            const origDragDrop = nodeType.prototype.onDragDrop;
            nodeType.prototype.onDragDrop = async function (event) {
                const handled = await uploadDroppedObj(this, event);
                if (handled) return true;
                return origDragDrop?.apply(this, arguments);
            };

            return;
        }

        if (nodeData.name !== NODE_NAME) return;

        installPromptCaptureHook();

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            origOnNodeCreated?.apply(this, arguments);
            installPromptCaptureHook();
            initState(this);
            installWidgetCallbacks(this);
            ensureNodeSize(this);
            requestAnimationFrame(() => markDirty(this));
        };

        const origOnRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            disposeNodeState(this);
            return origOnRemoved?.apply(this, arguments);
        };

        const origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            origOnConfigure?.apply(this, arguments);
            installPromptCaptureHook();
            initState(this);
            requestAnimationFrame(() => {
                installWidgetCallbacks(this);
                loadBackground(this);
                loadObj(this);
                loadSurfaceTextures(this);
                ensureNodeSize(this);
                markDirty(this);
            });
        };

        const origDrawForeground = nodeType.prototype.onDrawForeground;
        nodeType.prototype.onDrawForeground = function (ctx) {
            origDrawForeground?.apply(this, arguments);
            installWidgetCallbacks(this);
            drawViewport(this, ctx);
        };

        const origMouseDown = nodeType.prototype.onMouseDown;
        nodeType.prototype.onMouseDown = function (e, localPos) {
            if (!hitViewport(this, localPos)) {
                return origMouseDown?.apply(this, arguments);
            }

            const state = initState(this);
            const mode = e?.shiftKey ? "move" : (e?.button === 2 || e?.altKey ? "light" : "rotate");
            state.drag = mode;
            state.startMouse = [localPos[0], localPos[1]];
            state.startValues = {
                x_offset: widgetValue(this, "x_offset", 0),
                y_offset: widgetValue(this, "y_offset", 0),
                rotate_x: widgetValue(this, "rotate_x", 0),
                rotate_y: widgetValue(this, "rotate_y", 0),
                light_yaw: widgetValue(this, "light_yaw", 45),
                light_pitch: widgetValue(this, "light_pitch", 45),
            };
            return true;
        };

        const origMouseMove = nodeType.prototype.onMouseMove;
        nodeType.prototype.onMouseMove = function (e, localPos) {
            const state = initState(this);
            if (!state.drag) return origMouseMove?.apply(this, arguments);

            const dx = localPos[0] - state.startMouse[0];
            const dy = localPos[1] - state.startMouse[1];
            const xScale = (state.previewWorldW || 1) / (state.previewCanvasW || 1);
            const yScale = (state.previewWorldH || 1) / (state.previewCanvasH || 1);

            if (state.drag === "move") {
                setWidget(this, "x_offset", state.startValues.x_offset + dx * xScale);
                setWidget(this, "y_offset", state.startValues.y_offset + dy * yScale);
            } else if (state.drag === "light") {
                setWidget(this, "light_yaw", state.startValues.light_yaw + dx * 0.5);
                setWidget(this, "light_pitch", clamp(state.startValues.light_pitch - dy * 0.5, -90, 90));
            } else {
                setWidget(this, "rotate_y", state.startValues.rotate_y + dx * 0.5);
                setWidget(this, "rotate_x", state.startValues.rotate_x + dy * 0.5);
            }

            applyPlacement(this);
            updateLight(this);
            markDirty(this);
            return true;
        };

        const origMouseUp = nodeType.prototype.onMouseUp;
        nodeType.prototype.onMouseUp = function () {
            const state = initState(this);
            if (state.drag) {
                state.drag = null;
                stateCaptureDirty(this);
                return true;
            }
            return origMouseUp?.apply(this, arguments);
        };

        const origMouseWheel = nodeType.prototype.onMouseWheel;
        nodeType.prototype.onMouseWheel = function (e, localPos) {
            if (!hitViewport(this, localPos)) {
                return origMouseWheel?.apply(this, arguments);
            }

            const delta = e?.deltaY ?? 0;
            const scale = widgetValue(this, "scale", 300);
            const factor = delta > 0 ? 0.94 : 1.06;
            setWidget(this, "scale", scale * factor);
            applyPlacement(this);
            stateCaptureDirty(this);
            markDirty(this);
            return true;
        };

        const origDblClick = nodeType.prototype.onDblClick;
        nodeType.prototype.onDblClick = function (e, localPos) {
            if (localPos && hitViewport(this, localPos)) {
                resetPlacement(this);
                return true;
            }
            return origDblClick?.apply(this, arguments);
        };
    },
});
