// C ABI of the Rust core (src/ffi.rs). Keep the two in step.

#pragma once

#include <cstdint>

extern "C" {

// Opaque: a texture the core has shrunk and keeps (src/texture.rs).
struct ThTexture;

// Opaque: what a render delegate keeps between frames (src/ffi.rs ThCache):
// on the GPU path, its meshes' geometry and its textures on the card.
struct ThCache;

struct ThTextureTile {
    int32_t tile;               // UDIM number, 0 for a plain texture
    ThTexture const* texture;
};

struct ThMeshView {
    float const* points;        // point_count xyz, object space
    uint32_t point_count;
    uint32_t const* triangles;  // triangle_count index triples
    uint32_t triangle_count;
    float const* corner_normals;  // optional, 9 per triangle
    float const* corner_colors;   // optional, 9 per triangle, linear rgb
    float const* corner_uvs;      // optional, 6 per triangle
    float base_color[3];
    ThTextureTile const* tiles;
    uint32_t tile_count;
    double const* transforms;   // transform_count x 16, USD row-vector layout
    uint32_t transform_count;
    uint32_t polygonal;         // nonzero: scheme none/bilinear; cusp normals when no corner_normals
    uint64_t key;               // the Hydra mesh, unique in the process; 0 = none
    uint64_t version;           // changes whenever the mesh is rebuilt
    // Motion blur (optional): points at the shutter's closing (`points` are
    // then at its opening), and transform_key_count (2..4) transforms per
    // instance at moments evenly spread over the shutter, key-major.
    float const* points_close;
    double const* transform_keys;
    uint32_t transform_key_count;
    // One minus the surface's opacity, so a zeroed view is opaque: 0 opaque;
    // between 0 and 1 see-through, blended over the opaque surfaces (lit, no
    // depth written); 1 or more not drawn.
    float transparency;
    // The side of the triangles not drawn: 0 neither, 1 the back, 2 the
    // front (counter-clockwise is the front, through each instance's
    // transform; a mirroring one swaps them).
    uint32_t cull;
};

// A basis curves or points prim (src/curves.rs): drawn as camera-facing
// ribbons and discs the core makes for each frame, never thinner than a
// pixel or two. Interpolations: 0 constant, 1 uniform, 2 varying, 3 vertex.
struct ThCurvesView {
    float const* points;        // point_count xyz, object space
    uint32_t point_count;
    int32_t const* curve_vertex_counts;  // curve_count; ignored for points
    uint32_t curve_count;
    uint32_t kind;              // 0 points, 1 linear curves, 2 cubic curves
    uint32_t basis;             // cubic: 0 bezier, 1 bspline, 2 catmullRom
    uint32_t wrap;              // 0 nonperiodic, 1 periodic, 2 pinned
    float const* widths;        // optional, object space
    uint32_t width_count;
    uint32_t width_interp;
    float const* colors;        // optional, linear rgb
    uint32_t color_count;
    uint32_t color_interp;
    float base_color[3];
    double const* transforms;   // transform_count x 16, as ThMeshView's
    uint32_t transform_count;
    float const* points_close;  // motion blur, optional
    double const* transform_keys;
    uint32_t transform_key_count;
};

// A UsdLux light as authored (src/lights.rs SceneLight): an enabled,
// visible light sprim.
struct ThLight {
    double transform[16];       // light to world, USD row-vector layout
    uint32_t kind;              // 0 distant, 1 sphere, 2 rect, 3 disk, 4 cylinder, 5 dome
    uint32_t normalize;         // nonzero: inputs:normalize
    uint32_t point;             // nonzero: a sphere light's treatAsPoint
    float color[3];             // inputs:color, linear
    float intensity;            // inputs:intensity x 2^inputs:exposure x inputs:diffuse
    float temperature;          // inputs:colorTemperature (K) when enabled, else 0
    float angle;                // distant, degrees
    float radius;
    float width;
    float height;
    float length;
};

struct ThFrame {
    uint32_t width;
    uint32_t height;
    double world_to_clip[16];
    double view_to_world[16];
    uint32_t samples;           // per pixel: 1, 2, 4, 8 or 16 (others: the next fewer)
    uint32_t shading;           // 0 lit, 1 clay, 2 albedo, 3 occlusion
    uint32_t ao;                // nonzero: screen-space ambient occlusion
    uint32_t blur_samples;      // layers over shutter and lens; 0/1 = no blur
    uint32_t motion;            // nonzero: motion blur over the shutter
    uint32_t camera_keys;       // 0, or 2..4 camera-to-world keys over the shutter
    double cameras[64];
    float focal_length;         // HdCamera's, scene units
    float f_stop;               // 0 = pinhole, no depth of field
    float focus_distance;       // scene units
    uint32_t alpha;             // nonzero: transparent background, colour
                                // premultiplied by coverage, alpha = coverage;
                                // 0: the grey backdrop and alpha 1
    uint32_t gpu;               // nonzero: the GPU path where it works (a core
                                // built with it, a usable adapter), else the CPU
    uint32_t linear_depth;      // nonzero: depth out as distance along the
                                // camera's view axis, FLT_MAX where nothing is
                                // drawn (Houdini's viewer, depthstyle linear);
                                // 0: window depth in [0, 1], 1 for nothing
    ThLight const* lights;      // light_count of them, may be null when 0
    uint32_t light_count;
    uint32_t lighting;          // 0: the scene's lights when it has any, else
                                // the fixed camera rig; 1: the rig always
                                // (th:lighting "headlight")
    uint32_t opaque;            // nonzero: every mesh drawn opaque, its
                                // transparency ignored (th:transparency
                                // "off"); 0: see-through meshes blended
                                // over the opaque ones, as before
};

// 0 = ok, 1 = bad arguments, 2 = the core panicked. `cache` may be null; one
// call at a time per cache.
int32_t th_render(ThFrame const* frame, ThMeshView const* meshes, uint32_t mesh_count,
                  float* color, float* depth, ThCache const* cache);

// th_render for a viewport. Also writes `ids` (may be null): per texel, row
// 0 at the top, the index into `meshes` and the instance the texel's first
// sample sees, -1 -1 for none, on the CPU and GPU paths. `cancel` (may be
// null) is a 32-bit atomic read while drawing: set it non-zero from another
// thread to give the frame up, and the call returns 3 having written nothing.
int32_t th_render_ids(ThFrame const* frame, ThMeshView const* meshes, uint32_t mesh_count,
                      float* color, float* depth, int32_t* ids, uint32_t const* cancel,
                      ThCache const* cache);

// th_render_ids with basis curves and points as well. In `ids`, curves
// view j is mesh_count + j, its instance as a mesh's.
int32_t th_render_scene(ThFrame const* frame, ThMeshView const* meshes, uint32_t mesh_count,
                        ThCurvesView const* curves, uint32_t curves_count,
                        float* color, float* depth, int32_t* ids, uint32_t const* cancel,
                        ThCache const* cache);

// Triangles of a polygon mesh: Hydra's fan for a convex face, ear clipping
// for a concave one (src/triangulate.rs). Per triangle, three `corners`
// (positions in `indices`, where face-varying values are too) and its face.
// Returns the triangle count, or UINT32_MAX for a topology that does not add
// up, a short `capacity` (the sum of count - 2 over faces is enough) or a
// null output.
uint32_t th_triangulate(float const* points, uint32_t point_count,
                        int32_t const* counts, uint32_t count_n,
                        int32_t const* indices, uint32_t index_n,
                        int32_t const* holes, uint32_t hole_n, uint32_t left_handed,
                        uint32_t* corners, uint32_t* faces, uint32_t capacity);

// The normals the core draws a mesh with when it has none of its own, one
// xyz per triangle corner (9 * triangle_count floats): cusp normals for a
// polygonal mesh, else smooth point normals at each corner (src/ffi.rs).
// For a mesh drawn as one view per GeomSubset material: each view given the
// whole mesh's normals meets the next without a crease. 0 ok, 1 null.
int32_t th_mesh_normals(float const* points, uint32_t point_count,
                        uint32_t const* triangles, uint32_t triangle_count, uint32_t polygonal,
                        float* corner_normals);

// One per render delegate; freed with it.
ThCache* th_cache_create();
void th_cache_free(ThCache* cache);

// pixels: width * height linear RGBA, row 0 at the top. Null on failure.
ThTexture* th_texture_create(float const* pixels, uint32_t width, uint32_t height);
void th_texture_free(ThTexture* texture);

}  // extern "C"
