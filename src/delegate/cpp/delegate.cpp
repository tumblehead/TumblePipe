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
#include <pxr/imaging/hd/meshUtil.h>
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
#include <pxr/imaging/hd/tokens.h>
#include <pxr/imaging/hd/vtBufferSource.h>
#include <pxr/imaging/hdsi/implicitSurfaceSceneIndex.h>
#include <pxr/imaging/hio/image.h>
#include <pxr/usd/sdf/assetPath.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

TfToken const _tokDisplayColor("displayColor");
TfToken const _tokNone("none");          // subdivision schemes drawn faceted
TfToken const _tokBilinear("bilinear");
TfToken const _tokSupersample("th:supersample");
int const _defaultSupersample = 2;

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
// when it does not have that type or is short of values.
template <typename T>
std::vector<T> _CornerValues(HdSceneDelegate* delegate, SdfPath const& id, TfToken const& name,
                             HdInterpolation interp, HdMeshUtil const& util,
                             VtVec3iArray const& tris, VtIntArray const& params)
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
    size_t const n = tris.size();
    std::vector<T> out(n * 3);
    switch (interp) {
    case HdInterpolationConstant:
        std::fill(out.begin(), out.end(), values[0]);
        return out;
    case HdInterpolationUniform:
        for (size_t t = 0; t < n; ++t) {
            int const face = HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(params[t]);
            if (face < 0 || static_cast<size_t>(face) >= values.size()) return {};
            out[t * 3] = out[t * 3 + 1] = out[t * 3 + 2] = values[face];
        }
        return out;
    case HdInterpolationVertex:
    case HdInterpolationVarying:
        for (size_t t = 0; t < n; ++t) {
            for (int k = 0; k < 3; ++k) {
                int const v = tris[t][k];
                if (v < 0 || static_cast<size_t>(v) >= values.size()) return {};
                out[t * 3 + k] = values[v];
            }
        }
        return out;
    case HdInterpolationFaceVarying: {
        VtValue triangulated;
        HdVtBufferSource source(name, VtValue(values));
        HdMeshComputationResult const result = util.ComputeTriangulatedFaceVaryingPrimvar(
            source.GetData(), source.GetNumElements(), source.GetTupleType().type,
            &triangulated);
        if (result == HdMeshComputationResult::Error) {
            return {};
        }
        // Unchanged: every face was already a triangle, the input is the
        // triangulated primvar.
        VtArray<T> tv = result == HdMeshComputationResult::Unchanged
            ? values : _AsArray<T>(triangulated);
        if (tv.size() != n * 3) return {};
        std::copy(tv.begin(), tv.end(), out.begin());
        return out;
    }
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

    void Sync(HdSceneDelegate* delegate, HdRenderParam*, HdDirtyBits* dirtyBits) override
    {
        _UpdateInstancer(delegate, dirtyBits);
        SdfPath const& id = GetId();
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

    // Object-to-world of every instance of `prototypeId`, parents applied.
    std::vector<GfMatrix4d> ComputeInstanceTransforms(SdfPath const& prototypeId)
    {
        HdSceneDelegate* delegate = GetDelegate();
        GfMatrix4d const instancerXform = delegate->GetInstancerTransform(GetId());
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
        std::vector<GfMatrix4d> const parents = parent->ComputeInstanceTransforms(GetId());
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

class ThMesh final : public HdMesh
{
public:
    explicit ThMesh(SdfPath const& id) : HdMesh(id) {}

    HdDirtyBits GetInitialDirtyBitsMask() const override
    {
        return HdChangeTracker::AllSceneDirtyBits;
    }

    void Sync(HdSceneDelegate* delegate, HdRenderParam*, HdDirtyBits* dirtyBits,
              TfToken const&) override
    {
        SdfPath const& id = GetId();
        if (*dirtyBits & HdChangeTracker::DirtyMaterialId) {
            SetMaterialId(delegate->GetMaterialId(id));
        }
        if (HdChangeTracker::IsVisibilityDirty(*dirtyBits, id)) {
            _UpdateVisibility(delegate, dirtyBits);
        }
        if (HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
            _transform = delegate->GetTransform(id);
        }
        _UpdateInstancer(delegate, dirtyBits);
        HdInstancer::_SyncInstancerAndParents(delegate->GetRenderIndex(), GetInstancerId());

        if (HdChangeTracker::IsTopologyDirty(*dirtyBits, id)
            || HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->points)
            || HdChangeTracker::IsAnyPrimvarDirty(*dirtyBits, id)) {
            _Rebuild(delegate);
        }
        *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
    }

    // Fill `view` from this mesh; false when it has nothing to draw.
    bool View(ThMeshView* view, std::vector<double>* transforms, ThMaterial const* material,
              std::vector<GfMatrix4d> const& instances) const
    {
        if (_points.empty() || _triangles.empty()) return false;
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
        view->faceted = _faceted ? 1u : 0u;
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
    void _Rebuild(HdSceneDelegate* delegate)
    {
        SdfPath const& id = GetId();
        _points.clear();
        _triangles.clear();
        _normals.clear();
        _colors.clear();
        _uvs.clear();

        HdMeshTopology const topology = GetMeshTopology(delegate);

        // Points: computed (skinning, deformers) or authored.
        VtValue pointsValue;
        HdExtComputationPrimvarDescriptorVector computed;
        for (auto const& desc :
             delegate->GetExtComputationPrimvarDescriptors(id, HdInterpolationVertex)) {
            if (desc.name == HdTokens->points) computed.push_back(desc);
        }
        if (!computed.empty()) {
            auto const values = HdExtComputationUtils::GetComputedPrimvarValues(computed, delegate);
            auto it = values.find(HdTokens->points);
            if (it != values.end()) pointsValue = it->second;
        } else {
            pointsValue = delegate->Get(id, HdTokens->points);
        }
        VtVec3fArray const points = _AsArray<GfVec3f>(pointsValue);
        if (points.empty()) return;

        HdMeshUtil const util(&topology, id);
        VtVec3iArray tris;
        VtIntArray params;
        util.ComputeTriangleIndices(&tris, &params);
        if (tris.empty()) return;

        _points.reserve(points.size() * 3);
        for (GfVec3f const& p : points) {
            _points.insert(_points.end(), {p[0], p[1], p[2]});
        }
        _triangles.reserve(tris.size() * 3);
        for (GfVec3i const& t : tris) {
            for (int k = 0; k < 3; ++k) {
                _triangles.push_back(static_cast<uint32_t>(std::max(0, t[k])));
            }
        }

        for (HdInterpolation interp :
             {HdInterpolationConstant, HdInterpolationUniform, HdInterpolationVarying,
              HdInterpolationVertex, HdInterpolationFaceVarying}) {
            for (HdPrimvarDescriptor const& pv : delegate->GetPrimvarDescriptors(id, interp)) {
                if (pv.name == _tokDisplayColor) {
                    auto c = _CornerValues<GfVec3f>(delegate, id, pv.name, interp, util, tris, params);
                    _colors.clear();
                    for (GfVec3f const& v : c) _colors.insert(_colors.end(), {v[0], v[1], v[2]});
                } else if (pv.name == HdTokens->normals) {
                    auto n = _CornerValues<GfVec3f>(delegate, id, pv.name, interp, util, tris, params);
                    _normals.clear();
                    for (GfVec3f const& v : n) _normals.insert(_normals.end(), {v[0], v[1], v[2]});
                } else if (pv.role == HdPrimvarRoleTokens->textureCoordinate
                           || pv.name == TfToken("st") || pv.name == TfToken("uv")) {
                    auto uv = _CornerValues<GfVec2f>(delegate, id, pv.name, interp, util, tris, params);
                    if (!uv.empty()) {
                        std::vector<float>& out = _uvs[pv.name];
                        for (GfVec2f const& v : uv) out.insert(out.end(), {v[0], v[1]});
                    }
                }
            }
        }

        // A polygonal mesh (scheme none or bilinear, which is what implicit
        // cubes become) is drawn faceted when it has no normals, as Storm
        // draws it; the core decides what that means (ffi.rs).
        TfToken const scheme = topology.GetScheme();
        _faceted = scheme == _tokNone || scheme == _tokBilinear;
    }

    GfMatrix4d _transform{1.0};
    std::vector<float> _points;
    std::vector<uint32_t> _triangles;
    std::vector<float> _normals;
    std::vector<float> _colors;
    std::map<TfToken, std::vector<float>> _uvs;
    bool _faceted = false;
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
// Render pass
// -----------------------------------------------------------------------------

class ThRenderPass final : public HdRenderPass
{
public:
    ThRenderPass(HdRenderIndex* index, HdRprimCollection const& collection, int supersample)
        : HdRenderPass(index, collection), _supersample(supersample) {}

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
        frame.supersample = static_cast<uint32_t>(std::max(1, _supersample));

        // The meshes this pass draws: visible, and of a render tag (purpose)
        // the pass asked for.
        HdRenderIndex& index = *GetRenderIndex();
        std::vector<ThMeshView> views;
        std::vector<std::vector<double>> transforms;
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
        for (size_t i = 0; i < meshes.size(); ++i) {
            ThMesh const* mesh = meshes[i];
            std::vector<GfMatrix4d> instances;
            if (!mesh->GetInstancerId().IsEmpty()) {
                if (auto* instancer = dynamic_cast<ThInstancer*>(
                        index.GetInstancer(mesh->GetInstancerId()))) {
                    instances = instancer->ComputeInstanceTransforms(mesh->GetId());
                }
            }
            ThMaterial const* material = nullptr;
            if (!mesh->GetMaterialId().IsEmpty()) {
                material = dynamic_cast<ThMaterial const*>(
                    index.GetSprim(HdPrimTypeTokens->material, mesh->GetMaterialId()));
            }
            ThMeshView view;
            if (mesh->View(&view, &transforms[i], material, instances)) {
                views.push_back(view);
            }
        }

        auto const gathered = std::chrono::steady_clock::now();
        size_t const texels = static_cast<size_t>(width) * height;
        std::vector<float> color(texels * 4);
        std::vector<float> depth(texels);
        int32_t const status = th_render(&frame, views.data(),
                                         static_cast<uint32_t>(views.size()),
                                         color.data(), depth.data());
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
            std::fprintf(stderr,
                         "hdThPlayblast: %zu meshes, %zu instances, %zu triangles; "
                         "gather %lld ms, render %lld ms\n",
                         views.size(), instances, triangles,
                         static_cast<long long>(ms(started, gathered)),
                         static_cast<long long>(ms(gathered, done)));
        }
    }

private:
    int _supersample;
};

// -----------------------------------------------------------------------------
// Render delegate
// -----------------------------------------------------------------------------

class ThRenderDelegate final : public HdRenderDelegate
{
public:
    ThRenderDelegate() : _registry(std::make_shared<HdResourceRegistry>()) {}
    explicit ThRenderDelegate(HdRenderSettingsMap const& settings)
        : HdRenderDelegate(settings), _registry(std::make_shared<HdResourceRegistry>()) {}

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
        int const supersample = GetRenderSetting<int>(_tokSupersample, _defaultSupersample);
        return std::make_shared<ThRenderPass>(index, collection, supersample);
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
        if (typeId == HdPrimTypeTokens->camera) return new HdCamera(id);
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
    HdResourceRegistrySharedPtr _registry;
    TextureCache _textures;
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
