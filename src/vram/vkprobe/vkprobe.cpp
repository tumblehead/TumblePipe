// VK_LAYER_TT_vramprobe — a pass-through Vulkan layer that logs what a
// process allocates on the GPU, and what it names it.
//
// The question it exists to answer (VRAM Inspector, phase 2): does Houdini's
// Vulkan viewport name its buffers and images with VK_EXT_debug_utils? If it
// does, a layer can attribute exact GPU bytes to the prims and textures they
// belong to — something no HDK API exposes for the Solaris viewport. If it
// doesn't, a layer still gives exact totals by kind (vertex, index, texture,
// render target), which gpumem does not.
//
// Every call is forwarded unchanged; the layer only records. One line per
// event goes to the file named by TT_VKPROBE_LOG (default: vkprobe.log in
// the temp directory):
//
//   instance_ext,<name>            device_ext,<name>
//   buffer,<handle>,<size>,<usage>
//   image,<handle>,<w>,<h>,<d>,<mips>,<layers>,<format>,<usage>
//   alloc,<handle>,<size>,<type>   free,<handle>
//   bind_buffer,<buffer>,<memory>,<offset>
//   bind_image,<image>,<memory>,<offset>
//   destroy_buffer,<handle>        destroy_image,<handle>
//   name,<object type>,<handle>,<name>
//
// Build (x64 Developer Command Prompt), with the Vulkan headers the HDK ships:
//   cl /nologo /LD /O2 /EHsc /I"%HFS%\toolkit\include" vkprobe.cpp /Fe:tt_vramprobe.dll

#define _CRT_SECURE_NO_WARNINGS
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>

#ifdef _WIN32
#define PROBE_EXPORT extern "C" __declspec(dllexport)
#else
#define PROBE_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

std::mutex g_lock;
FILE *g_log = nullptr;

void open_log()
{
    if (g_log)
        return;
    const char *path = std::getenv("TT_VKPROBE_LOG");
    std::string p;
    if (path && *path)
        p = path;
    else
    {
        const char *tmp = std::getenv("TEMP");
        p = std::string(tmp ? tmp : ".") + "/vkprobe.log";
    }
    g_log = std::fopen(p.c_str(), "w");
}

template <typename... Args>
void log_line(const char *fmt, Args... args)
{
    std::lock_guard<std::mutex> guard(g_lock);
    open_log();
    if (!g_log)
        return;
    std::fprintf(g_log, fmt, args...);
    std::fputc('\n', g_log);
    std::fflush(g_log);
}

// Dispatchable handles start with a pointer to the loader's dispatch table;
// that pointer identifies the instance/device a handle belongs to.
template <typename T>
void *key_of(T handle) { return *reinterpret_cast<void **>(handle); }

struct InstanceData
{
    PFN_vkGetInstanceProcAddr gipa = nullptr;
    PFN_vkDestroyInstance destroy = nullptr;
    PFN_vkCreateDevice create_device = nullptr;
};

struct DeviceData
{
    PFN_vkGetDeviceProcAddr gdpa = nullptr;
    PFN_vkDestroyDevice destroy = nullptr;
    PFN_vkCreateBuffer create_buffer = nullptr;
    PFN_vkDestroyBuffer destroy_buffer = nullptr;
    PFN_vkCreateImage create_image = nullptr;
    PFN_vkDestroyImage destroy_image = nullptr;
    PFN_vkAllocateMemory allocate = nullptr;
    PFN_vkFreeMemory free = nullptr;
    PFN_vkBindBufferMemory bind_buffer = nullptr;
    PFN_vkBindImageMemory bind_image = nullptr;
    PFN_vkBindBufferMemory2 bind_buffer2 = nullptr;
    PFN_vkBindImageMemory2 bind_image2 = nullptr;
    PFN_vkSetDebugUtilsObjectNameEXT set_name = nullptr;
};

std::unordered_map<void *, InstanceData> g_instances;
std::unordered_map<void *, DeviceData> g_devices;
std::mutex g_tables;

InstanceData *instance_data(void *key)
{
    std::lock_guard<std::mutex> guard(g_tables);
    auto it = g_instances.find(key);
    return it == g_instances.end() ? nullptr : &it->second;
}

DeviceData *device_data(void *key)
{
    std::lock_guard<std::mutex> guard(g_tables);
    auto it = g_devices.find(key);
    return it == g_devices.end() ? nullptr : &it->second;
}

// ── instance ────────────────────────────────────────────────────────────────

VKAPI_ATTR VkResult VKAPI_CALL probe_CreateInstance(
    const VkInstanceCreateInfo *info, const VkAllocationCallbacks *alloc,
    VkInstance *instance)
{
    auto *link = (VkLayerInstanceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO
                     && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerInstanceCreateInfo *)link->pNext;
    if (!link)
        return VK_ERROR_INITIALIZATION_FAILED;

    PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;
    auto create = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
    VkResult result = create(info, alloc, instance);
    if (result != VK_SUCCESS)
        return result;

    InstanceData data;
    data.gipa = gipa;
    data.destroy = (PFN_vkDestroyInstance)gipa(*instance, "vkDestroyInstance");
    data.create_device = (PFN_vkCreateDevice)gipa(*instance, "vkCreateDevice");
    {
        std::lock_guard<std::mutex> guard(g_tables);
        g_instances[key_of(*instance)] = data;
    }
    for (uint32_t i = 0; i < info->enabledExtensionCount; ++i)
        log_line("instance_ext,%s", info->ppEnabledExtensionNames[i]);
    for (uint32_t i = 0; i < info->enabledLayerCount; ++i)
        log_line("instance_layer,%s", info->ppEnabledLayerNames[i]);
    return result;
}

VKAPI_ATTR void VKAPI_CALL probe_DestroyInstance(
    VkInstance instance, const VkAllocationCallbacks *alloc)
{
    void *key = key_of(instance);
    InstanceData *data = instance_data(key);
    if (data && data->destroy)
        data->destroy(instance, alloc);
    std::lock_guard<std::mutex> guard(g_tables);
    g_instances.erase(key);
}

// ── device ──────────────────────────────────────────────────────────────────

VKAPI_ATTR VkResult VKAPI_CALL probe_CreateDevice(
    VkPhysicalDevice physical, const VkDeviceCreateInfo *info,
    const VkAllocationCallbacks *alloc, VkDevice *device)
{
    auto *link = (VkLayerDeviceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO
                     && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerDeviceCreateInfo *)link->pNext;
    if (!link)
        return VK_ERROR_INITIALIZATION_FAILED;

    PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;
    auto create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
    VkResult result = create(physical, info, alloc, device);
    if (result != VK_SUCCESS)
        return result;

    DeviceData d;
    d.gdpa = gdpa;
#define LOAD(field, name) d.field = (decltype(d.field))gdpa(*device, name)
    LOAD(destroy, "vkDestroyDevice");
    LOAD(create_buffer, "vkCreateBuffer");
    LOAD(destroy_buffer, "vkDestroyBuffer");
    LOAD(create_image, "vkCreateImage");
    LOAD(destroy_image, "vkDestroyImage");
    LOAD(allocate, "vkAllocateMemory");
    LOAD(free, "vkFreeMemory");
    LOAD(bind_buffer, "vkBindBufferMemory");
    LOAD(bind_image, "vkBindImageMemory");
    LOAD(bind_buffer2, "vkBindBufferMemory2");
    LOAD(bind_image2, "vkBindImageMemory2");
    LOAD(set_name, "vkSetDebugUtilsObjectNameEXT");
#undef LOAD
    {
        std::lock_guard<std::mutex> guard(g_tables);
        g_devices[key_of(*device)] = d;
    }
    for (uint32_t i = 0; i < info->enabledExtensionCount; ++i)
        log_line("device_ext,%s", info->ppEnabledExtensionNames[i]);
    log_line("device,%p,set_name=%d", (void *)*device, d.set_name ? 1 : 0);
    return result;
}

VKAPI_ATTR void VKAPI_CALL probe_DestroyDevice(
    VkDevice device, const VkAllocationCallbacks *alloc)
{
    void *key = key_of(device);
    DeviceData *d = device_data(key);
    if (d && d->destroy)
        d->destroy(device, alloc);
    std::lock_guard<std::mutex> guard(g_tables);
    g_devices.erase(key);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_CreateBuffer(
    VkDevice device, const VkBufferCreateInfo *info,
    const VkAllocationCallbacks *alloc, VkBuffer *buffer)
{
    DeviceData *d = device_data(key_of(device));
    VkResult r = d->create_buffer(device, info, alloc, buffer);
    if (r == VK_SUCCESS)
        log_line("buffer,%llx,%llu,%x", (unsigned long long)*buffer,
                 (unsigned long long)info->size, (unsigned)info->usage);
    return r;
}

VKAPI_ATTR void VKAPI_CALL probe_DestroyBuffer(
    VkDevice device, VkBuffer buffer, const VkAllocationCallbacks *alloc)
{
    DeviceData *d = device_data(key_of(device));
    if (buffer != VK_NULL_HANDLE)
        log_line("destroy_buffer,%llx", (unsigned long long)buffer);
    d->destroy_buffer(device, buffer, alloc);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_CreateImage(
    VkDevice device, const VkImageCreateInfo *info,
    const VkAllocationCallbacks *alloc, VkImage *image)
{
    DeviceData *d = device_data(key_of(device));
    VkResult r = d->create_image(device, info, alloc, image);
    if (r == VK_SUCCESS)
        log_line("image,%llx,%u,%u,%u,%u,%u,%d,%x", (unsigned long long)*image,
                 info->extent.width, info->extent.height, info->extent.depth,
                 info->mipLevels, info->arrayLayers, (int)info->format,
                 (unsigned)info->usage);
    return r;
}

VKAPI_ATTR void VKAPI_CALL probe_DestroyImage(
    VkDevice device, VkImage image, const VkAllocationCallbacks *alloc)
{
    DeviceData *d = device_data(key_of(device));
    if (image != VK_NULL_HANDLE)
        log_line("destroy_image,%llx", (unsigned long long)image);
    d->destroy_image(device, image, alloc);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_AllocateMemory(
    VkDevice device, const VkMemoryAllocateInfo *info,
    const VkAllocationCallbacks *alloc, VkDeviceMemory *memory)
{
    DeviceData *d = device_data(key_of(device));
    VkResult r = d->allocate(device, info, alloc, memory);
    if (r == VK_SUCCESS)
        log_line("alloc,%llx,%llu,%u", (unsigned long long)*memory,
                 (unsigned long long)info->allocationSize, info->memoryTypeIndex);
    return r;
}

VKAPI_ATTR void VKAPI_CALL probe_FreeMemory(
    VkDevice device, VkDeviceMemory memory, const VkAllocationCallbacks *alloc)
{
    DeviceData *d = device_data(key_of(device));
    if (memory != VK_NULL_HANDLE)
        log_line("free,%llx", (unsigned long long)memory);
    d->free(device, memory, alloc);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_BindBufferMemory(
    VkDevice device, VkBuffer buffer, VkDeviceMemory memory, VkDeviceSize offset)
{
    DeviceData *d = device_data(key_of(device));
    log_line("bind_buffer,%llx,%llx,%llu", (unsigned long long)buffer,
             (unsigned long long)memory, (unsigned long long)offset);
    return d->bind_buffer(device, buffer, memory, offset);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_BindImageMemory(
    VkDevice device, VkImage image, VkDeviceMemory memory, VkDeviceSize offset)
{
    DeviceData *d = device_data(key_of(device));
    log_line("bind_image,%llx,%llx,%llu", (unsigned long long)image,
             (unsigned long long)memory, (unsigned long long)offset);
    return d->bind_image(device, image, memory, offset);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_BindBufferMemory2(
    VkDevice device, uint32_t count, const VkBindBufferMemoryInfo *infos)
{
    DeviceData *d = device_data(key_of(device));
    for (uint32_t i = 0; i < count; ++i)
        log_line("bind_buffer,%llx,%llx,%llu", (unsigned long long)infos[i].buffer,
                 (unsigned long long)infos[i].memory,
                 (unsigned long long)infos[i].memoryOffset);
    return d->bind_buffer2(device, count, infos);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_BindImageMemory2(
    VkDevice device, uint32_t count, const VkBindImageMemoryInfo *infos)
{
    DeviceData *d = device_data(key_of(device));
    for (uint32_t i = 0; i < count; ++i)
        log_line("bind_image,%llx,%llx,%llu", (unsigned long long)infos[i].image,
                 (unsigned long long)infos[i].memory,
                 (unsigned long long)infos[i].memoryOffset);
    return d->bind_image2(device, count, infos);
}

VKAPI_ATTR VkResult VKAPI_CALL probe_SetDebugUtilsObjectNameEXT(
    VkDevice device, const VkDebugUtilsObjectNameInfoEXT *info)
{
    DeviceData *d = device_data(key_of(device));
    log_line("name,%d,%llx,%s", (int)info->objectType,
             (unsigned long long)info->objectHandle,
             info->pObjectName ? info->pObjectName : "");
    return d->set_name ? d->set_name(device, info) : VK_SUCCESS;
}

// ── dispatch ────────────────────────────────────────────────────────────────

PFN_vkVoidFunction device_hook(const char *name)
{
#define HOOK(fn) if (!std::strcmp(name, "vk" #fn)) return (PFN_vkVoidFunction)probe_##fn
    HOOK(DestroyDevice);
    HOOK(CreateBuffer);
    HOOK(DestroyBuffer);
    HOOK(CreateImage);
    HOOK(DestroyImage);
    HOOK(AllocateMemory);
    HOOK(FreeMemory);
    HOOK(BindBufferMemory);
    HOOK(BindImageMemory);
    HOOK(BindBufferMemory2);
    HOOK(BindImageMemory2);
    HOOK(SetDebugUtilsObjectNameEXT);
#undef HOOK
    if (!std::strcmp(name, "vkBindBufferMemory2KHR"))
        return (PFN_vkVoidFunction)probe_BindBufferMemory2;
    if (!std::strcmp(name, "vkBindImageMemory2KHR"))
        return (PFN_vkVoidFunction)probe_BindImageMemory2;
    return nullptr;
}

} // namespace

PROBE_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
probe_GetDeviceProcAddr(VkDevice device, const char *name)
{
    if (PFN_vkVoidFunction hook = device_hook(name))
        return hook;
    DeviceData *d = device_data(key_of(device));
    return d ? d->gdpa(device, name) : nullptr;
}

PROBE_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
probe_GetInstanceProcAddr(VkInstance instance, const char *name)
{
    if (!std::strcmp(name, "vkCreateInstance"))
        return (PFN_vkVoidFunction)probe_CreateInstance;
    if (!std::strcmp(name, "vkDestroyInstance"))
        return (PFN_vkVoidFunction)probe_DestroyInstance;
    if (!std::strcmp(name, "vkCreateDevice"))
        return (PFN_vkVoidFunction)probe_CreateDevice;
    if (!std::strcmp(name, "vkGetDeviceProcAddr"))
        return (PFN_vkVoidFunction)probe_GetDeviceProcAddr;
    if (!std::strcmp(name, "vkGetInstanceProcAddr"))
        return (PFN_vkVoidFunction)probe_GetInstanceProcAddr;
    if (instance != VK_NULL_HANDLE)
        if (PFN_vkVoidFunction hook = device_hook(name))
            return hook;
    if (instance == VK_NULL_HANDLE)
        return nullptr;
    InstanceData *data = instance_data(key_of(instance));
    return data ? data->gipa(instance, name) : nullptr;
}

// vk_layer.h declares this without dllexport, so export it by name instead.
#ifdef _WIN32
#pragma comment(linker, "/EXPORT:vkNegotiateLoaderLayerInterfaceVersion")
#endif
extern "C" VKAPI_ATTR VkResult VKAPI_CALL
vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *version)
{
    if (version->loaderLayerInterfaceVersion > 2)
        version->loaderLayerInterfaceVersion = 2;
    version->pfnGetInstanceProcAddr = probe_GetInstanceProcAddr;
    version->pfnGetDeviceProcAddr = probe_GetDeviceProcAddr;
    version->pfnGetPhysicalDeviceProcAddr = nullptr;
    return VK_SUCCESS;
}
