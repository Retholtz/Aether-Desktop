#pragma once
/*
 * Minimal Khronos OpenXR 1.0 ABI & Loader Layer Interface definitions (x64 Windows).
 * Self-contained so aether_openxr_layer.dll compiles with zero external SDK downloads.
 */

#include <stdint.h>
#include <stddef.h>
#include <d3d11.h>

#ifdef __cplusplus
extern "C" {
#endif

#define XRAPI_ATTR
#define XRAPI_CALL __stdcall
#define XRAPI_PTR XRAPI_CALL

typedef uint64_t XrVersion;
typedef uint64_t XrFlags64;
typedef uint64_t XrSystemId;
typedef uint32_t XrBool32;
typedef int64_t  XrTime;
typedef int64_t  XrDuration;
typedef int32_t  XrResult;

typedef void* XrInstance;
typedef void* XrSession;
typedef void* XrSpace;
typedef void* XrSwapchain;

#define XR_NULL_HANDLE nullptr
#define XR_TRUE  1
#define XR_FALSE 0

#define XR_SUCCEEDED(result) ((result) >= 0)
#define XR_FAILED(result)    ((result) < 0)

#define XR_MAKE_VERSION(major, minor, patch) \
    ((((major) & 0xffffULL) << 48) | (((minor) & 0xffffULL) << 32) | ((patch) & 0xffffffffULL))
#define XR_CURRENT_API_VERSION XR_MAKE_VERSION(1, 0, 34)

#define XR_SUCCESS 0
#define XR_ERROR_FUNCTION_UNSUPPORTED -7
#define XR_ERROR_INITIALIZATION_FAILED -2

#define XR_MAX_API_LAYER_NAME_SIZE 256
#define XR_INFINITE_DURATION 0x7fffffffffffffffLL

typedef void (XRAPI_PTR *PFN_xrVoidFunction)(void);

typedef int32_t XrStructureType;
#define XR_TYPE_INSTANCE_CREATE_INFO 3
#define XR_TYPE_SESSION_CREATE_INFO 8
#define XR_TYPE_SWAPCHAIN_CREATE_INFO 9
#define XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO 55
#define XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO 56
#define XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO 57
#define XR_TYPE_REFERENCE_SPACE_CREATE_INFO 37
#define XR_TYPE_SPACE_LOCATION 42
#define XR_TYPE_FRAME_END_INFO 12
#define XR_TYPE_COMPOSITION_LAYER_QUAD 36
#define XR_TYPE_GRAPHICS_BINDING_D3D11_KHR 1000027000
#define XR_TYPE_SWAPCHAIN_IMAGE_D3D11_KHR 1000027001

typedef int32_t XrReferenceSpaceType;
#define XR_REFERENCE_SPACE_TYPE_VIEW 1
#define XR_REFERENCE_SPACE_TYPE_LOCAL 2
#define XR_REFERENCE_SPACE_TYPE_STAGE 3

typedef int32_t XrEyeVisibility;
#define XR_EYE_VISIBILITY_BOTH 0

typedef XrFlags64 XrCompositionLayerFlags;
#define XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT 0x00000002ULL

typedef XrFlags64 XrSwapchainUsageFlags;
#define XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT 0x00000001ULL
#define XR_SWAPCHAIN_USAGE_SAMPLED_BIT          0x00000020ULL
#define XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT     0x00000080ULL

typedef XrFlags64 XrSpaceLocationFlags;
#define XR_SPACE_LOCATION_ORIENTATION_VALID_BIT 0x00000001ULL
#define XR_SPACE_LOCATION_POSITION_VALID_BIT    0x00000002ULL

typedef struct XrBaseInStructure {
    XrStructureType type;
    const struct XrBaseInStructure* next;
} XrBaseInStructure;

typedef struct XrBaseOutStructure {
    XrStructureType type;
    struct XrBaseOutStructure* next;
} XrBaseOutStructure;

typedef struct XrQuaternionf {
    float x, y, z, w;
} XrQuaternionf;

typedef struct XrVector3f {
    float x, y, z;
} XrVector3f;

typedef struct XrPosef {
    XrQuaternionf orientation;
    XrVector3f position;
} XrPosef;

typedef struct XrExtent2Df {
    float width;
    float height;
} XrExtent2Df;

typedef struct XrOffset2Di {
    int32_t x;
    int32_t y;
} XrOffset2Di;

typedef struct XrExtent2Di {
    int32_t width;
    int32_t height;
} XrExtent2Di;

typedef struct XrRect2Di {
    XrOffset2Di offset;
    XrExtent2Di extent;
} XrRect2Di;

typedef struct XrSwapchainSubImage {
    XrSwapchain swapchain;
    XrRect2Di imageRect;
    uint32_t imageArrayIndex;
} XrSwapchainSubImage;

typedef struct XrCompositionLayerBaseHeader {
    XrStructureType type;
    const void* next;
    XrCompositionLayerFlags layerFlags;
    XrSpace space;
} XrCompositionLayerBaseHeader;

typedef struct XrCompositionLayerQuad {
    XrStructureType type;
    const void* next;
    XrCompositionLayerFlags layerFlags;
    XrSpace space;
    XrEyeVisibility eyeVisibility;
    XrSwapchainSubImage subImage;
    XrPosef pose;
    XrExtent2Df size;
} XrCompositionLayerQuad;

typedef struct XrFrameEndInfo {
    XrStructureType type;
    const void* next;
    XrTime displayTime;
    int32_t environmentBlendMode;
    uint32_t layerCount;
    const XrCompositionLayerBaseHeader* const* layers;
} XrFrameEndInfo;

typedef struct XrSessionCreateInfo {
    XrStructureType type;
    const void* next;
    XrFlags64 createFlags;
    XrSystemId systemId;
} XrSessionCreateInfo;

typedef struct XrGraphicsBindingD3D11KHR {
    XrStructureType type;
    const void* next;
    ID3D11Device* device;
} XrGraphicsBindingD3D11KHR;

typedef struct XrSwapchainCreateInfo {
    XrStructureType type;
    const void* next;
    XrFlags64 createFlags;
    XrSwapchainUsageFlags usageFlags;
    int64_t format;
    uint32_t sampleCount;
    uint32_t width;
    uint32_t height;
    uint32_t faceCount;
    uint32_t arraySize;
    uint32_t mipCount;
} XrSwapchainCreateInfo;

typedef struct XrSwapchainImageBaseHeader {
    XrStructureType type;
    void* next;
} XrSwapchainImageBaseHeader;

typedef struct XrSwapchainImageD3D11KHR {
    XrStructureType type;
    void* next;
    ID3D11Texture2D* texture;
} XrSwapchainImageD3D11KHR;

typedef struct XrSwapchainImageAcquireInfo {
    XrStructureType type;
    const void* next;
} XrSwapchainImageAcquireInfo;

typedef struct XrSwapchainImageWaitInfo {
    XrStructureType type;
    const void* next;
    XrDuration timeout;
} XrSwapchainImageWaitInfo;

typedef struct XrSwapchainImageReleaseInfo {
    XrStructureType type;
    const void* next;
} XrSwapchainImageReleaseInfo;

typedef struct XrReferenceSpaceCreateInfo {
    XrStructureType type;
    const void* next;
    XrReferenceSpaceType referenceSpaceType;
    XrPosef poseInReferenceSpace;
} XrReferenceSpaceCreateInfo;

typedef struct XrSpaceLocation {
    XrStructureType type;
    void* next;
    XrSpaceLocationFlags locationFlags;
    XrPosef pose;
} XrSpaceLocation;

typedef struct XrInstanceCreateInfo {
    XrStructureType type;
    const void* next;
    XrFlags64 createFlags;
    char applicationInfo[268];
    uint32_t enabledApiLayerCount;
    const char* const* enabledApiLayerNames;
    uint32_t enabledExtensionCount;
    const char* const* enabledExtensionNames;
} XrInstanceCreateInfo;

// Function pointer typedefs
typedef XrResult (XRAPI_PTR *PFN_xrGetInstanceProcAddr)(XrInstance instance, const char* name, PFN_xrVoidFunction* function);
typedef XrResult (XRAPI_PTR *PFN_xrDestroyInstance)(XrInstance instance);
typedef XrResult (XRAPI_PTR *PFN_xrCreateSession)(XrInstance instance, const XrSessionCreateInfo* createInfo, XrSession* session);
typedef XrResult (XRAPI_PTR *PFN_xrDestroySession)(XrSession session);
typedef XrResult (XRAPI_PTR *PFN_xrCreateReferenceSpace)(XrSession session, const XrReferenceSpaceCreateInfo* createInfo, XrSpace* space);
typedef XrResult (XRAPI_PTR *PFN_xrDestroySpace)(XrSpace space);
typedef XrResult (XRAPI_PTR *PFN_xrLocateSpace)(XrSpace space, XrSpace baseSpace, XrTime time, XrSpaceLocation* location);
typedef XrResult (XRAPI_PTR *PFN_xrCreateSwapchain)(XrSession session, const XrSwapchainCreateInfo* createInfo, XrSwapchain* swapchain);
typedef XrResult (XRAPI_PTR *PFN_xrDestroySwapchain)(XrSwapchain swapchain);
typedef XrResult (XRAPI_PTR *PFN_xrEnumerateSwapchainImages)(XrSwapchain swapchain, uint32_t imageCapacityInput, uint32_t* imageCountOutput, XrSwapchainImageBaseHeader* images);
typedef XrResult (XRAPI_PTR *PFN_xrAcquireSwapchainImage)(XrSwapchain swapchain, const XrSwapchainImageAcquireInfo* acquireInfo, uint32_t* index);
typedef XrResult (XRAPI_PTR *PFN_xrWaitSwapchainImage)(XrSwapchain swapchain, const XrSwapchainImageWaitInfo* waitInfo);
typedef XrResult (XRAPI_PTR *PFN_xrReleaseSwapchainImage)(XrSwapchain swapchain, const XrSwapchainImageReleaseInfo* releaseInfo);
typedef XrResult (XRAPI_PTR *PFN_xrEndFrame)(XrSession session, const XrFrameEndInfo* frameEndInfo);

// OpenXR Loader <-> API Layer negotiation structs (loader_interfaces.h)
typedef int32_t XrLoaderInterfaceStructs;
#define XR_LOADER_INTERFACE_STRUCT_LOADER_INFO 1
#define XR_LOADER_INTERFACE_STRUCT_API_LAYER_REQUEST 2
#define XR_LOADER_INTERFACE_STRUCT_API_LAYER_NEXT_INFO 4
#define XR_LOADER_INTERFACE_STRUCT_API_LAYER_CREATE_INFO 5

#define XR_CURRENT_LOADER_API_LAYER_VERSION 1

typedef struct XrApiLayerNextInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    char layerName[XR_MAX_API_LAYER_NAME_SIZE];
    PFN_xrGetInstanceProcAddr nextGetInstanceProcAddr;
    struct XrApiLayerNextInfo* next;
} XrApiLayerNextInfo;

typedef struct XrApiLayerCreateInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    void* loaderInstance;
    char settings_file_location[512];
    XrApiLayerNextInfo* nextInfo;
} XrApiLayerCreateInfo;

typedef XrResult (XRAPI_PTR *PFN_xrCreateApiLayerInstance)(
    const XrInstanceCreateInfo* info,
    const XrApiLayerCreateInfo* apiLayerInfo,
    XrInstance* instance);

typedef struct XrNegotiateLoaderInfo {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    uint32_t minInterfaceVersion;
    uint32_t maxInterfaceVersion;
    XrVersion minApiVersion;
    XrVersion maxApiVersion;
} XrNegotiateLoaderInfo;

typedef struct XrNegotiateApiLayerRequest {
    XrLoaderInterfaceStructs structType;
    uint32_t structVersion;
    size_t structSize;
    uint32_t layerInterfaceVersion;
    XrVersion layerApiVersion;
    PFN_xrGetInstanceProcAddr getInstanceProcAddr;
    PFN_xrCreateApiLayerInstance createApiLayerInstance;
} XrNegotiateApiLayerRequest;

// Shared Memory IPC contract between Aether Python and aether_openxr_layer.dll
#define AETHER_XR_SHM_NAME L"Local\\AetherOpenXROverlayV1"
#define AETHER_XR_SHM_MAGIC 0x41455852u  // "AEXR"
#define AETHER_XR_SHM_VERSION 1u
#define AETHER_XR_MAX_WIDTH 1024u
#define AETHER_XR_MAX_HEIGHT 1024u
#define AETHER_XR_HEADER_SIZE 256u
#define AETHER_XR_SHM_TOTAL_BYTES (AETHER_XR_HEADER_SIZE + (AETHER_XR_MAX_WIDTH * AETHER_XR_MAX_HEIGHT * 4u))

#pragma pack(push, 1)
typedef struct AetherVRSharedHeader {
    uint32_t magic;              // 0
    uint32_t version;            // 4
    uint32_t frame_seq;          // 8   (incremented by Python on new RGBA upload)
    uint32_t visible;            // 12  (0=hidden, 1=visible)
    uint32_t anchor;             // 16  (0=cockpit/LOCAL, 1=hmd/VIEW, 2=wrist_left, 3=wrist_right)
    uint32_t width;              // 20
    uint32_t height;             // 24
    uint32_t _pad0;              // 28
    float pos[3];                // 32..43  (x, y, z meters)
    float quat[4];               // 44..59  (x, y, z, w unit quaternion)
    float width_m;               // 60..63
    float height_m;              // 64..67
    float alpha;                 // 68..71
    uint64_t heartbeat_ms;       // 72..79  (epoch ms written by Python)
    uint64_t layer_heartbeat_ms; // 80..87  (epoch ms written by C++ layer on xrEndFrame)
    uint32_t hmd_pose_valid;     // 88..91  (written by C++ layer)
    float hmd_pos[3];            // 92..103 (written by C++ layer in LOCAL space)
    float hmd_quat[4];           // 104..119 (written by C++ layer in LOCAL space)
    uint8_t reserved[136];       // 120..255 -> total exactly 256 bytes
} AetherVRSharedHeader;
#pragma pack(pop)

static_assert(sizeof(AetherVRSharedHeader) == AETHER_XR_HEADER_SIZE, "AetherVRSharedHeader must be exactly 256 bytes");

#ifdef __cplusplus
}
#endif
