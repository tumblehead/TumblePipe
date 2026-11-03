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
//   ThInstancer   per-instance transforms, nested instancers included.
//   ThMaterial    the base colour or base-colour texture, read from a
//                 UsdPreviewSurface or MaterialX network.
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
#include <pxr/base/gf/rotation.h>
#include <pxr/base/gf/vec2f.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/gf/vec3i.h>
#include <pxr/base/gf/vec4f.h>
#include <pxr/base/tf/diagnostic.h>
#include <pxr/base/tf/stringUtils.h>
#include <pxr/base/vt/array.h>
#include <pxr/imaging/hd/aov.h>
#include <pxr/imaging/hd/camera.h>
#include <pxr/imaging/hd/changeTracker.h>
#include <pxr/imaging/hd/extComputation.h>
#include <pxr/imaging/hd/extComputationUtils.h>
#include <pxr/imaging/hd/instancer.h>
#include <pxr/imaging/hd/material.h>
#include <pxr/imaging/hd/mesh.h>
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
#include <pxr/usd/sdf/assetPath.h>

#include <algorithm>
#include <cstring>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <tuple>
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
// Motion is sampled this many frames either side of the frame, and the
// camera's shutter is held to that window.
float const _motionWindow = 1.0f;

// An on/off setting: a bool or int render setting, else `env` set to 1,
// true or on.
bool _SettingOn(VtValue const& value, char const* env)
{
    if (value.IsHolding<bool>()) return value.UncheckedGet<bool>();
    if (value.IsHolding<int>()) return value.UncheckedGet<int>() != 0;
    std::string const s = _EnvLower(env);
    return s == "1" || s == "true" || s == "on";
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

// What the render delegate tells its prims as they sync: whether to sample
// their motion (th:motionBlur).
struct ThRenderParam final : public HdRenderParam
{
    std::atomic<bool> motion{false};
};

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

// One value per triangle corner (3 per triangle) of primvar `name`, or empty
// when it does not have that type or is short of values. `corners` and
// `faces` are th_triangulate's: per triangle, three positions in the face
// vertex indices `fvi` (where a face-varying value is too) and its face.
template <typename T>
std::vector<T> _CornerValues(HdSceneDelegate* delegate, SdfPath const& id, TfToken const& name,
                             HdInterpolation interp, std::vector<uint32_t> const& corners,
                             std::vector<uint32_t> const& faces, VtIntArray const& fvi)
{
    VtIntArray indices;
    VtArray<T> values = _AsArray<T>(delegate->GetIndexedPrimvar(id, name, &indices));
    if (values.empty()) {
        return {};
    }
    if (!indices.empty()) {
        VtArray<T> flat(indices.size());
        for (size_t i = 0; i < indices.size(); ++i) {
            int const at = indices[i];
            flat[i] = at >= 0 && static_cast<size_t>(at) < values.size() ? values[at] : T();
        }
        values = flat;
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
// Instancer
// -----------------------------------------------------------------------------

class ThInstancer final : public HdInstancer
{
public:
    ThInstancer(HdSceneDelegate* delegate, SdfPath const& id) : HdInstancer(delegate, id) {}

    void Sync(HdSceneDelegate* delegate, HdRenderParam* param, HdDirtyBits* dirtyBits) override
    {
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

    void Sync(HdSceneDelegate* delegate, HdRenderParam*, HdDirtyBits* dirtyBits) override
    {
        if (*dirtyBits & HdMaterial::DirtyResource) {
            _hasColor = false;
            _usesDisplayColor = false;
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

private:
    // The base-colour input of the surface shader, whichever family it is.
    void _Read(HdMaterialNetwork2 const& net)
    {
        auto terminal = net.terminals.find(HdMaterialTerminalTokens->surface);
        if (terminal == net.terminals.end()) return;
        auto surface = net.nodes.find(terminal->second.upstreamNode);
        if (surface == net.nodes.end()) return;
        HdMaterialNode2 const& node = surface->second;

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

    enum class Source { Unknown, Texture, DisplayColor };

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

    // Fill `view` from this mesh; false when it has nothing to draw.
    // `instancesAt` gives its instances' transforms (none for a mesh not
    // instanced) at a moment of the shutter, or the frame's for null. With
    // the shutter open, the view also gets the mesh's points at its opening
    // and closing, and its transforms at keys across it, when they move.
    bool View(ThMeshView* view, std::vector<double>* transforms, std::vector<double>* keys,
              ThMaterial const* material,
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
                    view->corner_uvs = uv->second.data();
                    view->tiles = material->Tiles().data();
                    view->tile_count = static_cast<uint32_t>(material->Tiles().size());
                }
            }
        } else if (!_colors.empty()) {
            view->corner_colors = _colors.data();
        }
        view->transforms = transforms->data();
        view->transform_count = static_cast<uint32_t>(transforms->size() / 16);
        if (shutter.on) {
            _Motion(view, keys, instancesAt, instancerKeys, shutter, instances.size());
        }
        return true;
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
    // The shutter's motion of this mesh into `view`: its points at the
    // opening and closing (from sampled points, else velocities), and its
    // transforms at keys across it. Neither when it holds still.
    void _Motion(ThMeshView* view, std::vector<double>* keys,
                 std::function<std::vector<GfMatrix4d>(float const*)> const& instancesAt,
                 size_t instancerKeys, _Shutter const& s, size_t instanceCount) const
    {
        size_t const n = _points.size() / 3;
        bool const samples = _pointSamples.count >= 2;
        if (samples || !_velocities.empty()) {
            if (!(_motionFor == std::make_tuple(_version, s.open, s.close, s.fps))) {
                _motionFor = std::make_tuple(_version, s.open, s.close, s.fps);
                ++_motionVersion;
                _open.assign(n * 3, 0.0f);
                _close.assign(n * 3, 0.0f);
                auto fill = [&](std::vector<float>& out, float t) {
                    if (samples) {
                        VtVec3fArray const p = _pointSamples.Resample(t);
                        for (size_t i = 0; i < n && i < p.size(); ++i) {
                            for (int c = 0; c < 3; ++c) out[i * 3 + c] = p[i][c];
                        }
                        return;
                    }
                    float const sec = t / s.fps;
                    for (size_t i = 0; i < n; ++i) {
                        for (int c = 0; c < 3; ++c) {
                            float a = _accelerations.empty() ? 0.0f : _accelerations[i][c];
                            out[i * 3 + c] = _points[i * 3 + c] + _velocities[i][c] * sec + 0.5f * a * sec * sec;
                        }
                    }
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
        if (points.empty() || points.size() * 3 != _points.size()) return false;
        _PointMotion const m = _ReadMotion(delegate, computed, points.size(), motion);
        static_assert(sizeof(GfVec3f) == 3 * sizeof(float), "GfVec3f is three packed floats");
        bool const samePoints = std::memcmp(points.cdata(), _points.data(), _points.size() * sizeof(float)) == 0;
        if (samePoints && _SameMotion(m)) return true;
        ++_version;  // the GPU path's copy of the old points is stale
        std::memcpy(_points.data(), points.cdata(), _points.size() * sizeof(float));
        _SetMotion(m);
        return true;
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

        HdMeshTopology const topology = GetMeshTopology(delegate);

        HdExtComputationPrimvarDescriptorVector computed;
        VtVec3fArray const points = _ReadPoints(delegate, &computed);
        if (points.empty()) return;

        // Not HdMeshUtil::ComputeTriangleIndices: it fans every face, which
        // fills a concave face's notch and a keyhole face's opening (a wall's
        // window). th_triangulate keeps the fan for a convex face and
        // ear-clips a concave one, wound as HdMeshUtil winds it.
        VtIntArray const& counts = topology.GetFaceVertexCounts();
        VtIntArray const& fvi = topology.GetFaceVertexIndices();
        VtIntArray const& holes = topology.GetHoleIndices();
        size_t capacity = 0;
        for (int const c : counts) {
            if (c > 2) capacity += static_cast<size_t>(c - 2);
        }
        if (capacity == 0 || capacity > UINT32_MAX / 3) return;
        std::vector<uint32_t> corners(capacity * 3);
        std::vector<uint32_t> faces(capacity);
        static_assert(sizeof(GfVec3f) == 3 * sizeof(float), "GfVec3f is three packed floats");
        uint32_t const count = th_triangulate(
            reinterpret_cast<float const*>(points.cdata()), static_cast<uint32_t>(points.size()),
            counts.cdata(), static_cast<uint32_t>(counts.size()),
            fvi.cdata(), static_cast<uint32_t>(fvi.size()),
            holes.cdata(), static_cast<uint32_t>(holes.size()),
            topology.GetOrientation() != HdTokens->rightHanded ? 1u : 0u,
            corners.data(), faces.data(), static_cast<uint32_t>(capacity));
        if (count == UINT32_MAX || count == 0) return;
        corners.resize(static_cast<size_t>(count) * 3);
        faces.resize(count);

        _points.reserve(points.size() * 3);
        for (GfVec3f const& p : points) {
            _points.insert(_points.end(), {p[0], p[1], p[2]});
        }

        _SetMotion(_ReadMotion(delegate, computed, points.size(), motion));
        _triangles.reserve(corners.size());
        for (uint32_t const c : corners) {
            _triangles.push_back(static_cast<uint32_t>(std::max(0, fvi[c])));
        }

        for (HdInterpolation interp :
             {HdInterpolationConstant, HdInterpolationUniform, HdInterpolationVarying,
              HdInterpolationVertex, HdInterpolationFaceVarying}) {
            for (HdPrimvarDescriptor const& pv : delegate->GetPrimvarDescriptors(id, interp)) {
                if (pv.name == _tokDisplayColor) {
                    auto c = _CornerValues<GfVec3f>(delegate, id, pv.name, interp, corners, faces, fvi);
                    _colors.clear();
                    for (GfVec3f const& v : c) _colors.insert(_colors.end(), {v[0], v[1], v[2]});
                } else if (pv.name == HdTokens->normals) {
                    auto n = _CornerValues<GfVec3f>(delegate, id, pv.name, interp, corners, faces, fvi);
                    _normals.clear();
                    for (GfVec3f const& v : n) _normals.insert(_normals.end(), {v[0], v[1], v[2]});
                } else if (pv.role == HdPrimvarRoleTokens->textureCoordinate
                           || pv.name == TfToken("st") || pv.name == TfToken("uv")) {
                    auto uv = _CornerValues<GfVec2f>(delegate, id, pv.name, interp, corners, faces, fvi);
                    if (!uv.empty()) {
                        std::vector<float>& out = _uvs[pv.name];
                        for (GfVec2f const& v : uv) out.insert(out.end(), {v[0], v[1]});
                    }
                }
            }
        }

        // A polygonal mesh (scheme none or bilinear, which is what implicit
        // cubes become) with no normals gets Houdini's cusp normals rather
        // than smoothing across every edge; the core computes them (ffi.rs).
        TfToken const scheme = topology.GetScheme();
        _polygonal = scheme == _tokNone || scheme == _tokBilinear;
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
    bool _polygonal = false;
    uint64_t const _key;
    uint64_t _version = 0;
};

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
        return true;
    }

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

// What a pass blurs (th:motionBlur, th:depthOfField, th:blurSamples, th:fps).
struct _BlurSettings
{
    bool motion = false;
    bool dof = false;
    int samples = _defaultBlurSamples;
    float fps = 24.0f;
};

// -----------------------------------------------------------------------------
// Render pass
// -----------------------------------------------------------------------------

class ThRenderPass final : public HdRenderPass
{
public:
    ThRenderPass(HdRenderIndex* index, HdRprimCollection const& collection, int samples,
                 uint32_t shading, bool ao, _BlurSettings blur, ThCache const* cache)
        : HdRenderPass(index, collection), _samples(samples), _shading(shading), _ao(ao),
          _blur(blur), _cache(cache) {}

    bool IsConverged() const override { return true; }

protected:
    void _Execute(HdRenderPassStateSharedPtr const& state,
                  TfTokenVector const& renderTags) override
    {
        auto const started = std::chrono::steady_clock::now();
        ThRenderBuffer* colorBuffer = nullptr;
        ThRenderBuffer* depthBuffer = nullptr;
        std::vector<ThRenderBuffer*> others;
        for (HdRenderPassAovBinding const& binding : state->GetAovBindings()) {
            auto* buffer = dynamic_cast<ThRenderBuffer*>(binding.renderBuffer);
            if (!buffer || buffer->GetWidth() == 0 || buffer->GetHeight() == 0) continue;
            if (binding.aovName == HdAovTokens->color) colorBuffer = buffer;
            else if (binding.aovName == HdAovTokens->depth) depthBuffer = buffer;
            else others.push_back(buffer);
        }
        for (ThRenderBuffer* b : others) b->Clear(-1.0f);
        ThRenderBuffer* sized = colorBuffer ? colorBuffer : depthBuffer;
        if (!sized) return;

        unsigned int const width = sized->GetWidth();
        unsigned int const height = sized->GetHeight();
        GfMatrix4d const worldToView = state->GetWorldToViewMatrix();
        GfMatrix4d const worldToClip = worldToView * state->GetProjectionMatrix();
        GfMatrix4d const viewToWorld = worldToView.GetInverse();

        ThFrame frame{};
        frame.width = width;
        frame.height = height;
        std::copy(worldToClip.GetArray(), worldToClip.GetArray() + 16, frame.world_to_clip);
        std::copy(viewToWorld.GetArray(), viewToWorld.GetArray() + 16, frame.view_to_world);
        frame.samples = static_cast<uint32_t>(std::max(1, _samples));
        frame.shading = _shading;
        frame.ao = _ao ? 1u : 0u;

        // Motion blur over the render camera's shutter (held to the window
        // the prims sampled their motion over), depth of field from its lens.
        HdCamera const* camera = state->GetCamera();
        _Shutter shutter;
        shutter.fps = _blur.fps;
        if (_blur.motion && camera) {
            shutter.open = static_cast<float>(std::clamp(camera->GetShutterOpen(), -1.0 * _motionWindow, 1.0 * _motionWindow));
            shutter.close = static_cast<float>(std::clamp(camera->GetShutterClose(), -1.0 * _motionWindow, 1.0 * _motionWindow));
            shutter.on = shutter.close > shutter.open;
        }
        if (_blur.motion || _blur.dof) {
            frame.blur_samples = static_cast<uint32_t>(std::max(1, _blur.samples));
        }
        frame.motion = shutter.on ? 1u : 0u;
        if (auto const* thCamera = dynamic_cast<ThCamera const*>(camera)) {
            frame.camera_keys = thCamera->Keys(shutter, frame.cameras);
        }
        if (_blur.dof && camera) {
            frame.focal_length = camera->GetFocalLength();
            frame.f_stop = camera->GetFStop();
            frame.focus_distance = camera->GetFocusDistance();
        }

        // The meshes this pass draws: visible, and of a render tag (purpose)
        // the pass asked for.
        HdRenderIndex& index = *GetRenderIndex();
        std::vector<ThMeshView> views;
        std::vector<std::vector<double>> transforms;
        std::vector<std::vector<double>> keys;
        std::vector<ThMesh const*> meshes;
        for (SdfPath const& id : index.GetRprimIds()) {
            auto const* mesh = dynamic_cast<ThMesh const*>(index.GetRprim(id));
            if (!mesh || !mesh->IsVisible()) continue;
            if (!renderTags.empty()
                && std::find(renderTags.begin(), renderTags.end(), mesh->GetRenderTag())
                       == renderTags.end()) {
                continue;
            }
            meshes.push_back(mesh);
        }
        views.reserve(meshes.size());
        transforms.resize(meshes.size());
        keys.resize(meshes.size());
        for (size_t i = 0; i < meshes.size(); ++i) {
            ThMesh const* mesh = meshes[i];
            ThInstancer* instancer = mesh->GetInstancerId().IsEmpty() ? nullptr
                : dynamic_cast<ThInstancer*>(index.GetInstancer(mesh->GetInstancerId()));
            auto instancesAt = [&](float const* time) {
                return instancer ? instancer->ComputeInstanceTransforms(mesh->GetId(), time)
                                 : std::vector<GfMatrix4d>{};
            };
            size_t const instancerKeys = instancer && shutter.on ? instancer->KeyCount(shutter) : 0;
            ThMaterial const* material = nullptr;
            if (!mesh->GetMaterialId().IsEmpty()) {
                material = dynamic_cast<ThMaterial const*>(
                    index.GetSprim(HdPrimTypeTokens->material, mesh->GetMaterialId()));
            }
            ThMeshView view;
            if (mesh->View(&view, &transforms[i], &keys[i], material, instancesAt, instancerKeys, shutter)) {
                views.push_back(view);
            }
        }

        auto const gathered = std::chrono::steady_clock::now();
        size_t const texels = static_cast<size_t>(width) * height;
        std::vector<float> color(texels * 4);
        std::vector<float> depth(texels);
        int32_t const status = th_render(&frame, views.data(),
                                         static_cast<uint32_t>(views.size()),
                                         color.data(), depth.data(), _cache);
        if (status != 0) {
            TF_WARN("hdThPlayblast: the core failed to render (error %d)", status);
            return;
        }
        if (colorBuffer) colorBuffer->Write(color, 4);
        if (depthBuffer) depthBuffer->Write(depth, 1);

        // TH_PLAYBLAST_TIMING=1: where a frame's time goes (the scene sync
        // husk does before this pass is the gap between two lines).
        if (std::getenv("TH_PLAYBLAST_TIMING")) {
            auto const done = std::chrono::steady_clock::now();
            auto ms = [](auto a, auto b) {
                return std::chrono::duration_cast<std::chrono::milliseconds>(b - a).count();
            };
            size_t triangles = 0, instances = 0;
            for (ThMeshView const& v : views) {
                triangles += static_cast<size_t>(v.triangle_count) * v.transform_count;
                instances += v.transform_count;
            }
            static std::chrono::steady_clock::time_point previous;
            long long const since =
                previous.time_since_epoch().count() ? ms(previous, done) : 0;
            previous = done;
            size_t moving = 0;
            for (ThMeshView const& v : views) {
                moving += (v.points_close || v.transform_key_count) ? 1 : 0;
            }
            std::fprintf(stderr,
                         "hdThPlayblast: %zu meshes (%zu moving), %zu instances, %zu triangles; "
                         "blur %u samples, shutter %s %.3g..%.3g, %u camera keys, f-stop %.3g; "
                         "gather %lld ms, render %lld ms; mesh sync %lld ms (thread time), "
                         "%d rebuilt, %d with new points only; %lld ms since the last frame\n",
                         views.size(), moving, instances, triangles,
                         frame.blur_samples, shutter.on ? "open" : "off", shutter.open, shutter.close,
                         frame.camera_keys, frame.f_stop,
                         static_cast<long long>(ms(started, gathered)),
                         static_cast<long long>(ms(gathered, done)),
                         g_syncNs.exchange(0) / 1000000, g_rebuilds.exchange(0), g_pointUpdates.exchange(0), since);
        }
    }

private:
    int _samples;
    uint32_t _shading;
    bool _ao;
    _BlurSettings _blur;
    ThCache const* _cache;  // the delegate's; outlives its passes
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

    void SetRenderSetting(TfToken const& key, VtValue const& value) override
    {
        HdRenderDelegate::SetRenderSetting(key, value);
        _UpdateParam();
    }
    ~ThRenderDelegate() override { th_cache_free(_cache); }

    TfTokenVector const& GetSupportedRprimTypes() const override
    {
        static TfTokenVector const types{HdPrimTypeTokens->mesh};
        return types;
    }
    TfTokenVector const& GetSupportedSprimTypes() const override
    {
        static TfTokenVector const types{HdPrimTypeTokens->camera, HdPrimTypeTokens->material,
                                         HdPrimTypeTokens->extComputation};
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
        int samples = _defaultSamples;
        VtValue const samplesSetting = GetRenderSetting(_tokSamples);
        if (samplesSetting.IsHolding<int>()) {
            samples = samplesSetting.UncheckedGet<int>();
        } else if (char const* env = std::getenv("TH_PLAYBLAST_SAMPLES")) {
            if (int const value = std::atoi(env); value > 0) samples = value;
        }
        std::string shading = _SettingString(GetRenderSetting(_tokShading));
        if (shading.empty()) shading = _EnvLower("TH_PLAYBLAST_SHADING");
        VtValue const aoSetting = GetRenderSetting(_tokAo);
        bool ao = true;
        if (aoSetting.IsHolding<bool>()) {
            ao = aoSetting.UncheckedGet<bool>();
        } else if (aoSetting.IsHolding<int>()) {
            ao = aoSetting.UncheckedGet<int>() != 0;
        } else {
            std::string const env = _EnvLower("TH_PLAYBLAST_AO");
            ao = !(env == "0" || env == "false" || env == "off");
        }
        _BlurSettings blur;
        blur.motion = _SettingOn(GetRenderSetting(_tokMotionBlur), "TH_PLAYBLAST_MOTION_BLUR");
        blur.dof = _SettingOn(GetRenderSetting(_tokDepthOfField), "TH_PLAYBLAST_DOF");
        blur.samples = static_cast<int>(_SettingNumber(GetRenderSetting(_tokBlurSamples),
                                                       "TH_PLAYBLAST_BLUR_SAMPLES", _defaultBlurSamples));
        blur.fps = static_cast<float>(_SettingNumber(GetRenderSetting(_tokFps), "TH_PLAYBLAST_FPS", 24.0));
        return std::make_shared<ThRenderPass>(index, collection, samples,
                                              _ShadingNumber(shading), ao, blur, _cache);
    }

    HdInstancer* CreateInstancer(HdSceneDelegate* delegate, SdfPath const& id) override
    {
        return new ThInstancer(delegate, id);
    }
    void DestroyInstancer(HdInstancer* instancer) override { delete instancer; }

    HdRprim* CreateRprim(TfToken const& typeId, SdfPath const& id) override
    {
        if (typeId == HdPrimTypeTokens->mesh) {
            return new ThMesh(id);
        }
        return nullptr;
    }
    void DestroyRprim(HdRprim* rprim) override { delete rprim; }

    HdSprim* CreateSprim(TfToken const& typeId, SdfPath const& id) override
    {
        if (typeId == HdPrimTypeTokens->camera) return new ThCamera(id);
        if (typeId == HdPrimTypeTokens->material) return new ThMaterial(id, &_textures);
        if (typeId == HdPrimTypeTokens->extComputation) return new HdExtComputation(id);
        return nullptr;
    }
    HdSprim* CreateFallbackSprim(TfToken const& typeId) override
    {
        return CreateSprim(typeId, SdfPath::EmptyPath());
    }
    void DestroySprim(HdSprim* sprim) override { delete sprim; }

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
        "TumblePipe Playblast",
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
