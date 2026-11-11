// hdThPlayblast: TumblePipe's playblast render delegate, the Hydra side.
//
// husk loads this through plugInfo.json like any third-party renderer
// (`husk -R HdThPlayblastRendererPlugin ...`). The shim owns what Hydra
// requires of a renderer and turns the scene into plain arrays:
//
//   ThMesh        triangulated points, per-corner normals / display colour /
//                 texture coordinates, its transform; skinned points come
//                 from Hydra's CPU ext computations. Implicit shapes
//                 arrive as meshes too (_RegisterImplicitSurfacesToMesh).
//   ThCurves      basis curves and points as authored (vertices, counts,
//   ThPoints      basis, wrap, widths, display colour); the core draws them
//                 as camera-facing ribbons and discs (src/curves.rs).
//   ThInstancer   per-instance transforms, nested instancers included.
//   ThMaterial    the base colour or base-colour texture, and the opacity
//                 (opacity, transmission, cutout), read from a
//                 UsdPreviewSurface or MaterialX network.
//   ThLightPrim   a UsdLux light (distant, sphere, rect, disk, cylinder,
//                 dome) as authored, which the core lights the frame with
//                 (src/lights.rs) unless th:lighting asks for the headlight.
//   ThRenderPass  gathers the visible meshes for the pass's render tags
//                 (purposes) and the camera, and calls the Rust core
//                 (th_render) for colour and depth.
//
// Each mesh hands the core a key and a version (bumped by every rebuild),
// and the delegate a ThCache, so the GPU path keeps unchanged meshes on the
// card from frame to frame.
//
// Every pixel decision is the core's (src/raster.rs). Nothing here needs a
// GPU, a GL context or a display.

#include "th_delegate_core.h"

#include <pxr/pxr.h>
#include <pxr/base/gf/half.h>
#include <pxr/base/gf/matrix4d.h>
#include <pxr/base/gf/quatf.h>
#include <pxr/base/gf/quath.h>
#include <pxr/base/gf/range3d.h>
#include <pxr/base/gf/rotation.h>
#include <pxr/base/gf/vec2f.h>
#include <pxr/base/gf/vec3d.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/gf/vec3i.h>
#include <pxr/base/gf/vec4d.h>
#include <pxr/base/gf/vec4f.h>
#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/stringUtils.h>
#include <pxr/base/vt/array.h>
#include <pxr/base/work/loops.h>
#include <pxr/imaging/hd/aov.h>
#include <pxr/imaging/hd/basisCurves.h>
#include <pxr/imaging/hd/camera.h>
#include <pxr/imaging/hd/changeTracker.h>
#include <pxr/imaging/hd/extComputation.h>
#include <pxr/imaging/hd/extComputationUtils.h>
#include <pxr/imaging/hd/instancer.h>
#include <pxr/imaging/hd/light.h>
#include <pxr/imaging/hd/material.h>
#include <pxr/imaging/hd/mesh.h>
#include <pxr/imaging/hd/points.h>
#include <pxr/imaging/hd/renderBuffer.h>
#include <pxr/imaging/hd/renderDelegate.h>
#include <pxr/imaging/hd/renderIndex.h>
#include <pxr/imaging/hd/renderPass.h>
#include <pxr/imaging/hd/renderPassState.h>
#include <pxr/imaging/hd/rendererCreateArgs.h>
#include <pxr/imaging/hd/rendererPlugin.h>
#include <pxr/imaging/hd/rendererPluginRegistry.h>
#include <pxr/imaging/hd/resourceRegistry.h>
#include <pxr/imaging/hd/retainedDataSource.h>
#include <pxr/imaging/hd/sceneDelegate.h>
#include <pxr/imaging/hd/sceneIndexPluginRegistry.h>
#include <pxr/imaging/hd/timeSampleArray.h>
#include <pxr/imaging/hd/tokens.h>
#include <pxr/imaging/hdsi/implicitSurfaceSceneIndex.h>
#include <pxr/imaging/hio/image.h>
#include <pxr/imaging/pxOsd/refinerFactory.h>
#include <pxr/usd/sdf/assetPath.h>

#include <opensubdiv/far/primvarRefiner.h>
#include <opensubdiv/far/topologyRefiner.h>

#include <algorithm>
#include <cstring>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

TfToken const _tokDisplayColor("displayColor");
TfToken const _tokNone("none");          // polygonal subdivision schemes
TfToken const _tokBilinear("bilinear");
// Samples per pixel, 1, 2, 4, 8 or 16 (1 = no anti-aliasing); unset,
// TH_PLAYBLAST_SAMPLES, else the default.
TfToken const _tokSamples("th:samples");
int const _defaultSamples = 8;
// "lit", "clay", "albedo" or "occlusion"; unset, TH_PLAYBLAST_SHADING, else lit.
TfToken const _tokShading("th:shading");
// Screen-space ambient occlusion; unset, TH_PLAYBLAST_AO (0/false/off), else on.
TfToken const _tokAo("th:ao");

std::string _SettingString(VtValue const& value)
{
    if (value.IsHolding<TfToken>()) return value.UncheckedGet<TfToken>().GetString();
    if (value.IsHolding<std::string>()) return value.UncheckedGet<std::string>();
    return {};
}

std::string _EnvLower(char const* name)
{
    char const* value = std::getenv(name);
    return value ? TfStringToLower(TfStringTrim(value)) : std::string();
}

// The core's shading number (ThFrame::shading): 0 lit, 1 clay, 2 albedo, 3 occlusion.
uint32_t _ShadingNumber(std::string const& name)
{
    std::string const n = TfStringToLower(TfStringTrim(name));
    if (n == "clay") return 1;
    if (n == "albedo") return 2;
    if (n == "occlusion") return 3;
    return 0;
}

// What lights the frame: "scene" (the scene's own UsdLux lights when it has
// any, else the fixed camera rig) or "headlight" (the rig always); unset,
// TH_PLAYBLAST_LIGHTING, else scene.
TfToken const _tokLighting("th:lighting");

// The core's lighting number (ThFrame::lighting): 0 scene, 1 headlight.
uint32_t _LightingNumber(std::string const& name)
{
    std::string const n = TfStringToLower(TfStringTrim(name));
    return n == "headlight" ? 1u : 0u;
}

// Which faces every mesh drops: "off" (only what Hydra's cull styles ask,
// see Culling in ThMesh::Views) or "back" (every mesh's back faces, whatever
// Hydra asks); unset, TH_PLAYBLAST_CULL, else off.
TfToken const _tokCull("th:cull");

// Whether th:cull asks for every mesh's back faces culled.
bool _CullBack(std::string const& name)
{
    return TfStringToLower(TfStringTrim(name)) == "back";
}

// Motion blur over the render camera's shutter; unset, TH_PLAYBLAST_MOTION_BLUR
// (1/true/on), else off.
TfToken const _tokMotionBlur("th:motionBlur");
// Depth of field from the render camera's lens (an f-stop of 0 is none);
// unset, TH_PLAYBLAST_DOF, else off.
TfToken const _tokDepthOfField("th:depthOfField");
// Layers spread over the shutter and the lens when either blur is on (the
// core takes the largest power of two up to 64, at least th:samples); unset,
// TH_PLAYBLAST_BLUR_SAMPLES, else the default.
TfToken const _tokBlurSamples("th:blurSamples");
int const _defaultBlurSamples = 16;
// Frames per second, which turns a mesh's velocities (per second) into its
// motion over a shutter given in frames; unset, TH_PLAYBLAST_FPS, else 24.
TfToken const _tokFps("th:fps");
// A transparent background: no backdrop, the colour AOV premultiplied by
// coverage and its alpha the coverage (src/composite.rs); unset,
// TH_PLAYBLAST_ALPHA (1/true/on), else off (opaque over the grey backdrop).
TfToken const _tokAlpha("th:alpha");
// Motion is sampled this many frames either side of the frame, and the
// camera's shutter is held to that window.
float const _motionWindow = 1.0f;

// Draw on a background thread and return from Execute at once, as a
// viewport needs; unset, TH_PLAYBLAST_INTERACTIVE, else on in Houdini's
// scene viewer (which sets houdini:viewport; husk does not), off elsewhere.
TfToken const _tokInteractive("th:interactive");
TfToken const _tokHoudiniViewport("houdini:viewport");
// Draw on the GPU (src/gpu.rs) where the core was built with it and an
// adapter works, else on the CPU; unset, TH_PLAYBLAST_GPU, else on for an
// interactive pass (the viewer: a 4.5M triangle frame took 150 ms as a CPU
// draft, 22 ms on an RTX card) and off for husk, whose frames then match a
// farm node's with no GPU.
TfToken const _tokGpu("th:gpu");

// Whether a setting is there and not false, zero or empty.
bool _Truthy(VtValue const& value)
{
    if (value.IsEmpty()) return false;
    if (value.IsHolding<bool>()) return value.UncheckedGet<bool>();
    if (value.IsHolding<int>()) return value.UncheckedGet<int>() != 0;
    if (value.IsHolding<int64_t>()) return value.UncheckedGet<int64_t>() != 0;
    if (value.IsHolding<double>()) return value.UncheckedGet<double>() != 0.0;
    if (value.IsHolding<float>()) return value.UncheckedGet<float>() != 0.0f;
    std::string const s = TfStringToLower(TfStringTrim(_SettingString(value)));
    return !(s.empty() || s == "0" || s == "false" || s == "off");
}

// An on/off setting: a bool or int render setting, else `env` set to 1,
// true or on.
bool _SettingOn(VtValue const& value, char const* env)
{
    if (value.IsHolding<bool>()) return value.UncheckedGet<bool>();
    if (value.IsHolding<int>()) return value.UncheckedGet<int>() != 0;
    std::string const s = _EnvLower(env);
    return s == "1" || s == "true" || s == "on";
}

// An on/off setting with a default: a bool or int render setting, else
// `env` when set (1, true or on), else `fallback`.
bool _SettingOr(VtValue const& value, char const* env, bool fallback)
{
    if (value.IsHolding<bool>()) return value.UncheckedGet<bool>();
    if (value.IsHolding<int>()) return value.UncheckedGet<int>() != 0;
    if (std::getenv(env)) {
        std::string const s = _EnvLower(env);
        return s == "1" || s == "true" || s == "on";
    }
    return fallback;
}

// A positive number: the render setting, else `env`, else `fallback`.
double _SettingNumber(VtValue const& value, char const* env, double fallback)
{
    double v = 0.0;
    if (value.IsHolding<int>()) v = value.UncheckedGet<int>();
    else if (value.IsHolding<float>()) v = value.UncheckedGet<float>();
    else if (value.IsHolding<double>()) v = value.UncheckedGet<double>();
    else if (char const* e = std::getenv(env)) v = std::atof(e);
    return v > 0.0 ? v : fallback;
}

// -----------------------------------------------------------------------------
// Background rendering, for a viewport
// -----------------------------------------------------------------------------
//
// husk asks for a frame and waits for it; Houdini's scene viewer calls a
// pass's Execute on its UI thread, and a frame drawn there froze the UI for
// as long as it took (a 4.5M triangle scene: 355 ms per camera move). So an
// interactive pass (ThRenderPass) gathers the frame on the calling thread
// and draws it on a ThRenderThread of its own, returning at once and
// reporting itself not converged until the frame is in; the viewer keeps
// calling Execute meanwhile, and each call publishes whatever has finished.
//
// The frame borrows the meshes' and materials' arrays (ThMeshView), so
// nothing may change them while it is drawn: a mesh or material Sync, and
// every Destroy, first stops the delegate's background renders
// (_Workers::Edited), the way HdEmbree's render param stops its render
// thread before an edit. A camera or instancer Sync only counts as an edit
// (_Workers::Changed): a frame holds its own copy of the camera and of the
// instance transforms, and the frame being drawn as the camera moves is
// worth finishing (ThRenderThread::Request).

// Everything one frame draws: the views and the arrays they point at.
struct _Job
{
    std::vector<ThMeshView> views;
    std::vector<std::vector<double>> transforms;  // owned; views point in
    std::vector<std::vector<double>> keys;
    // Basis curves and points, and what they point at.
    std::vector<ThCurvesView> curves;
    std::vector<std::vector<double>> curveTransforms;
    std::vector<std::vector<double>> curveKeys;
    // Per view, the meshes' then the curves' (the core's ids count curves
    // view j as views.size() + j): the rprim's id, and from an instancer.
    std::vector<int32_t> primIds;
    std::vector<uint8_t> instanced;
    // The scene's enabled, visible lights, copied (ThFrame::lights points
    // here when the frame is drawn).
    std::vector<ThLight> lights;
    // A primId or instanceId buffer is bound (the viewer's picking). Only
    // then are ids asked of the core: the GPU path pays a pass for them.
    bool ids = false;
    // For the render stats: the subdivision surfaces drawn refined, the
    // refine level Houdini asked of them and the highest drawn (lower where
    // a husk frame needs no more, or a mesh would pass _kMostRefinedFaces),
    // and the triangles drawn.
    size_t subdivided = 0;
    int refineAsked = 0;
    int refineDrawn = 0;
    size_t triangles = 0;
};

// What the viewer's render stats show (HdRenderDelegate::GetRenderStats):
// the last frame put in the buffers.
struct _FrameStats
{
    bool any = false;
    bool draft = false;
    uint32_t samples = 0;
    size_t meshes = 0;
    size_t triangles = 0;
    size_t subdivided = 0;
    int refineAsked = 0;
    int refineDrawn = 0;
};

// A finished frame, row 0 at the top: colour, depth, and per texel the
// Hydra prim id and instance index (-1 where none) a viewport picks by
// (empty when no id buffer is bound). A viewport's images are reused frame
// to frame, so their buffers are allocated once.
struct _Image
{
    unsigned int width = 0;
    unsigned int height = 0;
    std::vector<float> color;
    std::vector<float> depth;
    std::vector<int32_t> primId;
    std::vector<int32_t> instanceId;
    std::vector<int32_t> coreIds;  // the core's (view, instance) per texel
    bool final = false;      // the request's last frame, not its draft
    uint64_t request = 0;
    double coreMs = 0.0;     // of the time it took, in the core (TH_PLAYBLAST_TIMING)
};

static_assert(sizeof(std::atomic<uint32_t>) == sizeof(uint32_t)
                  && std::atomic<uint32_t>::is_always_lock_free,
              "th_render_ids reads the cancel flag as a plain 32-bit atomic");

// Draw `job` through `frame` into `out` (sized to the frame). The core's
// status: 0 ok, 3 cancelled.
int32_t _Draw(_Job const& job, ThFrame const& asked, _Image* out,
              std::atomic<uint32_t> const* cancel, ThCache const* cache, std::mutex* cacheMutex)
{
    // The job's lights, which live as long as it does.
    ThFrame frame = asked;
    frame.lights = job.lights.empty() ? nullptr : job.lights.data();
    frame.light_count = static_cast<uint32_t>(job.lights.size());
    size_t const texels = static_cast<size_t>(frame.width) * frame.height;
    out->width = frame.width;
    out->height = frame.height;
    // Sized only: the core writes every texel (a cleared 1080p frame cost
    // milliseconds of page faults and stores).
    out->color.resize(texels * 4);
    out->depth.resize(texels);
    std::vector<int32_t>& ids = out->coreIds;
    ids.resize(job.ids ? texels * 2 : 0);
    int32_t status;
    {
        // One core call at a time per cache (the GPU path keeps it).
        std::lock_guard<std::mutex> lock(*cacheMutex);
        auto const started = std::chrono::steady_clock::now();
        status = th_render_scene(&frame, job.views.data(), static_cast<uint32_t>(job.views.size()),
                                 job.curves.data(), static_cast<uint32_t>(job.curves.size()),
                                 out->color.data(), out->depth.data(), job.ids ? ids.data() : nullptr,
                                 reinterpret_cast<uint32_t const*>(cancel), cache);
        out->coreMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    }
    if (status != 0) return status;
    if (!job.ids) {
        out->primId.clear();
        out->instanceId.clear();
        return 0;
    }
    out->primId.resize(texels);
    out->instanceId.resize(texels);
    for (size_t i = 0; i < texels; ++i) {
        int32_t const view = ids[i * 2];
        if (view < 0 || static_cast<size_t>(view) >= job.primIds.size()) {
            out->primId[i] = -1;
            out->instanceId[i] = -1;
            continue;
        }
        out->primId[i] = job.primIds[view];
        // Hydra's convention: an instance index for an instanced prim,
        // -1 for one that is not.
        out->instanceId[i] = job.instanced[view] ? ids[i * 2 + 1] : -1;
    }
    return 0;
}

// One pass's background render: the frames of the newest request drawn in
// order (a draft, then the finished frame), each published as it finishes.
//
// A newer request replaces the frames still queued, and the frame being
// drawn is finished unless the new request's draft would wait behind a
// costly frame (ThRenderThread::Request). Cancelling whatever was drawing
// starved the viewer: while the camera moved, each move cancelled a draft
// that was nearly done, and nothing showed until the camera stopped.
class ThRenderThread
{
public:
    ThRenderThread(ThCache const* cache, std::mutex* cacheMutex)
        : _cache(cache), _cacheMutex(cacheMutex), _thread([this] { _Run(); }) {}

    ~ThRenderThread()
    {
        Stop();
        {
            std::lock_guard<std::mutex> lock(_mutex);
            _quit = true;
        }
        _wake.notify_all();
        _thread.join();
    }

    // Give up the frame being drawn and anything queued; returns once the
    // thread holds no view of the scene.
    void Stop()
    {
        std::unique_lock<std::mutex> lock(_mutex);
        _frames.clear();
        _next = 0;
        _job.reset();
        if (_busy) {
            _cancel.store(1, std::memory_order_relaxed);
            _idle.wait(lock, [this] { return !_busy; });
        }
    }

    // Draw `frames` of `job` for `request`, in place of any frames still
    // queued, once the frame being drawn is done. That frame is cancelled
    // when `cancel` (another size: it could not be shown), or when it is a
    // request's finished frame and the new request starts with a draft
    // (which would wait behind it for nothing).
    void Request(std::shared_ptr<_Job const> job, std::vector<ThFrame> frames, uint64_t request, bool cancel)
    {
        {
            std::lock_guard<std::mutex> lock(_mutex);
            if (_busy && (cancel || (_drawingFinal && frames.size() > 1))) {
                _cancel.store(1, std::memory_order_relaxed);
            }
            _job = std::move(job);
            _frames = std::move(frames);
            _next = 0;
            _request = request;
        }
        _wake.notify_all();
    }

    // Swap the newest finished frame into `*out`, when it is newer than
    // `*seen`; `*out`'s buffers go back to the thread to draw into.
    bool Take(uint64_t* seen, _Image* out)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_serial == *seen) return false;
        *seen = _serial;
        std::swap(*out, _latest);
        return true;
    }

    // How long the last finished (not draft) frame took to draw.
    double LastFinalMs() const { return _lastFinalMs.load(std::memory_order_relaxed); }

private:
    void _Run()
    {
        std::unique_lock<std::mutex> lock(_mutex);
        for (;;) {
            _wake.wait(lock, [this] { return _quit || _next < _frames.size(); });
            if (_quit) return;
            std::shared_ptr<_Job const> job = _job;
            ThFrame const frame = _frames[_next++];
            bool const final = _next == _frames.size();
            uint64_t const request = _request;
            _busy = true;
            _drawingFinal = final;
            _cancel.store(0, std::memory_order_relaxed);
            lock.unlock();

            auto const started = std::chrono::steady_clock::now();
            int32_t const status = _Draw(*job, frame, &_spare, &_cancel, _cache, _cacheMutex);
            double const ms = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - started).count();
            if (status == 0) {
                _spare.final = final;
                _spare.request = request;
                if (final) _lastFinalMs.store(ms, std::memory_order_relaxed);
                if (std::getenv("TH_PLAYBLAST_TIMING")) {
                    std::fprintf(stderr, "hdThPlayblast: viewport %s %ux%u, %u samples%s, %zu meshes: %.0f ms (core %.0f)\n",
                                 final ? "frame" : "draft", _spare.width, _spare.height, frame.samples,
                                 frame.ao ? " + AO" : "", job->views.size(), ms, _spare.coreMs);
                }
            } else if (status != 3) {
                std::fprintf(stderr, "hdThPlayblast: the core failed to render (error %d)\n", status);
            }
            job.reset();  // before Stop may return

            lock.lock();
            if (status == 0) {
                std::swap(_latest, _spare);
                ++_serial;
            } else if (status != 3 && _request == request) {
                _next = _frames.size();  // the rest of the request would fail as well
            }
            _busy = false;
            _drawingFinal = false;
            _idle.notify_all();
        }
    }

    ThCache const* _cache;
    std::mutex* _cacheMutex;
    std::mutex _mutex;
    std::condition_variable _wake;
    std::condition_variable _idle;
    bool _quit = false;
    bool _busy = false;
    bool _drawingFinal = false;  // the frame drawing is its request's last
    std::shared_ptr<_Job const> _job;
    std::vector<ThFrame> _frames;  // the newest request's, drawn from _next
    size_t _next = 0;
    uint64_t _request = 0;
    std::atomic<uint32_t> _cancel{0};
    _Image _spare;   // drawn into (the thread's own)
    _Image _latest;  // the newest finished, until Take swaps it out
    uint64_t _serial = 0;
    std::atomic<double> _lastFinalMs{0.0};
    std::thread _thread;  // last: starts once everything above is built
};

// A delegate's background renders, which every scene edit stops first, and
// a count of the edits (a pass redraws when it changes). Shared with the
// passes, which may outlive their delegate's render param.
struct _Workers
{
    std::mutex mutex;
    std::vector<ThRenderThread*> threads;
    std::atomic<uint64_t> edits{0};
    std::mutex cacheMutex;  // one core call per cache at a time
    std::mutex statsMutex;
    _FrameStats stats;

    void Drawn(_FrameStats const& s)
    {
        std::lock_guard<std::mutex> lock(statsMutex);
        stats = s;
    }
    _FrameStats LastDrawn()
    {
        std::lock_guard<std::mutex> lock(statsMutex);
        return stats;
    }

    void Edited()
    {
        edits.fetch_add(1, std::memory_order_relaxed);
        std::lock_guard<std::mutex> lock(mutex);
        for (ThRenderThread* t : threads) t->Stop();
    }
    // A change no frame reads while it draws: counted, so the passes draw
    // again, without stopping them.
    void Changed() { edits.fetch_add(1, std::memory_order_relaxed); }
    void Add(ThRenderThread* t)
    {
        std::lock_guard<std::mutex> lock(mutex);
        threads.push_back(t);
    }
    void Remove(ThRenderThread* t)
    {
        std::lock_guard<std::mutex> lock(mutex);
        threads.erase(std::remove(threads.begin(), threads.end(), t), threads.end());
    }
};

// What the render delegate tells its prims as they sync: whether to sample
// their motion (th:motionBlur), and the background renders to stop before
// an edit.
struct ThRenderParam final : public HdRenderParam
{
    std::atomic<bool> motion{false};
    std::shared_ptr<_Workers> workers = std::make_shared<_Workers>();
};

// Before a prim changes the arrays a background render reads: stop it.
void _Edited(HdRenderParam* param)
{
    if (auto* p = static_cast<ThRenderParam*>(param)) p->workers->Edited();
}

// A prim changed that a background render copied when it started (the
// camera, an instancer's transforms): draw again, leaving it running.
void _Changed(HdRenderParam* param)
{
    if (auto* p = static_cast<ThRenderParam*>(param)) p->workers->Changed();
}

bool _SamplingMotion(HdRenderParam* param)
{
    auto* p = static_cast<ThRenderParam*>(param);
    return p && p->motion.load(std::memory_order_relaxed);
}

// A transform's samples over the motion window.
using _XformSamples = HdTimeSampleArray<GfMatrix4d, 4>;

// The shutter a frame is drawn over, frame-relative, and the frame rate.
struct _Shutter
{
    bool on = false;
    float open = 0.0f;
    float close = 0.0f;
    float fps = 24.0f;

    bool operator==(_Shutter const& o) const
    {
        return on == o.on && open == o.open && close == o.close && fps == o.fps;
    }

    // The moment of key `k` of `keys`, evenly spread from open to close.
    float At(size_t k, size_t keys) const
    {
        return keys < 2 ? open : open + (close - open) * static_cast<float>(k) / static_cast<float>(keys - 1);
    }
};

// Keys (2 to 4) a transform sampled as `a` needs over the shutter: the two
// ends, plus one for each sample strictly inside it.
size_t _KeyCount(_XformSamples const& a, _Shutter const& s)
{
    size_t inside = 0;
    for (size_t i = 0; i < a.count; ++i) {
        if (a.times[i] > s.open && a.times[i] < s.close) ++inside;
    }
    return std::min<size_t>(4, 2 + inside);
}

// A sampled transform at `t`, or `still` when it has no samples.
GfMatrix4d _XformAt(_XformSamples const& a, float t, GfMatrix4d const& still)
{
    return a.count > 0 ? a.Resample(t) : still;
}

// -----------------------------------------------------------------------------
// Textures: decoded once per file through Hio, kept by the core.
// -----------------------------------------------------------------------------

float _SrgbToLinear(float v)
{
    return v <= 0.04045f ? v / 12.92f : std::pow((v + 0.055f) / 1.055f, 2.4f);
}

// The texture at `path`, or null when Hio cannot read it (the mesh then
// falls back to its base colour).
ThTexture* _ReadTexture(std::string const& path)
{
    HioImageSharedPtr image = HioImage::OpenForReading(path);
    if (!image) {
        TF_WARN("hdThPlayblast: cannot read texture %s", path.c_str());
        return nullptr;
    }
    HioImage::StorageSpec spec;
    spec.width = image->GetWidth();
    spec.height = image->GetHeight();
    spec.depth = 1;
    spec.format = image->GetFormat();
    spec.flipped = false;
    size_t const texelSize = HioGetDataSizeOfFormat(spec.format);
    int const channels = HioGetComponentCount(spec.format);
    if (spec.width <= 0 || spec.height <= 0 || texelSize == 0 || channels <= 0) {
        return nullptr;
    }
    std::vector<uint8_t> raw(static_cast<size_t>(spec.width) * spec.height * texelSize);
    spec.data = raw.data();
    if (!image->Read(spec)) {
        TF_WARN("hdThPlayblast: failed to decode texture %s", path.c_str());
        return nullptr;
    }
    HioType const type = HioGetHioType(spec.format);
    bool const srgb = image->IsColorSpaceSRGB() || type == HioTypeUnsignedByteSRGB;
    size_t const texels = static_cast<size_t>(spec.width) * spec.height;
    std::vector<float> rgba(texels * 4, 1.0f);
    for (size_t i = 0; i < texels; ++i) {
        float px[4] = {0.0f, 0.0f, 0.0f, 1.0f};
        for (int c = 0; c < channels && c < 4; ++c) {
            size_t const at = i * channels + c;
            switch (type) {
            case HioTypeUnsignedByte:
            case HioTypeUnsignedByteSRGB:
                px[c] = raw[at] / 255.0f;
                break;
            case HioTypeUnsignedShort:
                px[c] = reinterpret_cast<uint16_t const*>(raw.data())[at] / 65535.0f;
                break;
            case HioTypeHalfFloat:
                px[c] = reinterpret_cast<GfHalf const*>(raw.data())[at];
                break;
            case HioTypeFloat:
                px[c] = reinterpret_cast<float const*>(raw.data())[at];
                break;
            default:
                return nullptr;
            }
            if (srgb && c < 3) {
                px[c] = _SrgbToLinear(px[c]);
            }
        }
        if (channels < 3) {  // grey (+ alpha)
            float const a = channels == 2 ? px[1] : 1.0f;
            px[1] = px[2] = px[0];
            px[3] = a;
        }
        std::copy(px, px + 4, &rgba[i * 4]);
    }
    return th_texture_create(rgba.data(), spec.width, spec.height);
}

class TextureCache
{
public:
    ~TextureCache()
    {
        for (auto& entry : _textures) {
            th_texture_free(entry.second);
        }
    }

    // The tiles of `path`: one tile 0 for a plain file, every UDIM tile that
    // exists for a `<UDIM>` path.
    std::vector<ThTextureTile> Tiles(std::string const& path)
    {
        std::vector<ThTextureTile> tiles;
        size_t const udim = path.find("<UDIM>");
        if (udim == std::string::npos) {
            if (ThTexture* t = _Get(path)) {
                tiles.push_back({0, t});
            }
            return tiles;
        }
        for (int tile = 1001; tile <= 1100; ++tile) {
            std::string file = path;
            file.replace(udim, 6, std::to_string(tile));
            std::error_code ec;
            if (!std::filesystem::exists(file, ec)) {
                continue;
            }
            if (ThTexture* t = _Get(file)) {
                tiles.push_back({tile, t});
            }
        }
        return tiles;
    }

private:
    ThTexture* _Get(std::string const& file)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        auto it = _textures.find(file);
        if (it != _textures.end()) {
            return it->second;
        }
        ThTexture* t = _ReadTexture(file);
        _textures[file] = t;  // a failed read is remembered too
        return t;
    }

    std::mutex _mutex;
    std::unordered_map<std::string, ThTexture*> _textures;
};

// -----------------------------------------------------------------------------
// Primvars to per-corner arrays
// -----------------------------------------------------------------------------

template <typename T>
VtArray<T> _AsArray(VtValue const& value)
{
    if (value.IsHolding<VtArray<T>>()) {
        return value.UncheckedGet<VtArray<T>>();
    }
    if (value.IsHolding<T>()) {
        return VtArray<T>(1, value.UncheckedGet<T>());
    }
    return VtArray<T>();
}

// Primvar `name`'s values with its indices applied (one per face vertex for
// an indexed face-varying primvar), or empty when it does not have that type.
template <typename T>
VtArray<T> _FlatValues(HdSceneDelegate* delegate, SdfPath const& id, TfToken const& name)
{
    VtIntArray indices;
    VtArray<T> values = _AsArray<T>(delegate->GetIndexedPrimvar(id, name, &indices));
    if (values.empty() || indices.empty()) {
        return values;
    }
    VtArray<T> flat(indices.size());
    for (size_t i = 0; i < indices.size(); ++i) {
        int const at = indices[i];
        flat[i] = at >= 0 && static_cast<size_t>(at) < values.size() ? values[at] : T();
    }
    return flat;
}

// One value per triangle corner (3 per triangle) of `values`, a primvar of
// interpolation `interp` over the faces triangulated, or empty when it is
// short of values. `corners` and `faces` are th_triangulate's: per triangle,
// three positions in the face vertex indices `fvi` (where a face-varying
// value is too) and its face.
template <typename T>
std::vector<T> _CornerValues(VtArray<T> const& values, HdInterpolation interp,
                             std::vector<uint32_t> const& corners,
                             std::vector<uint32_t> const& faces, VtIntArray const& fvi)
{
    if (values.empty()) {
        return {};
    }
    size_t const n = faces.size();
    std::vector<T> out(n * 3);
    switch (interp) {
    case HdInterpolationConstant:
        std::fill(out.begin(), out.end(), values[0]);
        return out;
    case HdInterpolationUniform:
        for (size_t t = 0; t < n; ++t) {
            if (faces[t] >= values.size()) return {};
            out[t * 3] = out[t * 3 + 1] = out[t * 3 + 2] = values[faces[t]];
        }
        return out;
    case HdInterpolationVertex:
    case HdInterpolationVarying:
        for (size_t c = 0; c < n * 3; ++c) {
            int const v = fvi[corners[c]];
            if (v < 0 || static_cast<size_t>(v) >= values.size()) return {};
            out[c] = values[v];
        }
        return out;
    case HdInterpolationFaceVarying:
        // One value per face vertex, holes included: read at the corner.
        for (size_t c = 0; c < n * 3; ++c) {
            if (corners[c] >= values.size()) return {};
            out[c] = values[corners[c]];
        }
        return out;
    default:
        return {};
    }
}

// -----------------------------------------------------------------------------
// Subdivision
// -----------------------------------------------------------------------------
//
// A Catmull-Clark or Loop mesh is drawn refined to the level Houdini asks
// for, as its own viewport does: HdDisplayStyle::refineLevel, which the
// viewer's Level of Detail sets (libHUSD's refine level fallback) and husk's
// --complexity. Refined uniformly by OpenSubdiv (through USD's pxOsd, which
// reads the scheme, creases, corners, holes and boundary rules from the
// topology, as Storm does), and drawn as the faces of the last level.
//
// That level is the most a mesh is refined to. Houdini's viewer gets it
// exactly, as Storm draws it. husk's default complexity (veryhigh) asks for
// level 8: 65,536 faces of each one, many more than a frame has pixels to
// show. So a husk frame refines each mesh only until its edges are about
// _kRefinedEdgePixels long where it comes closest to the camera, and not at
// all while it is out of view (ThMesh::ScreenLevel). Past that, refining
// changes nothing the frame can show.
//
// A pass picks each mesh's level as it draws, from its own camera
// (ThRenderPass::_SizeRefinement), and rebuilds the meshes whose level
// changed. Sync rebuilds a mesh at the level last picked for it: the cage,
// until a pass has picked one.

using _OsdRefiner = OpenSubdiv::Far::TopologyRefiner;

// A value OpenSubdiv's primvar refiner can blend.
template <typename T>
struct _OsdValue
{
    T v;
    void Clear() { v = T(0.0f); }
    void AddWithWeight(_OsdValue const& s, float w) { v += s.v * w; }
};

// How a primvar's values are carried down the levels.
enum class _Rate { Vertex, Varying, Uniform, FaceVarying };

// `coarse` (values at level 0, at `rate`, of face-varying channel `channel`)
// at the refiner's last level; empty when there are too few of them.
template <typename T>
VtArray<T> _Refine(_OsdRefiner const& refiner, _Rate rate, int channel, VtArray<T> const& coarse)
{
    auto count = [&](int level) -> size_t {
        OpenSubdiv::Far::TopologyLevel const& l = refiner.GetLevel(level);
        switch (rate) {
        case _Rate::Uniform: return static_cast<size_t>(l.GetNumFaces());
        case _Rate::FaceVarying: return static_cast<size_t>(l.GetNumFVarValues(channel));
        default: return static_cast<size_t>(l.GetNumVertices());
        }
    };
    size_t const first = count(0);
    if (coarse.size() < first) return {};
    std::vector<_OsdValue<T>> src(first), dst;
    for (size_t i = 0; i < first; ++i) src[i].v = coarse[i];
    OpenSubdiv::Far::PrimvarRefiner const primvars(refiner);
    for (int level = 1; level <= refiner.GetMaxLevel(); ++level) {
        dst.assign(count(level), _OsdValue<T>{T(0.0f)});
        _OsdValue<T> const* s = src.data();
        _OsdValue<T>* d = dst.data();
        switch (rate) {
        case _Rate::Vertex: primvars.Interpolate(level, s, d); break;
        case _Rate::Varying: primvars.InterpolateVarying(level, s, d); break;
        case _Rate::Uniform: primvars.InterpolateFaceUniform(level, s, d); break;
        case _Rate::FaceVarying: primvars.InterpolateFaceVarying(level, s, d, channel); break;
        }
        src.swap(dst);
    }
    VtArray<T> out(src.size());
    for (size_t i = 0; i < src.size(); ++i) out[i] = src[i].v;
    return out;
}

// The most faces a refined mesh may have: a mesh that would pass it at the
// level asked for is drawn a level or more lower (each level makes four
// faces of one), rather than run the process out of memory.
static constexpr size_t _kMostRefinedFaces = size_t(16) << 20;

// The level a mesh of `faces` faces is refined to when `asked` is.
static int _RefineLevelFor(size_t faces, int asked)
{
    int level = std::min(asked, 10);  // OpenSubdiv's limit
    while (level > 0 && (faces << (2 * level)) > _kMostRefinedFaces) --level;
    return level;
}

// The longest a husk frame leaves an edge of a refined mesh, in pixels.
static constexpr double _kRefinedEdgePixels = 2.0;

// The level that brings edges `pixels` long down to _kRefinedEdgePixels
// (each level halves them).
static int _LevelForPixels(double pixels)
{
    int level = 0;
    while (level < 10 && pixels > _kRefinedEdgePixels) {
        pixels *= 0.5;
        ++level;
    }
    return level;
}

// The bounds of a cage's `points` and the mean length of its faces' edges.
static void _MeasureCage(VtVec3fArray const& points, VtIntArray const& counts, VtIntArray const& indices,
                         GfRange3d* box, double* edge)
{
    *box = GfRange3d();
    for (GfVec3f const& p : points) box->UnionWith(GfVec3d(p));
    double sum = 0.0;
    size_t edges = 0;
    size_t at = 0;
    for (int const count : counts) {
        if (count < 0 || at + static_cast<size_t>(count) > indices.size()) break;
        for (int k = 0; k < count; ++k) {
            int const a = indices[at + k];
            int const b = indices[at + (k + 1) % count];
            if (a < 0 || b < 0 || static_cast<size_t>(a) >= points.size()
                || static_cast<size_t>(b) >= points.size()) {
                continue;
            }
            sum += (GfVec3d(points[a]) - GfVec3d(points[b])).GetLength();
            ++edges;
        }
        at += static_cast<size_t>(count);
    }
    *edge = edges ? sum / static_cast<double>(edges) : 0.0;
}

// -----------------------------------------------------------------------------
// Instancer
// -----------------------------------------------------------------------------

class ThInstancer final : public HdInstancer
{
public:
    ThInstancer(HdSceneDelegate* delegate, SdfPath const& id) : HdInstancer(delegate, id) {}

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits) override
    {
        // A frame copies its instance transforms as it is gathered.
        _Changed(param);
        _UpdateInstancer(delegate, dirtyBits);
        SdfPath const& id = GetId();
        if (_SamplingMotion(param) && HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
            std::lock_guard<std::mutex> lock(_mutex);
            delegate->SampleInstancerTransform(id, -_motionWindow, _motionWindow, &_xformSamples);
        }
        if (HdChangeTracker::IsAnyPrimvarDirty(*dirtyBits, id)) {
            std::lock_guard<std::mutex> lock(_mutex);
            for (HdPrimvarDescriptor const& pv :
                 delegate->GetPrimvarDescriptors(id, HdInterpolationInstance)) {
                if (HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, pv.name)) {
                    _primvars[pv.name] = delegate->Get(id, pv.name);
                }
            }
        }
        *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
    }

    // Keys (2 to 4) this instancer's transform, and its parents', need over
    // the shutter.
    size_t KeyCount(_Shutter const& s)
    {
        size_t keys = 2;
        {
            std::lock_guard<std::mutex> lock(_mutex);
            keys = _KeyCount(_xformSamples, s);
        }
        if (auto* parent = dynamic_cast<ThInstancer*>(GetDelegate()->GetRenderIndex().GetInstancer(GetParentId()))) {
            keys = std::max(keys, parent->KeyCount(s));
        }
        return keys;
    }

    // Object-to-world of every instance of `prototypeId`, parents applied;
    // with `time`, the instancers' transforms at that moment (frame-relative)
    // of their motion samples. The instances' own placement is the frame's.
    std::vector<GfMatrix4d> ComputeInstanceTransforms(SdfPath const& prototypeId, float const* time = nullptr)
    {
        HdSceneDelegate* delegate = GetDelegate();
        GfMatrix4d instancerXform = delegate->GetInstancerTransform(GetId());
        if (time) {
            std::lock_guard<std::mutex> lock(_mutex);
            instancerXform = _XformAt(_xformSamples, *time, instancerXform);
        }
        VtIntArray const indices = delegate->GetInstanceIndices(GetId(), prototypeId);

        std::vector<GfMatrix4d> xforms(indices.size(), instancerXform);
        {
            std::lock_guard<std::mutex> lock(_mutex);
            auto get = [&](TfToken const& token) -> VtValue {
                auto it = _primvars.find(token);
                return it == _primvars.end() ? VtValue() : it->second;
            };
            auto in = [&](size_t size, int at) { return at >= 0 && static_cast<size_t>(at) < size; };

            VtVec3fArray translate = _AsArray<GfVec3f>(get(HdInstancerTokens->instanceTranslations));
            VtValue rotateValue = get(HdInstancerTokens->instanceRotations);
            VtVec3fArray scale = _AsArray<GfVec3f>(get(HdInstancerTokens->instanceScales));
            VtMatrix4dArray matrices = _AsArray<GfMatrix4d>(get(HdInstancerTokens->instanceTransforms));

            for (size_t i = 0; i < indices.size(); ++i) {
                int const at = indices[i];
                if (in(translate.size(), at)) {
                    GfMatrix4d m(1.0);
                    m.SetTranslate(GfVec3d(translate[at]));
                    xforms[i] = m * xforms[i];
                }
                if (rotateValue.IsHolding<VtQuathArray>()) {
                    VtQuathArray const& q = rotateValue.UncheckedGet<VtQuathArray>();
                    if (in(q.size(), at)) {
                        GfMatrix4d m(1.0);
                        m.SetRotate(GfRotation(GfQuatd(q[at].GetReal(), GfVec3d(q[at].GetImaginary()))));
                        xforms[i] = m * xforms[i];
                    }
                } else if (rotateValue.IsHolding<VtQuatfArray>()) {
                    VtQuatfArray const& q = rotateValue.UncheckedGet<VtQuatfArray>();
                    if (in(q.size(), at)) {
                        GfMatrix4d m(1.0);
                        m.SetRotate(GfRotation(GfQuatd(q[at].GetReal(), GfVec3d(q[at].GetImaginary()))));
                        xforms[i] = m * xforms[i];
                    }
                }
                if (in(scale.size(), at)) {
                    GfMatrix4d m(1.0);
                    m.SetScale(GfVec3d(scale[at]));
                    xforms[i] = m * xforms[i];
                }
                if (in(matrices.size(), at)) {
                    xforms[i] = matrices[at] * xforms[i];
                }
            }
        }

        if (GetParentId().IsEmpty()) {
            return xforms;
        }
        auto* parent = dynamic_cast<ThInstancer*>(
            delegate->GetRenderIndex().GetInstancer(GetParentId()));
        if (!parent) {
            return xforms;
        }
        std::vector<GfMatrix4d> const parents = parent->ComputeInstanceTransforms(GetId(), time);
        std::vector<GfMatrix4d> out;
        out.reserve(xforms.size() * parents.size());
        for (GfMatrix4d const& p : parents) {
            for (GfMatrix4d const& x : xforms) {
                out.push_back(x * p);
            }
        }
        return out;
    }

private:
    std::mutex _mutex;
    std::map<TfToken, VtValue> _primvars;
    _XformSamples _xformSamples;
};

// -----------------------------------------------------------------------------
// Material: the colour a playblast shows for a surface.
// -----------------------------------------------------------------------------

class ThMaterial final : public HdMaterial
{
public:
    ThMaterial(SdfPath const& id, TextureCache* textures) : HdMaterial(id), _textures(textures) {}

    HdDirtyBits GetInitialDirtyBitsMask() const override { return HdMaterial::AllDirty; }

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits) override
    {
        _Edited(param);
        if (*dirtyBits & HdMaterial::DirtyResource) {
            _hasColor = false;
            _usesDisplayColor = false;
            _hasOpacity = false;
            _opacityFromDisplay = false;
            _opacity = 1.0f;
            _opacityScale = 1.0f;
            _tiles.clear();
            _uvName = TfToken("st");
            VtValue const resource = delegate->GetMaterialResource(GetId());
            if (resource.IsHolding<HdMaterialNetworkMap>()) {
                _Read(HdConvertToHdMaterialNetwork2(resource.UncheckedGet<HdMaterialNetworkMap>()));
            }
        }
        *dirtyBits = HdMaterial::Clean;
    }

    // A colour of its own (constant or texture). False when the material
    // shows the geometry's display colour, or says nothing usable.
    bool HasColor() const { return _hasColor && !_usesDisplayColor; }
    GfVec3f const& BaseColor() const { return _baseColor; }
    std::vector<ThTextureTile> const& Tiles() const { return _tiles; }
    TfToken const& UvName() const { return _uvName; }

    // An opacity of its own (a constant opacity, transmission or cutout),
    // in [0, 1]. False when the material says nothing about it, or wires it
    // to something we cannot evaluate (a texture): the surface then keeps
    // its display opacity.
    bool HasOpacity() const { return _hasOpacity; }
    float Opacity() const { return _opacity; }
    // The material shows the geometry's display opacity (a primvar reader
    // of displayOpacity), times OpacityScale (what its transmission lets
    // through).
    bool OpacityFromDisplay() const { return _opacityFromDisplay; }
    float OpacityScale() const { return _opacityScale; }

private:
    // A constant float out of a shader parameter: a float, a double, or the
    // mean of a colour (MaterialX standard_surface's opacity is a color3).
    static bool _Scalar(VtValue const& v, float* out)
    {
        if (v.IsHolding<float>()) { *out = v.UncheckedGet<float>(); return true; }
        if (v.IsHolding<double>()) { *out = static_cast<float>(v.UncheckedGet<double>()); return true; }
        if (v.IsHolding<GfVec3f>()) {
            GfVec3f const c = v.UncheckedGet<GfVec3f>();
            *out = (c[0] + c[1] + c[2]) / 3.0f;
            return true;
        }
        if (v.IsHolding<GfVec3d>()) {
            GfVec3d const c = v.UncheckedGet<GfVec3d>();
            *out = static_cast<float>((c[0] + c[1] + c[2]) / 3.0);
            return true;
        }
        return false;
    }

    // How see-through the surface shader is, whichever family it is:
    // UsdPreviewSurface opacity (and opacityThreshold, a cutout: under it
    // nothing, at or over it opaque), MaterialX standard_surface opacity and
    // transmission (Karma's material builder writes these), OpenPBR
    // geometry_opacity and transmission_weight. Transmission is taken as
    // see-through: opacity times one minus it, as the viewport shows glass.
    void _ReadOpacity(HdMaterialNetwork2 const& net, HdMaterialNode2 const& node)
    {
        float opacity = 1.0f;
        bool said = false;
        for (char const* name : {"opacity", "geometry_opacity"}) {
            TfToken const input(name);
            auto conn = node.inputConnections.find(input);
            if (conn != node.inputConnections.end() && !conn->second.empty()) {
                std::string file;
                // Only the display opacity is followed; a texture's alpha
                // or a procedural we cannot evaluate leaves it opaque.
                if (_Trace(net, conn->second[0].upstreamNode, 0, &file) == Source::DisplayOpacity) {
                    _opacityFromDisplay = true;
                }
                break;
            }
            auto param = node.parameters.find(input);
            if (param != node.parameters.end() && _Scalar(param->second, &opacity)) {
                said = true;
                break;
            }
        }
        for (char const* name : {"transmission", "transmission_weight"}) {
            auto param = node.parameters.find(TfToken(name));
            float t = 0.0f;
            if (param != node.parameters.end() && _Scalar(param->second, &t)) {
                _opacityScale = 1.0f - std::clamp(t, 0.0f, 1.0f);
                opacity *= _opacityScale;
                said = true;
                break;
            }
        }
        float threshold = 0.0f;
        auto cutout = node.parameters.find(TfToken("opacityThreshold"));
        if (said && cutout != node.parameters.end() && _Scalar(cutout->second, &threshold) && threshold > 0.0f) {
            opacity = opacity >= threshold ? 1.0f : 0.0f;
        }
        if (said && !_opacityFromDisplay && std::isfinite(opacity)) {
            _hasOpacity = true;
            _opacity = std::clamp(opacity, 0.0f, 1.0f);
        }
    }

    // The base-colour input of the surface shader, whichever family it is,
    // and its opacity (_ReadOpacity).
    void _Read(HdMaterialNetwork2 const& net)
    {
        auto terminal = net.terminals.find(HdMaterialTerminalTokens->surface);
        if (terminal == net.terminals.end()) return;
        auto surface = net.nodes.find(terminal->second.upstreamNode);
        if (surface == net.nodes.end()) return;
        HdMaterialNode2 const& node = surface->second;
        _ReadOpacity(net, node);

        static TfToken const inputs[] = {
            TfToken("diffuseColor"), TfToken("base_color"), TfToken("baseColor"),
            TfToken("color"), TfToken("basecolor")};
        for (TfToken const& input : inputs) {
            auto conn = node.inputConnections.find(input);
            if (conn != node.inputConnections.end() && !conn->second.empty()) {
                std::string file;
                Source const source = _Trace(net, conn->second[0].upstreamNode, 0, &file);
                if (source == Source::Texture) {
                    _tiles = _textures->Tiles(file);
                    if (!_tiles.empty()) {
                        _hasColor = true;
                        return;
                    }
                } else if (source == Source::DisplayColor) {
                    _usesDisplayColor = true;
                    return;
                }
                // Procedural colour we cannot evaluate: the viewport falls
                // back the same way, to the display colour.
                _usesDisplayColor = true;
                return;
            }
            auto param = node.parameters.find(input);
            if (param != node.parameters.end() && param->second.IsHolding<GfVec3f>()) {
                _baseColor = param->second.UncheckedGet<GfVec3f>();
                _hasColor = true;
                return;
            }
        }
    }

    enum class Source { Unknown, Texture, DisplayColor, DisplayOpacity };

    // Walk upstream from `path` to what the colour is made of: an image node
    // (a file parameter; its uv primvar is noted too) or the geometry's
    // display colour (MaterialX geomcolor, or a primvar reader of
    // displayColor). The first input of a node is followed first, so
    // multiply(geomcolor, noise) counts as display colour.
    Source _Trace(HdMaterialNetwork2 const& net, SdfPath const& path, int depth,
                  std::string* file)
    {
        if (depth > 8) return Source::Unknown;
        auto it = net.nodes.find(path);
        if (it == net.nodes.end()) return Source::Unknown;
        HdMaterialNode2 const& node = it->second;
        for (char const* key : {"file", "filename"}) {
            auto p = node.parameters.find(TfToken(key));
            if (p == node.parameters.end()) continue;
            if (p->second.IsHolding<SdfAssetPath>()) {
                SdfAssetPath const& asset = p->second.UncheckedGet<SdfAssetPath>();
                *file = asset.GetResolvedPath().empty() ? asset.GetAssetPath()
                                                        : asset.GetResolvedPath();
            } else if (p->second.IsHolding<std::string>()) {
                *file = p->second.UncheckedGet<std::string>();
            }
            if (!file->empty()) {
                _FindUvName(net, node, 0);
                return Source::Texture;
            }
        }
        std::string const type = node.nodeTypeId.GetString();
        if (type.find("geomcolor") != std::string::npos) {
            return Source::DisplayColor;
        }
        for (char const* key : {"geomprop", "varname", "primvarName"}) {
            auto p = node.parameters.find(TfToken(key));
            if (p == node.parameters.end()) continue;
            std::string name;
            if (p->second.IsHolding<TfToken>()) name = p->second.UncheckedGet<TfToken>().GetString();
            else if (p->second.IsHolding<std::string>()) name = p->second.UncheckedGet<std::string>();
            if (name == _tokDisplayColor.GetString()) return Source::DisplayColor;
            if (name == HdTokens->displayOpacity.GetString()) return Source::DisplayOpacity;
        }
        for (auto const& conn : node.inputConnections) {
            for (HdMaterialConnection2 const& c : conn.second) {
                Source const s = _Trace(net, c.upstreamNode, depth + 1, file);
                if (s != Source::Unknown) return s;
            }
        }
        return Source::Unknown;
    }

    void _FindUvName(HdMaterialNetwork2 const& net, HdMaterialNode2 const& node, int depth)
    {
        if (depth > 4) return;
        for (char const* key : {"varname", "geomprop", "primvarName"}) {
            auto p = node.parameters.find(TfToken(key));
            if (p == node.parameters.end()) continue;
            if (p->second.IsHolding<TfToken>()) {
                _uvName = p->second.UncheckedGet<TfToken>();
                return;
            }
            if (p->second.IsHolding<std::string>()) {
                _uvName = TfToken(p->second.UncheckedGet<std::string>());
                return;
            }
        }
        for (auto const& conn : node.inputConnections) {
            for (HdMaterialConnection2 const& c : conn.second) {
                auto it = net.nodes.find(c.upstreamNode);
                if (it != net.nodes.end()) {
                    _FindUvName(net, it->second, depth + 1);
                }
            }
        }
    }

    TextureCache* _textures;
    bool _hasColor = false;
    bool _usesDisplayColor = false;
    bool _hasOpacity = false;
    bool _opacityFromDisplay = false;
    float _opacity = 1.0f;
    float _opacityScale = 1.0f;
    GfVec3f _baseColor{0.5f};
    std::vector<ThTextureTile> _tiles;
    TfToken _uvName{"st"};
};

// -----------------------------------------------------------------------------
// Mesh
// -----------------------------------------------------------------------------

// TH_PLAYBLAST_TIMING: time spent in mesh Sync (Hydra syncs meshes on several
// threads, so this is thread time, not wall time) and the meshes rebuilt,
// since the last frame.
static bool const g_timing = std::getenv("TH_PLAYBLAST_TIMING") != nullptr;
static std::atomic<long long> g_syncNs{0};
static std::atomic<int> g_rebuilds{0};
static std::atomic<int> g_pointUpdates{0};
static std::atomic<int> g_subdivided{0};  // of those rebuilt, drawn refined
// Mesh keys for the core (ThMeshView::key): never reused in the process.
static std::atomic<uint64_t> g_nextMeshKey{1};
struct _SyncTimer {
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    ~_SyncTimer()
    {
        if (!g_timing) return;
        g_syncNs.fetch_add(std::chrono::duration_cast<std::chrono::nanoseconds>(
                               std::chrono::steady_clock::now() - start).count(),
                           std::memory_order_relaxed);
    }
};

class ThMesh final : public HdMesh
{
public:
    explicit ThMesh(SdfPath const& id) : HdMesh(id), _key(g_nextMeshKey.fetch_add(1)) {}

    HdDirtyBits GetInitialDirtyBitsMask() const override
    {
        return HdChangeTracker::AllSceneDirtyBits;
    }

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits,
              TfToken const&) override
    {
        _SyncTimer timer;
        _Edited(param);
        SdfPath const& id = GetId();
        bool const motion = _SamplingMotion(param);
        if (*dirtyBits & HdChangeTracker::DirtyMaterialId) {
            SetMaterialId(delegate->GetMaterialId(id));
        }
        if (HdChangeTracker::IsVisibilityDirty(*dirtyBits, id)) {
            _UpdateVisibility(delegate, dirtyBits);
        }
        if (HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
            _transform = delegate->GetTransform(id);
            _xformSamples.count = 0;
            if (motion) {
                delegate->SampleTransform(id, -_motionWindow, _motionWindow, &_xformSamples);
            }
        }
        _UpdateInstancer(delegate, dirtyBits);
        HdInstancer::_SyncInstancerAndParents(delegate->GetRenderIndex(), GetInstancerId());

        // The refine level Houdini asks for (the viewer's Level of Detail,
        // husk's --complexity). The pass refines the mesh to it as it draws
        // (see Subdivision), so a new one rebuilds nothing here.
        if (HdChangeTracker::IsDisplayStyleDirty(*dirtyBits, id)) {
            _refineLevel = GetDisplayStyle(delegate).refineLevel;
        }
        if (HdChangeTracker::IsCullStyleDirty(*dirtyBits, id)) {
            _cullStyle = delegate->GetCullStyle(id);
        }
        if (HdChangeTracker::IsDoubleSidedDirty(*dirtyBits, id)) {
            _doubleSided = delegate->GetDoubleSided(id);
        }
        bool const topologyDirty = HdChangeTracker::IsTopologyDirty(*dirtyBits, id);
        bool const pointsDirty = HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->points);
        if (topologyDirty || pointsDirty || HdChangeTracker::IsAnyPrimvarDirty(*dirtyBits, id)) {
            // Only the points (an animated mesh's every frame): the
            // triangles and every per-corner primvar stay as they are.
            bool const othersDirty = (*dirtyBits & (HdChangeTracker::DirtyNormals | HdChangeTracker::DirtyWidths
                                                    | HdChangeTracker::DirtyPrimvar)) != 0;
            if (!topologyDirty && !othersDirty && pointsDirty && _UpdatePoints(delegate, motion)) {
                g_pointUpdates.fetch_add(1, std::memory_order_relaxed);
            } else {
                _Rebuild(delegate, motion);
                g_rebuilds.fetch_add(1, std::memory_order_relaxed);
            }
        }
        *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
    }

    // The refine level Houdini asks of this mesh (0 for a polygonal one),
    // and the level it is drawn at (lower where a husk frame needs no more,
    // or refining it that far would pass _kMostRefinedFaces).
    int RefineAsked() const { return _polygonal ? 0 : _refineLevel; }
    int RefineDrawn() const { return _refiner ? _refiner->GetMaxLevel() : 0; }

    // A subdivision surface Sync has built (only those are refined).
    bool Subdivides() const { return !_polygonal && !_coarse.empty(); }

    // The most a pass may refine this mesh: the level asked for, lower when
    // that would pass _kMostRefinedFaces.
    int RefineCap() const { return _polygonal ? 0 : _RefineLevelFor(_cageFaces, _refineLevel); }

    // The level a pass last refined it to (0 until one has).
    int SizedLevel() const { return _sizedLevel; }

    // Rebuild it refined to `level`, a pass's pick (ThRenderPass::_SizeRefinement).
    void Resize(HdSceneDelegate* delegate, int level, bool motion)
    {
        _sizedLevel = level;
        _Rebuild(delegate, motion);
        g_rebuilds.fetch_add(1, std::memory_order_relaxed);
    }

    // The level a husk frame refines it to, seen through `worldToView` and
    // `projection` at `width x height` (see Subdivision): enough that its
    // cage's mean edge comes down to _kRefinedEdgePixels at the depth of
    // its nearest corner; none out of view (every corner of its bounds
    // past one side of the frame, with a margin for blur, or in front of
    // the near plane, which the core clips at). An instanced mesh takes its
    // nearest instance's. Never past RefineCap.
    int ScreenLevel(std::vector<GfMatrix4d> const& instances, GfMatrix4d const& worldToView,
                    GfMatrix4d const& projection, unsigned int width, unsigned int height) const
    {
        int const cap = RefineCap();
        if (cap == 0 || _cageBox.IsEmpty() || !(_cageEdge > 0.0)) return 0;
        // Hydra's projections are GL's, for row vectors: a perspective one
        // puts -z (the depth) in w.
        bool const perspective = projection[2][3] != 0.0;
        double const nearPlane = perspective ? projection[3][2] / (projection[2][2] - 1.0) : 0.0;
        // The pixels a unit spans across the view at depth 1 (perspective)
        // or at any depth (orthographic).
        double const k = 0.5 * std::max(std::abs(projection[0][0]) * width,
                                        std::abs(projection[1][1]) * height);
        int level = 0;
        auto at = [&](GfMatrix4d const& toWorld) {
            GfMatrix4d const toView = toWorld * worldToView;
            GfMatrix4d const toClip = toView * projection;
            int beyond[5] = {0, 0, 0, 0, 0};
            double nearest = std::numeric_limits<double>::infinity();
            for (int i = 0; i < 8; ++i) {
                GfVec3d const c = _cageBox.GetCorner(i);
                GfVec4d const p = GfVec4d(c[0], c[1], c[2], 1.0) * toClip;
                double const side = 1.25 * p[3];
                beyond[0] += p[0] < -side;
                beyond[1] += p[0] > side;
                beyond[2] += p[1] < -side;
                beyond[3] += p[1] > side;
                beyond[4] += p[2] < -p[3];
                nearest = std::min(nearest, -toView.TransformAffine(c)[2]);
            }
            if (std::find(beyond, beyond + 5, 8) != beyond + 5) return;
            double const scale = std::max({GfVec3d(toView[0][0], toView[0][1], toView[0][2]).GetLength(),
                                           GfVec3d(toView[1][0], toView[1][1], toView[1][2]).GetLength(),
                                           GfVec3d(toView[2][0], toView[2][1], toView[2][2]).GetLength()});
            double pixels = _cageEdge * scale * k;
            if (perspective) {
                double const depth = std::max(nearest, nearPlane);
                if (!(depth > 0.0)) {
                    level = cap;
                    return;
                }
                pixels /= depth;
            }
            level = std::max(level, _LevelForPixels(pixels));
        };
        if (GetInstancerId().IsEmpty()) {
            at(_transform);
        } else {
            for (GfMatrix4d const& inst : instances) {
                at(_transform * inst);
                if (level >= cap) break;
            }
        }
        return std::min(level, cap);
    }

    // Append this mesh's views to `views`: one, or one per part (see
    // Subsets), each painted by its material (`materialOf` finds a material
    // by path, null for none). Returns how many; none when it has nothing
    // to draw. `instancesAt` gives its instances' transforms (none for a
    // mesh not instanced) at a moment of the shutter, or the frame's for
    // null. With the shutter open, the views also get the mesh's points at
    // its opening and closing, and its transforms at keys across it, when
    // they move.
    //
    // Culling, as Storm resolves it: the prim's cull style, or the pass's
    // (`passCull`) when the prim's is DontCare; the "unless double-sided"
    // styles cull only a mesh that is not doubleSided. husk's pass says
    // Nothing and USD prims DontCare, so playblasts cull nothing; the
    // viewer's pass, and stand-ins such as a draw mode's cards (Back),
    // can. The core culls by winding (raster::Cull). `cullBack` (th:cull
    // "back") overrides all of that: every view drops its back faces.
    size_t Views(std::vector<ThMeshView>* views, std::vector<double>* transforms, std::vector<double>* keys,
                 std::function<ThMaterial const*(SdfPath const&)> const& materialOf,
                 std::function<std::vector<GfMatrix4d>(float const*)> const& instancesAt,
                 size_t instancerKeys, _Shutter const& shutter, HdCullStyle passCull,
                 bool cullBack) const
    {
        ThMeshView view;
        if (!_View(&view, transforms, keys, instancesAt, instancerKeys, shutter)) return 0;
        view.cull = cullBack ? 1u : _CullCode(passCull);
        if (_parts.empty()) {
            _Paint(&view, materialOf(GetMaterialId()), 0);
            views->push_back(view);
            return 1;
        }
        for (size_t i = 0; i < _parts.size(); ++i) {
            _Part const& part = _parts[i];
            ThMeshView v = view;
            v.triangles = _triangles.data() + size_t(part.first) * 3;
            v.triangle_count = part.count;
            v.corner_normals = _normals.empty() ? nullptr : _normals.data() + size_t(part.first) * 9;
            v.key = i == 0 ? _key : _partKeys[i - 1];
            _Paint(&v, materialOf(part.material.IsEmpty() ? GetMaterialId() : part.material), part.first);
            views->push_back(v);
        }
        return _parts.size();
    }

protected:
    HdDirtyBits _PropagateDirtyBits(HdDirtyBits bits) const override { return bits; }
    void _InitRepr(TfToken const& reprToken, HdDirtyBits*) override
    {
        auto it = std::find_if(_reprs.begin(), _reprs.end(), _ReprComparator(reprToken));
        if (it == _reprs.end()) {
            _reprs.emplace_back(reprToken, HdReprSharedPtr());
        }
    }

private:
    // ThMeshView::cull for a pass whose cull style is `passCull` (see
    // Culling in Views): 0 nothing, 1 back, 2 front.
    uint32_t _CullCode(HdCullStyle passCull) const
    {
        HdCullStyle const style = _cullStyle != HdCullStyleDontCare ? _cullStyle : passCull;
        switch (style) {
        case HdCullStyleBack: return 1u;
        case HdCullStyleFront: return 2u;
        case HdCullStyleBackUnlessDoubleSided: return _doubleSided ? 0u : 1u;
        case HdCullStyleFrontUnlessDoubleSided: return _doubleSided ? 0u : 2u;
        default: return 0u;
        }
    }

    // `view`'s geometry, transforms and motion: everything but what its
    // material paints (_Paint).
    bool _View(ThMeshView* view, std::vector<double>* transforms, std::vector<double>* keys,
               std::function<std::vector<GfMatrix4d>(float const*)> const& instancesAt,
               size_t instancerKeys, _Shutter const& shutter) const
    {
        if (_points.empty() || _triangles.empty()) return false;
        std::vector<GfMatrix4d> const instances = instancesAt(nullptr);
        transforms->clear();
        auto push = [&](GfMatrix4d const& m) {
            double const* d = m.GetArray();
            transforms->insert(transforms->end(), d, d + 16);
        };
        if (GetInstancerId().IsEmpty()) {
            push(_transform);
        } else {
            for (GfMatrix4d const& inst : instances) {
                push(_transform * inst);
            }
        }
        if (transforms->empty()) return false;

        *view = ThMeshView{};
        view->points = _points.data();
        view->point_count = static_cast<uint32_t>(_points.size() / 3);
        view->triangles = _triangles.data();
        view->triangle_count = static_cast<uint32_t>(_triangles.size() / 3);
        view->corner_normals = _normals.empty() ? nullptr : _normals.data();
        view->polygonal = _polygonal ? 1u : 0u;
        view->key = _key;
        view->version = _version;
        view->transforms = transforms->data();
        view->transform_count = static_cast<uint32_t>(transforms->size() / 16);
        if (shutter.on) {
            _Motion(view, keys, instancesAt, instancerKeys, shutter, instances.size());
        }
        return true;
    }

    // Paint `view`, the triangles from `first` on, with `material` (null for
    // none): its colour, texture and opacity.
    void _Paint(ThMeshView* view, ThMaterial const* material, uint32_t first) const
    {
        size_t const at = first;
        view->base_color[0] = view->base_color[1] = view->base_color[2] = 0.5f;

        // The material's colour wins, as in the viewport with materials on;
        // display colour otherwise.
        if (material && material->HasColor()) {
            GfVec3f const& c = material->BaseColor();
            view->base_color[0] = c[0];
            view->base_color[1] = c[1];
            view->base_color[2] = c[2];
            if (!material->Tiles().empty()) {
                auto uv = _uvs.find(material->UvName());
                if (uv == _uvs.end()) uv = _uvs.find(TfToken("st"));
                if (uv != _uvs.end()) {
                    view->corner_uvs = uv->second.data() + at * 6;
                    view->tiles = material->Tiles().data();
                    view->tile_count = static_cast<uint32_t>(material->Tiles().size());
                }
            }
        } else if (!_colors.empty()) {
            view->corner_colors = _colors.data() + at * 9;
        }
        // Opacity likewise: the material's when it has its own (constant,
        // transmission, cutout), the display opacity otherwise, or when the
        // material reads it. One value a view: a varying display opacity is
        // drawn at the mesh's mean.
        float opacity = _displayOpacity;
        if (material && material->OpacityFromDisplay()) {
            opacity = _displayOpacity * material->OpacityScale();
        } else if (material && material->HasOpacity()) {
            opacity = material->Opacity();
        }
        view->transparency = std::isfinite(opacity) ? 1.0f - std::clamp(opacity, 0.0f, 1.0f) : 0.0f;
    }

    // The shutter's motion of this mesh into `view`: its points at the
    // opening and closing (from sampled points, else velocities), and its
    // transforms at keys across it. Neither when it holds still.
    void _Motion(ThMeshView* view, std::vector<double>* keys,
                 std::function<std::vector<GfMatrix4d>(float const*)> const& instancesAt,
                 size_t instancerKeys, _Shutter const& s, size_t instanceCount) const
    {
        // Moved as authored (the coarse points), then refined as drawn.
        size_t const n = _coarse.size();
        bool const samples = _pointSamples.count >= 2;
        if (samples || !_velocities.empty()) {
            if (!(_motionFor == std::make_tuple(_version, s.open, s.close, s.fps))) {
                _motionFor = std::make_tuple(_version, s.open, s.close, s.fps);
                ++_motionVersion;
                auto fill = [&](std::vector<float>& out, float t) {
                    VtVec3fArray p;
                    if (samples) {
                        p = _pointSamples.Resample(t);
                        if (p.size() != n) p = _coarse;
                    } else {
                        float const sec = t / s.fps;
                        p.resize(n);
                        for (size_t i = 0; i < n; ++i) {
                            GfVec3f const a = _accelerations.empty() ? GfVec3f(0.0f) : _accelerations[i];
                            p[i] = _coarse[i] + _velocities[i] * sec + a * (0.5f * sec * sec);
                        }
                    }
                    out = _Drawn(p);
                };
                fill(_open, s.open);
                fill(_close, s.close);
            }
            if (_open != _close) {
                view->points = _open.data();
                view->points_close = _close.data();
                // The GPU path's cached corners are of `points`.
                view->version = _version + (_motionVersion << 32);
            }
        }

        bool const instanced = !GetInstancerId().IsEmpty();
        if (_xformSamples.count < 2 && instancerKeys < 3 && !instanced) return;
        size_t const count = std::max(_KeyCount(_xformSamples, s), instancerKeys);
        keys->clear();
        for (size_t k = 0; k < count; ++k) {
            float const t = s.At(k, count);
            GfMatrix4d const own = _XformAt(_xformSamples, t, _transform);
            std::vector<GfMatrix4d> const at = instanced ? instancesAt(&t) : std::vector<GfMatrix4d>{GfMatrix4d(1.0)};
            if (instanced && at.size() != instanceCount) {
                keys->clear();  // instances came and went: drawn still
                return;
            }
            for (GfMatrix4d const& inst : at) {
                GfMatrix4d const m = instanced ? own * inst : own;
                double const* d = m.GetArray();
                keys->insert(keys->end(), d, d + 16);
            }
        }
        size_t const per = keys->size() / count;
        bool moves = false;
        for (size_t k = 1; k < count && !moves; ++k) {
            moves = !std::equal(keys->begin(), keys->begin() + per, keys->begin() + k * per);
        }
        if (moves) {
            view->transform_keys = keys->data();
            view->transform_key_count = static_cast<uint32_t>(count);
        }
    }

    // The mesh's points over the motion window: sampled points (authored, or
    // computed by skinning at each sample), else velocities (and
    // accelerations).
    struct _PointMotion
    {
        HdTimeSampleArray<VtVec3fArray, 4> samples;
        VtVec3fArray velocities;
        VtVec3fArray accelerations;
    };

    // The points: computed (skinning, deformers) or authored; `computed`
    // gets the computation descriptors, for _ReadMotion.
    VtVec3fArray _ReadPoints(HdSceneDelegate* delegate, HdExtComputationPrimvarDescriptorVector* computed) const
    {
        SdfPath const& id = GetId();
        VtValue pointsValue;
        for (auto const& desc :
             delegate->GetExtComputationPrimvarDescriptors(id, HdInterpolationVertex)) {
            if (desc.name == HdTokens->points) computed->push_back(desc);
        }
        if (!computed->empty()) {
            auto const values = HdExtComputationUtils::GetComputedPrimvarValues(*computed, delegate);
            auto it = values.find(HdTokens->points);
            if (it != values.end()) pointsValue = it->second;
        } else {
            pointsValue = delegate->Get(id, HdTokens->points);
        }
        return _AsArray<GfVec3f>(pointsValue);
    }

    // The motion of `count` points over the window; none without `motion`.
    _PointMotion _ReadMotion(HdSceneDelegate* delegate, HdExtComputationPrimvarDescriptorVector const& computed,
                             size_t count, bool motion) const
    {
        SdfPath const& id = GetId();
        _PointMotion out;
        out.samples.count = 0;
        if (!motion) return out;
        HdTimeSampleArray<VtValue, 4> box;
        box.count = 0;
        if (!computed.empty()) {
            HdExtComputationUtils::SampledValueStore<4> store;
            HdExtComputationUtils::SampleComputedPrimvarValues<4>(
                computed, delegate, -_motionWindow, _motionWindow, 4, &store);
            auto it = store.find(HdTokens->points);
            if (it != store.end()) box = it->second;
        } else {
            delegate->SamplePrimvar(id, HdTokens->points, -_motionWindow, _motionWindow, &box);
        }
        HdTimeSampleArray<VtVec3fArray, 4> sampled;
        bool whole = box.count >= 2 && sampled.UnboxFrom(box);
        for (size_t i = 0; whole && i < sampled.count; ++i) {
            whole = sampled.values[i].size() == count;
        }
        if (whole) {
            out.samples = sampled;
        } else {
            VtVec3fArray const v = _AsArray<GfVec3f>(delegate->Get(id, HdTokens->velocities));
            if (v.size() == count) {
                out.velocities = v;
                VtVec3fArray const a = _AsArray<GfVec3f>(delegate->Get(id, HdTokens->accelerations));
                if (a.size() == count) out.accelerations = a;
            }
        }
        return out;
    }

    void _SetMotion(_PointMotion const& m)
    {
        _pointSamples = m.samples;
        _velocities = m.velocities;
        _accelerations = m.accelerations;
    }

    static bool _SameBits(VtVec3fArray const& a, VtVec3fArray const& b)
    {
        return a.size() == b.size()
            && (a.empty() || std::memcmp(a.cdata(), b.cdata(), a.size() * sizeof(GfVec3f)) == 0);
    }

    // `m` is, bit for bit, the motion the mesh has.
    bool _SameMotion(_PointMotion const& m) const
    {
        if (m.samples.count != _pointSamples.count) return false;
        for (size_t i = 0; i < m.samples.count; ++i) {
            if (std::memcmp(&m.samples.times[i], &_pointSamples.times[i], sizeof(float)) != 0
                || !_SameBits(m.samples.values[i], _pointSamples.values[i])) {
                return false;
            }
        }
        return _SameBits(m.velocities, _velocities) && _SameBits(m.accelerations, _accelerations);
    }

    // Only the points are dirty: re-read them and their motion and keep
    // the triangles and every per-corner primvar. Points identical to the
    // mesh's, bit for bit (a held frame of an animated mesh), change
    // nothing, not even the version, so the GPU path keeps its copy. False
    // when the mesh needs a whole _Rebuild instead: never built, or a point
    // count that no longer matches its triangles.
    bool _UpdatePoints(HdSceneDelegate* delegate, bool motion)
    {
        if (_triangles.empty()) return false;
        HdExtComputationPrimvarDescriptorVector computed;
        VtVec3fArray const points = _ReadPoints(delegate, &computed);
        if (points.empty() || points.size() != _coarse.size()) return false;
        _PointMotion const m = _ReadMotion(delegate, computed, points.size(), motion);
        if (_SameBits(points, _coarse) && _SameMotion(m)) return true;
        ++_version;  // the GPU path's copy of the old points is stale
        _coarse = points;
        if (!_polygonal) _MeasureCage(points, _cageCounts, _cageIndices, &_cageBox, &_cageEdge);
        _points = _Drawn(points);
        if (_coreNormals) _MakeCoreNormals();
        _SetMotion(m);
        return true;
    }

    // The whole mesh's normals as the core would work them out (cusp or
    // smooth), one per corner, for a mesh drawn in parts (see Subsets).
    void _MakeCoreNormals()
    {
        _normals.assign(_triangles.size() * 3, 0.0f);
        th_mesh_normals(_points.data(), static_cast<uint32_t>(_points.size() / 3), _triangles.data(),
                        static_cast<uint32_t>(_triangles.size() / 3), _polygonal ? 1u : 0u, _normals.data());
        _coreNormals = true;
    }

    // Subsets: faces bound to materials of their own by GeomSubsets (a
    // UsdGeomSubset of the materialBind family, which Hydra hands over in
    // the topology with its material's path) are drawn as parts of the
    // mesh, a view per material, each painted as a whole mesh is (_Paint):
    // colour or texture, opacity, display colour where its material has no
    // colour. Faces in no subset, or in one bound to nothing, keep the
    // mesh's material. A part is a run of the triangles, which are sorted
    // by material once every per-corner value is made, so the face each
    // triangle came from (refined faces carry their cage face's) decides
    // and holes, triangulation and subdivision change nothing about it.
    // Every part shares the points; without normals of its own the mesh
    // gets the whole mesh's from the core (th_mesh_normals), so its parts
    // meet without the crease that normals worked out per part would make.
    // A mesh whose subsets bind no material, or whose every face keeps the
    // mesh's, is one view as before.
    void _SplitBySubsets(HdMeshTopology const& topology, std::vector<uint32_t> const& faces)
    {
        size_t const cage = topology.GetFaceVertexCounts().size();
        std::vector<SdfPath> materials(1);  // 0: the mesh's own
        std::vector<uint32_t> groupOf;      // per cage face, into `materials`
        for (HdGeomSubset const& s : topology.GetGeomSubsets()) {
            if (s.type != HdGeomSubset::TypeFaceSet || s.materialId.IsEmpty()) continue;
            if (groupOf.empty()) groupOf.assign(cage, 0);
            auto const it = std::find(materials.begin(), materials.end(), s.materialId);
            uint32_t const g = static_cast<uint32_t>(it - materials.begin());
            if (it == materials.end()) materials.push_back(s.materialId);
            for (int const f : s.indices) {
                // Overlapping subsets (invalid USD): the first one's.
                if (f >= 0 && static_cast<size_t>(f) < cage && groupOf[f] == 0) groupOf[f] = g;
            }
        }
        if (groupOf.empty()) return;
        // Each triangle's cage face: a refined face's is its parent's at
        // level 0, carried down the levels as a uniform value.
        std::vector<uint32_t> cageFace(faces);
        if (_refiner) {
            VtDoubleArray ids(cage);
            for (size_t f = 0; f < cage; ++f) ids[f] = static_cast<double>(f);
            VtDoubleArray const refined = _Refine(*_refiner, _Rate::Uniform, 0, ids);
            for (uint32_t& f : cageFace) {
                f = f < refined.size() ? static_cast<uint32_t>(refined[f]) : UINT32_MAX;
            }
        }
        size_t const n = cageFace.size();
        std::vector<uint32_t> group(n);
        std::vector<size_t> sizes(materials.size(), 0);
        for (size_t t = 0; t < n; ++t) {
            group[t] = cageFace[t] < cage ? groupOf[cageFace[t]] : 0;
            ++sizes[group[t]];
        }
        if (sizes[0] == n) return;  // every face keeps the mesh's material
        // Sort the triangles by group (stable), and every per-corner array
        // with them.
        std::vector<size_t> start(materials.size(), 0);
        for (size_t g = 1; g < materials.size(); ++g) start[g] = start[g - 1] + sizes[g - 1];
        std::vector<size_t> order(n);  // new position -> old triangle
        {
            std::vector<size_t> fill = start;
            for (size_t t = 0; t < n; ++t) order[fill[group[t]]++] = t;
        }
        auto permute = [&](auto& values, size_t per) {
            if (values.size() != n * per) return;
            std::remove_reference_t<decltype(values)> out(values.size());
            for (size_t i = 0; i < n; ++i) {
                std::copy_n(values.begin() + order[i] * per, per, out.begin() + i * per);
            }
            values.swap(out);
        };
        permute(_triangles, 3);
        permute(_normals, 9);
        permute(_colors, 9);
        for (auto& uv : _uvs) permute(uv.second, 6);
        for (size_t g = 0; g < materials.size(); ++g) {
            if (sizes[g] == 0) continue;
            _parts.push_back({materials[g], static_cast<uint32_t>(start[g]), static_cast<uint32_t>(sizes[g])});
        }
        while (_partKeys.size() + 1 < _parts.size()) _partKeys.push_back(g_nextMeshKey.fetch_add(1));
        if (_parts.size() > 1 && _normals.empty()) _MakeCoreNormals();
    }

    // `coarse` (points as authored) as drawn, three floats a point:
    // refined when the mesh is.
    std::vector<float> _Drawn(VtVec3fArray const& coarse) const
    {
        VtVec3fArray const drawn = _refiner ? _Refine(*_refiner, _Rate::Vertex, 0, coarse) : coarse;
        static_assert(sizeof(GfVec3f) == 3 * sizeof(float), "GfVec3f is three packed floats");
        float const* f = reinterpret_cast<float const*>(drawn.cdata());
        return std::vector<float>(f, f + drawn.size() * 3);
    }

    // Primvar `name` (of `interp`) over the faces drawn: as authored, or
    // carried down to the refined mesh's faces, vertices or face vertices.
    template <typename T>
    VtArray<T> _SurfaceValues(HdSceneDelegate* delegate, TfToken const& name, HdInterpolation interp) const
    {
        SdfPath const& id = GetId();
        if (!_refiner) return _FlatValues<T>(delegate, id, name);
        switch (interp) {
        case HdInterpolationConstant: return _FlatValues<T>(delegate, id, name);
        case HdInterpolationUniform: return _Refine(*_refiner, _Rate::Uniform, 0, _FlatValues<T>(delegate, id, name));
        case HdInterpolationVertex: return _Refine(*_refiner, _Rate::Vertex, 0, _FlatValues<T>(delegate, id, name));
        case HdInterpolationVarying: return _Refine(*_refiner, _Rate::Varying, 0, _FlatValues<T>(delegate, id, name));
        case HdInterpolationFaceVarying: {
            auto channel = _fvarChannels.find(name);
            if (channel == _fvarChannels.end()) return {};
            VtIntArray indices;  // the channel's topology, given to the refiner
            VtArray<T> const values = _AsArray<T>(delegate->GetIndexedPrimvar(id, name, &indices));
            VtArray<T> const refined = _Refine(*_refiner, _Rate::FaceVarying, channel->second, values);
            if (refined.empty()) return {};
            OpenSubdiv::Far::TopologyLevel const& last = _refiner->GetLevel(_refiner->GetMaxLevel());
            VtArray<T> out;
            out.reserve(static_cast<size_t>(last.GetNumFaceVertices()));
            for (int f = 0; f < last.GetNumFaces(); ++f) {
                for (int const v : last.GetFaceFVarValues(f, channel->second)) {
                    out.push_back(static_cast<size_t>(v) < refined.size() ? refined[v] : T(0.0f));
                }
            }
            return out;
        }
        default: return {};
        }
    }

    void _Rebuild(HdSceneDelegate* delegate, bool motion)
    {
        SdfPath const& id = GetId();
        ++_version;  // the GPU path's copy of the old geometry is stale
        _points.clear();
        _triangles.clear();
        _normals.clear();
        _colors.clear();
        _uvs.clear();
        _displayOpacity = 1.0f;
        _parts.clear();
        _coreNormals = false;

        _refiner.reset();
        _fvarChannels.clear();
        _coarse = VtVec3fArray();
        _cageFaces = 0;
        _cageCounts = VtIntArray();
        _cageIndices = VtIntArray();
        _cageBox = GfRange3d();
        _cageEdge = 0.0;

        HdMeshTopology const topology = GetMeshTopology(delegate);

        HdExtComputationPrimvarDescriptorVector computed;
        VtVec3fArray const points = _ReadPoints(delegate, &computed);
        if (points.empty()) return;
        _coarse = points;

        // A polygonal mesh (scheme none or bilinear, which is what implicit
        // cubes become) with no normals gets Houdini's cusp normals rather
        // than smoothing across every edge; the core computes them (ffi.rs).
        TfToken const scheme = topology.GetScheme();
        _polygonal = scheme == _tokNone || scheme == _tokBilinear;

        // The primvars drawn: display colour, normals and uvs: those of the
        // texture coordinate role, `st` or `uv`, and whatever primvar the
        // mesh's materials (its own and its subsets') read their texture
        // coordinates from (a draw mode's cards read `cardsUv`, which has
        // no role).
        std::set<TfToken> uvNames{TfToken("st"), TfToken("uv")};
        {
            HdRenderIndex& index = delegate->GetRenderIndex();
            auto note = [&](SdfPath const& path) {
                if (path.IsEmpty()) return;
                if (auto const* m = dynamic_cast<ThMaterial const*>(index.GetSprim(HdPrimTypeTokens->material, path))) {
                    uvNames.insert(m->UvName());
                }
            };
            note(GetMaterialId());
            for (HdGeomSubset const& s : topology.GetGeomSubsets()) note(s.materialId);
        }
        std::vector<std::pair<HdPrimvarDescriptor, HdInterpolation>> drawnPrimvars;
        for (HdInterpolation interp :
             {HdInterpolationConstant, HdInterpolationUniform, HdInterpolationVarying,
              HdInterpolationVertex, HdInterpolationFaceVarying}) {
            for (HdPrimvarDescriptor const& pv : delegate->GetPrimvarDescriptors(id, interp)) {
                // The display opacity: one value a mesh, its mean whatever
                // its interpolation (the core blends a mesh as a whole).
                if (pv.name == HdTokens->displayOpacity) {
                    VtFloatArray const values = _FlatValues<float>(delegate, id, pv.name);
                    if (!values.empty()) {
                        double sum = 0.0;
                        for (float const v : values) sum += v;
                        _displayOpacity = static_cast<float>(sum / static_cast<double>(values.size()));
                    }
                    continue;
                }
                if (pv.name == _tokDisplayColor || pv.name == HdTokens->normals
                    || pv.role == HdPrimvarRoleTokens->textureCoordinate || uvNames.count(pv.name)) {
                    drawnPrimvars.emplace_back(pv, interp);
                }
            }
        }

        // The faces drawn: as authored, or refined (see Subdivision).
        VtIntArray counts = topology.GetFaceVertexCounts();
        VtIntArray fvi = topology.GetFaceVertexIndices();
        VtIntArray holes = topology.GetHoleIndices();
        VtVec3fArray drawn = points;
        if (!_polygonal) {
            // What a pass sizes its refinement by (ScreenLevel).
            _cageFaces = counts.size();
            _cageCounts = counts;
            _cageIndices = fvi;
            _MeasureCage(points, counts, fvi, &_cageBox, &_cageEdge);
        }
        // The level a pass last picked for it (the cage until one has).
        int const level = _polygonal ? 0 : std::min(_sizedLevel, _RefineLevelFor(counts.size(), _refineLevel));
        if (level > 0) {
            // Each face-varying primvar drawn is a channel of its own, its
            // indices (or one value per face vertex) its topology.
            std::vector<VtIntArray> channels;
            for (auto const& [pv, interp] : drawnPrimvars) {
                if (interp != HdInterpolationFaceVarying || pv.name == HdTokens->normals) continue;
                VtIntArray indices;
                VtValue const values = delegate->GetIndexedPrimvar(id, pv.name, &indices);
                if (indices.empty()) {
                    indices.resize(values.GetArraySize());
                    for (size_t i = 0; i < indices.size(); ++i) indices[i] = static_cast<int>(i);
                }
                if (indices.size() != fvi.size()) continue;
                _fvarChannels[pv.name] = static_cast<int>(channels.size());
                channels.push_back(indices);
            }
            _refiner = PxOsdRefinerFactory::Create(topology.GetPxOsdMeshTopology(), channels, id.GetToken());
            if (_refiner && static_cast<size_t>(_refiner->GetLevel(0).GetNumVertices()) <= points.size()) {
                _OsdRefiner::UniformOptions options(level);
                options.fullTopologyInLastLevel = true;
                _refiner->RefineUniform(options);
                drawn = _Refine(*_refiner, _Rate::Vertex, 0, points);
                OpenSubdiv::Far::TopologyLevel const& last = _refiner->GetLevel(level);
                counts.clear();
                fvi.clear();
                holes.clear();
                for (int f = 0; f < last.GetNumFaces(); ++f) {
                    OpenSubdiv::Far::ConstIndexArray const verts = last.GetFaceVertices(f);
                    counts.push_back(verts.size());
                    for (int const v : verts) fvi.push_back(v);
                    if (last.IsFaceHole(f)) holes.push_back(f);
                }
                g_subdivided.fetch_add(1, std::memory_order_relaxed);
            } else {
                _refiner.reset();
                _fvarChannels.clear();
            }
        }

        // Not HdMeshUtil::ComputeTriangleIndices: it fans every face, which
        // fills a concave face's notch and a keyhole face's opening (a wall's
        // window). th_triangulate keeps the fan for a convex face and
        // ear-clips a concave one, wound as HdMeshUtil winds it.
        size_t capacity = 0;
        for (int const c : counts) {
            if (c > 2) capacity += static_cast<size_t>(c - 2);
        }
        if (capacity == 0 || capacity > UINT32_MAX / 3) return;
        std::vector<uint32_t> corners(capacity * 3);
        std::vector<uint32_t> faces(capacity);
        static_assert(sizeof(GfVec3f) == 3 * sizeof(float), "GfVec3f is three packed floats");
        uint32_t const count = th_triangulate(
            reinterpret_cast<float const*>(drawn.cdata()), static_cast<uint32_t>(drawn.size()),
            counts.cdata(), static_cast<uint32_t>(counts.size()),
            fvi.cdata(), static_cast<uint32_t>(fvi.size()),
            holes.cdata(), static_cast<uint32_t>(holes.size()),
            topology.GetOrientation() != HdTokens->rightHanded ? 1u : 0u,
            corners.data(), faces.data(), static_cast<uint32_t>(capacity));
        if (count == UINT32_MAX || count == 0) return;
        corners.resize(static_cast<size_t>(count) * 3);
        faces.resize(count);

        float const* drawnFloats = reinterpret_cast<float const*>(drawn.cdata());
        _points.assign(drawnFloats, drawnFloats + drawn.size() * 3);

        _SetMotion(_ReadMotion(delegate, computed, points.size(), motion));
        _triangles.reserve(corners.size());
        for (uint32_t const c : corners) {
            _triangles.push_back(static_cast<uint32_t>(std::max(0, fvi[c])));
        }

        for (auto const& [pv, interp] : drawnPrimvars) {
            if (pv.name == _tokDisplayColor) {
                auto c = _CornerValues(_SurfaceValues<GfVec3f>(delegate, pv.name, interp), interp, corners, faces, fvi);
                _colors.clear();
                for (GfVec3f const& v : c) _colors.insert(_colors.end(), {v[0], v[1], v[2]});
            } else if (pv.name == HdTokens->normals) {
                // A refined surface's normals are its own (smooth, from the
                // core), as USD has it: authored ones are for the cage.
                if (_refiner) continue;
                auto n = _CornerValues(_FlatValues<GfVec3f>(delegate, id, pv.name), interp, corners, faces, fvi);
                _normals.clear();
                for (GfVec3f const& v : n) _normals.insert(_normals.end(), {v[0], v[1], v[2]});
            } else {
                auto uv = _CornerValues(_SurfaceValues<GfVec2f>(delegate, pv.name, interp), interp, corners, faces, fvi);
                if (!uv.empty()) {
                    std::vector<float>& out = _uvs[pv.name];
                    for (GfVec2f const& v : uv) out.insert(out.end(), {v[0], v[1]});
                }
            }
        }
        _SplitBySubsets(topology, faces);
    }

    GfMatrix4d _transform{1.0};
    _XformSamples _xformSamples;
    HdTimeSampleArray<VtVec3fArray, 4> _pointSamples;
    VtVec3fArray _velocities;
    VtVec3fArray _accelerations;
    // The points at the last drawn shutter's opening and closing, and what
    // they were made for (version, open, close, fps).
    mutable std::vector<float> _open;
    mutable std::vector<float> _close;
    mutable std::tuple<uint64_t, float, float, float> _motionFor{~uint64_t(0), 0.0f, 0.0f, 0.0f};
    mutable uint64_t _motionVersion = 0;
    std::vector<float> _points;
    std::vector<uint32_t> _triangles;
    std::vector<float> _normals;
    std::vector<float> _colors;
    std::map<TfToken, std::vector<float>> _uvs;
    float _displayOpacity = 1.0f;  // the mean of displayOpacity, 1 when unauthored
    bool _polygonal = false;
    // A mesh drawn in parts (see Subsets): each a run of the triangles and
    // the material it is painted with (empty: the mesh's). Empty for one
    // drawn whole. Part 0 is keyed as the mesh, part i by _partKeys[i - 1];
    // `_coreNormals`: the normals are the core's, remade with the points.
    struct _Part
    {
        SdfPath material;
        uint32_t first = 0;
        uint32_t count = 0;
    };
    std::vector<_Part> _parts;
    std::vector<uint64_t> _partKeys;
    bool _coreNormals = false;
    // The points as authored (before refining), the refine level Houdini
    // asks for, the level a pass last picked (see Subdivision), and the
    // refiner of a mesh drawn refined (null otherwise) with each
    // face-varying primvar's channel in it.
    VtVec3fArray _coarse;
    int _refineLevel = 0;
    int _sizedLevel = 0;
    // The prim's cull style and doubleSided (see Culling in Views).
    HdCullStyle _cullStyle = HdCullStyleDontCare;
    bool _doubleSided = false;
    // A subdivision surface's cage, as ScreenLevel sizes it: its face count,
    // topology (to measure it again when only the points change), bounds and
    // mean edge length, all as authored.
    size_t _cageFaces = 0;
    VtIntArray _cageCounts;
    VtIntArray _cageIndices;
    GfRange3d _cageBox;
    double _cageEdge = 0.0;
    PxOsdTopologyRefinerSharedPtr _refiner;
    std::map<TfToken, int> _fvarChannels;
    uint64_t const _key;
    uint64_t _version = 0;
};

// -----------------------------------------------------------------------------
// Basis curves and points
// -----------------------------------------------------------------------------
//
// Handed to the core as they are authored (ThCurvesView): vertices, curve
// vertex counts, basis and wrap, widths and display colour. The core
// evaluates cubic curves and draws curves as camera-facing ribbons and
// points as camera-facing discs, never thinner than a pixel or two, so a
// curve with no width shows as a line as in the GL viewport (src/curves.rs).
// That geometry depends on the camera, so the core makes it for each frame;
// nothing of it is kept on the card.

// A model's draw-mode stand-in curves (model:drawMode bounds or origin,
// with UsdRenderers.json's drawmodesupport on): UsdImaging's box or axes
// as linear basis curves of width 1, which Storm and Houdini's viewer draw
// as one-pixel lines whatever their width. Drawn with their width they
// would be ribbons a scene unit across: a solid block, not a wire box. The
// draw mode scene index names them `boundsCurves` and `originCurves` under
// the model; a scene delegate that says what draw mode a prim has
// (HdSceneDelegate::GetModelDrawMode) is believed too.
bool _DrawModeStandin(HdSceneDelegate* delegate, SdfPath const& id)
{
    static TfToken const bounds("bounds"), origin("origin");
    static TfToken const boundsCurves("boundsCurves"), originCurves("originCurves");
    if (id.GetNameToken() == boundsCurves || id.GetNameToken() == originCurves) return true;
    HdModelDrawMode const m = delegate->GetModelDrawMode(id);
    return m.applyDrawMode && (m.drawMode == bounds || m.drawMode == origin);
}

// Hydra's interpolation as ThCurvesView's: 0 constant, 1 uniform, 2
// varying, 3 vertex (face-varying, which curves have none of, as vertex).
uint32_t _InterpCode(HdInterpolation interp)
{
    switch (interp) {
    case HdInterpolationUniform: return 1;
    case HdInterpolationVarying: return 2;
    case HdInterpolationVertex:
    case HdInterpolationFaceVarying: return 3;
    default: return 0;
    }
}

// A basis curves (Base = HdBasisCurves) or points (HdPoints) prim.
template <typename Base>
class ThStrokes final : public Base
{
    static constexpr bool _curves = std::is_same<Base, HdBasisCurves>::value;

public:
    explicit ThStrokes(SdfPath const& id) : Base(id) {}

    HdDirtyBits GetInitialDirtyBitsMask() const override
    {
        return HdChangeTracker::AllSceneDirtyBits;
    }

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits,
              TfToken const&) override
    {
        _SyncTimer timer;
        _Edited(param);
        SdfPath const& id = this->GetId();
        bool const motion = _SamplingMotion(param);
        if (*dirtyBits & HdChangeTracker::DirtyMaterialId) {
            this->SetMaterialId(delegate->GetMaterialId(id));
        }
        if (HdChangeTracker::IsVisibilityDirty(*dirtyBits, id)) {
            this->_UpdateVisibility(delegate, dirtyBits);
        }
        if (HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
            _transform = delegate->GetTransform(id);
            _xformSamples.count = 0;
            if (motion) {
                delegate->SampleTransform(id, -_motionWindow, _motionWindow, &_xformSamples);
            }
        }
        this->_UpdateInstancer(delegate, dirtyBits);
        HdInstancer::_SyncInstancerAndParents(delegate->GetRenderIndex(), this->GetInstancerId());
        if (HdChangeTracker::IsTopologyDirty(*dirtyBits, id) || HdChangeTracker::IsAnyPrimvarDirty(*dirtyBits, id)
            || (*dirtyBits & HdChangeTracker::DirtyWidths)) {
            _Rebuild(delegate, motion);
        }
        *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
    }

    // Fill `view` from this prim, as ThMesh::View does; false when it has
    // nothing to draw.
    bool View(ThCurvesView* view, std::vector<double>* transforms, std::vector<double>* keys,
              ThMaterial const* material,
              std::function<std::vector<GfMatrix4d>(float const*)> const& instancesAt,
              size_t instancerKeys, _Shutter const& shutter) const
    {
        if (_points.empty() || (_curves && _counts.empty())) return false;
        std::vector<GfMatrix4d> const instances = instancesAt(nullptr);
        bool const instanced = !this->GetInstancerId().IsEmpty();
        transforms->clear();
        auto push = [](std::vector<double>* out, GfMatrix4d const& m) {
            double const* d = m.GetArray();
            out->insert(out->end(), d, d + 16);
        };
        if (!instanced) {
            push(transforms, _transform);
        } else {
            for (GfMatrix4d const& inst : instances) push(transforms, _transform * inst);
        }
        if (transforms->empty()) return false;

        *view = ThCurvesView{};
        view->points = _points.data();
        view->point_count = static_cast<uint32_t>(_points.size() / 3);
        view->curve_vertex_counts = _counts.empty() ? nullptr : _counts.data();
        view->curve_count = static_cast<uint32_t>(_counts.size());
        view->kind = _kind;
        view->basis = _basis;
        view->wrap = _wrap;
        if (!_widths.empty()) {
            view->widths = _widths.data();
            view->width_count = static_cast<uint32_t>(_widths.size());
            view->width_interp = _widthInterp;
        }
        view->base_color[0] = view->base_color[1] = view->base_color[2] = 0.5f;
        if (material && material->HasColor()) {
            GfVec3f const& c = material->BaseColor();
            view->base_color[0] = c[0];
            view->base_color[1] = c[1];
            view->base_color[2] = c[2];
        } else if (!_colors.empty()) {
            view->colors = _colors.data();
            view->color_count = static_cast<uint32_t>(_colors.size() / 3);
            view->color_interp = _colorInterp;
        }
        view->transforms = transforms->data();
        view->transform_count = static_cast<uint32_t>(transforms->size() / 16);
        if (!shutter.on) return true;

        // The points at the shutter's opening and closing.
        size_t const n = _points.size() / 3;
        // Refilled only when rebuilt or the shutter changed: a background
        // frame may still be reading them.
        if ((_pointSamples.count >= 2 || !_velocities.empty())
            && !(_motionFor == std::make_tuple(_version, shutter.open, shutter.close, shutter.fps))) {
            _motionFor = std::make_tuple(_version, shutter.open, shutter.close, shutter.fps);
            auto fill = [&](std::vector<float>& out, float t) {
                out.assign(_points.begin(), _points.end());
                if (_pointSamples.count >= 2) {
                    VtVec3fArray const p = _Deindexed(_pointSamples.Resample(t));
                    if (p.size() == n) std::memcpy(out.data(), p.cdata(), n * sizeof(GfVec3f));
                } else {
                    float const sec = t / shutter.fps;
                    for (size_t i = 0; i < n && i < _velocities.size(); ++i) {
                        GfVec3f const a = i < _accelerations.size() ? _accelerations[i] : GfVec3f(0.0f);
                        GfVec3f const d = _velocities[i] * sec + a * (0.5f * sec * sec);
                        for (int k = 0; k < 3; ++k) out[i * 3 + k] += d[k];
                    }
                }
            };
            fill(_open, shutter.open);
            fill(_close, shutter.close);
        }
        if ((_pointSamples.count >= 2 || !_velocities.empty()) && _open.size() == _points.size() && _open != _close) {
            view->points = _open.data();
            view->points_close = _close.data();
        }
        // The transforms at keys across it.
        if (_xformSamples.count < 2 && instancerKeys < 3 && !instanced) return true;
        size_t const count = std::max(_KeyCount(_xformSamples, shutter), instancerKeys);
        keys->clear();
        for (size_t k = 0; k < count; ++k) {
            float const t = shutter.At(k, count);
            GfMatrix4d const own = _XformAt(_xformSamples, t, _transform);
            if (!instanced) {
                push(keys, own);
                continue;
            }
            std::vector<GfMatrix4d> const at = instancesAt(&t);
            if (at.size() != instances.size()) {
                keys->clear();  // instances came and went: drawn still
                return true;
            }
            for (GfMatrix4d const& inst : at) push(keys, own * inst);
        }
        size_t const per = keys->size() / count;
        for (size_t k = 1; k < count; ++k) {
            if (!std::equal(keys->begin(), keys->begin() + per, keys->begin() + k * per)) {
                view->transform_keys = keys->data();
                view->transform_key_count = static_cast<uint32_t>(count);
                break;
            }
        }
        return true;
    }

protected:
    HdDirtyBits _PropagateDirtyBits(HdDirtyBits bits) const override { return bits; }
    void _InitRepr(TfToken const& reprToken, HdDirtyBits*) override
    {
        auto it = std::find_if(this->_reprs.begin(), this->_reprs.end(), HdRprim::_ReprComparator(reprToken));
        if (it == this->_reprs.end()) {
            this->_reprs.emplace_back(reprToken, HdReprSharedPtr());
        }
    }

private:
    // Curves with indices: the points they index, one per curve vertex.
    VtVec3fArray _Deindexed(VtVec3fArray const& points) const
    {
        if (_indices.empty()) return points;
        VtVec3fArray out(_indices.size());
        for (size_t i = 0; i < _indices.size(); ++i) {
            int const at = _indices[i];
            out[i] = at >= 0 && static_cast<size_t>(at) < points.size() ? points[at] : GfVec3f(0.0f);
        }
        return out;
    }

    void _Rebuild(HdSceneDelegate* delegate, bool motion)
    {
        SdfPath const& id = this->GetId();
        ++_version;
        _points.clear();
        _counts.clear();
        _widths.clear();
        _colors.clear();
        _indices = VtIntArray();
        _pointSamples.count = 0;
        _velocities = VtVec3fArray();
        _accelerations = VtVec3fArray();

        _kind = 0;
        if constexpr (_curves) {
            HdBasisCurvesTopology const topology = this->GetBasisCurvesTopology(delegate);
            TfToken const basis = topology.GetCurveBasis();
            TfToken const wrap = topology.GetCurveWrap();
            _kind = topology.GetCurveType() == HdTokens->linear ? 1u : 2u;
            _basis = basis == HdTokens->bspline ? 1u
                : (basis == HdTokens->catmullRom || basis == TfToken("centripetalCatmullRom")) ? 2u : 0u;
            _wrap = wrap == HdTokens->periodic ? 1u : wrap == HdTokens->pinned ? 2u : 0u;
            for (int const c : topology.GetCurveVertexCounts()) _counts.push_back(c);
            if (topology.HasIndices()) _indices = topology.GetCurveIndices();
        }

        VtVec3fArray const points = _Deindexed(_AsArray<GfVec3f>(delegate->Get(id, HdTokens->points)));
        if (points.empty()) return;
        float const* f = reinterpret_cast<float const*>(points.cdata());
        _points.assign(f, f + points.size() * 3);
        bool const standin = _curves && _DrawModeStandin(delegate, id);

        // A vertex primvar of indexed curves is per point: read at the
        // curve vertices, as the points are.
        auto perVertex = [&](HdInterpolation interp) {
            return interp == HdInterpolationVertex && !_indices.empty();
        };
        for (HdInterpolation interp :
             {HdInterpolationConstant, HdInterpolationUniform, HdInterpolationVarying, HdInterpolationVertex}) {
            for (HdPrimvarDescriptor const& pv : delegate->GetPrimvarDescriptors(id, interp)) {
                if (pv.name == HdTokens->widths) {
                    if (standin) continue;
                    VtFloatArray w = _FlatValues<float>(delegate, id, pv.name);
                    if (w.empty()) {
                        VtDoubleArray const d = _FlatValues<double>(delegate, id, pv.name);
                        for (double const v : d) w.push_back(static_cast<float>(v));
                    }
                    if (perVertex(interp)) {
                        VtFloatArray const at = w;
                        w.resize(_indices.size());
                        for (size_t i = 0; i < _indices.size(); ++i) {
                            int const k = _indices[i];
                            w[i] = k >= 0 && static_cast<size_t>(k) < at.size() ? at[k] : 0.0f;
                        }
                    }
                    _widths.assign(w.begin(), w.end());
                    _widthInterp = _InterpCode(interp);
                } else if (pv.name == _tokDisplayColor) {
                    VtVec3fArray c = _FlatValues<GfVec3f>(delegate, id, pv.name);
                    if (perVertex(interp)) c = _Deindexed(c);
                    float const* cf = reinterpret_cast<float const*>(c.cdata());
                    _colors.assign(cf, cf + c.size() * 3);
                    _colorInterp = _InterpCode(interp);
                }
            }
        }

        if (!motion) return;
        HdTimeSampleArray<VtValue, 4> box;
        box.count = 0;
        delegate->SamplePrimvar(id, HdTokens->points, -_motionWindow, _motionWindow, &box);
        HdTimeSampleArray<VtVec3fArray, 4> sampled;
        bool whole = box.count >= 2 && sampled.UnboxFrom(box);
        for (size_t i = 0; whole && i < sampled.count; ++i) {
            whole = _Deindexed(sampled.values[i]).size() == points.size();
        }
        if (whole) {
            _pointSamples = sampled;
        } else if (_indices.empty()) {
            VtVec3fArray const v = _AsArray<GfVec3f>(delegate->Get(id, HdTokens->velocities));
            if (v.size() == points.size()) {
                _velocities = v;
                VtVec3fArray const a = _AsArray<GfVec3f>(delegate->Get(id, HdTokens->accelerations));
                if (a.size() == points.size()) _accelerations = a;
            }
        }
    }

    GfMatrix4d _transform{1.0};
    _XformSamples _xformSamples;
    std::vector<float> _points;  // per curve vertex (indices applied), or per point
    std::vector<int32_t> _counts;
    VtIntArray _indices;
    uint32_t _kind = 0;
    uint32_t _basis = 0;
    uint32_t _wrap = 0;
    std::vector<float> _widths;
    uint32_t _widthInterp = 0;
    std::vector<float> _colors;
    uint32_t _colorInterp = 0;
    HdTimeSampleArray<VtVec3fArray, 4> _pointSamples;
    VtVec3fArray _velocities;
    VtVec3fArray _accelerations;
    // The points at the last drawn shutter's opening and closing, and what
    // they were made for (version, open, close, fps).
    mutable std::vector<float> _open;
    mutable std::vector<float> _close;
    mutable std::tuple<uint64_t, float, float, float> _motionFor{~uint64_t(0), 0.0f, 0.0f, 0.0f};
    uint64_t _version = 0;
};

using ThCurves = ThStrokes<HdBasisCurves>;
using ThPoints = ThStrokes<HdPoints>;

// -----------------------------------------------------------------------------
// Render buffer: one AOV image in host memory.
// -----------------------------------------------------------------------------

class ThRenderBuffer final : public HdRenderBuffer
{
public:
    explicit ThRenderBuffer(SdfPath const& id) : HdRenderBuffer(id) {}

    bool Allocate(GfVec3i const& dimensions, HdFormat format, bool /*multiSampled*/) override
    {
        _Deallocate();
        if (dimensions[2] != 1) {
            TF_WARN("hdThPlayblast: 3D render buffers are not supported (%s)",
                    GetId().GetText());
            return false;
        }
        _width = static_cast<unsigned int>(dimensions[0]);
        _height = static_cast<unsigned int>(dimensions[1]);
        _format = format;
        _data.assign(static_cast<size_t>(_width) * _height * HdDataSizeOfFormat(format), 0);
        ++_generation;
        return true;
    }

    // Bumped by every allocation: a pass rewrites a buffer that is new.
    uint64_t Generation() const { return _generation; }

    unsigned int GetWidth() const override { return _width; }
    unsigned int GetHeight() const override { return _height; }
    unsigned int GetDepth() const override { return 1; }
    HdFormat GetFormat() const override { return _format; }
    bool IsMultiSampled() const override { return false; }
    void* Map() override { ++_mappers; return _data.data(); }
    void Unmap() override { --_mappers; }
    bool IsMapped() const override { return _mappers > 0; }
    void Resolve() override {}
    bool IsConverged() const override { return true; }

    // Write an image of `stride` floats per texel into this buffer's own
    // format. The core fills row 0 at the top; a Hydra render buffer keeps
    // row 0 at the bottom (husk wrote the first test frame upside down).
    void Write(std::vector<float> const& image, size_t stride)
    {
        HdFormat const component = HdGetComponentFormat(_format);
        size_t const channels = HdGetComponentCount(_format);
        if (image.size() < static_cast<size_t>(_width) * _height * stride) return;
        if (component == HdFormatFloat32 && channels == stride) {
            // The formats the delegate asks for (GetDefaultAovDescriptor):
            // whole rows, flipped (a viewport frame's copy, on its UI thread).
            _CopyRows(image.data(), stride * sizeof(float));
            return;
        }
        for (size_t y = 0; y < _height; ++y) {
            size_t const src_row = static_cast<size_t>(_height - 1 - y) * _width;
            size_t const dst_row = y * _width;
            for (size_t x = 0; x < _width; ++x) {
                for (size_t c = 0; c < channels; ++c) {
                    float const v = c < stride ? image[(src_row + x) * stride + c] : 0.0f;
                    _Store(component, (dst_row + x) * channels + c, v);
                }
            }
        }
    }

    // Write one int per texel (prim and instance ids), row 0 at the top as
    // in Write.
    void WriteInts(std::vector<int32_t> const& image)
    {
        if (image.size() < static_cast<size_t>(_width) * _height) return;
        HdFormat const component = HdGetComponentFormat(_format);
        size_t const channels = HdGetComponentCount(_format);
        if (component == HdFormatInt32 && channels == 1) {
            _CopyRows(image.data(), sizeof(int32_t));
            return;
        }
        for (size_t y = 0; y < _height; ++y) {
            size_t const src_row = static_cast<size_t>(_height - 1 - y) * _width;
            size_t const dst_row = y * _width;
            for (size_t x = 0; x < _width; ++x) {
                int32_t const v = image[src_row + x];
                for (size_t c = 0; c < channels; ++c) {
                    size_t const at = (dst_row + x) * channels + c;
                    if (component == HdFormatInt32) {
                        reinterpret_cast<int32_t*>(_data.data())[at] = v;
                    } else {
                        _Store(component, at, static_cast<float>(v));
                    }
                }
            }
        }
    }

    // Fill every channel with one value (ids at -1).
    void Clear(float value)
    {
        HdFormat const component = HdGetComponentFormat(_format);
        size_t const count =
            static_cast<size_t>(_width) * _height * HdGetComponentCount(_format);
        for (size_t i = 0; i < count; ++i) {
            _Store(component, i, value);
        }
    }

protected:
    void _Deallocate() override
    {
        _data.clear();
        _width = _height = 0;
        _format = HdFormatInvalid;
    }

private:
    // Copy an image of this buffer's own texel layout (`texel` bytes each),
    // row 0 at the top, to row 0 at the bottom.
    void _CopyRows(void const* image, size_t texel)
    {
        size_t const row = static_cast<size_t>(_width) * texel;
        auto const* src = static_cast<uint8_t const*>(image);
        for (size_t y = 0; y < _height; ++y) {
            std::memcpy(_data.data() + y * row, src + static_cast<size_t>(_height - 1 - y) * row, row);
        }
    }

    void _Store(HdFormat component, size_t at, float value)
    {
        switch (component) {
        case HdFormatFloat32:
            reinterpret_cast<float*>(_data.data())[at] = value;
            break;
        case HdFormatFloat16:
            reinterpret_cast<GfHalf*>(_data.data())[at] = GfHalf(value);
            break;
        case HdFormatInt32:
            reinterpret_cast<int32_t*>(_data.data())[at] = static_cast<int32_t>(value);
            break;
        case HdFormatUNorm8: {
            float const clamped = value < 0.0f ? 0.0f : (value > 1.0f ? 1.0f : value);
            _data[at] = static_cast<uint8_t>(clamped * 255.0f + 0.5f);
            break;
        }
        default:
            break;
        }
    }

    unsigned int _width = 0;
    unsigned int _height = 0;
    HdFormat _format = HdFormatInvalid;
    std::vector<uint8_t> _data;
    int _mappers = 0;
    uint64_t _generation = 0;
};

// -----------------------------------------------------------------------------
// Camera: Hydra's, plus its transform sampled over the motion window.
// -----------------------------------------------------------------------------

class ThCamera final : public HdCamera
{
public:
    explicit ThCamera(SdfPath const& id) : HdCamera(id) {}

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits) override
    {
        // A frame copies the camera (ThRenderPass::_MakeFrame).
        _Changed(param);
        bool const moved = *dirtyBits & HdCamera::DirtyTransform;
        HdCamera::Sync(delegate, param, dirtyBits);
        if (moved) {
            _samples.count = 0;
            if (_SamplingMotion(param) && !GetId().IsEmpty()) {
                delegate->SampleTransform(GetId(), -_motionWindow, _motionWindow, &_samples);
            }
        }
    }

    // Camera-to-world at 2 to 4 keys evenly spread over the shutter, into
    // `out` (16 doubles each); the count, or 0 when the camera holds still.
    uint32_t Keys(_Shutter const& s, double* out) const
    {
        if (!s.on || _samples.count < 2) return 0;
        size_t const count = _KeyCount(_samples, s);
        bool moves = false;
        GfMatrix4d const first = _samples.Resample(s.At(0, count));
        for (size_t k = 0; k < count; ++k) {
            GfMatrix4d const m = _samples.Resample(s.At(k, count));
            moves = moves || m != first;
            std::copy(m.GetArray(), m.GetArray() + 16, out + k * 16);
        }
        return moves ? static_cast<uint32_t>(count) : 0;
    }

private:
    _XformSamples _samples;
};

// -----------------------------------------------------------------------------
// Lights: the scene's UsdLux lights, as authored, for the core to light the
// frame with (src/lights.rs). No shadows, filters, shaping or textures.
// -----------------------------------------------------------------------------

// The light sprims synced, and the core's number for each (ThLight::kind).
std::vector<std::pair<TfToken, uint32_t>> const& _LightKinds()
{
    static std::vector<std::pair<TfToken, uint32_t>> const kinds{
        {HdPrimTypeTokens->distantLight, 0u}, {HdPrimTypeTokens->sphereLight, 1u},
        {HdPrimTypeTokens->rectLight, 2u},    {HdPrimTypeTokens->diskLight, 3u},
        {HdPrimTypeTokens->cylinderLight, 4u}, {HdPrimTypeTokens->domeLight, 5u},
    };
    return kinds;
}

TfToken const _tokTreatAsPoint("treatAsPoint");

float _LightFloat(VtValue const& v, float fallback)
{
    if (v.IsHolding<float>()) return v.UncheckedGet<float>();
    if (v.IsHolding<double>()) return static_cast<float>(v.UncheckedGet<double>());
    if (v.IsHolding<int>()) return static_cast<float>(v.UncheckedGet<int>());
    if (v.IsHolding<GfHalf>()) return static_cast<float>(v.UncheckedGet<GfHalf>());
    return fallback;
}

bool _LightBool(VtValue const& v, bool fallback)
{
    if (v.IsHolding<bool>()) return v.UncheckedGet<bool>();
    if (v.IsHolding<int>()) return v.UncheckedGet<int>() != 0;
    return fallback;
}

class ThLightPrim final : public HdLight
{
public:
    ThLightPrim(SdfPath const& id, uint32_t kind) : HdLight(id) { _light.kind = kind; }

    HdDirtyBits GetInitialDirtyBitsMask() const override { return AllDirty; }

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits) override
    {
        SdfPath const& id = GetId();
        if (delegate && !id.IsEmpty()) {
            if (*dirtyBits & DirtyTransform) {
                GfMatrix4d const m = delegate->GetTransform(id);
                std::copy(m.GetArray(), m.GetArray() + 16, _light.transform);
            }
            // DirtyParams covers visibility too (HdLight::DirtyBits).
            if (*dirtyBits & DirtyParams) {
                auto get = [&](TfToken const& name) { return delegate->GetLightParamValue(id, name); };
                _visible = delegate->GetVisible(id);
                float const intensity = _LightFloat(get(HdLightTokens->intensity), 1.0f);
                float const exposure = _LightFloat(get(HdLightTokens->exposure), 0.0f);
                float const diffuse = _LightFloat(get(HdLightTokens->diffuse), 1.0f);
                _light.intensity = intensity * std::exp2(exposure) * diffuse;
                VtValue const color = get(HdLightTokens->color);
                GfVec3f c(1.0f);
                if (color.IsHolding<GfVec3f>()) c = color.UncheckedGet<GfVec3f>();
                else if (color.IsHolding<GfVec3d>()) c = GfVec3f(color.UncheckedGet<GfVec3d>());
                std::copy(c.data(), c.data() + 3, _light.color);
                bool const tinted = _LightBool(get(HdLightTokens->enableColorTemperature), false);
                _light.temperature = tinted ? _LightFloat(get(HdLightTokens->colorTemperature), 6500.0f) : 0.0f;
                _light.normalize = _LightBool(get(HdLightTokens->normalize), false) ? 1u : 0u;
                _light.point = _LightBool(get(_tokTreatAsPoint), false) ? 1u : 0u;
                _light.angle = _LightFloat(get(HdLightTokens->angle), 0.53f);
                _light.radius = _LightFloat(get(HdLightTokens->radius), 0.5f);
                _light.width = _LightFloat(get(HdLightTokens->width), 1.0f);
                _light.height = _LightFloat(get(HdLightTokens->height), 1.0f);
                _light.length = _LightFloat(get(HdLightTokens->length), 1.0f);
            }
            _synced = true;
        }
        // A frame copies the lights (ThRenderPass::_Gather): draw again.
        _Changed(param);
        *dirtyBits = Clean;
    }

    // The light as the core takes it, or false when it lights nothing
    // (invisible, or never synced).
    bool View(ThLight* out) const
    {
        if (!_synced || !_visible) return false;
        *out = _light;
        return true;
    }

private:
    ThLight _light{{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}};
    bool _visible = true;
    bool _synced = false;
};

// What a pass blurs (th:motionBlur, th:depthOfField, th:blurSamples, th:fps).
struct _BlurSettings
{
    bool motion = false;
    bool dof = false;
    int samples = _defaultBlurSamples;
    float fps = 24.0f;
};

// What a pass draws with. Read from the render settings for every frame
// (ThRenderDelegate::_ReadSettings), as a viewport changes them while it
// runs; husk sets them before the first.
struct _PassSettings
{
    int samples = _defaultSamples;
    uint32_t shading = 0;
    uint32_t lighting = 0;  // th:lighting: 0 scene, 1 headlight
    bool cullBack = false;  // th:cull "back": every mesh's back faces culled
    bool ao = true;
    bool alpha = false;
    _BlurSettings blur;
    // Draw on a background thread (a viewport) rather than in Execute (husk).
    bool interactive = false;
    // Refine subdivision surfaces only as far as the frame shows (husk),
    // rather than to the level asked, as Storm does in Houdini's viewer
    // (see Subdivision).
    bool screenSized = true;
    bool gpu = false;  // th:gpu
    // The depth AOV as distance from the camera, as Houdini's viewer reads
    // it (UsdRenderers.json "depthstyle": "linear"), rather than Hydra's
    // window depth (husk, the farm).
    bool linearDepth = false;

    auto Tie() const
    {
        return std::make_tuple(samples, shading, lighting, cullBack, ao, alpha, blur.motion, blur.dof, blur.samples, blur.fps,
                               interactive, screenSized, gpu, linearDepth);
    }
    bool operator==(_PassSettings const& o) const { return Tie() == o.Tie(); }
};

// A full frame that took longer than this is drawn first as a draft (one
// sample, no ambient occlusion or blur) while the viewport waits for it:
// a moving camera is then drawn as drafts, at the rate a viewport plays.
double const _draftAboveMs = 20.0;

// -----------------------------------------------------------------------------
// Render pass
// -----------------------------------------------------------------------------

class ThRenderPass final : public HdRenderPass
{
public:
    ThRenderPass(HdRenderIndex* index, HdRprimCollection const& collection,
                 std::function<_PassSettings()> settings, ThCache const* cache,
                 std::shared_ptr<_Workers> workers)
        : HdRenderPass(index, collection), _settings(std::move(settings)), _cache(cache),
          _workers(std::move(workers)) {}

    ~ThRenderPass() override
    {
        if (_thread) {
            _workers->Remove(_thread.get());
            _thread.reset();
        }
    }

    // husk's frames are drawn in Execute; a viewport's is converged once
    // its finished frame (not the draft) is in the buffers.
    bool IsConverged() const override { return _converged.load(std::memory_order_relaxed); }

protected:
    // The render buffers the pass writes, by AOV.
    struct _Bindings
    {
        ThRenderBuffer* color = nullptr;
        ThRenderBuffer* depth = nullptr;
        ThRenderBuffer* primId = nullptr;
        ThRenderBuffer* instanceId = nullptr;
        std::vector<ThRenderBuffer*> others;  // filled with -1
        ThRenderBuffer* sized = nullptr;      // the one the frame takes its size from
        // Each bound buffer and its allocation: a new one needs the frame again.
        std::vector<std::pair<ThRenderBuffer const*, uint64_t>> identity;
    };

    void _Execute(HdRenderPassStateSharedPtr const& state,
                  TfTokenVector const& renderTags) override
    {
        _Bindings buffers;
        for (HdRenderPassAovBinding const& binding : state->GetAovBindings()) {
            auto* buffer = dynamic_cast<ThRenderBuffer*>(binding.renderBuffer);
            if (!buffer || buffer->GetWidth() == 0 || buffer->GetHeight() == 0) continue;
            buffers.identity.emplace_back(buffer, buffer->Generation());
            if (binding.aovName == HdAovTokens->color) buffers.color = buffer;
            else if (binding.aovName == HdAovTokens->depth) buffers.depth = buffer;
            else if (binding.aovName == HdAovTokens->primId) buffers.primId = buffer;
            else if (binding.aovName == HdAovTokens->instanceId) buffers.instanceId = buffer;
            else buffers.others.push_back(buffer);
        }
        buffers.sized = buffers.color ? buffers.color : buffers.depth;
        if (!buffers.sized) {
            _converged = true;
            return;
        }
        _PassSettings const settings = _settings();
        // A new depth buffer reads as nothing drawn until a frame is
        // published, not as zeros (the near plane, or at the camera when
        // linear): the viewer may pick a tumble pivot from it at once.
        if (buffers.depth
            && (buffers.depth != _depthCleared.first || buffers.depth->Generation() != _depthCleared.second)) {
            buffers.depth->Clear(settings.linearDepth ? std::numeric_limits<float>::max() : 1.0f);
            _depthCleared = {buffers.depth, buffers.depth->Generation()};
        }
        if (settings.interactive) {
            _ExecuteInBackground(state, renderTags, settings, buffers);
        } else {
            _ExecuteNow(state, renderTags, settings, buffers);
        }
    }

private:
    // husk: draw the frame here and return with it in the buffers.
    void _ExecuteNow(HdRenderPassStateSharedPtr const& state, TfTokenVector const& renderTags,
                     _PassSettings const& settings, _Bindings const& buffers)
    {
        auto const started = std::chrono::steady_clock::now();
        _SizeRefinement(state, renderTags, settings, buffers.sized->GetWidth(), buffers.sized->GetHeight());
        _Shutter shutter;
        ThFrame const frame = _MakeFrame(state, settings, buffers.sized->GetWidth(),
                                         buffers.sized->GetHeight(), &shutter);
        std::shared_ptr<_Job> const job = _Gather(state, renderTags, shutter, buffers, settings.cullBack);
        auto const gathered = std::chrono::steady_clock::now();
        _Image image;
        int32_t const status = _Draw(*job, frame, &image, nullptr, _cache, &_workers->cacheMutex);
        if (status != 0) {
            TF_WARN("hdThPlayblast: the core failed to render (error %d)", status);
            return;
        }
        _Publish(buffers, image);
        _converged = true;
        _workers->Drawn(_StatsOf(*job, frame.samples, false));

        // TH_PLAYBLAST_TIMING=1: where a frame's time goes (the scene sync
        // husk does before this pass is the gap between two lines).
        if (std::getenv("TH_PLAYBLAST_TIMING")) {
            auto const done = std::chrono::steady_clock::now();
            auto ms = [](auto a, auto b) {
                return std::chrono::duration_cast<std::chrono::milliseconds>(b - a).count();
            };
            size_t triangles = 0, instances = 0, moving = 0;
            for (ThMeshView const& v : job->views) {
                triangles += static_cast<size_t>(v.triangle_count) * v.transform_count;
                instances += v.transform_count;
                moving += (v.points_close || v.transform_key_count) ? 1 : 0;
            }
            static std::chrono::steady_clock::time_point previous;
            long long const since =
                previous.time_since_epoch().count() ? ms(previous, done) : 0;
            previous = done;
            std::fprintf(stderr,
                         "hdThPlayblast: %zu meshes (%zu moving), %zu instances, %zu triangles; "
                         "blur %u samples, shutter %s %.3g..%.3g, %u camera keys, f-stop %.3g; "
                         "gather %lld ms, render %lld ms; mesh sync %lld ms (thread time), "
                         "%d rebuilt (%d subdivided), %d with new points only; refine level %d, drawn at %d; "
                         "%lld ms since the last frame\n",
                         job->views.size(), moving, instances, triangles,
                         frame.blur_samples, shutter.on ? "open" : "off", shutter.open, shutter.close,
                         frame.camera_keys, frame.f_stop,
                         static_cast<long long>(ms(started, gathered)),
                         static_cast<long long>(ms(gathered, done)),
                         g_syncNs.exchange(0) / 1000000, g_rebuilds.exchange(0), g_subdivided.exchange(0),
                         g_pointUpdates.exchange(0), job->refineAsked, job->refineDrawn, since);
        }
    }

    // A viewport: start the frame on the pass's thread when anything it
    // depends on changed, and put whatever has finished in the buffers.
    void _ExecuteInBackground(HdRenderPassStateSharedPtr const& state, TfTokenVector const& renderTags,
                              _PassSettings const& settings, _Bindings const& buffers)
    {
        using _Clock = std::chrono::steady_clock;
        auto const entered = _Clock::now();
        auto ms = [](_Clock::time_point a, _Clock::time_point b) {
            return std::chrono::duration<double, std::milli>(b - a).count();
        };
        unsigned int const width = buffers.sized->GetWidth();
        unsigned int const height = buffers.sized->GetHeight();
        // A mesh it rebuilds stops the render and counts as an edit, so
        // the key below asks for the frame again.
        _SizeRefinement(state, renderTags, settings, width, height);
        auto const sized = _Clock::now();
        double stopMs = 0.0, gatherMs = 0.0, takeMs = 0.0, publishMs = 0.0;
        bool started = false, published = false;
        GfMatrix4d const worldToView = state->GetWorldToViewMatrix();
        _Key key{width, height, worldToView, state->GetProjectionMatrix(), settings, renderTags,
                 _workers->edits.load(std::memory_order_relaxed), buffers.identity, state->GetCullStyle()};
        if (!_thread) {
            _thread = std::make_unique<ThRenderThread>(_cache, &_workers->cacheMutex);
            _workers->Add(_thread.get());
        }
        if (!(key == _key)) {
            started = true;
            _Shutter shutter;
            ThFrame const frame = _MakeFrame(state, settings, width, height, &shutter);
            // The frame drawing reads the scene while this one is gathered
            // (an edit to the scene stopped it already), unless gathering
            // rewrites what it reads: a moving mesh's points at the ends of
            // the shutter, kept for the last one asked (ThMesh::_Motion).
            if (shutter.on && !(shutter == _startedShutter)) {
                auto const t0 = _Clock::now();
                _thread->Stop();
                stopMs = ms(t0, _Clock::now());
            }
            // One of another size could not be shown: no use finishing it.
            bool const resized = width != _key.width || height != _key.height || buffers.identity != _key.buffers;
            _requested = _Clock::now();
            std::vector<ThFrame> frames;
            double const last = _thread->LastFinalMs();
            bool const costly = frame.samples > 1 || frame.ao || frame.blur_samples > 1;
            if (costly && (last == 0.0 || last > _draftAboveMs)) {
                ThFrame draft = frame;
                draft.samples = 1;
                draft.ao = 0;
                draft.blur_samples = 0;
                draft.motion = 0;
                draft.camera_keys = 0;
                frames.push_back(draft);
            }
            frames.push_back(frame);
            auto const t2 = _Clock::now();
            _started = _Gather(state, renderTags, shutter, buffers, settings.cullBack);
            gatherMs = ms(t2, _Clock::now());
            _startedSamples = frame.samples;
            _startedShutter = shutter;
            _thread->Request(_started, std::move(frames), ++_request, resized);
            _key = std::move(key);
            _converged = false;
        }
        _Image& image = _shown;
        auto const t3 = _Clock::now();
        bool const taken = _thread->Take(&_seen, &image);
        auto const t4 = _Clock::now();
        takeMs = ms(t3, t4);
        bool draft = false;
        if (taken && image.width == width && image.height == height) {
            _Publish(buffers, image);
            publishMs = ms(t4, _Clock::now());
            published = true;
            draft = !image.final;
            if (image.request == _request && _started) {
                _workers->Drawn(_StatsOf(*_started, image.final ? _startedSamples : 1, !image.final));
            }
            if (image.final && image.request == _request) _converged = true;
        }
        // TH_PLAYBLAST_TIMING=1: what each Execute that started a frame or
        // published one cost the viewer's thread, how long after the
        // request a frame reached the buffers, and how often Houdini calls.
        if (g_timing && (started || published)) {
            auto const left = _Clock::now();
            std::fprintf(stderr,
                         "hdThPlayblast: execute %ux%u%s%s: %.1f ms on the viewer's thread (refine %.1f, stop %.1f, "
                         "gather %.1f, take %.1f, publish %.1f)%s; %.1f ms since the last execute\n",
                         width, height, started ? " started" : "",
                         published ? (draft ? " published draft" : " published frame") : "", ms(entered, left),
                         ms(entered, sized), stopMs, gatherMs, takeMs, publishMs,
                         published && image.request == _request
                             ? TfStringPrintf(", shown %.1f ms after the request", ms(_requested, left)).c_str()
                             : "",
                         _lastExecute.time_since_epoch().count() ? ms(_lastExecute, entered) : 0.0);
        }
        _lastExecute = entered;
    }

    static _FrameStats _StatsOf(_Job const& job, uint32_t samples, bool draft)
    {
        _FrameStats s;
        s.any = true;
        s.draft = draft;
        s.samples = samples;
        s.meshes = job.views.size();
        s.triangles = job.triangles;
        s.subdivided = job.subdivided;
        s.refineAsked = job.refineAsked;
        s.refineDrawn = job.refineDrawn;
        return s;
    }

    // What a viewport frame depends on besides the scene's prims (whose
    // edits _Workers counts).
    struct _Key
    {
        unsigned int width = 0;
        unsigned int height = 0;
        GfMatrix4d worldToView{1.0};
        GfMatrix4d projection{1.0};
        _PassSettings settings;
        TfTokenVector renderTags;
        uint64_t edits = 0;
        std::vector<std::pair<ThRenderBuffer const*, uint64_t>> buffers;
        HdCullStyle cull = HdCullStyleDontCare;

        bool operator==(_Key const& o) const
        {
            return width == o.width && height == o.height && worldToView == o.worldToView
                && projection == o.projection && settings == o.settings && renderTags == o.renderTags
                && edits == o.edits && buffers == o.buffers && cull == o.cull;
        }
    };

    // The frame `settings` ask for through the pass's camera, at
    // `width x height`, and the shutter it is drawn over.
    ThFrame _MakeFrame(HdRenderPassStateSharedPtr const& state, _PassSettings const& settings,
                       unsigned int width, unsigned int height, _Shutter* shutter) const
    {
        GfMatrix4d const worldToView = state->GetWorldToViewMatrix();
        GfMatrix4d const worldToClip = worldToView * state->GetProjectionMatrix();
        GfMatrix4d const viewToWorld = worldToView.GetInverse();

        ThFrame frame{};
        frame.width = width;
        frame.height = height;
        std::copy(worldToClip.GetArray(), worldToClip.GetArray() + 16, frame.world_to_clip);
        std::copy(viewToWorld.GetArray(), viewToWorld.GetArray() + 16, frame.view_to_world);
        frame.samples = static_cast<uint32_t>(std::max(1, settings.samples));
        frame.shading = settings.shading;
        frame.lighting = settings.lighting;  // the lights come with the job (_Draw)
        frame.ao = settings.ao ? 1u : 0u;
        frame.alpha = settings.alpha ? 1u : 0u;
        frame.gpu = settings.gpu ? 1u : 0u;
        frame.linear_depth = settings.linearDepth ? 1u : 0u;

        // Motion blur over the render camera's shutter (held to the window
        // the prims sampled their motion over), depth of field from its lens.
        _BlurSettings const& blur = settings.blur;
        HdCamera const* camera = state->GetCamera();
        *shutter = _Shutter{};
        shutter->fps = blur.fps;
        if (blur.motion && camera) {
            shutter->open = static_cast<float>(std::clamp(camera->GetShutterOpen(), -1.0 * _motionWindow, 1.0 * _motionWindow));
            shutter->close = static_cast<float>(std::clamp(camera->GetShutterClose(), -1.0 * _motionWindow, 1.0 * _motionWindow));
            shutter->on = shutter->close > shutter->open;
        }
        if (blur.motion || blur.dof) {
            frame.blur_samples = static_cast<uint32_t>(std::max(1, blur.samples));
        }
        frame.motion = shutter->on ? 1u : 0u;
        if (auto const* thCamera = dynamic_cast<ThCamera const*>(camera)) {
            frame.camera_keys = thCamera->Keys(*shutter, frame.cameras);
        }
        if (blur.dof && camera) {
            frame.focal_length = camera->GetFocalLength();
            frame.f_stop = camera->GetFStop();
            frame.focus_distance = camera->GetFocusDistance();
        }
        return frame;
    }

    // A mesh the pass draws: visible, and of a render tag (purpose) the
    // pass asked for.
    static bool _Draws(ThMesh const* mesh, TfTokenVector const& renderTags)
    {
        return mesh && mesh->IsVisible()
            && (renderTags.empty()
                || std::find(renderTags.begin(), renderTags.end(), mesh->GetRenderTag()) != renderTags.end());
    }

    // Refine each subdivision surface drawn to the level this frame needs
    // (see Subdivision): the level asked in Houdini's viewer, as far as the
    // camera shows in husk (ThMesh::ScreenLevel). The meshes whose level
    // changed are rebuilt, several at once as Hydra syncs them.
    void _SizeRefinement(HdRenderPassStateSharedPtr const& state, TfTokenVector const& renderTags,
                         _PassSettings const& settings, unsigned int width, unsigned int height) const
    {
        HdRenderIndex& index = *GetRenderIndex();
        GfMatrix4d const worldToView = state->GetWorldToViewMatrix();
        GfMatrix4d const projection = state->GetProjectionMatrix();
        std::vector<std::pair<ThMesh*, int>> resized;
        for (SdfPath const& id : index.GetRprimIds()) {
            // The delegate made it (CreateRprim): not a const object.
            auto* mesh = const_cast<ThMesh*>(dynamic_cast<ThMesh const*>(index.GetRprim(id)));
            if (!_Draws(mesh, renderTags) || !mesh->Subdivides()) continue;
            int level = mesh->RefineCap();
            if (level > 0 && settings.screenSized) {
                std::vector<GfMatrix4d> instances;
                if (!mesh->GetInstancerId().IsEmpty()) {
                    if (auto* instancer = dynamic_cast<ThInstancer*>(index.GetInstancer(mesh->GetInstancerId()))) {
                        instances = instancer->ComputeInstanceTransforms(mesh->GetId(), nullptr);
                    }
                }
                level = mesh->ScreenLevel(instances, worldToView, projection, width, height);
            }
            if (level != mesh->SizedLevel()) resized.emplace_back(mesh, level);
        }
        if (resized.empty()) return;
        _workers->Edited();  // nothing may draw the meshes while they change
        bool const motion = _SamplingMotion(index.GetRenderDelegate()->GetRenderParam());
        WorkParallelForN(resized.size(), [&](size_t begin, size_t end) {
            for (size_t i = begin; i < end; ++i) {
                ThMesh* mesh = resized[i].first;
                if (HdSceneDelegate* delegate = index.GetSceneDelegateForRprim(mesh->GetId())) {
                    mesh->Resize(delegate, resized[i].second, motion);
                }
            }
        });
    }

    // The meshes the pass draws (_Draws), with what they point at, culled
    // as the pass state says (ThMesh::Views), or every back face when
    // `cullBack` (th:cull "back").
    std::shared_ptr<_Job> _Gather(HdRenderPassStateSharedPtr const& state, TfTokenVector const& renderTags,
                                  _Shutter const& shutter, _Bindings const& buffers, bool cullBack) const
    {
        HdCullStyle const passCull = state->GetCullStyle();
        auto job = std::make_shared<_Job>();
        job->ids = buffers.primId || buffers.instanceId;
        HdRenderIndex& index = *GetRenderIndex();
        std::vector<ThMesh const*> meshes;
        for (SdfPath const& id : index.GetRprimIds()) {
            auto const* mesh = dynamic_cast<ThMesh const*>(index.GetRprim(id));
            if (_Draws(mesh, renderTags)) meshes.push_back(mesh);
        }
        job->views.reserve(meshes.size());
        job->transforms.resize(meshes.size());
        job->keys.resize(meshes.size());
        auto materialOf = [&index](SdfPath const& path) -> ThMaterial const* {
            if (path.IsEmpty()) return nullptr;
            return dynamic_cast<ThMaterial const*>(index.GetSprim(HdPrimTypeTokens->material, path));
        };
        for (size_t i = 0; i < meshes.size(); ++i) {
            ThMesh const* mesh = meshes[i];
            ThInstancer* instancer = mesh->GetInstancerId().IsEmpty() ? nullptr
                : dynamic_cast<ThInstancer*>(index.GetInstancer(mesh->GetInstancerId()));
            auto instancesAt = [&](float const* time) {
                return instancer ? instancer->ComputeInstanceTransforms(mesh->GetId(), time)
                                 : std::vector<GfMatrix4d>{};
            };
            size_t const instancerKeys = instancer && shutter.on ? instancer->KeyCount(shutter) : 0;
            // A mesh drawn in parts (see Subsets) is several views, each
            // picked as the mesh.
            size_t const first = job->views.size();
            if (mesh->Views(&job->views, &job->transforms[i], &job->keys[i], materialOf, instancesAt, instancerKeys,
                            shutter, passCull, cullBack) == 0) {
                continue;
            }
            for (size_t k = first; k < job->views.size(); ++k) {
                ThMeshView const& view = job->views[k];
                job->triangles += static_cast<size_t>(view.triangle_count) * view.transform_count;
                job->primIds.push_back(mesh->GetPrimId());
                job->instanced.push_back(instancer ? 1 : 0);
            }
            job->refineAsked = std::max(job->refineAsked, mesh->RefineAsked());
            if (int const drawn = mesh->RefineDrawn()) {
                ++job->subdivided;
                job->refineDrawn = std::max(job->refineDrawn, drawn);
            }
        }
        _GatherStrokes(renderTags, shutter, job.get());
        _GatherLights(job.get());
        return job;
    }

    // The scene's enabled, visible lights. TH_PLAYBLAST_LIGHTS=1 lists them.
    // Houdini's own default headlight is not one: husk (and Houdini's USD
    // layer, libHUSD) adds a distant light named __the_headlight
    // (/husk_headlight/__the_headlight in husk) to a scene without lights,
    // and such a scene is lit by the fixed camera rig, as before scene
    // lights existed.
    void _GatherLights(_Job* job) const
    {
        HdRenderIndex& index = *GetRenderIndex();
        bool const report = std::getenv("TH_PLAYBLAST_LIGHTS") != nullptr;
        static TfToken const houdiniHeadlight("__the_headlight");
        for (auto const& [type, kind] : _LightKinds()) {
            if (!index.IsSprimTypeSupported(type)) continue;
            for (SdfPath const& id : index.GetSprimSubtree(type, SdfPath::AbsoluteRootPath())) {
                auto const* prim = dynamic_cast<ThLightPrim const*>(index.GetSprim(type, id));
                ThLight light;
                bool const lit = prim && id.GetNameToken() != houdiniHeadlight && prim->View(&light);
                if (lit) job->lights.push_back(light);
                if (report) {
                    std::fprintf(stderr, "hdThPlayblast: light %s (%s)%s", id.GetText(), type.GetText(),
                                 lit ? "" : " off\n");
                    if (lit) {
                        std::fprintf(stderr, ": intensity %g, colour %g %g %g, %g K, normalize %u, point %u, "
                                     "angle %g, radius %g, %g x %g, length %g, at %g %g %g\n",
                                     light.intensity, light.color[0], light.color[1], light.color[2],
                                     light.temperature, light.normalize, light.point, light.angle, light.radius,
                                     light.width, light.height, light.length, light.transform[12],
                                     light.transform[13], light.transform[14]);
                    }
                }
            }
        }
    }

    // The basis curves and points the pass draws, after the meshes.
    void _GatherStrokes(TfTokenVector const& renderTags, _Shutter const& shutter, _Job* job) const
    {
        HdRenderIndex& index = *GetRenderIndex();
        std::vector<HdRprim const*> prims;
        for (SdfPath const& id : index.GetRprimIds()) {
            HdRprim const* prim = index.GetRprim(id);
            if (!dynamic_cast<ThCurves const*>(prim) && !dynamic_cast<ThPoints const*>(prim)) continue;
            if (!prim->IsVisible()) continue;
            if (!renderTags.empty()
                && std::find(renderTags.begin(), renderTags.end(), prim->GetRenderTag()) == renderTags.end()) {
                continue;
            }
            prims.push_back(prim);
        }
        job->curves.reserve(prims.size());
        job->curveTransforms.resize(prims.size());
        job->curveKeys.resize(prims.size());
        for (size_t i = 0; i < prims.size(); ++i) {
            HdRprim const* prim = prims[i];
            ThInstancer* instancer = prim->GetInstancerId().IsEmpty() ? nullptr
                : dynamic_cast<ThInstancer*>(index.GetInstancer(prim->GetInstancerId()));
            auto instancesAt = [&](float const* time) {
                return instancer ? instancer->ComputeInstanceTransforms(prim->GetId(), time)
                                 : std::vector<GfMatrix4d>{};
            };
            size_t const instancerKeys = instancer && shutter.on ? instancer->KeyCount(shutter) : 0;
            ThMaterial const* material = nullptr;
            if (!prim->GetMaterialId().IsEmpty()) {
                material = dynamic_cast<ThMaterial const*>(
                    index.GetSprim(HdPrimTypeTokens->material, prim->GetMaterialId()));
            }
            ThCurvesView view;
            bool drawn = false;
            if (auto const* curves = dynamic_cast<ThCurves const*>(prim)) {
                drawn = curves->View(&view, &job->curveTransforms[i], &job->curveKeys[i], material, instancesAt,
                                     instancerKeys, shutter);
            } else if (auto const* points = dynamic_cast<ThPoints const*>(prim)) {
                drawn = points->View(&view, &job->curveTransforms[i], &job->curveKeys[i], material, instancesAt,
                                     instancerKeys, shutter);
            }
            if (drawn) {
                job->curves.push_back(view);
                job->primIds.push_back(prim->GetPrimId());
                job->instanced.push_back(instancer ? 1 : 0);
            }
        }
    }

    // Put a finished frame in the bound buffers.
    static void _Publish(_Bindings const& buffers, _Image const& image)
    {
        if (buffers.color) buffers.color->Write(image.color, 4);
        if (buffers.depth) buffers.depth->Write(image.depth, 1);
        if (buffers.primId) buffers.primId->WriteInts(image.primId);
        if (buffers.instanceId) buffers.instanceId->WriteInts(image.instanceId);
        for (ThRenderBuffer* b : buffers.others) b->Clear(-1.0f);
    }

    std::function<_PassSettings()> _settings;
    ThCache const* _cache;  // the delegate's; outlives its passes
    std::shared_ptr<_Workers> _workers;
    std::atomic<bool> _converged{false};
    // A viewport's: its thread, the frame it is drawing and the last shown.
    std::unique_ptr<ThRenderThread> _thread;
    _Key _key;
    uint64_t _request = 0;
    uint64_t _seen = 0;
    // The depth buffer last cleared, and its allocation.
    std::pair<ThRenderBuffer const*, uint64_t> _depthCleared{nullptr, 0};
    // The job of the frame drawing and its samples, for the render stats,
    // and the shutter it was gathered over.
    std::shared_ptr<_Job> _started;
    uint32_t _startedSamples = 0;
    _Shutter _startedShutter;
    // The last frame taken from the thread (its buffers go back to it).
    _Image _shown;
    // TH_PLAYBLAST_TIMING: when the frame drawing was asked for, and the
    // last Execute.
    std::chrono::steady_clock::time_point _requested;
    std::chrono::steady_clock::time_point _lastExecute;
};

// -----------------------------------------------------------------------------
// Render delegate
// -----------------------------------------------------------------------------

class ThRenderDelegate final : public HdRenderDelegate
{
public:
    ThRenderDelegate() : _registry(std::make_shared<HdResourceRegistry>()) { _UpdateParam(); }
    explicit ThRenderDelegate(HdRenderSettingsMap const& settings)
        : HdRenderDelegate(settings), _registry(std::make_shared<HdResourceRegistry>()) { _UpdateParam(); }

    HdRenderParam* GetRenderParam() const override { return const_cast<ThRenderParam*>(&_param); }

    // The viewer's render stats (its Display Options): the last frame
    // drawn, as one line under the renderer's name.
    VtDictionary GetRenderStats() const override
    {
        _FrameStats const s = _param.workers->LastDrawn();
        VtDictionary out;
        out["rendererName"] = VtValue(std::string("TumbleView"));
        out["percentDone"] = VtValue(s.any && !s.draft ? 100.0 : 0.0);
        if (!s.any) return out;
        std::string line = TfStringPrintf(
            "%s, %u sample%s; %zu mesh%s, %zu triangles",
            s.draft ? "draft" : "full", s.samples, s.samples == 1 ? "" : "s",
            s.meshes, s.meshes == 1 ? "" : "es", s.triangles);
        if (s.refineAsked > 0) {
            line += TfStringPrintf("; refine level %d", s.refineAsked);
            if (s.refineDrawn != s.refineAsked) line += TfStringPrintf(" (drawn at %d)", s.refineDrawn);
            line += TfStringPrintf(", %zu subdivided", s.subdivided);
        }
        out["rendererStage"] = VtValue(line);
        out["meshes"] = VtValue(static_cast<int64_t>(s.meshes));
        out["triangles"] = VtValue(static_cast<int64_t>(s.triangles));
        out["samples"] = VtValue(static_cast<int>(s.samples));
        out["draft"] = VtValue(s.draft);
        out["refineLevel"] = VtValue(s.refineAsked);
        out["refineLevelDrawn"] = VtValue(s.refineDrawn);
        out["subdividedMeshes"] = VtValue(static_cast<int64_t>(s.subdivided));
        return out;
    }

    void SetRenderSetting(TfToken const& key, VtValue const& value) override
    {
        HdRenderDelegate::SetRenderSetting(key, value);
        _UpdateParam();
    }
    ~ThRenderDelegate() override
    {
        _param.workers->Edited();  // no background frame may read the cache
        th_cache_free(_cache);
    }

    TfTokenVector const& GetSupportedRprimTypes() const override
    {
        static TfTokenVector const types{HdPrimTypeTokens->mesh, HdPrimTypeTokens->basisCurves,
                                         HdPrimTypeTokens->points};
        return types;
    }
    TfTokenVector const& GetSupportedSprimTypes() const override
    {
        static TfTokenVector const types = [] {
            TfTokenVector t{HdPrimTypeTokens->camera, HdPrimTypeTokens->material,
                            HdPrimTypeTokens->extComputation};
            for (auto const& kind : _LightKinds()) t.push_back(kind.first);
            return t;
        }();
        return types;
    }
    TfTokenVector const& GetSupportedBprimTypes() const override
    {
        static TfTokenVector const types{HdPrimTypeTokens->renderBuffer};
        return types;
    }

    HdResourceRegistrySharedPtr GetResourceRegistry() const override { return _registry; }

    // Karma's own networks first (outputs:kma:surface, MaterialX nodes:
    // what a Karma material builder writes), then plain MaterialX, then the
    // universal one. The universal UsdPreviewSurface a Karma material carries
    // is a generic 0.8 grey placeholder, so it must come last or every
    // surface turns white (HideAndReek/010's set did).
    TfTokenVector GetMaterialRenderContexts() const override
    {
        return {TfToken("kma"), TfToken("mtlx"), TfToken("")};
    }

    HdAovDescriptor GetDefaultAovDescriptor(TfToken const& name) const override
    {
        if (name == HdAovTokens->color) {
            return HdAovDescriptor(HdFormatFloat32Vec4, false, VtValue(GfVec4f(0.0f)));
        }
        if (name == HdAovTokens->depth) {
            return HdAovDescriptor(HdFormatFloat32, false, VtValue(1.0f));
        }
        if (name == HdAovTokens->primId || name == HdAovTokens->instanceId
            || name == HdAovTokens->elementId) {
            return HdAovDescriptor(HdFormatInt32, false, VtValue(-1));
        }
        return HdAovDescriptor();
    }

    HdRenderPassSharedPtr CreateRenderPass(HdRenderIndex* index,
                                           HdRprimCollection const& collection) override
    {
        // TH_PLAYBLAST_SETTINGS=1: what the host set (Houdini's viewer and
        // husk differ; th:interactive's default comes from it).
        if (std::getenv("TH_PLAYBLAST_SETTINGS")) {
            for (auto const& [key, value] : _settingsMap) {
                std::fprintf(stderr, "hdThPlayblast: setting %s = %s (%s)\n", key.GetText(),
                             TfStringify(value).c_str(), value.GetTypeName().c_str());
            }
        }
        return std::make_shared<ThRenderPass>(index, collection, [this] { return _ReadSettings(); },
                                              _cache, _param.workers);
    }

    HdInstancer* CreateInstancer(HdSceneDelegate* delegate, SdfPath const& id) override
    {
        return new ThInstancer(delegate, id);
    }
    void DestroyInstancer(HdInstancer* instancer) override
    {
        _param.workers->Edited();
        delete instancer;
    }

    HdRprim* CreateRprim(TfToken const& typeId, SdfPath const& id) override
    {
        if (typeId == HdPrimTypeTokens->mesh) {
            return new ThMesh(id);
        }
        if (typeId == HdPrimTypeTokens->basisCurves) {
            return new ThCurves(id);
        }
        if (typeId == HdPrimTypeTokens->points) {
            return new ThPoints(id);
        }
        return nullptr;
    }
    void DestroyRprim(HdRprim* rprim) override
    {
        _param.workers->Edited();
        delete rprim;
    }

    HdSprim* CreateSprim(TfToken const& typeId, SdfPath const& id) override
    {
        if (typeId == HdPrimTypeTokens->camera) return new ThCamera(id);
        if (typeId == HdPrimTypeTokens->material) return new ThMaterial(id, &_textures);
        if (typeId == HdPrimTypeTokens->extComputation) return new HdExtComputation(id);
        for (auto const& [type, kind] : _LightKinds()) {
            if (typeId == type) return new ThLightPrim(id, kind);
        }
        return nullptr;
    }
    HdSprim* CreateFallbackSprim(TfToken const& typeId) override
    {
        return CreateSprim(typeId, SdfPath::EmptyPath());
    }
    void DestroySprim(HdSprim* sprim) override
    {
        _param.workers->Edited();
        delete sprim;
    }

    HdBprim* CreateBprim(TfToken const& typeId, SdfPath const& id) override
    {
        if (typeId == HdPrimTypeTokens->renderBuffer) {
            return new ThRenderBuffer(id);
        }
        return nullptr;
    }
    HdBprim* CreateFallbackBprim(TfToken const& typeId) override
    {
        return CreateBprim(typeId, SdfPath::EmptyPath());
    }
    void DestroyBprim(HdBprim* bprim) override { delete bprim; }

    void CommitResources(HdChangeTracker*) override {}

private:
    // A pass's settings, read for every frame (_PassSettings).
    _PassSettings _ReadSettings() const
    {
        _PassSettings s;
        VtValue const samplesSetting = GetRenderSetting(_tokSamples);
        if (samplesSetting.IsHolding<int>()) {
            s.samples = samplesSetting.UncheckedGet<int>();
        } else if (char const* env = std::getenv("TH_PLAYBLAST_SAMPLES")) {
            if (int const value = std::atoi(env); value > 0) s.samples = value;
        }
        std::string shading = _SettingString(GetRenderSetting(_tokShading));
        if (shading.empty()) shading = _EnvLower("TH_PLAYBLAST_SHADING");
        s.shading = _ShadingNumber(shading);
        std::string lighting = _SettingString(GetRenderSetting(_tokLighting));
        if (lighting.empty()) lighting = _EnvLower("TH_PLAYBLAST_LIGHTING");
        s.lighting = _LightingNumber(lighting);
        std::string cull = _SettingString(GetRenderSetting(_tokCull));
        if (cull.empty()) cull = _EnvLower("TH_PLAYBLAST_CULL");
        s.cullBack = _CullBack(cull);
        VtValue const aoSetting = GetRenderSetting(_tokAo);
        if (aoSetting.IsHolding<bool>()) {
            s.ao = aoSetting.UncheckedGet<bool>();
        } else if (aoSetting.IsHolding<int>()) {
            s.ao = aoSetting.UncheckedGet<int>() != 0;
        } else {
            std::string const env = _EnvLower("TH_PLAYBLAST_AO");
            s.ao = !(env == "0" || env == "false" || env == "off");
        }
        s.blur.motion = _SettingOn(GetRenderSetting(_tokMotionBlur), "TH_PLAYBLAST_MOTION_BLUR");
        s.blur.dof = _SettingOn(GetRenderSetting(_tokDepthOfField), "TH_PLAYBLAST_DOF");
        s.blur.samples = static_cast<int>(_SettingNumber(GetRenderSetting(_tokBlurSamples),
                                                         "TH_PLAYBLAST_BLUR_SAMPLES", _defaultBlurSamples));
        s.blur.fps = static_cast<float>(_SettingNumber(GetRenderSetting(_tokFps), "TH_PLAYBLAST_FPS", 24.0));
        bool const viewer = _Truthy(GetRenderSetting(_tokHoudiniViewport));
        s.interactive = _SettingOr(GetRenderSetting(_tokInteractive), "TH_PLAYBLAST_INTERACTIVE", viewer);
        s.screenSized = !viewer;
        s.linearDepth = viewer;
        // A viewport draws over Houdini's own background and grid.
        s.alpha = _SettingOr(GetRenderSetting(_tokAlpha), "TH_PLAYBLAST_ALPHA", s.interactive);
        s.gpu = _SettingOr(GetRenderSetting(_tokGpu), "TH_PLAYBLAST_GPU", s.interactive);
        return s;
    }

    // Prims sample their motion while th:motionBlur is on.
    void _UpdateParam()
    {
        _param.motion = _SettingOn(GetRenderSetting(_tokMotionBlur), "TH_PLAYBLAST_MOTION_BLUR");
    }

    HdResourceRegistrySharedPtr _registry;
    ThRenderParam _param;
    TextureCache _textures;
    // Meshes and textures the GPU path keeps on the card between frames.
    ThCache* _cache = th_cache_create();
};

}  // namespace

// -----------------------------------------------------------------------------
// Renderer plugin
// -----------------------------------------------------------------------------

class HdThPlayblastRendererPlugin final : public HdRendererPlugin
{
public:
    HdRenderDelegate* CreateRenderDelegate() override { return new ThRenderDelegate(); }
    HdRenderDelegate* CreateRenderDelegate(HdRenderSettingsMap const& settings) override
    {
        return new ThRenderDelegate(settings);
    }
    void DeleteRenderDelegate(HdRenderDelegate* delegate) override { delete delegate; }

    // Host memory only: no GPU, no GL context, no display.
    bool IsSupported(HdRendererCreateArgs const&, std::string*) const override { return true; }
};

// Plugin registration.
//
// A plain C++ static initializer, not TF_REGISTRY_FUNCTION, for the reason
// src/resolver/cpp/resolver.cpp gives: MSVC can drop the .pxrctor section
// the Tf registry macros rely on. The CMake build force-links this object
// (/WHOLEARCHIVE) because nothing references it by symbol.
//
// Implicit shapes (UsdGeomCube, Sphere, Cylinder, Cone, Capsule, Plane)
// reach a render delegate as their own prim types, and this one draws meshes
// only: without this they vanished from the frame. Hydra's implicit-surface
// scene index turns them into meshes for us. Houdini's USD registers no
// plugin for it (Storm has its own), so it is appended by callback; the name
// is the renderer's displayName in plugInfo.json.
namespace {
void _RegisterImplicitSurfacesToMesh()
{
    HdDataSourceBaseHandle const toMesh = HdRetainedTypedSampledDataSource<TfToken>::New(
        HdsiImplicitSurfaceSceneIndexTokens->toMesh);
    TfToken const types[] = {HdPrimTypeTokens->sphere, HdPrimTypeTokens->cube,
                             HdPrimTypeTokens->cone, HdPrimTypeTokens->cylinder,
                             HdPrimTypeTokens->capsule, HdPrimTypeTokens->plane};
    HdDataSourceBaseHandle const modes[] = {toMesh, toMesh, toMesh, toMesh, toMesh, toMesh};
    HdSceneIndexPluginRegistry::GetInstance().RegisterSceneIndexForRenderer(
        "TumbleView",
        [](std::string const&, HdSceneIndexBaseRefPtr const& input,
           HdContainerDataSourceHandle const& args) -> HdSceneIndexBaseRefPtr {
            return HdsiImplicitSurfaceSceneIndex::New(input, args);
        },
        HdRetainedContainerDataSource::New(std::size(types), types, modes),
        0, HdSceneIndexPluginRegistry::InsertionOrderAtStart);
}

struct _PluginRegistrar {
    _PluginRegistrar()
    {
        HdRendererPluginRegistry::Define<HdThPlayblastRendererPlugin>();
        _RegisterImplicitSurfacesToMesh();
    }
};
const _PluginRegistrar _pluginRegistrar;
}  // namespace

PXR_NAMESPACE_CLOSE_SCOPE
