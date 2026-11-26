/*
 * aether_openxr_layer.cpp - Lightweight Native OpenXR Implicit API Layer for Aether Desktop.
 *
 * Injects Aether's Dot / Mini / Normal / Max HUD directly into any D3D11 OpenXR game
 * (Meta Quest Link, Virtual Desktop VDXR, SteamVR OpenXR, WMR, Varjo, Pimax) by
 * appending a single XrCompositionLayerQuad during xrEndFrame.
 *
 * Reads pixels and 3D pose from Windows Shared Memory ("Local\AetherOpenXROverlayV1")
 * written by Aether's Python OpenXROverlayBackend. When Aether is not running or
 * heartbeat is stale (> 5 s), this layer is a complete no-op with ~0 overhead.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string.h>
#include <mutex>
#include <vector>
#include <unordered_map>

#include "openxr_abi.h"

namespace {

uint64_t NowEpochMs() {
    FILETIME ft{};
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER uli{};
    uli.LowPart = ft.dwLowDateTime;
    uli.HighPart = ft.dwHighDateTime;
    // Windows FILETIME (100ns since 1601) -> Unix epoch ms
    return (uli.QuadPart - 116444736000000000ULL) / 10000ULL;
}

struct InstanceState {
    XrInstance instance = XR_NULL_HANDLE;
    PFN_xrGetInstanceProcAddr nextGetProc = nullptr;
    PFN_xrDestroyInstance nextDestroyInstance = nullptr;
    PFN_xrCreateSession nextCreateSession = nullptr;
    PFN_xrDestroySession nextDestroySession = nullptr;
    PFN_xrCreateReferenceSpace nextCreateReferenceSpace = nullptr;
    PFN_xrDestroySpace nextDestroySpace = nullptr;
    PFN_xrLocateSpace nextLocateSpace = nullptr;
    PFN_xrCreateSwapchain nextCreateSwapchain = nullptr;
    PFN_xrDestroySwapchain nextDestroySwapchain = nullptr;
    PFN_xrEnumerateSwapchainImages nextEnumerateSwapchainImages = nullptr;
    PFN_xrAcquireSwapchainImage nextAcquireSwapchainImage = nullptr;
    PFN_xrWaitSwapchainImage nextWaitSwapchainImage = nullptr;
    PFN_xrReleaseSwapchainImage nextReleaseSwapchainImage = nullptr;
    PFN_xrEndFrame nextEndFrame = nullptr;
};

struct SessionState {
    XrSession session = XR_NULL_HANDLE;
    XrInstance instance = XR_NULL_HANDLE;
    ID3D11Device* d3dDevice = nullptr;
    ID3D11DeviceContext* d3dContext = nullptr;
    XrSpace viewSpace = XR_NULL_HANDLE;
    XrSpace localSpace = XR_NULL_HANDLE;
    XrSwapchain swapchain = XR_NULL_HANDLE;
    uint32_t scWidth = 0;
    uint32_t scHeight = 0;
    uint32_t lastUploadedSeq = 0;
    std::vector<XrSwapchainImageD3D11KHR> scImages;
};

std::mutex g_mutex;
std::unordered_map<XrInstance, InstanceState> g_instances;
std::unordered_map<XrSession, SessionState> g_sessions;

HANDLE g_shmHandle = nullptr;
uint8_t* g_shmView = nullptr;
uint64_t g_lastShmProbeMs = 0;

AetherVRSharedHeader* EnsureSharedMem() {
    if (g_shmView) {
        return reinterpret_cast<AetherVRSharedHeader*>(g_shmView);
    }
    uint64_t now = NowEpochMs();
    if (now - g_lastShmProbeMs < 500) {
        return nullptr;
    }
    g_lastShmProbeMs = now;
    g_shmHandle = OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, FALSE, AETHER_XR_SHM_NAME);
    if (!g_shmHandle) {
        return nullptr;
    }
    g_shmView = static_cast<uint8_t*>(
        MapViewOfFile(g_shmHandle, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, AETHER_XR_SHM_TOTAL_BYTES)
    );
    if (!g_shmView) {
        CloseHandle(g_shmHandle);
        g_shmHandle = nullptr;
        return nullptr;
    }
    return reinterpret_cast<AetherVRSharedHeader*>(g_shmView);
}

void CloseSharedMem() {
    if (g_shmView) {
        UnmapViewOfFile(g_shmView);
        g_shmView = nullptr;
    }
    if (g_shmHandle) {
        CloseHandle(g_shmHandle);
        g_shmHandle = nullptr;
    }
}

void DestroySessionOverlayResources(InstanceState& inst, SessionState& sess) {
    if (sess.swapchain && inst.nextDestroySwapchain) {
        inst.nextDestroySwapchain(sess.swapchain);
        sess.swapchain = XR_NULL_HANDLE;
    }
    sess.scImages.clear();
    sess.scWidth = 0;
    sess.scHeight = 0;
    sess.lastUploadedSeq = 0;

    if (sess.viewSpace && inst.nextDestroySpace) {
        inst.nextDestroySpace(sess.viewSpace);
        sess.viewSpace = XR_NULL_HANDLE;
    }
    if (sess.localSpace && inst.nextDestroySpace) {
        inst.nextDestroySpace(sess.localSpace);
        sess.localSpace = XR_NULL_HANDLE;
    }
    if (sess.d3dContext) {
        sess.d3dContext->Release();
        sess.d3dContext = nullptr;
    }
    if (sess.d3dDevice) {
        sess.d3dDevice->Release();
        sess.d3dDevice = nullptr;
    }
}

bool EnsureSpaces(InstanceState& inst, SessionState& sess) {
    if (!inst.nextCreateReferenceSpace) return false;
    XrPosef idPose{};
    idPose.orientation.w = 1.0f;

    if (!sess.viewSpace) {
        XrReferenceSpaceCreateInfo ci{};
        ci.type = XR_TYPE_REFERENCE_SPACE_CREATE_INFO;
        ci.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
        ci.poseInReferenceSpace = idPose;
        if (XR_FAILED(inst.nextCreateReferenceSpace(sess.session, &ci, &sess.viewSpace))) {
            sess.viewSpace = XR_NULL_HANDLE;
            return false;
        }
    }
    if (!sess.localSpace) {
        XrReferenceSpaceCreateInfo ci{};
        ci.type = XR_TYPE_REFERENCE_SPACE_CREATE_INFO;
        ci.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
        ci.poseInReferenceSpace = idPose;
        if (XR_FAILED(inst.nextCreateReferenceSpace(sess.session, &ci, &sess.localSpace))) {
            sess.localSpace = XR_NULL_HANDLE;
            return false;
        }
    }
    return true;
}

bool EnsureSwapchain(InstanceState& inst, SessionState& sess, uint32_t w, uint32_t h) {
    if (!sess.d3dDevice || !sess.d3dContext) return false;
    if (!inst.nextCreateSwapchain || !inst.nextEnumerateSwapchainImages) return false;
    if (w < 4 || h < 4 || w > AETHER_XR_MAX_WIDTH || h > AETHER_XR_MAX_HEIGHT) return false;

    if (sess.swapchain && sess.scWidth == w && sess.scHeight == h && !sess.scImages.empty()) {
        return true;
    }
    if (sess.swapchain && inst.nextDestroySwapchain) {
        inst.nextDestroySwapchain(sess.swapchain);
        sess.swapchain = XR_NULL_HANDLE;
        sess.scImages.clear();
        sess.scWidth = 0;
        sess.scHeight = 0;
        sess.lastUploadedSeq = 0;
    }

    XrSwapchainCreateInfo sci{};
    sci.type = XR_TYPE_SWAPCHAIN_CREATE_INFO;
    sci.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT | XR_SWAPCHAIN_USAGE_SAMPLED_BIT | XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT;
    sci.format = static_cast<int64_t>(DXGI_FORMAT_R8G8B8A8_UNORM);
    sci.sampleCount = 1;
    sci.width = w;
    sci.height = h;
    sci.faceCount = 1;
    sci.arraySize = 1;
    sci.mipCount = 1;

    if (XR_FAILED(inst.nextCreateSwapchain(sess.session, &sci, &sess.swapchain)) || !sess.swapchain) {
        sess.swapchain = XR_NULL_HANDLE;
        return false;
    }

    uint32_t count = 0;
    if (XR_FAILED(inst.nextEnumerateSwapchainImages(sess.swapchain, 0, &count, nullptr)) || count == 0) {
        inst.nextDestroySwapchain(sess.swapchain);
        sess.swapchain = XR_NULL_HANDLE;
        return false;
    }

    sess.scImages.assign(count, XrSwapchainImageD3D11KHR{});
    for (auto& img : sess.scImages) {
        img.type = XR_TYPE_SWAPCHAIN_IMAGE_D3D11_KHR;
        img.next = nullptr;
        img.texture = nullptr;
    }
    if (XR_FAILED(inst.nextEnumerateSwapchainImages(
            sess.swapchain,
            count,
            &count,
            reinterpret_cast<XrSwapchainImageBaseHeader*>(sess.scImages.data())))) {
        inst.nextDestroySwapchain(sess.swapchain);
        sess.swapchain = XR_NULL_HANDLE;
        sess.scImages.clear();
        return false;
    }

    sess.scWidth = w;
    sess.scHeight = h;
    sess.lastUploadedSeq = 0;
    return true;
}

XrResult XRAPI_CALL Layer_xrCreateSession(
    XrInstance instance,
    const XrSessionCreateInfo* createInfo,
    XrSession* session)
{
    PFN_xrCreateSession nextFn = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_instances.find(instance);
        if (it != g_instances.end()) nextFn = it->second.nextCreateSession;
    }
    if (!nextFn) return XR_ERROR_FUNCTION_UNSUPPORTED;

    XrResult res = nextFn(instance, createInfo, session);
    if (XR_SUCCEEDED(res) && session && *session && createInfo) {
        SessionState st{};
        st.session = *session;
        st.instance = instance;

        // Inspect next chain for D3D11 graphics binding
        const XrBaseInStructure* cur = static_cast<const XrBaseInStructure*>(createInfo->next);
        while (cur) {
            if (cur->type == XR_TYPE_GRAPHICS_BINDING_D3D11_KHR) {
                const auto* b = reinterpret_cast<const XrGraphicsBindingD3D11KHR*>(cur);
                if (b->device) {
                    st.d3dDevice = b->device;
                    st.d3dDevice->AddRef();
                    st.d3dDevice->GetImmediateContext(&st.d3dContext);
                }
                break;
            }
            cur = cur->next;
        }
        std::lock_guard<std::mutex> lock(g_mutex);
        g_sessions[*session] = std::move(st);
    }
    return res;
}

XrResult XRAPI_CALL Layer_xrDestroySession(XrSession session) {
    PFN_xrDestroySession nextFn = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto sit = g_sessions.find(session);
        if (sit != g_sessions.end()) {
            auto iit = g_instances.find(sit->second.instance);
            if (iit != g_instances.end()) {
                nextFn = iit->second.nextDestroySession;
                DestroySessionOverlayResources(iit->second, sit->second);
            }
            g_sessions.erase(sit);
        }
    }
    if (!nextFn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    return nextFn(session);
}

XrResult XRAPI_CALL Layer_xrDestroyInstance(XrInstance instance) {
    PFN_xrDestroyInstance nextFn = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_instances.find(instance);
        if (it != g_instances.end()) {
            nextFn = it->second.nextDestroyInstance;
            g_instances.erase(it);
        }
        if (g_instances.empty()) {
            CloseSharedMem();
        }
    }
    if (!nextFn) return XR_ERROR_FUNCTION_UNSUPPORTED;
    return nextFn(instance);
}

XrResult XRAPI_CALL Layer_xrEndFrame(XrSession session, const XrFrameEndInfo* frameEndInfo) {
    PFN_xrEndFrame nextEndFrame = nullptr;
    XrFrameEndInfo modifiedInfo{};
    std::vector<const XrCompositionLayerBaseHeader*> layersCopy;
    XrCompositionLayerQuad quadLayer{};
    bool injected = false;

    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto sit = g_sessions.find(session);
        if (sit != g_sessions.end()) {
            auto iit = g_instances.find(sit->second.instance);
            if (iit != g_instances.end()) {
                InstanceState& inst = iit->second;
                SessionState& sess = sit->second;
                nextEndFrame = inst.nextEndFrame;

                AetherVRSharedHeader* hdr = EnsureSharedMem();
                if (hdr && hdr->magic == AETHER_XR_SHM_MAGIC && hdr->version == AETHER_XR_SHM_VERSION && frameEndInfo) {
                    uint64_t now = NowEpochMs();
                    hdr->layer_heartbeat_ms = now;

                    if (EnsureSpaces(inst, sess) && inst.nextLocateSpace && frameEndInfo->displayTime > 0) {
                        XrSpaceLocation loc{};
                        loc.type = XR_TYPE_SPACE_LOCATION;
                        if (XR_SUCCEEDED(inst.nextLocateSpace(sess.viewSpace, sess.localSpace, frameEndInfo->displayTime, &loc))) {
                            const XrSpaceLocationFlags need = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT;
                            if ((loc.locationFlags & need) == need) {
                                hdr->hmd_pos[0] = loc.pose.position.x;
                                hdr->hmd_pos[1] = loc.pose.position.y;
                                hdr->hmd_pos[2] = loc.pose.position.z;
                                hdr->hmd_quat[0] = loc.pose.orientation.x;
                                hdr->hmd_quat[1] = loc.pose.orientation.y;
                                hdr->hmd_quat[2] = loc.pose.orientation.z;
                                hdr->hmd_quat[3] = loc.pose.orientation.w;
                                hdr->hmd_pose_valid = 1;
                            }
                        }
                    }

                    bool fresh = (hdr->heartbeat_ms > 0) && (now >= hdr->heartbeat_ms ? (now - hdr->heartbeat_ms < 5000ULL) : true);
                    if (hdr->visible && fresh && hdr->width >= 4 && hdr->height >= 4 &&
                        hdr->width <= AETHER_XR_MAX_WIDTH && hdr->height <= AETHER_XR_MAX_HEIGHT &&
                        EnsureSpaces(inst, sess) && EnsureSwapchain(inst, sess, hdr->width, hdr->height)) {

                        uint32_t seq = hdr->frame_seq;
                        if (seq != sess.lastUploadedSeq && inst.nextAcquireSwapchainImage &&
                            inst.nextWaitSwapchainImage && inst.nextReleaseSwapchainImage) {
                            uint32_t imgIdx = 0;
                            XrSwapchainImageAcquireInfo ai{ XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO, nullptr };
                            if (XR_SUCCEEDED(inst.nextAcquireSwapchainImage(sess.swapchain, &ai, &imgIdx)) &&
                                imgIdx < sess.scImages.size() && sess.scImages[imgIdx].texture) {
                                XrSwapchainImageWaitInfo wi{ XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO, nullptr, XR_INFINITE_DURATION };
                                if (XR_SUCCEEDED(inst.nextWaitSwapchainImage(sess.swapchain, &wi))) {
                                    const uint8_t* pixels = g_shmView + AETHER_XR_HEADER_SIZE;
                                    sess.d3dContext->UpdateSubresource(
                                        sess.scImages[imgIdx].texture,
                                        0,
                                        nullptr,
                                        pixels,
                                        hdr->width * 4u,
                                        0
                                    );
                                    sess.lastUploadedSeq = seq;
                                }
                                XrSwapchainImageReleaseInfo ri{ XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO, nullptr };
                                inst.nextReleaseSwapchainImage(sess.swapchain, &ri);
                            }
                        }

                        if (sess.lastUploadedSeq > 0) {
                            quadLayer.type = XR_TYPE_COMPOSITION_LAYER_QUAD;
                            quadLayer.next = nullptr;
                            quadLayer.layerFlags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
                            quadLayer.space = (hdr->anchor == 1) ? sess.viewSpace : sess.localSpace;
                            quadLayer.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
                            quadLayer.subImage.swapchain = sess.swapchain;
                            quadLayer.subImage.imageRect.offset = { 0, 0 };
                            quadLayer.subImage.imageRect.extent = { static_cast<int32_t>(hdr->width), static_cast<int32_t>(hdr->height) };
                            quadLayer.subImage.imageArrayIndex = 0;
                            quadLayer.pose.position = { hdr->pos[0], hdr->pos[1], hdr->pos[2] };
                            quadLayer.pose.orientation = { hdr->quat[0], hdr->quat[1], hdr->quat[2], hdr->quat[3] };
                            quadLayer.size = { hdr->width_m > 0.005f ? hdr->width_m : 0.4f, hdr->height_m > 0.005f ? hdr->height_m : 0.2f };

                            modifiedInfo = *frameEndInfo;
                            layersCopy.reserve(frameEndInfo->layerCount + 1);
                            for (uint32_t i = 0; i < frameEndInfo->layerCount; ++i) {
                                layersCopy.push_back(frameEndInfo->layers[i]);
                            }
                            layersCopy.push_back(reinterpret_cast<const XrCompositionLayerBaseHeader*>(&quadLayer));
                            modifiedInfo.layerCount = static_cast<uint32_t>(layersCopy.size());
                            modifiedInfo.layers = layersCopy.data();
                            injected = true;
                        }
                    }
                }
            }
        }
    }

    if (!nextEndFrame) return XR_ERROR_FUNCTION_UNSUPPORTED;
    return nextEndFrame(session, injected ? &modifiedInfo : frameEndInfo);
}

XrResult XRAPI_CALL Layer_xrGetInstanceProcAddr(
    XrInstance instance,
    const char* name,
    PFN_xrVoidFunction* function)
{
    if (!name || !function) return XR_ERROR_FUNCTION_UNSUPPORTED;
    if (strcmp(name, "xrGetInstanceProcAddr") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(Layer_xrGetInstanceProcAddr);
        return XR_SUCCESS;
    }
    if (strcmp(name, "xrDestroyInstance") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(Layer_xrDestroyInstance);
        return XR_SUCCESS;
    }
    if (strcmp(name, "xrCreateSession") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(Layer_xrCreateSession);
        return XR_SUCCESS;
    }
    if (strcmp(name, "xrDestroySession") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(Layer_xrDestroySession);
        return XR_SUCCESS;
    }
    if (strcmp(name, "xrEndFrame") == 0) {
        *function = reinterpret_cast<PFN_xrVoidFunction>(Layer_xrEndFrame);
        return XR_SUCCESS;
    }

    PFN_xrGetInstanceProcAddr nextGetProc = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        auto it = g_instances.find(instance);
        if (it != g_instances.end()) nextGetProc = it->second.nextGetProc;
    }
    if (!nextGetProc) return XR_ERROR_FUNCTION_UNSUPPORTED;
    return nextGetProc(instance, name, function);
}

XrResult XRAPI_CALL Layer_xrCreateApiLayerInstance(
    const XrInstanceCreateInfo* info,
    const XrApiLayerCreateInfo* apiLayerInfo,
    XrInstance* instance)
{
    if (!apiLayerInfo || !apiLayerInfo->nextInfo) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    PFN_xrGetInstanceProcAddr nextGetProc = apiLayerInfo->nextInfo->nextGetInstanceProcAddr;
    if (!nextGetProc) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }

    PFN_xrCreateApiLayerInstance nextCreate = nullptr;
    nextGetProc(XR_NULL_HANDLE, "xrCreateApiLayerInstance", reinterpret_cast<PFN_xrVoidFunction*>(&nextCreate));

    XrApiLayerCreateInfo nextLayerInfo = *apiLayerInfo;
    nextLayerInfo.nextInfo = apiLayerInfo->nextInfo->next;

    XrResult res = XR_ERROR_INITIALIZATION_FAILED;
    if (nextCreate) {
        res = nextCreate(info, &nextLayerInfo, instance);
    } else {
        typedef XrResult (XRAPI_PTR *PFN_xrCreateInstance)(const XrInstanceCreateInfo*, XrInstance*);
        PFN_xrCreateInstance nextCreateInst = nullptr;
        nextGetProc(XR_NULL_HANDLE, "xrCreateInstance", reinterpret_cast<PFN_xrVoidFunction*>(&nextCreateInst));
        if (nextCreateInst) {
            res = nextCreateInst(info, instance);
        }
    }

    if (XR_SUCCEEDED(res) && instance && *instance) {
        InstanceState st{};
        st.instance = *instance;
        st.nextGetProc = nextGetProc;
        auto load = [&](const char* fnName, auto& outPtr) {
            PFN_xrVoidFunction fn = nullptr;
            if (XR_SUCCEEDED(nextGetProc(*instance, fnName, &fn))) {
                outPtr = reinterpret_cast<std::remove_reference_t<decltype(outPtr)>>(fn);
            }
        };
        load("xrDestroyInstance", st.nextDestroyInstance);
        load("xrCreateSession", st.nextCreateSession);
        load("xrDestroySession", st.nextDestroySession);
        load("xrCreateReferenceSpace", st.nextCreateReferenceSpace);
        load("xrDestroySpace", st.nextDestroySpace);
        load("xrLocateSpace", st.nextLocateSpace);
        load("xrCreateSwapchain", st.nextCreateSwapchain);
        load("xrDestroySwapchain", st.nextDestroySwapchain);
        load("xrEnumerateSwapchainImages", st.nextEnumerateSwapchainImages);
        load("xrAcquireSwapchainImage", st.nextAcquireSwapchainImage);
        load("xrWaitSwapchainImage", st.nextWaitSwapchainImage);
        load("xrReleaseSwapchainImage", st.nextReleaseSwapchainImage);
        load("xrEndFrame", st.nextEndFrame);

        std::lock_guard<std::mutex> lock(g_mutex);
        g_instances[*instance] = st;
    }
    return res;
}

}  // namespace

extern "C" __declspec(dllexport) XrResult XRAPI_CALL xrNegotiateLoaderApiLayerInterface(
    const XrNegotiateLoaderInfo* loaderInfo,
    const char* /*layerName*/,
    XrNegotiateApiLayerRequest* apiLayerRequest)
{
    if (!loaderInfo || !apiLayerRequest) return XR_ERROR_INITIALIZATION_FAILED;
    if (loaderInfo->structType != XR_LOADER_INTERFACE_STRUCT_LOADER_INFO ||
        apiLayerRequest->structType != XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST) {
        return XR_ERROR_INITIALIZATION_FAILED;
    }
    apiLayerRequest->layerInterfaceVersion = XR_CURRENT_LOADER_API_LAYER_VERSION;
    apiLayerRequest->layerApiVersion = XR_CURRENT_API_VERSION;
    apiLayerRequest->getInstanceProcAddr = Layer_xrGetInstanceProcAddr;
    apiLayerRequest->createApiLayerInstance = Layer_xrCreateApiLayerInstance;
    return XR_SUCCESS;
}
