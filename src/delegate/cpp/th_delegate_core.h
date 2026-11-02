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
};

// 0 = ok, 1 = bad arguments, 2 = the core panicked. `cache` may be null; one
// call at a time per cache.
int32_t th_render(ThFrame const* frame, ThMeshView const* meshes, uint32_t mesh_count,
                  float* color, float* depth, ThCache const* cache);

// One per render delegate; freed with it.
ThCache* th_cache_create();
void th_cache_free(ThCache* cache);

// pixels: width * height linear RGBA, row 0 at the top. Null on failure.
ThTexture* th_texture_create(float const* pixels, uint32_t width, uint32_t height);
void th_texture_free(ThTexture* texture);

}  // extern "C"
