// tt_vramhook — the VRAM Inspector's measurements: every viewport's GPU
// geometry memory per USD prim, read inside the viewport's own redraw, and
// Houdini's GPU memory registry for everything else. See README.md.
//
// Why not Houdini's `gpumem`: GUI_GeoRender::gpuMemoryUsage (22.0.368) has two
// bugs. It reads every entry of its primitive list without a null check, and
// hiding a prim leaves null entries until the list is compacted — that is the
// segfault behind gpumem's crash, and it happens inside a redraw too, so
// timing alone does not avoid it. And it zeroes its per-render outputs once
// per primitive rather than once per render, so a primitive drawn with several
// renders is counted as a running total (GR_GeoRender::gpuMemoryUsage adds
// into its outputs): 1576 MiB reported, 430 MiB real, on the HideAndReek
// lighting shot. This hook rebuilds the sum per primitive through the
// null-checked getPrimitive(), zeroing per part.
//
// Python never triggers a walk of live renderers: it asks for one
// (`ttvramhook_request`, or tt_vramhook_request via ctypes), the next redraw
// of any viewport performs it, and `ttvramhook_snapshot` / tt_vramhook_snapshot
// return the last result — copied numbers and strings only; no HDK pointer
// survives the callback.

#include <CMD/CMD_Args.h>
#include <CMD/CMD_Manager.h>
#include <DM/DM_RenderTable.h>
#include <DM/DM_SceneHook.h>
#include <DM/DM_VPortAgent.h>
#include <DM/DM_GeoDetail.h>
#include <GUI/GUI_DetailLook.h>
#include <GUI/GUI_GeoRender.h>
#include <GR/GR_Primitive.h>
#include <OP/OP_Node.h>
#include <RV/RV_Instance.h>
#include <RV/RV_VKMemory.h>
#include <RV/RV_TextureCache.h>
#include <VE/VE_Memory.h>
#include <UT/UT_Array.h>
#include <UT/UT_MemoryResource.h>
#include <UT/UT_DSOVersion.h>
#include <UT/UT_WorkBuffer.h>
#include <SYS/SYS_Visibility.h>

#include <atomic>
#include <map>
#include <chrono>
#include <mutex>
#include <string>
#include <unordered_set>

namespace {

// A request is a generation number. Each viewport's hook walks once per new
// generation, on its own next redraw, so one request measures every viewer
// (not just whichever redraws first). Snapshots are kept per viewport and
// dropped when the viewport goes away.
std::mutex          theLock;
std::map<int, std::string> theSnapshots;      // viewport id -> last walk
std::string         theReturned;              // stable copy handed to ctypes
std::atomic<long long> theRequest{0};
std::atomic<long long> theWalks{0};
std::string         theClients = "[]";        // memory registry, last walk

std::string snapshotJson()
{
    std::lock_guard<std::mutex> lock(theLock);
    std::string out = "{\"request\":" + std::to_string(theRequest.load());
    out += ",\"walks\":" + std::to_string(theWalks.load());
    out += ",\"clients\":" + theClients;
    out += ",\"viewports\":[";
    bool first = true;
    for (const auto &[id, snap] : theSnapshots)
    {
        if (!first)
            out += ',';
        first = false;
        out += snap;
    }
    out += "]}";
    return out;
}


void jsonString(std::string &out, const char *s)
{
    out += '"';
    for (const char *p = s ? s : ""; *p; ++p)
    {
        unsigned char c = (unsigned char)*p;
        switch (c)
        {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20)
                {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                }
                else
                    out += (char)c;
        }
    }
    out += '"';
}

void nodePath(std::string &out, const OP_Node *node)
{
    if (!node) { out += "null"; return; }
    UT_WorkBuffer path;
    node->getFullPath(path);
    jsonString(out, path.buffer());
}

// GUI_GeoRender's primitive list is protected. Naming the members through a
// derived class makes the pointers-to-member legal to form; they are then
// applied to the real object, so no object is ever cast to this type.
struct GeoRenderPeek : GUI_GeoRender
{
    static int numPrims(const GUI_GeoRender *geo)
    {
        return (geo->*(&GeoRenderPeek::getNumPrimitives))();
    }
    static GR_Primitive *prim(const GUI_GeoRender *geo, int i)
    {
        return (geo->*(&GeoRenderPeek::getPrimitive))(i);
    }
};

void walkRender(std::string &out, GUI_GeoRender *geo)
{
    // NOT geo->gpuMemoryUsage(): it reads each myPrimitives entry without a
    // null check, and hiding a prim leaves a null entry until the array is
    // compacted, so it segfaults (libGUI 22.0.368, gpuMemoryUsage+0x53 —
    // the same fault behind gpumem's crash). getPrimitive() null-checks the
    // entry, so the sum is rebuilt here from the primitives instead, with
    // each part's outputs zeroed (GR_GeoRender::gpuMemoryUsage adds into
    // them; Houdini's version zeroes once per primitive and overcounts).
    out += "{\"cache\":";
    jsonString(out, geo->getCacheName());
    out += ",\"class\":";
    jsonString(out, geo->className());
    out += ",\"node\":";
    nodePath(out, geo->getGeometry());
    out += ",\"node_id\":" + std::to_string(geo->getNodeID());

    exint used = 0, cached = 0, pinned = 0;
    int null_prims = 0;
    const int nprims = GeoRenderPeek::numPrims(geo);
    std::string prims;
    int named = 0;
    UT_Array<GR_GeoRender *> parts;
    for (int i = 0; i < nprims; ++i)
    {
        GR_Primitive *prim = GeoRenderPeek::prim(geo, i);
        if (!prim)
        {
            ++null_prims;
            continue;
        }

        // A primitive's GPU memory is the sum of the GR_GeoRenders it draws
        // with (in Solaris, one primitive per USD prim, named by its path).
        exint pused = 0, pcached = 0, ppinned = 0;
        std::string sizes;
        parts.clear();
        prim->getGeoRenders(parts);
        for (GR_GeoRender *part : parts)
        {
            if (!part)
                continue;
            exint u = 0, c = 0, p = 0;
            part->gpuMemoryUsage(u, c, p);
            pused += u;
            pcached += c;
            ppinned += p;
            if (!sizes.empty())
                sizes += ',';
            sizes += std::to_string(u);
        }
        used += pused;
        cached += pcached;
        pinned += ppinned;

        if (named++)
            prims += ',';
        prims += "{\"cache\":";
        jsonString(prims, prim->getCacheName());
        prims += ",\"class\":";
        jsonString(prims, prim->className());
        prims += ",\"parts\":[" + sizes + "]";
        prims += ",\"used\":" + std::to_string(pused);
        prims += ",\"cached\":" + std::to_string(pcached);
        prims += ",\"pinned\":" + std::to_string(ppinned);
        prims += '}';
    }
    out += ",\"used\":" + std::to_string(used);
    out += ",\"cached\":" + std::to_string(cached);
    out += ",\"pinned\":" + std::to_string(pinned);
    out += ",\"nprims\":" + std::to_string(nprims);
    out += ",\"null_prims\":" + std::to_string(null_prims);
    out += ",\"prims\":[" + prims + "]}";
}

void walkLook(std::string &out, GUI_DetailLook *look, const char *list)
{
    out += "{\"list\":\"";
    out += list;
    out += "\",\"name\":";
    jsonString(out, look->getName());
    out += ",\"class\":";
    jsonString(out, look->className());
    out += ",\"retired\":";
    out += look->isRetired() ? "true" : "false";
    out += ",\"object\":";
    nodePath(out, look->getObject());
    out += ",\"sop\":";
    nodePath(out, look->getSop());

    // A retired look's renders may already be going away: name it, skip it.
    out += ",\"renders\":[";
    if (!look->isRetired())
    {
        UT_Array<GUI_GeoRender *> geos;
        look->getGeometryRenders(geos);
        bool first = true;
        for (GUI_GeoRender *geo : geos)
        {
            if (!geo)
                continue;
            if (!first)
                out += ',';
            first = false;
            walkRender(out, geo);
        }
    }
    out += "]}";
}

void walkViewport(std::string &out, DM_VPortAgent &vport)
{
    out += "{\"lops\":";
    out += vport.isLopsView() ? "true" : "false";
    out += ",\"opaque\":" + std::to_string(vport.getNumOpaqueObjects());
    out += ",\"transparent\":" + std::to_string(vport.getNumTransparentObjects());
    out += ",\"unlit\":" + std::to_string(vport.getNumUnlitObjects());
    out += ",\"xray\":" + std::to_string(vport.getNumXRayObjects());
    out += ",\"looks\":[";

    std::unordered_set<const GUI_DetailLook *> seen;
    bool first = true;
    auto visit = [&](GUI_DetailLook *look, const char *list) {
        if (!look || !seen.insert(look).second)
            return;
        if (!first)
            out += ',';
        first = false;
        walkLook(out, look, list);
    };

    for (int i = 0; i < vport.getNumOpaqueObjects(); ++i)
        visit((GUI_DetailLook *)vport.getOpaqueObject(i).look(), "opaque");
    for (int i = 0; i < vport.getNumTransparentObjects(); ++i)
        visit((GUI_DetailLook *)vport.getTransparentObject(i).look(), "transparent");
    for (int i = 0; i < vport.getNumUnlitObjects(); ++i)
        visit((GUI_DetailLook *)vport.getUnlitObject(i).look(), "unlit");
    for (int i = 0; i < vport.getNumXRayObjects(); ++i)
        visit((GUI_DetailLook *)vport.getXRayObject(i).look(), "xray");

    // NOT getDetailLookByIndex: it has no count and does not return null past
    // the end — looping until null read a garbage look and segfaulted the
    // first spike session (crash.Lighting_light_v0126.tumblehead_21380).
    // Only lists that come with a count are walked.
    out += "]}";
}

void walkTotals(std::string &out)
{
    out += "\"textures\":{\"bytes\":" + std::to_string(RV_TextureCache::currentSizeBytes());
    out += ",\"count\":" + std::to_string(RV_TextureCache::totalTextureCount());
    out += ",\"max\":" + std::to_string(RV_TextureCache::maxSizeBytes()) + "}";

    // hasInstance() first: getInstance() would create one.
    if (!RV_Instance::hasInstance())
        return;
    RV_Instance *inst = RV_Instance::getInstance();
    if (!inst)
        return;
    out += ",\"device\":";
    jsonString(out, inst->deviceName().c_str());
    exint dvma = 0, dvk = 0, dtotal = 0, svma = 0, svk = 0, stotal = 0;
    inst->getMemAllocator().getBaseAllocator().getMemoryUsage(
        &dvma, &dvk, &dtotal, &svma, &svk, &stotal);
    out += ",\"vulkan\":{\"device_vma\":" + std::to_string(dvma);
    out += ",\"device_vk\":" + std::to_string(dvk);
    out += ",\"device_total\":" + std::to_string(dtotal);
    out += ",\"shared_vma\":" + std::to_string(svma);
    out += ",\"shared_vk\":" + std::to_string(svk);
    out += ",\"shared_total\":" + std::to_string(stotal) + "}";
}

// Houdini's GPU memory registry — what `gpumem` prints: one UT_MemoryResource
// per device, each with its UT_MemoryClients (texture cache, OpenCL pool,
// Copernicus, Karma XPU, one geometry client per viewer pane). Every client
// is asked for its use EXCEPT the viewer geometry ones: theirs is
// GUI_GeoRender::gpuMemoryUsage, the null-entry crash and the running-total
// overcount. Those are listed by name only (Python needs the pane names) and
// measured by this hook's own per-primitive walk instead.
std::string walkClients()
{
    std::string out = "[";
    bool first = true;
    for (const auto &[device, resource] : UT_MemoryResource::memoryResources())
    {
        if (!resource)
            continue;
        for (const auto &[name, entry] : resource->clients())
        {
            UT_MemoryClient *client = entry.first;
            if (!client)
                continue;
            const bool geometry = name.startsWith("Vulkan Geometry");
            exint used = 0, cached = 0;
            if (!geometry)
                client->memoryUse(resource.get(), used, cached);
            if (!first)
                out += ',';
            first = false;
            out += "{\"device\":";
            jsonString(out, device.c_str());
            out += ",\"name\":";
            jsonString(out, name.c_str());
            out += ",\"geometry\":";
            out += geometry ? "true" : "false";
            out += ",\"used\":" + std::to_string(used);
            out += ",\"cached\":" + std::to_string(cached) + "}";
        }
    }
    out += "]";
    return out;
}

class VramRender : public DM_SceneRenderHook
{
public:
    VramRender(DM_VPortAgent &vport)
        : DM_SceneRenderHook(vport, DM_VIEWPORT_ALL) {}

    ~VramRender() override
    {
        std::lock_guard<std::mutex> lock(theLock);
        theSnapshots.erase(getID());
    }

    bool render(RE_RenderContext, const DM_SceneHookData &) override
    {
        const long long request = theRequest.load();
        if (request <= myWalked)
            return false;
        myWalked = request;

        auto t0 = std::chrono::steady_clock::now();
        std::string out = "{";
        out += "\"request\":" + std::to_string(request);
        out += ",\"walk\":" + std::to_string(++theWalks);
        out += ",\"viewport_id\":" + std::to_string(getID());
        out += ",\"active_viewport\":";
        jsonString(out, GUI_DetailLook::getActiveViewport());
        out += ",\"display_lop\":";
        nodePath(out, getDisplayLOP());
        out += ",";
        walkTotals(out);
        out += ",\"viewport\":";
        walkViewport(out, viewport());
        auto ms = std::chrono::duration<double, std::milli>(
            std::chrono::steady_clock::now() - t0).count();
        out += ",\"walk_ms\":" + std::to_string(ms) + "}";

        std::string clients = walkClients();
        std::lock_guard<std::mutex> lock(theLock);
        theSnapshots[getID()].swap(out);
        theClients.swap(clients);
        return false;           // drew nothing; native rendering is unaffected
    }

private:
    long long myWalked = 0;
};

class VramHook : public DM_SceneHook
{
public:
    VramHook() : DM_SceneHook("tt_vramhook", 0, DM_HOOK_ALL_VIEWS) {}

    DM_SceneRenderHook *newSceneRender(DM_VPortAgent &vport,
                                       DM_SceneHookType,
                                       DM_SceneHookPolicy) override
    {
        return new VramRender(vport);
    }

    void retireSceneRender(DM_VPortAgent &, DM_SceneRenderHook *hook) override
    {
        delete hook;
    }
};

} // namespace

extern "C" {

SYS_VISIBILITY_EXPORT void newRenderHook(DM_RenderTable *table)
{
    static VramHook hook;
    table->registerSceneHook(&hook, DM_HOOK_POST_RENDER, DM_HOOK_AFTER_NATIVE);
}

/// hscript: `ttvramhook_request` asks every viewport for a walk and prints the
/// request number; `ttvramhook_snapshot` prints every viewport's last walk.
/// For callers that cannot use ctypes (the session bridge).
static void cmdRequest(CMD_Args &args)
{
    args.out() << ++theRequest << '\n';
}

static void cmdSnapshot(CMD_Args &args)
{
    args.out() << snapshotJson() << '\n';
}

/// `ttvramhook_free`: ask every non-geometry client to release the memory it
/// reports as cached (held for reuse), as `gpumem -f` did. Geometry clients
/// are left alone: their memory query is the crashing one. Prints the bytes
/// freed.
static void cmdFree(CMD_Args &args)
{
    exint total = 0;
    for (const auto &[device, resource] : UT_MemoryResource::memoryResources())
    {
        if (!resource)
            continue;
        for (const auto &[name, entry] : resource->clients())
        {
            UT_MemoryClient *client = entry.first;
            if (!client || name.startsWith("Vulkan Geometry"))
                continue;
            exint used = 0, cached = 0;
            client->memoryUse(resource.get(), used, cached);
            if (cached <= 0)
                continue;
            int nice = entry.second;
            if (nice < 0 || nice >= UT_MemoryClient::CANNOT_FREE)
                continue;
            exint freed = 0;
            if (client->freeMemoryRequest(resource.get(),
                                          UT_MemoryClient::NORMAL_REQUEST,
                                          (UT_MemoryClient::Niceness)nice,
                                          cached, freed))
                total += freed;
        }
    }
    args.out() << total << '\n';
}

SYS_VISIBILITY_EXPORT void CMDextendLibrary(CMD_Manager *cman)
{
    cman->installCommand("ttvramhook_request", "", cmdRequest);
    cman->installCommand("ttvramhook_snapshot", "", cmdSnapshot);
    cman->installCommand("ttvramhook_free", "", cmdFree);
}

/// Ask every viewport for one walk on its next redraw; returns the request
/// number, which each viewport's snapshot echoes once it has answered.
SYS_VISIBILITY_EXPORT long long tt_vramhook_request()
{
    return ++theRequest;
}

/// Every viewport's last snapshot as JSON. Valid until the next call (from
/// any thread); copy it at once.
SYS_VISIBILITY_EXPORT const char *tt_vramhook_snapshot()
{
    std::string json = snapshotJson();
    std::lock_guard<std::mutex> lock(theLock);
    theReturned.swap(json);
    return theReturned.c_str();
}

}
